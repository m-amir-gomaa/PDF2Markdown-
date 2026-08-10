# PDF2Markdown

A serverless web application that converts PDF files to clean Markdown (and extracts images) using [MinerU](https://github.com/opendatalab/MinerU) running on an NVIDIA A10G GPU in the cloud.

**Live demo:** https://m-amir-gomaa--mineru-pdf-extractor-web-app.modal.run

---

## What it does

1. You upload a PDF through a browser UI.
2. The web server creates a job and immediately returns a live log panel.
3. The PDF is sent to a GPU worker on [Modal](https://modal.com) running MinerU.
4. Progress streams back to your browser in real time via **Server-Sent Events (SSE)**.
5. When extraction finishes, a download button appears. You get a ZIP containing:
   - `auto/<filename>.md` — the extracted Markdown
   - Any images embedded in the PDF (PNG/JPG/WebP)

---

## Architecture

```
Browser
  │
  │  POST /upload  (multipart, PDF bytes + optional Modal credentials)
  ▼
FastAPI  (web/main.py)
  │  asyncio.create_task → run_pipeline()
  │
  │  GET /stream/<job_id>   ← SSE: browser polls for progress
  ▼
web/pipeline.py
  │  loop.run_in_executor()   ← blocks a thread, not the event loop
  ▼
modal_fn.py → extract_pdf.remote()
  │  (Modal serialises bytes, schedules on GPU worker)
  ▼
Modal A10G GPU container
  │  mineru -p <file>.pdf -o /tmp/out -b pipeline
  │  zip up .md + images, filter garbage
  ▼
  bytes returned to web container → stored in JobState.result_zip
  │
  GET /download/<job_id>   → ZIP delivered to browser
```

### Two containers, one Modal app

| Container | Image | Role |
|---|---|---|
| `web_app` | Debian slim + FastAPI/Uvicorn/Jinja2 | Serves HTTP, manages job state, streams SSE |
| `extract_pdf` | Debian slim + MinerU + PyTorch + CUDA | Heavy ML extraction, GPU-accelerated |

They communicate through Modal's RPC layer — `extract_pdf.remote(pdf_bytes, filename)` serialises the call across containers transparently.

---

## File structure

```
PDF2MarkdownPipeline/
├── app.py                  # Modal entry point — wires web_app onto the Modal app
├── modal_fn.py             # GPU worker: MinerU extraction, ZIP output, file filter
│
├── web/
│   ├── main.py             # FastAPI routes: /, /upload, /stream/<id>, /download/<id>
│   ├── jobs.py             # In-memory job store (JobState dataclass + asyncio.Event)
│   ├── pipeline.py         # Bridges async FastAPI ↔ blocking Modal RPC call
│   └── templates/
│       └── index.html      # Jinja2 template — HTMX + Alpine.js UI
│
├── static/
│   └── style.css           # Custom CSS (minimal, layout helpers)
│
└── scripts/
    ├── test_e2e.py         # Full end-to-end test suite (25 tests, httpx async)
    ├── extract_modal.py    # CLI script: extract a local PDF via Modal
    ├── extract_strang.py   # CLI script: alternate extraction approach
    ├── extract_solutions.py# CLI script: solutions/debug variant
    └── record_demo_modal.py# Playwright script to record a GIF demo
```

---

## Key files explained

### `modal_fn.py` — the GPU worker

Defines the Modal `App` object and the `extract_pdf` function.

```python
@app.function(image=mineru_image, gpu="A10G", timeout=3600)
def extract_pdf(pdf_bytes: bytes, base_name: str) -> bytes:
    ...
```

- Receives raw PDF bytes, writes to `/tmp`, runs `mineru` via `subprocess`.
- MinerU outputs a folder with `.md`, images, and several JSON/PDF debug files.
- `should_keep()` filters the output — only `.md`, `.png/.jpg/.webp/.svg`, and specific JSON files are kept.
- Returns a ZIP of the clean output as bytes.

**Image definition** (`mineru_image`): Debian slim + system libs (`libgl1`, `libglib2.0-0`, etc. required by OpenCV/PIL) + pip packages (`mineru[pipeline]`, `torch`, `torchvision`, `transformers`, `accelerate`).

### `app.py` — the Modal entry point

```python
from modal_fn import app   # imports the App object defined there

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi", "uvicorn", "jinja2", "python-multipart")
    .env({"CACHE_BUST": "2"})           # bump this integer to force image rebuild
    .add_local_dir("web", "/root/web")
    .add_local_dir("static", "/root/static")
    .add_local_file("modal_fn.py", "/root/modal_fn.py")  # needed so web container can import it
)

@app.function(image=image)
@modal.asgi_app()
def web_app():
    from web.main import app as fastapi_app
    return fastapi_app
```

> **Why `modal_fn.py` is mounted into the web image:** `app.py` imports `modal_fn` at module level (`from modal_fn import app`). Without it in the container, every boot crashes with `ModuleNotFoundError`.

### `web/jobs.py` — in-memory job store

```python
@dataclass
class JobState:
    job_id: str
    filename: str
    status: Literal["queued", "uploading", "extracting", "done", "error"]
    progress_messages: list[str]
    result_zip: bytes | None
    _new_event: asyncio.Event          # signals SSE clients when new messages arrive
```

`add_message()` appends to `progress_messages` and sets the event. The SSE generator in `main.py` waits on `job.wait_for_new_event()` with a 1-second timeout (to send keep-alive pings).

### `web/pipeline.py` — async ↔ blocking bridge

FastAPI is async. Modal's `.remote()` call is synchronous and blocks until the GPU job finishes (potentially minutes). Running it directly in an `async` function would freeze the entire event loop. The fix:

```python
loop = asyncio.get_running_loop()
result_zip = await loop.run_in_executor(None, _run_modal_sync, pdf_bytes, filename, client)
```

`run_in_executor(None, ...)` sends the blocking call to Python's default `ThreadPoolExecutor`, so FastAPI keeps serving other requests (SSE streaming, health checks) while the GPU job runs.

### `web/main.py` — FastAPI routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Renders `index.html` (Jinja2 template) |
| `POST` | `/upload` | Accepts PDF + optional Modal credentials. Creates a `JobState`, fires `run_pipeline` as a background task, returns HTMX HTML fragment |
| `GET` | `/stream/<job_id>` | SSE endpoint. Yields `data: <html>\n\n` for each log message. On completion sends a single-line HTMX out-of-band swap for the download button |
| `GET` | `/download/<job_id>` | Returns the raw ZIP bytes with `Content-Disposition: attachment` |

**SSE format note:** Each SSE message must have `data:` on the same line as its content:
```
data: <div>Starting GPU extraction on Modal...</div>\n\n
```
Multi-line `data:` blocks (where content is on the next line) confuse streaming HTTP clients even though browsers tolerate them.

### Frontend: HTMX + Alpine.js

- **HTMX** handles the form POST (`hx-post="/upload"`, `hx-target="#result"`) and SSE connection (`hx-ext="sse"`, `sse-connect="/stream/<id>"`).
- **Alpine.js** manages the credential modal (show/hide state, form field binding).
- No build step. No bundler. Both libraries are loaded from CDN.

---

## Modal credentials — how they work

By default, the app uses the **owner's** Modal account credentials (set during `modal token set`) to run GPU jobs. This means all anonymous users share the owner's compute quota.

Users can optionally provide their own Modal API keys (`token_id` + `token_secret`) via the UI. If provided:

```python
custom_client = await modal.Client.from_credentials(token_id, token_secret)
with custom_client:
    extract_pdf.remote(pdf_bytes, filename)
```

The `with client:` context tells Modal's SDK to authenticate that specific call with the user's credentials. Jobs then bill to the user's account.

---

## Deployment (Modal — free tier)

### Prerequisites

```bash
pip install modal
modal token set   # authenticate with your Modal account
```

### Deploy (first time or after changes)

```bash
cd ~/Projects/PDF2MarkdownPipeline
source .venv/bin/activate
modal deploy app.py
```

Output:
```
✓ App deployed in 2.4s! 🎉
└── 🔨 Created web function web_app =>
    https://<your-username>--mineru-pdf-extractor-web-app.modal.run
```

The URL is your live app. **No server to manage.** Modal handles scaling, cold starts, and teardown automatically.

### Force a full image rebuild

If you change Python dependencies or need to bust Modal's image cache, increment `CACHE_BUST` in `app.py`:

```python
.env({"CACHE_BUST": "3"})   # was 2, bump to any new value
```

Then redeploy. Modal will build a fresh image.

### View live logs

```bash
modal app logs mineru-pdf-extractor
```

Or open the Modal dashboard: https://modal.com/apps

### Stop / take down the app

```bash
modal app stop mineru-pdf-extractor
```

This stops the deployed app. To re-enable, run `modal deploy app.py` again.

### Check app status

```bash
modal app list
```

Shows all your deployed apps and their state (`deployed`, `stopped`, etc.).

---

## Running end-to-end tests

The test suite (`scripts/test_e2e.py`) hits the **live deployed app** over HTTPS. It does not mock anything.

```bash
cd ~/Projects/PDF2MarkdownPipeline
source .venv/bin/activate
python scripts/test_e2e.py
```

**What it tests (25 checks):**

| Section | Checks |
|---|---|
| Homepage | 200 OK, HTMX loaded, Alpine loaded, form fields present |
| Static assets | `style.css` served correctly |
| Upload validation | Non-PDF rejected (400), missing file rejected (422) |
| Unknown job IDs | `/stream/<bogus>` → 404, `/download/<bogus>` → 404 |
| Full extraction pipeline | Upload → SSE stream → completion event with download `href` |
| ZIP content | Valid ZIP, contains `.md` file, no garbage files, idempotent re-download |
| Download guard | `/download/<id>` before job completes → 400 |

The test includes a warmup loop (up to 3 minutes) to handle Modal cold starts.

---

## Local development (web server only, no GPU)

You can run the FastAPI web server locally for UI development. GPU calls will fail unless you have Modal credentials configured, but the UI, routing, and SSE plumbing are testable.

```bash
cd ~/Projects/PDF2MarkdownPipeline
source .venv/bin/activate
uvicorn web.main:app --reload --port 8000
```

Open http://localhost:8000.

To stop: `Ctrl+C`.

---

## Known behaviour / gotchas

| Behaviour | Reason |
|---|---|
| First request after idle takes 60–180 seconds | Modal GPU containers cold-start. Subsequent requests within the idle timeout are fast. |
| Job state is lost on container restart | `jobs: dict` is in-memory. A Modal container restart (after idle timeout) clears all jobs. Download links from old sessions will 404. |
| All anonymous users share the owner's GPU quota | By design. Users with their own Modal key can connect it via the credential modal to bill their own account. |
| Modal logs show activity you didn't trigger | Every HTTP request to the web container creates a Modal log entry. The warmup/test scripts also generate entries. |

---

## Tech stack summary

| Layer | Technology | Why |
|---|---|---|
| GPU compute | [MinerU](https://github.com/opendatalab/MinerU) on Modal A10G | State-of-the-art PDF→Markdown, serverless GPU |
| Serverless platform | [Modal](https://modal.com) | Free tier, GPU on demand, Python-native deployment |
| Web framework | [FastAPI](https://fastapi.tiangolo.com) | Async, ASGI, excellent typing support |
| Streaming | Server-Sent Events (SSE) | One-way server→browser push, no WebSocket complexity |
| Frontend interaction | [HTMX](https://htmx.org) | SSE extension, out-of-band swaps, zero JS build step |
| UI state | [Alpine.js](https://alpinejs.dev) | Lightweight reactive state for the credential modal |
| Templating | Jinja2 | Standard Python HTML templating |
| Testing | httpx (async) | Streaming SSE test support, no browser needed |
