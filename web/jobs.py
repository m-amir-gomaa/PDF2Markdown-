"""Transactional single-writer ledger. No PDF bytes, license keys or payment secrets."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time
import uuid

class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

class LimitError(Exception):
    pass

class Ledger:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
            filename TEXT NOT NULL,pages INTEGER NOT NULL,plan TEXT NOT NULL,cycle TEXT NOT NULL,
            seconds INTEGER NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,
            call_id TEXT,error TEXT,UNIQUE(owner,request_id));
            CREATE TABLE IF NOT EXISTS quotas (owner TEXT NOT NULL,cycle TEXT NOT NULL,used INTEGER NOT NULL,
            expires REAL NOT NULL,PRIMARY KEY(owner,cycle));''')

    def db(self):
        db=sqlite3.connect(self.path,timeout=30,factory=ClosingConnection);db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
        return db

    def reserve(self,owner,request_id,digest,filename,pages,plan,cycle,seconds,limits,retention,ip_owner):
        now=time.time()
        with closing(self.db()) as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT * FROM jobs WHERE owner=? AND request_id=?',(owner,request_id)).fetchone()
            if prior:
                if prior['digest']!=digest:
                    raise LimitError('This request already belongs to a different document.')
                return {**dict(prior),'_new':False}
            buckets=[(owner,cycle,limits['paid_jobs'])] if plan=='paid' else [
                (owner,'trial',1),(ip_owner,'trial',1),('global-trial',time.strftime('%Y-%m',time.gmtime()),limits['trial_monthly_global']),
                ('global-trial',time.strftime('%Y-%m-%d',time.gmtime()),limits['trial_daily_global'])]
            for who,window,cap in buckets:
                row=db.execute('SELECT used FROM quotas WHERE owner=? AND cycle=?',(who,window)).fetchone()
                if row and row['used']>=cap:
                    raise LimitError('The conversion allowance is used. Subscribe, wait for renewal or contact support.')
            for who,window,cap in buckets:
                db.execute('INSERT INTO quotas VALUES (?,?,1,?) ON CONFLICT(owner,cycle) DO UPDATE SET used=used+1',
                           (who,window,now+75*86400))
            job_id=str(uuid.uuid4())
            db.execute('INSERT INTO jobs (id,owner,request_id,digest,filename,pages,plan,cycle,seconds,status,created,expires) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (job_id,owner,request_id,digest,filename,pages,plan,cycle,seconds,'submitting',now,now+retention))
            db.commit();return {**dict(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()),'_new':True}

    def get(self,owner,job_id):
        with closing(self.db()) as db:
            row=db.execute('SELECT * FROM jobs WHERE id=? AND owner=? AND expires>?',(job_id,owner,time.time())).fetchone()
            return dict(row) if row else None

    def internal(self,job_id):
        with closing(self.db()) as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            return dict(row) if row else None

    def list(self,owner):
        with closing(self.db()) as db:
            return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE owner=? AND expires>? ORDER BY created DESC LIMIT 20',(owner,time.time()))]

    def submitted(self,job_id,call_id):
        with self.db() as db:
            db.execute("UPDATE jobs SET call_id=?,status=CASE WHEN status='submitting' THEN 'queued' ELSE status END WHERE id=?",(call_id,job_id))

    def start(self,job_id):
        with self.db() as db:
            cursor=db.execute("UPDATE jobs SET status='running' WHERE id=? AND status IN ('submitting','queued') AND expires>?",(job_id,time.time()))
            return cursor.rowcount==1

    def finish(self,job_id,status,error=None):
        if status not in ('done','error'):
            raise ValueError('invalid terminal state')
        with self.db() as db:
            db.execute("UPDATE jobs SET status=?,error=? WHERE id=? AND status='running'",(status,error,job_id))

    def expire(self):
        with self.db() as db:
            rows=[r[0] for r in db.execute('SELECT id FROM jobs WHERE expires<=?',(time.time(),))]
            db.execute('DELETE FROM jobs WHERE expires<=?',(time.time(),));db.execute('DELETE FROM quotas WHERE expires<=?',(time.time(),))
            return rows
