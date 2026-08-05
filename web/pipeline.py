import asyncio
import uuid
from web.jobs import jobs, JobState

def _run_modal_sync(pdf_bytes: bytes, filename: str) -> bytes:
    # Import here to avoid circular imports if any, and to ensure modal is loaded
    from modal_fn import extract_pdf
    
    # We call the remote function synchronously
    # In a real Modal environment, this blocks until the remote execution finishes.
    return extract_pdf.remote(pdf_bytes, filename)

async def run_pipeline(job_id: str, pdf_bytes: bytes, filename: str, token_id: str = "", token_secret: str = ""):
    job = jobs[job_id]
    
    try:
        job.status = "extracting"
        job.add_message("Starting GPU extraction on Modal...")
        
        # Run the blocking Modal call in a thread pool so we don't freeze the FastAPI event loop
        loop = asyncio.get_running_loop()
        result_zip = await loop.run_in_executor(None, _run_modal_sync, pdf_bytes, filename)
        
        job.result_zip = result_zip
        job.status = "done"
        job.add_message("Extraction complete! Zip file ready.")
        
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.add_message(f"Error: {str(e)}")
