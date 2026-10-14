"""Serialized transactional document jobs and consumable service credits.

Units are 1/10,000 USD. A quote is reserved before dispatch. Successful parts
settle once; failed/cancelled parts release their page allowance.
"""

import json
import time
import uuid
from contextlib import closing
from web.jobs import Ledger


class BookError(Exception):
    pass


class BookLedger(Ledger):
    def __init__(self, path):
        super().__init__(path)
        with self.db() as db:
            db.executescript("""
   CREATE TABLE IF NOT EXISTS accounts(owner TEXT PRIMARY KEY,available INTEGER NOT NULL DEFAULT 0,reserved INTEGER NOT NULL DEFAULT 0,recovery TEXT UNIQUE);
   CREATE TABLE IF NOT EXISTS purchases(order_id TEXT PRIMARY KEY,owner TEXT NOT NULL,granted INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
   CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_id TEXT NOT NULL,digest TEXT NOT NULL,filename TEXT NOT NULL,total INTEGER NOT NULL,pages TEXT NOT NULL,mode TEXT NOT NULL,lang TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,UNIQUE(owner,request_id));
   CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY,owner TEXT NOT NULL,document TEXT NOT NULL,request_id TEXT NOT NULL,trial INTEGER NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,charged INTEGER NOT NULL DEFAULT 0,UNIQUE(owner,request_id));
   CREATE TABLE IF NOT EXISTS runs(job TEXT NOT NULL,revision INTEGER NOT NULL,fee INTEGER NOT NULL,fee_paid INTEGER NOT NULL DEFAULT 0,request_id TEXT NOT NULL,PRIMARY KEY(job,revision),UNIQUE(job,request_id));
   CREATE TABLE IF NOT EXISTS parts(job TEXT NOT NULL,number INTEGER NOT NULL,revision INTEGER NOT NULL,pages TEXT NOT NULL,units INTEGER NOT NULL,status TEXT NOT NULL,deadline REAL,error TEXT,PRIMARY KEY(job,number));
   CREATE TABLE IF NOT EXISTS corrections(job TEXT NOT NULL,page INTEGER NOT NULL,text TEXT NOT NULL,version INTEGER NOT NULL,updated REAL NOT NULL,PRIMARY KEY(job,page));
   CREATE TABLE IF NOT EXISTS correction_history(job TEXT NOT NULL,page INTEGER NOT NULL,text TEXT NOT NULL,version INTEGER NOT NULL,updated REAL NOT NULL,PRIMARY KEY(job,page,version));
   """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(parts)")}
            for name in ("mode", "lang"):
                if name not in columns:
                    db.execute(f"ALTER TABLE parts ADD COLUMN {name} TEXT")

    def account(self, owner):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO accounts(owner) VALUES (?)", (owner,))
            r = dict(
                db.execute(
                    "SELECT available,reserved,recovery FROM accounts WHERE owner=?",
                    (owner,),
                ).fetchone()
            )
            r["has_recovery"] = bool(r.pop("recovery"))
            r["purchases"] = db.execute(
                "SELECT COUNT(*) FROM purchases WHERE owner=?", (owner,)
            ).fetchone()[0]
            return r

    def documents_list(self, owner):
        with closing(self.db()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM documents WHERE owner=? AND expires>? ORDER BY created DESC LIMIT 5",
                    (owner, time.time()),
                )
            ]

    def purchase_list(self, owner=None):
        with closing(self.db()) as db:
            query = "SELECT order_id,owner,granted,revoked FROM purchases"
            return [
                dict(r)
                for r in db.execute(
                    query + " WHERE owner=?" if owner else query,
                    (owner,) if owner else (),
                )
            ]

    def retry_existing(self, owner, job_id, request_id):
        with closing(self.db()) as db:
            job = db.execute(
                "SELECT id FROM books WHERE id=? AND owner=? AND deleted=0 AND expires>?",
                (job_id, owner, time.time()),
            ).fetchone()
            if not job:
                return None
            run = db.execute(
                "SELECT * FROM runs WHERE job=? AND request_id=?", (job_id, request_id)
            ).fetchone()
            if not run:
                return None
            parts = db.execute(
                "SELECT * FROM parts WHERE job=? AND revision=?",
                (job_id, run["revision"]),
            ).fetchall()
            return {
                "id": job_id,
                "revision": run["revision"],
                "quoted": run["fee"] + sum(p["units"] for p in parts),
                "pages": sorted({n for p in parts for n in json.loads(p["pages"])}),
                "mode": parts[0]["mode"] if parts else None,
                "_new": False,
            }

    def set_recovery(self, owner, key):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO accounts(owner) VALUES (?)", (owner,))
            db.execute("UPDATE accounts SET recovery=? WHERE owner=?", (key, owner))

    def recover(self, key):
        with closing(self.db()) as db:
            row = db.execute(
                "SELECT owner FROM accounts WHERE recovery=?", (key,)
            ).fetchone()
            return row[0] if row else None

    def credit(self, owner, order_id, units):
        if type(units) is not int or not 0 < units <= 1000000:
            raise BookError("Invalid processing pack.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM purchases WHERE order_id=?", (order_id,)
            ).fetchone()
            if prior:
                return prior["owner"]
            db.execute("INSERT OR IGNORE INTO accounts(owner) VALUES (?)", (owner,))
            db.execute(
                "INSERT INTO purchases(order_id,owner,granted) VALUES (?,?,?)",
                (order_id, owner, units),
            )
            db.execute(
                "UPDATE accounts SET available=available+? WHERE owner=?",
                (units, owner),
            )
            return owner

    def reverse(self, order_id, units):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute(
                "SELECT * FROM purchases WHERE order_id=?", (order_id,)
            ).fetchone()
            if not r:
                return False
            revoked = min(r["granted"], max(r["revoked"], units))
            delta = revoked - r["revoked"]
            db.execute(
                "UPDATE purchases SET revoked=? WHERE order_id=?", (revoked, order_id)
            )
            db.execute(
                "UPDATE accounts SET available=available-? WHERE owner=?",
                (delta, r["owner"]),
            )
            account = db.execute(
                "SELECT available FROM accounts WHERE owner=?", (r["owner"],)
            ).fetchone()
            if account[0] < 0:
                for job in db.execute(
                    "SELECT * FROM books WHERE owner=? AND deleted=0 AND cancelled=0",
                    (r["owner"],),
                ).fetchall():
                    self._cancel(db, job)
            # Negative credit after an already-spent refund prevents new work; never hides completed outputs.
            return True

    def prepare(
        self, owner, request_id, digest, filename, total, pages, mode, lang, retention
    ):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM documents WHERE owner=? AND request_id=?",
                (owner, request_id),
            ).fetchone()
            if row:
                if (
                    row["digest"] != digest
                    or json.loads(row["pages"]) != pages
                    or row["mode"] != mode
                    or row["lang"] != lang
                ):
                    raise BookError("This upload request belongs to different options.")
                if row["expires"] <= time.time():
                    raise BookError("This upload has expired. Upload again.")
                return {**dict(row), "_new": False}
            # Avoid unlimited stored preflight uploads before any payment.
            if (
                db.execute(
                    "SELECT COUNT(*) FROM documents WHERE owner=? AND expires>?",
                    (owner, time.time()),
                ).fetchone()[0]
                >= 5
            ):
                raise BookError(
                    "Five documents are already open. Delete or finish one first."
                )
            if (
                db.execute(
                    "SELECT COUNT(*) FROM documents WHERE created>?",
                    (time.time() - 86400,),
                ).fetchone()[0]
                >= 100
            ):
                raise BookError("Upload capacity is used today. Try later.")
            did = str(uuid.uuid4())
            now = time.time()
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    did,
                    owner,
                    request_id,
                    digest,
                    filename,
                    total,
                    json.dumps(pages),
                    mode,
                    lang,
                    now,
                    now + retention,
                ),
            )
            return {
                **dict(
                    db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
                ),
                "_new": True,
            }

    def document_get(self, owner, document):
        with closing(self.db()) as db:
            r = db.execute(
                "SELECT * FROM documents WHERE id=? AND owner=? AND expires>?",
                (document, owner, time.time()),
            ).fetchone()
            return dict(r) if r else None

    def discard_document(self, owner, document):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM documents WHERE id=? AND owner=?", (document, owner)
            ).fetchone()
            if not row:
                raise BookError("Upload not found.")
            if db.execute(
                "SELECT COUNT(*) FROM books WHERE document=? AND deleted=0 AND expires>?",
                (document, time.time()),
            ).fetchone()[0]:
                raise BookError("Delete retained conversions using this source first.")
            db.execute("DELETE FROM documents WHERE id=?", (document,))
            return True

    def _hold(self, db, owner, units):
        db.execute("INSERT OR IGNORE INTO accounts(owner) VALUES (?)", (owner,))
        r = db.execute(
            "SELECT available FROM accounts WHERE owner=?", (owner,)
        ).fetchone()
        if r["available"] < units:
            raise BookError("Add processing credits before confirming this quote.")
        db.execute(
            "UPDATE accounts SET available=available-?,reserved=reserved+? WHERE owner=?",
            (units, units, owner),
        )

    def _run(
        self,
        db,
        job,
        revision,
        pages,
        mode,
        fee,
        request,
        trial,
        settings,
        lang="latin",
    ):
        per = (
            0
            if trial
            else (settings.text_page_units if mode == "text" else settings.page_units)
        )
        fee = 0 if trial else fee
        units = fee + len(pages) * per
        if not trial:
            self._hold(db, job["owner"], units)
            cycle = time.strftime("%Y-%m-%d", time.gmtime())
            buckets = [
                (
                    "global-text-attempts" if mode == "text" else "global-gpu-attempts",
                    settings.text_daily_pages
                    if mode == "text"
                    else settings.gpu_daily_pages,
                ),
                (
                    "owner-attempts:"
                    + job["owner"]
                    + ":"
                    + ("text" if mode == "text" else "gpu"),
                    settings.text_owner_daily_pages
                    if mode == "text"
                    else settings.gpu_owner_daily_pages,
                ),
            ]
            for who, cap in buckets:
                prior = db.execute(
                    "SELECT used FROM quotas WHERE owner=? AND cycle=?", (who, cycle)
                ).fetchone()
                if (prior[0] if prior else 0) + len(pages) > cap:
                    raise BookError(
                        "Today's processing safety limit is reached. No credits reserved; try tomorrow or contact support."
                    )
            for who, cap in buckets:
                db.execute(
                    "INSERT INTO quotas VALUES (?,?,?,?) ON CONFLICT(owner,cycle) DO UPDATE SET used=used+excluded.used",
                    (who, cycle, len(pages), time.time() + 75 * 86400),
                )
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,0,?)", (job["id"], revision, fee, request)
        )
        start = db.execute(
            "SELECT COALESCE(MAX(number),-1)+1 FROM parts WHERE job=?", (job["id"],)
        ).fetchone()[0]
        for idx, offset in enumerate(range(0, len(pages), settings.chunk_pages)):
            chosen = pages[offset : offset + settings.chunk_pages]
            db.execute(
                "INSERT INTO parts(job,number,revision,pages,units,status,mode,lang) VALUES (?,?,?,?,?,?,?,?)",
                (
                    job["id"],
                    start + idx,
                    revision,
                    json.dumps(chosen),
                    len(chosen) * per,
                    "queued",
                    mode,
                    lang,
                ),
            )
        return units

    def confirm(self, owner, document, request_id, trial, ip, settings):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM books WHERE owner=? AND request_id=?",
                (owner, request_id),
            ).fetchone()
            if prior:
                if prior["document"] != document or bool(prior["trial"]) != trial:
                    raise BookError("This confirmation belongs to a different quote.")
                return {**dict(prior), "_new": False}
            d = db.execute(
                "SELECT * FROM documents WHERE id=? AND owner=? AND expires>?",
                (document, owner, time.time()),
            ).fetchone()
            if not d:
                raise BookError("Upload not found or expired.")
            pages = json.loads(d["pages"])
            now = time.time()
            if trial:
                pages = pages[: settings.trial_pages]
                buckets = [
                    (owner, "trial", 1),
                    (ip, "trial", 1),
                    (
                        "global-trial",
                        time.strftime("%Y-%m-%d", time.gmtime()),
                        settings.trial_daily_global,
                    ),
                    (
                        "global-trial",
                        time.strftime("%Y-%m", time.gmtime()),
                        settings.trial_monthly_global,
                    ),
                ]
                for who, cycle, cap in buckets:
                    r = db.execute(
                        "SELECT used FROM quotas WHERE owner=? AND cycle=?",
                        (who, cycle),
                    ).fetchone()
                    if r and r[0] >= cap:
                        raise BookError(
                            "The small-document trial allowance is used. Paid conversion requires processing credits."
                        )
                for who, cycle, cap in buckets:
                    db.execute(
                        "INSERT INTO quotas VALUES (?,?,1,?) ON CONFLICT(owner,cycle) DO UPDATE SET used=used+1",
                        (who, cycle, now + 75 * 86400),
                    )
            else:
                count = db.execute(
                    "SELECT COUNT(*) FROM parts p JOIN books b ON p.job=b.id WHERE b.owner=? AND p.status IN ('queued','running') AND b.deleted=0",
                    (owner,),
                ).fetchone()[0]
                if count >= 100:
                    raise BookError("Finish or cancel pending work before adding more.")
            jid = str(uuid.uuid4())
            db.execute(
                "INSERT INTO books(id,owner,document,request_id,trial,created,expires) VALUES (?,?,?,?,?,?,?)",
                (jid, owner, document, request_id, int(trial), now, d["expires"]),
            )
            job = dict(db.execute("SELECT * FROM books WHERE id=?", (jid,)).fetchone())
            self._run(
                db,
                job,
                1,
                pages,
                d["mode"],
                settings.text_base_units
                if d["mode"] == "text"
                else settings.base_units,
                request_id,
                trial,
                settings,
                d["lang"],
            )
            return {**job, "_new": True}

    def _release(self, db, owner, units):
        if units:
            db.execute(
                "UPDATE accounts SET available=available+?,reserved=reserved-? WHERE owner=?",
                (units, units, owner),
            )

    def _close_run(self, db, job, revision):
        if db.execute(
            "SELECT COUNT(*) FROM parts WHERE job=? AND revision=? AND status IN ('queued','running')",
            (job["id"], revision),
        ).fetchone()[0]:
            return
        run = db.execute(
            "SELECT * FROM runs WHERE job=? AND revision=?", (job["id"], revision)
        ).fetchone()
        if run and not run["fee_paid"]:
            self._release(db, job["owner"], run["fee"])
            db.execute(
                "UPDATE runs SET fee_paid=1 WHERE job=? AND revision=?",
                (job["id"], revision),
            )

    def part_start(self, job_id, number):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM books WHERE id=?", (job_id,)).fetchone()
            if (
                not job
                or job["deleted"]
                or job["cancelled"]
                or job["expires"] <= time.time()
            ):
                return False
            return (
                db.execute(
                    "UPDATE parts SET status='running',deadline=? WHERE job=? AND number=? AND status='queued'",
                    (time.time() + 600, job_id, number),
                ).rowcount
                == 1
            )

    def part_finish(self, job_id, number, ok, error=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM books WHERE id=?", (job_id,)).fetchone()
            part = db.execute(
                "SELECT * FROM parts WHERE job=? AND number=?", (job_id, number)
            ).fetchone()
            if not job or not part or part["status"] != "running":
                return False
            ok = bool(ok and not job["deleted"])
            status = "done" if ok else "error"
            if not job["trial"]:
                if ok:
                    run = db.execute(
                        "SELECT * FROM runs WHERE job=? AND revision=?",
                        (job_id, part["revision"]),
                    ).fetchone()
                    charge = part["units"] + (0 if run["fee_paid"] else run["fee"])
                    db.execute(
                        "UPDATE accounts SET reserved=reserved-? WHERE owner=?",
                        (charge, job["owner"]),
                    )
                    db.execute(
                        "UPDATE runs SET fee_paid=1 WHERE job=? AND revision=?",
                        (job_id, part["revision"]),
                    )
                    db.execute(
                        "UPDATE books SET charged=charged+? WHERE id=?",
                        (charge, job_id),
                    )
                else:
                    self._release(db, job["owner"], part["units"])
            db.execute(
                "UPDATE parts SET status=?,error=? WHERE job=? AND number=?",
                (
                    status,
                    None
                    if ok
                    else (
                        error
                        or "Section extraction failed. Its processing allowance was released."
                    ),
                    job_id,
                    number,
                ),
            )
            self._close_run(db, job, part["revision"])
            return ok

    def book_internal(self, job_id):
        with closing(self.db()) as db:
            r = db.execute(
                "SELECT b.*,d.filename,d.digest,d.total,d.mode,d.lang,d.pages AS selected FROM books b JOIN documents d ON b.document=d.id WHERE b.id=?",
                (job_id,),
            ).fetchone()
            if not r:
                return None
            job = dict(r)
            job["parts"] = [
                {**dict(p), "pages": json.loads(p["pages"])}
                for p in db.execute(
                    "SELECT * FROM parts WHERE job=? ORDER BY number", (job_id,)
                )
            ]
            job["selected"] = json.loads(job["selected"])
            return job

    def book_get(self, owner, job_id):
        job = self.book_internal(job_id)
        if (
            not job
            or job["owner"] != owner
            or job["deleted"]
            or job["expires"] <= time.time()
        ):
            return None
        latest = [p for p in job["parts"] if p["revision"] == job["revision"]]
        states = {p["status"] for p in latest}
        job["status"] = (
            "running"
            if "running" in states
            else "queued"
            if "queued" in states
            else "cancelled"
            if job["cancelled"]
            else "partial"
            if "error" in states and any(p["status"] == "done" for p in job["parts"])
            else "error"
            if "error" in states
            else "done"
        )
        job["completed_pages"] = sorted(
            {n for p in job["parts"] if p["status"] == "done" for n in p["pages"]}
        )
        wanted = {n for p in job["parts"] for n in p["pages"]}
        if job["status"] == "done" and not wanted <= set(job["completed_pages"]):
            job["status"] = "partial"
        return job

    def book_list(self, owner):
        with closing(self.db()) as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM books WHERE owner=? AND deleted=0 AND expires>? ORDER BY created DESC LIMIT 20",
                    (owner, time.time()),
                )
            ]
        return [self.book_get(owner, j) for j in ids]

    def _cancel(self, db, job):
        for p in db.execute(
            "SELECT * FROM parts WHERE job=? AND status='queued'", (job["id"],)
        ).fetchall():
            if not job["trial"]:
                self._release(db, job["owner"], p["units"])
            db.execute(
                "UPDATE parts SET status='cancelled' WHERE job=? AND number=?",
                (job["id"], p["number"]),
            )
        for r in db.execute(
            "SELECT revision FROM runs WHERE job=?", (job["id"],)
        ).fetchall():
            self._close_run(db, job, r[0])
        db.execute("UPDATE books SET cancelled=1 WHERE id=?", (job["id"],))

    def cancel(self, owner, job_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT * FROM books WHERE id=? AND owner=? AND deleted=0",
                (job_id, owner),
            ).fetchone()
            if not job:
                raise BookError("Conversion not found.")
            self._cancel(db, job)
            return True

    def retry(self, owner, job_id, request_id, pages, settings, mode=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT * FROM books WHERE id=? AND owner=? AND deleted=0 AND expires>?",
                (job_id, owner, time.time()),
            ).fetchone()
            if not job:
                raise BookError("Conversion not found or expired.")
            prior = db.execute(
                "SELECT revision FROM runs WHERE job=? AND request_id=?",
                (job_id, request_id),
            ).fetchone()
            if prior:
                return {
                    "id": job_id,
                    "revision": prior[0],
                    "request_id": request_id,
                    "_new": False,
                }
            if job["trial"]:
                raise BookError("Reprocessing requires processing credits.")
            if db.execute(
                "SELECT COUNT(*) FROM parts WHERE job=? AND status IN ('queued','running')",
                (job_id,),
            ).fetchone()[0]:
                raise BookError(
                    "Wait for running work or cancel queued sections before retrying."
                )
            doc = db.execute(
                "SELECT * FROM documents WHERE id=?", (job["document"],)
            ).fetchone()
            parts = db.execute("SELECT * FROM parts WHERE job=?", (job_id,)).fetchall()
            allowed = set(json.loads(doc["pages"]))
            complete = {
                n
                for p in parts
                if p["status"] == "done"
                for n in json.loads(p["pages"])
            }
            chosen = sorted(set(pages)) if pages else sorted(allowed - complete)
            if not chosen or not set(chosen) <= allowed:
                raise BookError("Choose previously selected pages to reprocess.")
            mode = mode or doc["mode"]
            if mode not in ("auto", "ocr", "text"):
                raise BookError("Unsupported reprocessing mode.")
            fee = (
                settings.repair_base_units
                if set(chosen) & complete
                else (
                    0
                    if job["charged"]
                    else settings.text_base_units
                    if mode == "text"
                    else settings.base_units
                )
            )
            revision = job["revision"] + 1
            units = self._run(
                db,
                dict(job),
                revision,
                chosen,
                mode,
                fee,
                request_id,
                False,
                settings,
                doc["lang"],
            )
            db.execute(
                "UPDATE books SET revision=?,cancelled=0 WHERE id=?", (revision, job_id)
            )
            return {
                "id": job_id,
                "revision": revision,
                "request_id": request_id,
                "quoted": units,
                "_new": True,
            }

    def delete(self, owner, job_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT * FROM books WHERE id=? AND owner=?", (job_id, owner)
            ).fetchone()
            if not job:
                raise BookError("Conversion not found.")
            self._cancel(db, job)
            db.execute("UPDATE books SET deleted=1 WHERE id=?", (job_id,))
            db.execute("DELETE FROM corrections WHERE job=?", (job_id,))
            db.execute("DELETE FROM correction_history WHERE job=?", (job_id,))
            used = db.execute(
                "SELECT COUNT(*) FROM books WHERE document=? AND deleted=0 AND expires>?",
                (job["document"], time.time()),
            ).fetchone()[0]
            if not used:
                db.execute(
                    "UPDATE documents SET expires=? WHERE id=?",
                    (time.time(), job["document"]),
                )
            return {"document": job["document"], "source_unused": not bool(used)}

    def edit(self, owner, job_id, page, text, version):
        job = self.book_get(owner, job_id)
        if not job or page not in job["completed_pages"]:
            raise BookError("This completed page is not available.")
        if not isinstance(text, str) or len(text.encode()) > 200000:
            raise BookError("Page edit exceeds the limit.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT version FROM corrections WHERE job=? AND page=?", (job_id, page)
            ).fetchone()
            old = prior[0] if prior else 0
            if version != old:
                raise BookError(
                    "This page changed. Reload before saving to avoid overwriting edits."
                )
            new = old + 1
            now = time.time()
            db.execute(
                "INSERT OR REPLACE INTO corrections VALUES (?,?,?,?,?)",
                (job_id, page, text, new, now),
            )
            db.execute(
                "INSERT INTO correction_history VALUES (?,?,?,?,?)",
                (job_id, page, text, new, now),
            )
            return new

    def edits(self, owner, job_id):
        if not self.book_get(owner, job_id):
            raise BookError("Conversion not found.")
        with closing(self.db()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT page,text,version,updated FROM corrections WHERE job=?",
                    (job_id,),
                )
            ]

    def history(self, owner, job_id, page):
        if not self.book_get(owner, job_id):
            raise BookError("Conversion not found.")
        with closing(self.db()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT text,version,updated FROM correction_history WHERE job=? AND page=? ORDER BY version DESC LIMIT 20",
                    (job_id, page),
                )
            ]

    def stale(self):
        with closing(self.db()) as db:
            rows = [
                (r[0], r[1])
                for r in db.execute(
                    "SELECT job,number FROM parts WHERE status='running' AND deadline<?",
                    (time.time(),),
                )
            ]
        for job, number in rows:
            self.part_finish(
                job,
                number,
                False,
                "Worker ended without a result. Reserved page allowance released; retry explicitly.",
            )
        return len(rows)

    def expired(self):
        with closing(self.db()) as db:
            jobs = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM books WHERE expires<=? OR deleted=1", (time.time(),)
                )
            ]
            documents = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM documents WHERE expires<=?", (time.time(),)
                )
            ]
        for j in jobs:
            self.delete(j["owner"], j["id"])
            with closing(self.db()) as db:
                running = [
                    r[0]
                    for r in db.execute(
                        "SELECT number FROM parts WHERE job=? AND status='running'",
                        (j["id"],),
                    )
                ]
            for number in running:
                self.part_finish(j["id"], number, False, "Expired.")
        return {"jobs": [j["id"] for j in jobs], "documents": documents}

    def purged(self, jobs, documents):
        with self.db() as db:
            for jid in jobs:
                for table in ("parts", "runs", "corrections", "correction_history"):
                    db.execute(f"DELETE FROM {table} WHERE job=?", (jid,))
                db.execute(
                    "DELETE FROM books WHERE id=? AND (deleted=1 OR expires<=?)",
                    (jid, time.time()),
                )
            for did in documents:
                db.execute(
                    "DELETE FROM documents WHERE id=? AND expires<=?",
                    (did, time.time()),
                )
