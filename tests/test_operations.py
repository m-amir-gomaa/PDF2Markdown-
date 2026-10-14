import json
from pathlib import Path
from contextlib import closing
import sqlite3
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from scripts.operations import dashboard, validate_snapshot
from tests import test_books as bookfixtures
from tests import test_studio as studiofixtures
from web.books import BookLedger, BookError
from web.credits import CreditProvider
from web.inspect_worker import bounded_inspect
import asyncio
import os
import subprocess
from unittest.mock import patch
import httpx


class OperationsTests(unittest.TestCase):
    setUp = bookfixtures.BooksTests.setUp
    tearDown = bookfixtures.BooksTests.tearDown
    document = bookfixtures.BooksTests.document

    def test_admission_budget_is_atomic_under_concurrent_confirmations(self):
        self.db.control(daily_units=3000, monthly_units=3000)
        document = self.document(20)

        def submit(_):
            try:
                return self.db.confirm(
                    self.owner, document["id"], str(uuid.uuid4()), False, "ip", self.s
                )
            except BookError:
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            jobs = list(pool.map(submit, range(20)))
        self.assertEqual(sum(j is not None for j in jobs), 2)
        self.assertEqual(self.db.operations()["admitted_monthly_units"], 3000)
        self.assertEqual(self.db.account(self.owner)["reserved"], 12000)
        for j in jobs:
            if j:
                self.db.cancel(self.owner, j["id"])
        self.assertEqual(self.db.account(self.owner)["reserved"], 0)
        self.assertEqual(self.db.operations()["admitted_monthly_units"], 3000)

    def test_pause_blocks_new_jobs_and_releases_queued_holds(self):
        document = self.document(2)
        j = self.db.confirm(
            self.owner, document["id"], str(uuid.uuid4()), False, "ip", self.s
        )
        self.db.control(paused=True)
        before = self.db.account(self.owner)
        with self.assertRaises(BookError):
            self.db.confirm(
                self.owner, document["id"], str(uuid.uuid4()), False, "ip", self.s
            )
        self.assertEqual(self.db.account(self.owner), before)
        self.assertFalse(self.db.part_allowed(j["id"], 0))
        self.assertFalse(self.db.part_start(j["id"], 0))
        self.assertEqual(self.db.account(self.owner)["reserved"], 0)

    def test_pause_allows_already_running_work_to_settle_once(self):
        document = self.document(2)
        j = self.db.confirm(
            self.owner, document["id"], str(uuid.uuid4()), False, "ip", self.s
        )
        self.db.part_start(j["id"], 0)
        self.db.control(paused=True)
        self.assertTrue(self.db.part_finish(j["id"], 0, True, elapsed=2))
        self.assertFalse(self.db.part_finish(j["id"], 0, True, elapsed=2))
        self.assertTrue(self.db.operations()["wallets"][1]["balance_invariant_ok"])

    def test_credit_and_refund_arrive_atomically_and_replay_cannot_regrant(self):
        owner = str(uuid.uuid4())
        self.db.credit(owner, "1:99", 50000, revoked=30000, verified=True)
        self.assertEqual(self.db.account(owner)["available"], 20000)
        self.db.credit(owner, "1:99", 50000, revoked=50000, verified=True)
        self.db.credit(owner, "1:99", 50000, revoked=0, verified=True)
        self.assertEqual(self.db.account(owner)["available"], 0)
        report = self.db.operations()
        self.assertEqual(report["commercial_purchases"], 1)
        self.assertEqual(report["wallets"][0]["revoked_units"], 50000)
        self.assertTrue(report["wallets"][0]["balance_invariant_ok"])

    def test_financial_totals_survive_file_purge_and_snapshot_strips_content(self):
        document = self.document(2)
        j = self.db.confirm(
            self.owner, document["id"], str(uuid.uuid4()), False, "ip", self.s
        )
        self.db.part_start(j["id"], 0)
        self.db.part_finish(j["id"], 0, True, elapsed=3)
        self.db.edit(self.owner, j["id"], 1, "PRIVATE_DOCUMENT_MARKDOWN", 0)
        blob = self.db.snapshot()
        self.assertNotIn(b"PRIVATE_DOCUMENT_MARKDOWN", blob)
        self.assertNotIn(b"book.pdf", blob)
        validate_snapshot(blob)
        self.db.delete(self.owner, j["id"])
        self.db.purged([j["id"]], [document["id"]])
        report = self.db.operations()
        self.assertEqual(report["wallets"][1]["consumed_units"], 5100)
        self.assertTrue(report["wallets"][1]["balance_invariant_ok"])

    def test_old_ledger_migrates_without_losing_purged_service_consumption(self):
        path = Path(self.tmp.name) / "old.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                "CREATE TABLE accounts(owner TEXT PRIMARY KEY,available INTEGER NOT NULL DEFAULT 0,reserved INTEGER NOT NULL DEFAULT 0,recovery TEXT UNIQUE);CREATE TABLE purchases(order_id TEXT PRIMARY KEY,owner TEXT NOT NULL,granted INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);"
            )
            db.execute("INSERT INTO accounts VALUES ('old',10000,2000,NULL)")
            db.execute("INSERT INTO purchases VALUES ('fixture','old',50000,5000)")
        migrated = BookLedger(path)
        r = migrated.operations()["wallets"][1]
        self.assertEqual(r["consumed_units"], 33000)
        self.assertTrue(r["balance_invariant_ok"])

    def test_request_caps_and_safe_aggregate_events(self):
        for _ in range(30):
            self.db.admit_request("inspect", "a" * 64)
        with self.assertRaises(BookError):
            self.db.admit_request("inspect", "a" * 64)
        self.db.event("landing", "<script>not_allowed</script>")
        report = self.db.operations()
        self.assertEqual(report["events_30d"][0]["source"], "other")
        self.assertNotIn(self.owner, json.dumps(report))
        self.assertNotIn("<script>", dashboard(report))
        with self.assertRaises(ValueError):
            self.db.control(daily_units=-1)

    def test_recorded_actual_losses_trigger_alert(self):
        month = self.db.operations()["month_utc"]
        self.db.actuals(month, 10000, 20000, 5000)
        self.assertTrue(
            any("cash costs exceed" in a for a in self.db.operations()["alerts"])
        )


class InspectionTests(unittest.TestCase):
    def test_real_bounded_parser_handles_valid_pdf_and_rejects_bad_pdf(self):
        pdf = bookfixtures.DocumentTests().pdf()
        with (
            patch.dict(
                os.environ,
                {
                    "DOCUMENT_STUDIO_SESSION_KEY": "secret-fixture",
                    "MODAL_TOKEN_SECRET": "secret-fixture",
                },
            ),
            patch("web.inspect_worker.subprocess.run", wraps=subprocess.run) as run,
        ):
            self.assertEqual(bounded_inspect(pdf, 1024 * 1024, 1000)["total_pages"], 4)
            self.assertNotIn("DOCUMENT_STUDIO_SESSION_KEY", run.call_args.kwargs["env"])
            self.assertNotIn("MODAL_TOKEN_SECRET", run.call_args.kwargs["env"])
        with self.assertRaises(ValueError):
            bounded_inspect(b"%PDF-broken", 1024 * 1024, 1000)


class RequestSafetyTests(unittest.TestCase):
    setUp = studiofixtures.StudioTests.setUp
    tearDown = studiofixtures.StudioTests.tearDown
    post = studiofixtures.StudioTests.post

    def test_small_json_limit_and_no_public_admin(self):
        response = self.client.post(
            "/api/checkout",
            content=b"x" * 65537,
            headers={"X-CSRF-Token": self.csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get("/api/admin").status_code, 404)
        self.assertEqual(self.client.get("/api/operations").status_code, 404)

    def test_test_credentials_do_not_activate_public_payments(self):
        self.s = replace(
            self.s,
            billing_mode="test",
            store_id="1",
            variant5="5",
            variant10="10",
            lemon_api_key="fixture",
            webhook_secret="fixture",
        )
        from web.studio import create_app
        from fastapi.testclient import TestClient

        async def store(operation, args):
            return getattr(self.db, operation)(**args)

        async def unused(*args):
            raise AssertionError("Not called")

        with TestClient(
            create_app(self.s, store, unused, unused), base_url="https://testserver"
        ) as client:
            csrf = client.get("/api/session").json()["csrf"]
            for path, body in (
                ("/api/checkout", {"pack": 5}),
                ("/api/access", {"license_key": "fixture-key"}),
            ):
                self.assertEqual(
                    client.post(
                        path, json=body, headers={"X-CSRF-Token": csrf}
                    ).status_code,
                    402,
                )


class PartialRefundTests(unittest.TestCase):
    setUp = studiofixtures.CreditsTests.setUp

    def test_proportional_partial_refund_and_isolated_provider_test_mode(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "99",
                        "attributes": {
                            "store_id": 1,
                            "test_mode": False,
                            "currency": "USD",
                            "subtotal": 500,
                            "discount_total": 0,
                            "status": "partial_refund",
                            "refunded": False,
                            "total_usd": 550,
                            "refunded_amount_usd": 275,
                            "first_order_item": {"variant_id": 5},
                        },
                    }
                },
            )

        result = asyncio.run(
            CreditProvider(self.s, httpx.MockTransport(handler)).order(99)
        )
        self.assertEqual(result["revoked"], 25000)
        test_settings = replace(self.s, billing_mode="test")
        # Test orders are usable in an isolated provider harness, never public grants.
        test_provider = CreditProvider(
            test_settings, studiofixtures.CreditsTests.transport(self, test=True)
        )
        self.assertEqual(asyncio.run(test_provider.order(99))["units"], 50000)
