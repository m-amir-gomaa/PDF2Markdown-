import asyncio
import uuid
import os
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web.jobs import jobs, JobState
from web.pipeline import run_pipeline

app = FastAPI(title="PDF2Markdown")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="web/templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    token_id: str = Form(""),
    token_secret: str = Form("")
):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
        
    pdf_bytes = await file.read()
    
    # Create job
    job_id = str(uuid.uuid4())
    base_name = os.path.splitext(file.filename)[0].replace(" ", "_")
    
    job = JobState(job_id=job_id, filename=base_name)
    jobs[job_id] = job
    
    # Start pipeline in background
    asyncio.create_task(run_pipeline(job_id, pdf_bytes, base_name, token_id, token_secret))
    
    # Return HTML fragment for HTMX to swap into the UI
    return HTMLResponse(f"""
    <div id="job-container" class="fade-in mt-6 p-6 bg-slate-800 rounded-lg shadow-xl border border-slate-700">
        <h3 class="text-xl font-semibold mb-4 text-blue-400">Processing: {file.filename}</h3>
        
        <div id="log-container" class="bg-slate-900 rounded p-4 h-64 overflow-y-auto font-mono text-sm text-slate-300 mb-4" 
             hx-ext="sse" 
             sse-connect="/stream/{job_id}" 
             sse-swap="message" 
             hx-swap="beforeend">
             <!-- Logs will stream here -->
        </div>
        
        <div id="download-container-{job_id}">
            <div class="flex items-center space-x-3 text-slate-400">
                <svg class="animate-spin h-5 w-5 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>Extracting on Modal GPU...</span>
            </div>
        </div>
    </div>
    """)

@app.get("/stream/{job_id}")
async def stream_logs(job_id: str, request: Request):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
        
    job = jobs[job_id]
    
    async def event_generator():
        last_index = 0
        while True:
            if await request.is_disconnected():
                break
                
            # Yield any new messages
            while last_index < len(job.progress_messages):
                msg = job.progress_messages[last_index]
                yield f"data: <div>{msg}</div>\n\n"
                last_index += 1
                
            if job.status in ["done", "error"]:
                # Job finished, send final state and stop streaming
                if job.status == "done":
                    # Send HTMX out-of-band swap for the download button
                    yield f"""data: 
<div hx-swap-oob="innerHTML:#download-container-{job_id}">
    <a href="/download/{job_id}" class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 focus:ring-offset-slate-800 transition-colors">
        <svg class="-ml-1 mr-2 h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
        Download Markdown & Images
    </a>
</div>\n\n"""
                elif job.status == "error":
                    yield f"""data: 
<div hx-swap-oob="innerHTML:#download-container-{job_id}">
    <div class="text-red-500 flex items-center space-x-2">
        <svg class="h-5 w-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" />
        </svg>
        <span>Extraction failed. Check logs above.</span>
    </div>
</div>\n\n"""
                break
                
            # Wait for next event
            try:
                await asyncio.wait_for(job.wait_for_new_event().wait(), timeout=1.0)
            except asyncio.TimeoutError:
                # Keep-alive
                yield ":\n\n"
                
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/download/{job_id}")
async def download_result(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
        
    job = jobs[job_id]
    
    if job.status != "done" or not job.result_zip:
        raise HTTPException(status_code=400, detail="Result not ready")
        
    return Response(
        content=job.result_zip,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={job.filename}_markdown.zip"}
    )
