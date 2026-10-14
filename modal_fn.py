"""Owner-only MinerU compute and a serialized persistent ledger."""
from pathlib import Path
import modal

app=modal.App('mineru-pdf-extractor')
ledger_volume=modal.Volume.from_name('document-studio-ledger-v1',create_if_missing=True)
result_volume=modal.Volume.from_name('document-studio-results-v1',create_if_missing=True)
model_volume=modal.Volume.from_name('document-studio-model-cache-v1',create_if_missing=True)
base=modal.Image.debian_slim(python_version='3.13').pip_install(
    'fastapi==0.141.1','httpx==0.28.1','cryptography==46.0.5','python-multipart==0.0.32','pypdf==6.14.2'
).add_local_dir('web','/root/web').add_local_dir('static','/root/static').add_local_file('modal_fn.py','/root/modal_fn.py')
mineru_image=(modal.Image.debian_slim(python_version='3.12')
    .apt_install('libgl1','libglib2.0-0','libsm6','libxext6','libxrender-dev')
    .pip_install('mineru[pipeline]==2.7.6','pypdf==6.14.2')
    .env({'HF_HOME':'/models/huggingface','MODELSCOPE_CACHE':'/models/modelscope'})
    .add_local_dir('web','/root/web'))

@app.function(image=base,volumes={'/ledger':ledger_volume},max_containers=1,timeout=60)
def ledger(operation,arguments):
    # Exactly one input at a time; never call the SQLite file from web/GPU containers.
    from web.jobs import Ledger
    allowed={'reserve','get','list','internal','submitted','start','finish','expire'}
    if operation not in allowed:raise ValueError('unknown ledger operation')
    ledger_volume.reload()
    db=Ledger('/ledger/state.sqlite')
    value=getattr(db,operation)(**arguments)
    if operation in {'reserve','submitted','start','finish','expire'}:ledger_volume.commit()
    return value

@app.function(image=mineru_image,gpu='A10G',cpu=2,memory=8192,timeout=345,max_containers=2,retries=0,
              volumes={'/results':result_volume,'/models':model_volume})
def process_job(job_id,data):
    from web.pipeline import convert_pdf,validate_pdf
    if not ledger.remote('start',{'job_id':job_id}):return
    record=ledger.remote('internal',{'job_id':job_id})
    try:
        validate_pdf(data,10*1024*1024,1 if record['plan']=='trial' else 50)
        result=convert_pdf(data,record['seconds'])
        target=Path('/results')/(job_id+'.zip')
        target.write_bytes(result);result_volume.commit();model_volume.commit()
        ledger.remote('finish',{'job_id':job_id,'status':'done'})
    except Exception:
        ledger.remote('finish',{'job_id':job_id,'status':'error','error':'Conversion failed or reached its time limit. Contact support before retrying.'})

@app.function(image=mineru_image,gpu='A10G',cpu=2,memory=8192,timeout=3600,max_containers=1,retries=0,
              volumes={'/models':model_volume})
def extract_pdf(pdf_bytes,base_name):
    # Compatibility for authenticated owner scripts. Never exposed as a public HTTP route.
    from web.pipeline import convert_pdf,validate_pdf
    validate_pdf(pdf_bytes,100*1024*1024,1000)
    result=convert_pdf(pdf_bytes,3500,max_result=250*1024*1024);model_volume.commit();return result

@app.function(image=base,volumes={'/results':result_volume},schedule=modal.Period(hours=1),timeout=60)
def purge_expired():
    # Privacy cleanup is a small scheduled usage cost, disclosed in the operating plan.
    expired=ledger.remote('expire',{})
    result_volume.reload()
    for job_id in expired:(Path('/results')/(job_id+'.zip')).unlink(missing_ok=True)
    if expired:result_volume.commit()
