"""Private owner operations on the same serialized SQLite connection as credits.

No public admin route, document contents, raw addresses or tracking IDs. Monetary
units are 1/10,000 USD. Admission envelopes are conservative operating controls,
not a Modal invoice or a provider-enforced account spending limit.
"""

from contextlib import closing
import json
import math
import re
import sqlite3
import time

EVENTS = frozenset(
    {
        "landing",
        "inspection",
        "confirmation",
        "checkout",
        "purchase_restore",
        "export",
        "retry",
        "request_error",
        "server_error",
        "payment_error",
        "reconcile_error",
        "trial_started",
        "paid_run_started",
    }
)
SOURCES = frozenset({"direct", "portfolio", "github", "obsidian", "research", "other"})


class Operations:
    def setup_operations(self, db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS operations_control(id INTEGER PRIMARY KEY CHECK(id=1),paused INTEGER NOT NULL DEFAULT 0,daily_units INTEGER NOT NULL DEFAULT 100000,monthly_units INTEGER NOT NULL DEFAULT 250000);
        INSERT OR IGNORE INTO operations_control(id) VALUES (1);
        CREATE TABLE IF NOT EXISTS operation_events(day TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(day,kind,source));
        CREATE TABLE IF NOT EXISTS compute_attempts(job TEXT NOT NULL,number INTEGER NOT NULL,day TEXT NOT NULL,month TEXT NOT NULL,mode TEXT NOT NULL,envelope INTEGER NOT NULL,started REAL,seconds REAL,outcome TEXT NOT NULL DEFAULT 'admitted',PRIMARY KEY(job,number));
        CREATE TABLE IF NOT EXISTS monthly_actuals(month TEXT PRIMARY KEY,net_receipts INTEGER NOT NULL,modal_cost INTEGER NOT NULL,other_cost INTEGER NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS control_audit(at REAL NOT NULL,settings TEXT NOT NULL);
        """)
        columns = {r[1] for r in db.execute("PRAGMA table_info(accounts)")}
        if "spent" not in columns:
            db.execute(
                "ALTER TABLE accounts ADD COLUMN spent INTEGER NOT NULL DEFAULT 0"
            )
            # Identity survives old book deletion: funding - revocation = balance + consumption.
            db.execute(
                """UPDATE accounts SET spent=COALESCE((SELECT SUM(granted-revoked) FROM purchases WHERE owner=accounts.owner),0)-available-reserved"""
            )
        columns = {r[1] for r in db.execute("PRAGMA table_info(purchases)")}
        if "verified" not in columns:
            db.execute(
                "ALTER TABLE purchases ADD COLUMN verified INTEGER NOT NULL DEFAULT 0"
            )
            db.execute(
                "ALTER TABLE purchases ADD COLUMN checked REAL NOT NULL DEFAULT 0"
            )

    def _event(self, db, kind, source="direct"):
        if kind not in EVENTS:
            raise ValueError("Unknown metric")
        if source not in SOURCES:
            source = "other"
        day = time.strftime("%Y-%m-%d", time.gmtime())
        db.execute(
            "INSERT INTO operation_events VALUES (?,?,?,1) ON CONFLICT(day,kind,source) DO UPDATE SET count=count+1",
            (day, kind, source),
        )

    def event(self, kind, source="direct"):
        with self.db() as db:
            self._event(db, kind, source)

    def admission(self, db, job, start, count, mode):
        from web.books import BookError

        control = db.execute("SELECT * FROM operations_control WHERE id=1").fetchone()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        month = day[:7]
        # $0.15 GPU / $0.01 CPU per <=20-page section, incl. failures. Never
        # release envelopes on cancellation: races/idle costs must not recycle it.
        per = 100 if mode == "text" else 1500
        admitted = count * per
        daily = db.execute(
            "SELECT COALESCE(SUM(envelope),0) FROM compute_attempts WHERE day=?", (day,)
        ).fetchone()[0]
        monthly = db.execute(
            "SELECT COALESCE(SUM(envelope),0) FROM compute_attempts WHERE month=?",
            (month,),
        ).fetchone()[0]
        if control["paused"]:
            raise BookError(
                "Processing is temporarily paused. Your credits were not reserved; retained exports remain available."
            )
        if (
            daily + admitted > control["daily_units"]
            or monthly + admitted > control["monthly_units"]
        ):
            raise BookError(
                "The processing safety budget is reached. No credits reserved. Try later or contact support."
            )
        db.executemany(
            "INSERT INTO compute_attempts(job,number,day,month,mode,envelope) VALUES (?,?,?,?,?,?)",
            [(job, start + i, day, month, mode, per) for i in range(count)],
        )

    def control(self, paused=None, daily_units=None, monthly_units=None):
        for value in (daily_units, monthly_units):
            if value is not None and (
                type(value) is not int or not 0 <= value <= 100000000
            ):
                raise ValueError("Budget must be between $0 and $10,000")
        if paused is not None and type(paused) is not bool:
            raise ValueError("Pause must be a boolean")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, value in {
                "paused": paused,
                "daily_units": daily_units,
                "monthly_units": monthly_units,
            }.items():
                if value is not None:
                    db.execute(
                        f"UPDATE operations_control SET {key}=? WHERE id=1", (value,)
                    )
            value = dict(
                db.execute("SELECT * FROM operations_control WHERE id=1").fetchone()
            )
            if any(v is not None for v in (paused, daily_units, monthly_units)):
                db.execute(
                    "INSERT INTO control_audit VALUES (?,?)",
                    (time.time(), json.dumps(value)),
                )
            return value

    def part_allowed(self, job_id, number):
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
            if db.execute(
                "SELECT paused FROM operations_control WHERE id=1"
            ).fetchone()[0]:
                self._cancel(db, job)
                return False
            return bool(
                db.execute(
                    "SELECT 1 FROM parts WHERE job=? AND number=? AND status='queued'",
                    (job_id, number),
                ).fetchone()
            )

    def admit_request(self, bucket, identity):
        from web.books import BookError

        caps = {
            "inspect": (3600, 30, 300),
            "session": (60, 60, 600),
            "payment": (60, 12, 120),
        }
        if bucket not in caps or not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("Invalid request bucket")
        window, individual, global_cap = caps[bucket]
        cycle = str(int(time.time()) // window)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for who, cap in (
                ("request:" + bucket + ":" + identity, individual),
                ("request:" + bucket + ":global", global_cap),
            ):
                prior = db.execute(
                    "SELECT used FROM quotas WHERE owner=? AND cycle=?", (who, cycle)
                ).fetchone()
                if prior and prior[0] >= cap:
                    raise BookError(
                        "Too many requests. Wait a little before trying again."
                    )
                db.execute(
                    "INSERT INTO quotas VALUES (?,?,1,?) ON CONFLICT(owner,cycle) DO UPDATE SET used=used+1",
                    (who, cycle, time.time() + 86400),
                )

    def actuals(self, month, net_receipts, modal_cost, other_cost):
        if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
            raise ValueError("Use YYYY-MM")
        if any(
            type(v) is not int or not 0 <= v <= 10000000000
            for v in (net_receipts, modal_cost, other_cost)
        ):
            raise ValueError("Use non-negative USD amounts")
        with self.db() as db:
            db.execute(
                "INSERT INTO monthly_actuals VALUES (?,?,?,?,?) ON CONFLICT(month) DO UPDATE SET net_receipts=excluded.net_receipts,modal_cost=excluded.modal_cost,other_cost=excluded.other_cost,updated=excluded.updated",
                (month, net_receipts, modal_cost, other_cost, time.time()),
            )

    def snapshot(self):
        # Executed by the one writer, not by copying an open SQLite file.
        with (
            closing(self.db()) as source,
            closing(sqlite3.connect(":memory:")) as target,
        ):
            source.backup(target)
            # Financial recovery does not require users' Markdown or filenames.
            # VACUUM removes discarded content from SQLite's free pages too.
            for table in ("corrections", "correction_history"):
                target.execute(f"DELETE FROM {table}")
            target.execute("UPDATE documents SET filename='[redacted]',digest=''")
            target.execute("UPDATE jobs SET filename='[redacted]',digest=''")
            target.commit()
            target.execute("VACUUM")
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot integrity check failed")
            return target.serialize()

    def operations(self):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        month = day[:7]
        with closing(self.db()) as db:
            control = dict(
                db.execute("SELECT * FROM operations_control WHERE id=1").fetchone()
            )
            wallets = []
            for kind, condition in (
                (
                    "commercial",
                    "EXISTS(SELECT 1 FROM purchases p WHERE p.owner=a.owner AND verified=1) AND NOT EXISTS(SELECT 1 FROM purchases p WHERE p.owner=a.owner AND verified=0)",
                ),
                (
                    "fixtures_or_unverified",
                    "EXISTS(SELECT 1 FROM purchases p WHERE p.owner=a.owner AND verified=0)",
                ),
            ):
                a = db.execute(
                    f"SELECT COUNT(*),COALESCE(SUM(available),0),COALESCE(SUM(reserved),0),COALESCE(SUM(spent),0),COALESCE(SUM(MAX(available,0)+reserved),0) FROM accounts a WHERE {condition}"
                ).fetchone()
                p = db.execute(
                    f"SELECT COALESCE(SUM(granted),0),COALESCE(SUM(revoked),0) FROM purchases WHERE owner IN (SELECT a.owner FROM accounts a WHERE {condition})"
                ).fetchone()
                wallets.append(
                    {
                        "kind": kind,
                        "accounts": a[0],
                        "available_units": a[1],
                        "reserved_units": a[2],
                        "consumed_units": a[3],
                        "outstanding_face_units": a[4],
                        "funded_face_units": p[0],
                        "revoked_units": p[1],
                        "balance_invariant_ok": p[0] - p[1] == a[1] + a[2] + a[3],
                    }
                )
            attempts = [
                dict(r)
                for r in db.execute(
                    "SELECT mode,outcome,COUNT(*) AS attempts,COALESCE(SUM(seconds),0) AS observed_seconds,SUM(envelope) AS envelope_units FROM compute_attempts WHERE month=? GROUP BY mode,outcome",
                    (month,),
                )
            ]
            daily = db.execute(
                "SELECT COALESCE(SUM(envelope),0) FROM compute_attempts WHERE day=?",
                (day,),
            ).fetchone()[0]
            monthly = db.execute(
                "SELECT COALESCE(SUM(envelope),0) FROM compute_attempts WHERE month=?",
                (month,),
            ).fetchone()[0]
            trial_usage = {"day": 0, "month": 0}
            for key, cycle in (("day", day), ("month", month)):
                trial = db.execute(
                    "SELECT used FROM quotas WHERE owner='global-trial' AND cycle=?",
                    (cycle,),
                ).fetchone()
                trial_usage[key] = trial[0] if trial else 0
            outstanding = db.execute(
                "SELECT COUNT(*) FROM parts WHERE status IN ('queued','running')"
            ).fetchone()[0]
            stale = db.execute(
                "SELECT COUNT(*) FROM parts WHERE status='running' AND deadline<?",
                (time.time(),),
            ).fetchone()[0]
            negatives = db.execute(
                "SELECT COUNT(*) FROM accounts WHERE available<0"
            ).fetchone()[0]
            events = [
                dict(r)
                for r in db.execute(
                    "SELECT day,kind,source,count FROM operation_events WHERE day>=? ORDER BY day,kind,source",
                    (time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400)),),
                )
            ]
            actuals = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM monthly_actuals ORDER BY month DESC LIMIT 12"
                )
            ]
            commercial_purchases = db.execute(
                "SELECT COUNT(*) FROM purchases p WHERE verified=1 AND NOT EXISTS(SELECT 1 FROM purchases u WHERE u.owner=p.owner AND u.verified=0)"
            ).fetchone()[0]
            repeat_buyers = db.execute(
                "SELECT COUNT(*) FROM (SELECT owner FROM purchases p WHERE verified=1 AND NOT EXISTS(SELECT 1 FROM purchases u WHERE u.owner=p.owner AND u.verified=0) GROUP BY owner HAVING COUNT(*)>1)"
            ).fetchone()[0]
            invalid_balances = db.execute(
                "SELECT COUNT(*) FROM accounts a WHERE reserved<0 OR spent<0 OR available+reserved+spent!=COALESCE((SELECT SUM(granted-revoked) FROM purchases WHERE owner=a.owner),0)"
            ).fetchone()[0]
        alerts = []
        if invalid_balances:
            alerts.append(
                "Credit balance invariant failed: pause processing and inspect the private ledger."
            )
        if stale:
            alerts.append("Stale workers need reconciliation.")
        if negatives:
            alerts.append(
                "Refunds exceeded remaining credit; affected accounts cannot start new work."
            )
        for name, admitted, cap in (
            ("daily", daily, control["daily_units"]),
            ("monthly", monthly, control["monthly_units"]),
        ):
            if admitted >= cap * 0.8:
                alerts.append(
                    f"{name.capitalize()} admission budget is at least 80% used."
                )
        failures = sum(a["attempts"] for a in attempts if a["outcome"] == "error")
        completed = sum(
            a["attempts"] for a in attempts if a["outcome"] in ("done", "error")
        )
        if failures >= 3 and failures / max(completed, 1) > 0.2:
            alerts.append(
                "Section failure rate exceeds 20% with at least three failures; investigate before expanding traffic."
            )
        recent_errors = sum(
            e["count"]
            for e in events
            if e["day"] == day
            and e["kind"] in ("server_error", "payment_error", "reconcile_error")
        )
        if recent_errors:
            alerts.append(
                "Server/payment/reconciliation errors occurred today; inspect sanitized platform logs."
            )
        current = next((a for a in actuals if a["month"] == month), None)
        if not current:
            alerts.append(
                "Current-month actual receipts and costs have not been reconciled; profit is unverified."
            )
        elif current["net_receipts"] < current["modal_cost"] + current["other_cost"]:
            alerts.append("Recorded current-month cash costs exceed net receipts.")
        # Function-body wall time excludes cold load, idle, orchestration, web,
        # ledger, storage and egress. Display it only as an estimate, never actual.
        observed = sum(
            a["observed_seconds"] * (0.00001754 if a["mode"] == "text" else 0.00034996)
            for a in attempts
        )
        return {
            "at": time.time(),
            "day_utc": day,
            "month_utc": month,
            "control": control,
            "admitted_daily_units": daily,
            "admitted_monthly_units": monthly,
            "observed_worker_estimate_units": math.ceil(observed * 10000),
            "attempts": attempts,
            "wallets": wallets,
            "commercial_purchases": commercial_purchases,
            "repeat_buyers": repeat_buyers,
            "trial_usage": trial_usage,
            "pending_sections": outstanding,
            "stale_sections": stale,
            "negative_accounts": negatives,
            "invalid_balances": invalid_balances,
            "events_30d": events,
            "actuals": actuals,
            "alerts": alerts,
            "measurement_note": "Event counts include owner verification, bots and reloads, and are not unique users. Credit-backed run counts include fixture credit, not necessarily customer purchases. Admission envelopes and worker estimates are not invoices or guaranteed account cost caps. Only explicitly verified live orders count as commercial; fixture/mixed accounts are excluded.",
        }
