import asyncio
from pathlib import Path
import modal
from modal_fn import app,base,ledger,process_job,result_volume

@app.function(image=base,volumes={'/results':result_volume},
              secrets=[modal.Secret.from_name('document-studio-session')],max_containers=2,timeout=120)
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def web_app():
    from web.main import create_app
    from web.config import Settings
    async def store(operation,arguments):return await ledger.remote.aio(operation,arguments)
    async def dispatch(job_id,data):
        record=await ledger.remote.aio('internal',{'job_id':job_id})
        call=await process_job.spawn.aio(job_id,data)
        return call.object_id
    async def read_result(job_id):
        def read():
            result_volume.reload();return (Path('/results')/(job_id+'.zip')).read_bytes()
        return await asyncio.to_thread(read)
    return create_app(Settings.from_env(),store,dispatch,read_result)

@app.local_entrypoint()
def probe(pdf:str):
    from modal_fn import extract_pdf
    result=extract_pdf.remote(Path(pdf).read_bytes(),'verification')
    target=Path('artifacts/verification-result.zip');target.parent.mkdir(exist_ok=True);target.write_bytes(result)
    print('Private extraction probe wrote',target,len(result),'bytes')
