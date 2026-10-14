"""Document Studio: paid access and a bounded trial on the owner's Modal account."""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
import uuid
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from web.billing import BillingError, LemonVerifier
from web.config import Settings
from web.jobs import LimitError
from web.pipeline import validate_pdf

ROOT=Path(__file__).resolve().parents[1]

class BodyLimit:
    def __init__(self,app,limit):self.app,self.limit=app,limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        try:
            length=int(headers.get(b'content-length',b'0'))
        except ValueError:length=self.limit+1
        if length>self.limit:
            return await JSONResponse({'detail':'Upload exceeds the size limit.'},413)(scope,receive,send)
        total=0
        async def bounded():
            nonlocal total
            event=await receive();total+=len(event.get('body',b''))
            if total>self.limit:raise HTTPException(413,'Upload exceeds the size limit.')
            return event
        return await self.app(scope,bounded,send)


def create_app(settings,store,dispatcher,read_result,verifier=None):
    cipher=Fernet(settings.session_key.encode());verifier=verifier or LemonVerifier(settings)
    app=FastAPI(title='Document Studio',docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(BodyLimit,limit=settings.paid_bytes+65536)
    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

    def identity(value):
        return hmac.new(settings.session_key.encode(),value.encode(),hashlib.sha256).hexdigest()

    def session(request):
        try:
            value=json.loads(cipher.decrypt(request.cookies.get('studio_session','').encode(),ttl=7*86400))
            if not isinstance(value.get('uid'),str) or not isinstance(value.get('csrf'),str):raise ValueError()
            return value
        except (InvalidToken,ValueError,KeyError,TypeError):
            raise HTTPException(401,'Refresh the page to start a secure session.') from None

    def csrf(request,value):
        origin=request.headers.get('origin')
        if (origin and origin!=str(request.base_url).rstrip('/')) or not hmac.compare_digest(
                request.headers.get('x-csrf-token',''),value['csrf']):
            raise HTTPException(403,'Refresh the page and try again.')

    def cookie(response,value):
        response.set_cookie('studio_session',cipher.encrypt(json.dumps(value).encode()).decode(),
                            secure=True,httponly=True,samesite='lax',max_age=7*86400,path='/')

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Cache-Control':'no-store','Permissions-Policy':'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://m-amir-gomaa.github.io; base-uri 'none'; form-action 'self'"})
        return response

    @app.get('/')
    async def index():return FileResponse(ROOT/'web/templates/index.html',media_type='text/html')

    @app.get('/health')
    async def health():return {'service':'document-studio','status':'ok','billing_enabled':settings.billing_ready}

    @app.get('/api/session')
    async def get_session(request:Request):
        try:value=session(request)
        except HTTPException:value={'uid':str(uuid.uuid4()),'csrf':secrets.token_urlsafe(32)}
        response=JSONResponse({'csrf':value['csrf'],'paid_session':bool(value.get('license')),
            'billing_enabled':settings.billing_ready,'checkout_url':settings.checkout_url if settings.billing_ready else None,
            'price_usd':1,'paid_jobs':settings.paid_jobs,'paid_pages':settings.paid_pages,
            'trial_pages':settings.trial_pages,'retention_hours':settings.retention_seconds//3600})
        cookie(response,value);return response

    @app.post('/api/access')
    async def access(request:Request):
        value=session(request);csrf(request,value)
        try:
            body=await request.json();key=body.get('license_key')
            grant=await verifier.verify(key)
        except BillingError as exc:raise HTTPException(402,str(exc)) from None
        except (ValueError,AttributeError):raise HTTPException(400,'Enter a valid purchase key.') from None
        value={'uid':identity('license:'+grant['license_id']),'csrf':secrets.token_urlsafe(32),'license':key}
        response=JSONResponse({'connected':True,'csrf':value['csrf']});cookie(response,value);return response

    @app.post('/api/logout')
    async def logout(request:Request):
        value=session(request);csrf(request,value)
        response=JSONResponse({'signed_out':True});response.delete_cookie('studio_session',path='/');return response

    @app.post('/api/jobs')
    async def upload(request:Request,file:UploadFile=File(...),request_id:str=Form(...)):
        value=session(request);csrf(request,value)
        try:request_id=str(uuid.UUID(request_id))
        except ValueError:raise HTTPException(400,'Invalid conversion request.') from None
        plan,cycle='trial','trial'
        if value.get('license'):
            try:grant=await verifier.verify(value['license'])
            except BillingError as exc:raise HTTPException(402,str(exc)) from None
            plan,cycle='paid',grant['period']
        max_bytes=settings.paid_bytes if plan=='paid' else settings.trial_bytes
        max_pages=settings.paid_pages if plan=='paid' else settings.trial_pages
        raw=bytearray()
        try:
            while part:=await file.read(1024*1024):
                raw.extend(part)
                if len(raw)>max_bytes:raise HTTPException(413,'This PDF exceeds your upload-size allowance.')
        finally:await file.close()
        data=bytes(raw)
        try:pages=await asyncio.to_thread(validate_pdf,data,max_bytes,max_pages)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
        filename=(file.filename or 'document.pdf').replace('\\','/').rsplit('/',1)[-1][:150]
        filename=''.join(c for c in filename if ord(c)>=32)
        # The platform edge supplies this address; global limits remain the hard anti-abuse cap.
        ip=request.headers.get('x-forwarded-for','').split(',')[-1].strip() or (request.client.host if request.client else 'unknown')
        args=dict(owner=value['uid'],request_id=request_id,digest=hashlib.sha256(data).hexdigest(),filename=filename,
                  pages=pages,plan=plan,cycle=cycle,seconds=settings.paid_seconds if plan=='paid' else settings.trial_seconds,
                  limits={'paid_jobs':settings.paid_jobs,'trial_monthly_global':settings.trial_monthly_global,
                          'trial_daily_global':settings.trial_daily_global},retention=settings.retention_seconds,
                  ip_owner=identity('ip:'+ip))
        try:job=await store('reserve',args)
        except LimitError as exc:raise HTTPException(429,str(exc)) from None
        if job.pop('_new',False):
            try:
                call_id=await dispatcher(job['id'],data)
                await store('submitted',{'job_id':job['id'],'call_id':call_id})
            except Exception:
                # Indeterminate submission must never automatically submit another billed job.
                return JSONResponse({'id':job['id'],'status':'submission_uncertain','detail':'Check this conversion again before starting another.'},202)
        return {'id':job['id'],'status':job['status']}

    @app.get('/api/jobs')
    async def list_jobs(request:Request):
        value=session(request);rows=await store('list',{'owner':value['uid']})
        return [{k:r[k] for k in ('id','filename','pages','status','expires','error')} for r in rows]

    @app.get('/api/jobs/{job_id}')
    async def status(job_id:str,request:Request):
        row=await store('get',{'owner':session(request)['uid'],'job_id':job_id})
        if row is None:raise HTTPException(404,'Conversion not found or expired.')
        return {k:row[k] for k in ('id','filename','pages','status','expires','error')}

    @app.get('/api/jobs/{job_id}/download')
    async def download(job_id:str,request:Request):
        row=await store('get',{'owner':session(request)['uid'],'job_id':job_id})
        if row is None:raise HTTPException(404,'Conversion not found or expired.')
        if row['status']!='done':raise HTTPException(409,'The archive is not ready.')
        try:result=await read_result(job_id)
        except Exception:raise HTTPException(503,'Download temporarily unavailable. Try again.') from None
        return Response(result,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="document-studio-{job_id}.zip"'})

    return app
