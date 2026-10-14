"""Owner-only MinerU compute and a serialized persistent ledger."""

from pathlib import Path
import modal

app = modal.App("mineru-pdf-extractor")
ledger_volume = modal.Volume.from_name(
    "document-studio-ledger-v1", create_if_missing=True
)
result_volume = modal.Volume.from_name(
    "document-studio-results-v1", create_if_missing=True
)
model_volume = modal.Volume.from_name(
    "document-studio-model-cache-v1", create_if_missing=True
)
base = (
    modal.Image.debian_slim(python_version="3.13")
    .pip_install(
        "fastapi==0.141.1",
        "httpx==0.28.1",
        "cryptography==46.0.5",
        "python-multipart==0.0.32",
        "pypdf==6.14.2",
    )
    .add_local_dir("web", "/root/web")
    .add_local_dir("static", "/root/static")
    .add_local_file("modal_fn.py", "/root/modal_fn.py")
)
mineru_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0", "libsm6", "libxext6", "libxrender-dev")
    .pip_install("mineru[pipeline]==2.7.6", "pypdf==6.14.2")
    .env({"HF_HOME": "/models/huggingface", "MODELSCOPE_CACHE": "/models/modelscope"})
    .add_local_dir("web", "/root/web")
)


@app.function(
    image=base, volumes={"/ledger": ledger_volume}, max_containers=1, timeout=60
)
def ledger(operation, arguments):
    # Exactly one input at a time; never call the SQLite file from web/GPU containers.
    from web.jobs import Ledger

    allowed = {
        "reserve",
        "get",
        "list",
        "internal",
        "submitted",
        "start",
        "finish",
        "expire",
    }
    if operation not in allowed:
        raise ValueError("unknown ledger operation")
    ledger_volume.reload()
    db = Ledger("/ledger/state.sqlite")
    value = getattr(db, operation)(**arguments)
    if operation in {"reserve", "submitted", "start", "finish", "expire"}:
        ledger_volume.commit()
    return value


@app.function(
    image=mineru_image,
    gpu="A10G",
    cpu=2,
    memory=8192,
    timeout=345,
    max_containers=2,
    retries=0,
    volumes={"/results": result_volume, "/models": model_volume},
)
def process_job(job_id, data):
    from web.pipeline import convert_pdf, validate_pdf

    if not ledger.remote("start", {"job_id": job_id}):
        return
    record = ledger.remote("internal", {"job_id": job_id})
    try:
        validate_pdf(data, 10 * 1024 * 1024, 1 if record["plan"] == "trial" else 50)
        result = convert_pdf(data, record["seconds"])
        target = Path("/results") / (job_id + ".zip")
        target.write_bytes(result)
        result_volume.commit()
        model_volume.commit()
        ledger.remote("finish", {"job_id": job_id, "status": "done"})
    except Exception:
        ledger.remote(
            "finish",
            {
                "job_id": job_id,
                "status": "error",
                "error": "Conversion failed or reached its time limit. Contact support before retrying.",
            },
        )


@app.function(
    image=mineru_image,
    gpu="A10G",
    cpu=2,
    memory=8192,
    timeout=3600,
    max_containers=1,
    retries=0,
    volumes={"/models": model_volume},
)
def extract_pdf(pdf_bytes, base_name):
    # Compatibility for authenticated owner scripts. Never exposed as a public HTTP route.
    from web.pipeline import convert_pdf, validate_pdf

    validate_pdf(pdf_bytes, 100 * 1024 * 1024, 1000)
    result = convert_pdf(pdf_bytes, 3500, max_result=250 * 1024 * 1024)
    model_volume.commit()
    return result


@app.function(
    image=base,
    volumes={"/results": result_volume},
    schedule=modal.Period(hours=1),
    timeout=60,
)
def purge_expired():
    # Privacy cleanup is a small scheduled usage cost, disclosed in the operating plan.
    expired = ledger.remote("expire", {})
    result_volume.reload()
    for job_id in expired:
        (Path("/results") / (job_id + ".zip")).unlink(missing_ok=True)
    if expired:
        result_volume.commit()


@app.function(
    image=base, volumes={"/ledger": ledger_volume}, max_containers=1, timeout=60
)
def book_store(operation, arguments):
    from web.books import BookLedger

    allowed = {
        "account",
        "documents_list",
        "purchase_list",
        "retry_existing",
        "set_recovery",
        "recover",
        "credit",
        "reverse",
        "prepare",
        "discard_document",
        "document_get",
        "confirm",
        "part_start",
        "part_finish",
        "book_internal",
        "book_get",
        "book_list",
        "cancel",
        "retry",
        "delete",
        "edit",
        "edits",
        "history",
        "stale",
        "expired",
        "purged",
    }
    if operation not in allowed:
        raise ValueError("Unknown book-ledger operation")
    ledger_volume.reload()
    db = BookLedger("/ledger/books.sqlite")
    value = getattr(db, operation)(**arguments)
    ledger_volume.commit()
    return value


@app.function(
    image=mineru_image,
    gpu="A10G",
    cpu=(2, 2),
    memory=(8192, 8192),
    timeout=240,
    max_containers=2,
    retries=0,
    scaledown_window=5,
    volumes={"/results": result_volume, "/models": model_volume},
)
def gpu_section(job_id, number):
    return _section(job_id, number)


@app.function(
    image=base,
    cpu=(1, 1),
    memory=(2048, 2048),
    timeout=240,
    max_containers=2,
    retries=0,
    scaledown_window=5,
    volumes={"/results": result_volume},
)
def text_section(job_id, number):
    return _section(job_id, number)


def _section(job_id, number):
    from web.documents import extract_section

    if not book_store.remote("part_start", {"job_id": job_id, "number": number}):
        return
    job = book_store.remote("book_internal", {"job_id": job_id})
    part = next(p for p in job["parts"] if p["number"] == number)
    result_volume.reload()
    target = Path("/results/books") / job_id / f"{number}.zip"
    try:
        source = (Path("/results/sources") / (job["document"] + ".pdf")).read_bytes()
        mode = part.get("mode") or job["mode"]
        result = extract_section(
            source, part["pages"], mode, part.get("lang") or job["lang"]
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result)
        result_volume.commit()
        accepted = book_store.remote(
            "part_finish", {"job_id": job_id, "number": number, "ok": True}
        )
        if not accepted:
            target.unlink(missing_ok=True)
            result_volume.commit()
        if mode != "text":
            try:
                model_volume.commit()
            except Exception:
                pass
    except Exception:
        target.unlink(missing_ok=True)
        result_volume.commit()
        book_store.remote(
            "part_finish",
            {
                "job_id": job_id,
                "number": number,
                "ok": False,
                "error": "This section failed or exceeded its time limit. Its allowance was released; other completed pages remain available.",
            },
        )


@app.function(
    image=base,
    cpu=0.125,
    memory=512,
    timeout=9500,
    max_containers=2,
    retries=0,
    scaledown_window=5,
)
def run_book(job_id):
    from concurrent.futures import ThreadPoolExecutor

    job = book_store.remote("book_internal", {"job_id": job_id})
    if not job or job["deleted"]:
        return
    pending = [p for p in job["parts"] if p["status"] == "queued"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                (
                    text_section
                    if (p.get("mode") or job["mode"]) == "text"
                    else gpu_section
                ).remote,
                job_id,
                p["number"],
            )
            for p in pending
        ]
        for f in futures:
            try:
                f.result()
            except Exception:
                pass  # Ledger deadlines reconcile terminated workers; never automatically bill a retry.


@app.function(
    image=base,
    volumes={"/results": result_volume},
    schedule=modal.Period(hours=1),
    timeout=120,
)
def purge_books():
    import shutil

    book_store.remote("stale", {})
    expired = book_store.remote("expired", {})
    result_volume.reload()
    for jid in expired["jobs"]:
        shutil.rmtree(Path("/results/books") / jid, ignore_errors=True)
    for did in expired["documents"]:
        (Path("/results/sources") / (did + ".pdf")).unlink(missing_ok=True)
    result_volume.commit()
    book_store.remote("purged", expired)
