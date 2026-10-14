import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
import zipfile
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pypdf import PdfWriter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from web.billing import LemonVerifier,BillingError
from web.config import Settings
from web.jobs import Ledger,LimitError
from web.main import create_app
from web.pipeline import validate_pdf,convert_pdf,should_keep


def pdf(pages=1):
    writer=PdfWriter()
    for _ in range(pages):writer.add_blank_page(width=300,height=300)
    buffer=BytesIO();writer.write(buffer);return buffer.getvalue()


class SaasTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.ledger=Ledger(self.folder.name+'/db.sqlite')
        self.settings=Settings(Fernet.generate_key().decode());self.submissions=[]
        async def store(operation,args):return getattr(self.ledger,operation)(**args)
        async def dispatch(job_id,data):self.submissions.append(job_id);return 'call-fixture'
        async def result(job_id):return b'zip-fixture'
        self.app=create_app(self.settings,store,dispatch,result)
        self.client=TestClient(self.app,base_url='https://testserver')
        self.csrf=self.client.get('/api/session').json()['csrf']

    def tearDown(self):self.client.close();self.folder.cleanup()

    def upload(self,client=None,payload=None,request_id=None,csrf=None,filename='sample.pdf'):
        return (client or self.client).post('/api/jobs',files={'file':(filename,payload if payload is not None else pdf(),'application/pdf')},
            data={'request_id':request_id or str(uuid.uuid4())},headers={'X-CSRF-Token':csrf or self.csrf})

    def test_no_billing_config_fails_closed(self):
        response=self.client.post('/api/access',json={'license_key':'fake-key-123456'},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code,402);self.assertFalse(self.client.get('/health').json()['billing_enabled'])
        self.assertEqual(self.submissions,[])

    def test_csrf_and_cross_origin_rejected(self):
        self.assertEqual(self.client.post('/api/jobs',files={'file':('x.pdf',pdf())},data={'request_id':str(uuid.uuid4())}).status_code,403)
        self.assertEqual(self.client.post('/api/logout',headers={'X-CSRF-Token':self.csrf,'Origin':'https://attacker.example'}).status_code,403)

    def test_cookie_secure_and_tampering_rejected(self):
        response=self.client.get('/api/session');cookie=response.headers['set-cookie'].lower()
        self.assertIn('httponly',cookie);self.assertIn('secure',cookie);self.assertIn('samesite=lax',cookie)
        other=TestClient(self.app,base_url='https://testserver');other.cookies.set('studio_session','tampered')
        self.assertEqual(other.get('/api/jobs').status_code,401);other.close()

    def test_trial_idempotent_and_limited(self):
        request_id=str(uuid.uuid4());first=self.upload(request_id=request_id);second=self.upload(request_id=request_id)
        self.assertEqual(first.status_code,200);self.assertEqual(first.json()['id'],second.json()['id'])
        self.assertEqual(len(self.submissions),1);self.assertEqual(self.upload().status_code,429)

    def test_same_request_different_document_rejected(self):
        request_id=str(uuid.uuid4());self.upload(request_id=request_id)
        self.assertEqual(self.upload(request_id=request_id,payload=pdf()+b'\n').status_code,429)
        self.assertEqual(len(self.submissions),1)

    def test_owner_isolation_for_status_and_download(self):
        job_id=self.upload().json()['id'];other=TestClient(self.app,base_url='https://testserver');other.get('/api/session')
        self.assertEqual(other.get('/api/jobs/'+job_id).status_code,404)
        self.assertEqual(other.get('/api/jobs/'+job_id+'/download').status_code,404);other.close()

    def test_invalid_oversized_multipage_encrypted_rejected_before_compute(self):
        self.assertEqual(self.upload(payload=b'not PDF').status_code,400)
        self.assertEqual(self.upload(payload=pdf(2)).status_code,400)
        self.assertEqual(self.upload(payload=b'%PDF-'+b'a'*(1024*1024)).status_code,413)
        writer=PdfWriter();writer.add_blank_page(width=300,height=300);writer.encrypt('secret');buffer=BytesIO();writer.write(buffer)
        self.assertEqual(self.upload(payload=buffer.getvalue()).status_code,400);self.assertEqual(self.submissions,[])

    def test_filename_never_html_and_download_owned(self):
        job_id=self.upload(filename='<script>alert(1)</script>.pdf').json()['id']
        self.ledger.start(job_id);self.ledger.finish(job_id,'done')
        response=self.client.get('/api/jobs/'+job_id+'/download');self.assertEqual(response.status_code,200)
        self.assertNotIn('script',response.headers['content-disposition'])
        self.assertEqual(response.content,b'zip-fixture')

    def test_restart_preserves_jobs_and_quota(self):
        job_id=self.upload().json()['id'];reopened=Ledger(self.ledger.path)
        self.assertEqual(reopened.internal(job_id)['call_id'],'call-fixture')
        self.assertEqual(self.upload().status_code,429)

    def test_database_concurrent_paid_quota_is_atomic(self):
        def reserve(i):
            try:self.ledger.reserve('paid',str(i),'sha','file.pdf',1,'paid','cycle',300,
                {'paid_jobs':2},172800,'ip');return 1
            except LimitError:return 0
        with ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(reserve,range(30)))
        self.assertEqual(sum(results),2)

    def test_no_duplicate_worker_start(self):
        job_id=self.upload().json()['id'];self.assertTrue(self.ledger.start(job_id));self.assertFalse(self.ledger.start(job_id))

    def test_expiry_removes_access_but_not_monthly_quota(self):
        job_id=self.upload().json()['id']
        with self.ledger.db() as db:db.execute('UPDATE jobs SET expires=0 WHERE id=?',(job_id,))
        self.assertEqual(self.ledger.expire(),[job_id]);self.assertEqual(self.upload().status_code,429)

    def test_pdf_converter_uses_isolated_temp_and_preserves_metadata(self):
        paths=[]
        def runner(argv,**kwargs):
            out=Path(argv[argv.index('-o')+1]);paths.append(out.parent)
            p=out/'document'/'auto';p.mkdir(parents=True);(p/'document.md').write_text('# Original')
            (p/'document_content_list.json').write_text('[{"page_idx":0,"type":"text","text":"Source paragraph"}]');(p/'document_span.pdf').write_bytes(b'private debug')
            self.assertTrue(kwargs['check']);self.assertEqual(kwargs['timeout'],165)
        value=convert_pdf(pdf(),180,runner)
        with zipfile.ZipFile(BytesIO(value)) as z:
            self.assertIn('document_content_list.json',z.namelist());self.assertNotIn('document_span.pdf',z.namelist())
            self.assertEqual(json.loads(z.read('SOURCE-MAP.json'))[0]['pdf_page'],1)
            self.assertIn(b'document.pdf#page=1',z.read('SOURCE-NAVIGATION.md'))
            self.assertEqual(json.loads(z.read('REVIEW.json'))['replacement_characters'],0)
            self.assertEqual(z.read('document.pdf'),pdf())
        self.assertFalse(paths[0].exists())


class BillingTests(unittest.TestCase):
    def settings(self):return Settings(Fernet.generate_key().decode(),checkout_url='https://example.lemonsqueezy.com/buy/id',store_id='1',variant_id='5',lemon_api_key='synthetic',commercial_model='subscription_with_included_compute')
    def transport(self,wrong_variant=False,refunded=False,expired=False,test=False):
        def handler(request):
            if request.url.path.endswith('/validate'):
                value={'valid':True,'license_key':{'id':7,'status':'active','expires_at':None},'meta':{'store_id':1,'variant_id':9 if wrong_variant else 5,'order_id':2,'order_item_id':3}}
            elif '/orders/' in request.url.path:value={'data':{'attributes':{'status':'refunded' if refunded else 'paid','test_mode':test}}}
            else:value={'data':[{'attributes':{'store_id':1,'variant_id':5,'test_mode':test,'status':'active','renews_at':'2000-01-01T00:00:00Z' if expired else '2099-01-01T00:00:00Z'}}]}
            return httpx.Response(200,json=value)
        return httpx.MockTransport(handler)
    def test_paid_cycle_and_identity_come_from_provider(self):
        value=asyncio.run(LemonVerifier(self.settings(),self.transport()).verify('synthetic-license'))
        self.assertEqual(value['license_id'],'7');self.assertEqual(value['period'],'2099-01-01T00:00:00Z')
    def test_wrong_product_refund_expired_and_test_keys_rejected(self):
        for flags in [{'wrong_variant':True},{'refunded':True},{'expired':True},{'test':True}]:
            with self.subTest(flags=flags),self.assertRaises(BillingError):
                asyncio.run(LemonVerifier(self.settings(),self.transport(**flags)).verify('synthetic-license'))
    def test_provider_failure_is_closed_and_sanitized(self):
        def handler(request):raise httpx.ConnectError('secret never logged')
        with self.assertRaises(BillingError) as error:
            asyncio.run(LemonVerifier(self.settings(),httpx.MockTransport(handler)).verify('synthetic-license'))
        self.assertNotIn('secret',str(error.exception))

if __name__=='__main__':unittest.main()
