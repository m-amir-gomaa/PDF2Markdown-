import asyncio, hashlib, hmac, json, tempfile, unittest, uuid
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
import httpx
from web.books import BookLedger
from web.config import Settings
from web.credits import CreditProvider
from web.billing import BillingError
from web.studio import create_app
from web.documents import extract_section
from tests import test_books as bookfixtures


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = BookLedger(self.tmp.name + "/db.sqlite")
        self.s = Settings(Fernet.generate_key().decode())
        self.files = {}
        self.dispatches = []

        async def store(op, args):
            return getattr(self.db, op)(**args)

        async def storage(op, args):
            if op == "put_source":
                self.files[args["document"]] = args["data"]
            elif op == "source":
                return self.files[args["document"]]
            elif op == "parts":
                return {n: self.files[(args["job"], n)] for n in args["numbers"]}
            elif op == "delete_job":
                for key in list(self.files):
                    if isinstance(key, tuple) and key[0] == args["job"]:
                        del self.files[key]
            elif op == "delete_source":
                self.files.pop(args["document"], None)

        async def dispatcher(jid):
            self.dispatches.append(jid)

        self.app = create_app(self.s, store, storage, dispatcher)
        self.client = TestClient(self.app, base_url="https://testserver")
        self.csrf = self.client.get("/api/session").json()["csrf"]

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def post(self, path, body):
        return self.client.post(path, json=body, headers={"X-CSRF-Token": self.csrf})

    def upload(self, pages="1-3", mode="text"):
        return self.client.post(
            "/api/documents",
            files={
                "file": (
                    "source.pdf",
                    bookfixtures.DocumentTests().pdf(),
                    "application/pdf",
                )
            },
            data={"request_id": str(uuid.uuid4()), "pages": pages, "mode": mode},
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_inspection_before_compute_and_unfunded_confirmation(self):
        r = self.upload()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.dispatches, [])
        q = r.json()
        r = self.post(
            "/api/jobs",
            {
                "document": q["id"],
                "request_id": str(uuid.uuid4()),
                "expected_units": q["quote"]["units"],
            },
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.dispatches, [])

    def test_trial_bounds_idempotency_owner_isolation(self):
        q = self.upload().json()
        rid = str(uuid.uuid4())
        body = {"document": q["id"], "request_id": rid, "trial": True}
        r = self.post("/api/jobs", body)
        self.assertEqual(r.status_code, 200)
        jid = r.json()["id"]
        self.assertEqual(self.post("/api/jobs", body).json()["id"], jid)
        self.assertEqual(len(self.dispatches), 1)
        j = self.db.book_internal(jid)
        self.assertEqual(j["parts"][0]["pages"], [1])
        other = TestClient(self.app, base_url="https://testserver")
        other.get("/api/session")
        self.assertEqual(other.get("/api/jobs/" + jid).status_code, 404)
        self.assertEqual(other.get("/api/jobs/" + jid + "/source").status_code, 404)
        other.close()

    def test_complete_edit_download_and_recovery(self):
        q = self.upload("1,3").json()
        r = self.post(
            "/api/jobs",
            {"document": q["id"], "request_id": str(uuid.uuid4()), "trial": True},
        )
        jid = r.json()["id"]
        j = self.db.book_internal(jid)
        self.db.part_start(jid, 0)
        self.files[(jid, 0)] = extract_section(self.files[q["id"]], [1], "text")
        self.db.part_finish(jid, 0, True)
        pages = self.client.get("/api/jobs/" + jid + "/pages").json()["pages"]
        self.assertEqual(pages[0]["number"], 1)
        self.assertEqual(
            self.post(
                f"/api/jobs/{jid}/pages/1", {"text": "A correction", "version": 0}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.post(
                f"/api/jobs/{jid}/pages/1", {"text": "Lost edit", "version": 0}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.get(f"/api/jobs/{jid}/download?layout=pages").status_code, 200
        )
        key = self.post("/api/recovery", {}).json()["recovery_key"]
        self.client.cookies.clear()
        self.csrf = self.client.get("/api/session").json()["csrf"]
        self.assertEqual(
            self.post("/api/restore", {"recovery_key": key}).status_code, 200
        )
        self.csrf = self.client.get("/api/session").json()["csrf"]
        self.assertEqual(self.client.get("/api/jobs/" + jid).status_code, 200)

    def test_csrf_bad_selection_and_quote_changes(self):
        self.assertEqual(self.client.post("/api/recovery").status_code, 403)
        self.assertEqual(self.upload("999").status_code, 400)
        q = self.upload().json()
        self.assertEqual(
            self.post(
                "/api/jobs",
                {
                    "document": q["id"],
                    "request_id": str(uuid.uuid4()),
                    "expected_units": 1,
                },
            ).status_code,
            409,
        )

    def test_payment_configuration_fails_closed(self):
        self.assertFalse(self.client.get("/health").json()["billing_enabled"])
        self.assertEqual(self.post("/api/checkout", {"pack": 5}).status_code, 402)
        self.assertEqual(
            self.post("/api/access", {"license_key": "fake-purchase"}).status_code, 402
        )


class CreditsTests(unittest.TestCase):
    def setUp(self):
        self.s = Settings(
            Fernet.generate_key().decode(),
            store_id="1",
            lemon_api_key="fixture",
            webhook_secret="fixture-webhook",
            variant5="5",
            variant10="10",
        )

    def transport(self, status="paid", variant=5, test=False, store=1):
        def handler(request):
            if request.url.path.endswith("/validate"):
                return httpx.Response(
                    200,
                    json={
                        "valid": True,
                        "meta": {"store_id": 1, "variant_id": 5, "order_id": 99},
                        "license_key": {
                            "id": 1,
                            "status": "active",
                            "expires_at": None,
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "99",
                        "attributes": {
                            "store_id": store,
                            "test_mode": test,
                            "currency": "USD",
                            "subtotal": 500,
                            "discount_total": 0,
                            "status": status,
                            "refunded": status == "refunded",
                            "first_order_item": {"variant_id": variant},
                        },
                    }
                },
            )

        return httpx.MockTransport(handler)

    def test_one_time_key_grants_scoped_order(self):
        v = asyncio.run(CreditProvider(self.s, self.transport()).key("fixture-key-123"))
        self.assertEqual(v["units"], 50000)
        self.assertEqual(v["order_id"], "1:99")

    def test_wrong_store_variant_test_and_unpaid_rejected(self):
        for kwargs in [
            {"store": 2},
            {"variant": 77},
            {"test": True},
            {"status": "pending"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(BillingError):
                asyncio.run(CreditProvider(self.s, self.transport(**kwargs)).order(99))

    def test_signature_and_refund(self):
        provider = CreditProvider(self.s, self.transport(status="refunded"))
        v = asyncio.run(provider.order(99))
        self.assertEqual(v["revoked"], 50000)
        body = json.dumps(
            {
                "meta": {"event_name": "order_refunded"},
                "data": {"type": "orders", "id": "99"},
            }
        ).encode()
        signature = hmac.new(
            self.s.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(provider.webhook(body, signature)["order_id"], "99")
        with self.assertRaises(BillingError):
            provider.webhook(body, "bad")


class PayloadTests(unittest.TestCase):
    setUp = StudioTests.setUp
    tearDown = StudioTests.tearDown

    def test_invalid_json_and_non_object_rejected(self):
        h = {"X-CSRF-Token": self.csrf, "Content-Type": "application/json"}
        for body in ["not-json", "[]"]:
            self.assertEqual(
                self.client.post("/api/checkout", content=body, headers=h).status_code,
                400,
            )
