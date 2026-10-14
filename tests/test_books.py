import json, tempfile, unittest, uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from web.books import BookLedger, BookError
from web.documents import parse_pages, inspect_pdf, build_archive
from web.config import Settings
from cryptography.fernet import Fernet


class BooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = BookLedger(self.tmp.name + "/books.sqlite")
        self.owner = str(uuid.uuid4())
        self.s = Settings(Fernet.generate_key().decode())
        self.db.credit(self.owner, "test-order", 100000)

    def tearDown(self):
        self.tmp.cleanup()

    def document(self, pages=45):
        return self.db.prepare(
            self.owner,
            str(uuid.uuid4()),
            "a" * 64,
            "book.pdf",
            pages,
            list(range(1, pages + 1)),
            "auto",
            "latin",
            self.s.retention_seconds,
        )

    def test_pages(self):
        self.assertEqual(parse_pages("1-3, 7,2", 10), [1, 2, 3, 7])
        for bad in ["0", "11", "3-1", "x", "1,,2", "1-999999999"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_pages(bad, 10)

    def test_atomic_credit_reservation_and_idempotency(self):
        doc = self.document()
        request = str(uuid.uuid4())
        first = self.db.confirm(self.owner, doc["id"], request, False, "ip", self.s)
        same = self.db.confirm(self.owner, doc["id"], request, False, "ip", self.s)
        self.assertEqual(first["id"], same["id"])
        self.assertFalse(same["_new"])
        self.assertEqual(self.db.account(self.owner)["reserved"], 7250)
        jobs = []

        def submit(i):
            try:
                return self.db.confirm(
                    self.owner, doc["id"], str(uuid.uuid4()), False, "ip", self.s
                )["id"]
            except BookError:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            jobs = list(pool.map(submit, range(25)))
        self.assertGreaterEqual(self.db.account(self.owner)["available"], 0)

    def test_partial_failure_returns_reserved_funds(self):
        job = self.db.confirm(
            self.owner, self.document()["id"], str(uuid.uuid4()), False, "ip", self.s
        )
        self.assertTrue(self.db.part_start(job["id"], 0))
        self.assertFalse(self.db.part_start(job["id"], 0))
        self.db.part_finish(job["id"], 0, True)
        self.db.part_finish(job["id"], 0, True)
        self.db.part_start(job["id"], 1)
        self.db.part_finish(job["id"], 1, False)
        self.db.part_start(job["id"], 2)
        self.db.part_finish(job["id"], 2, True)
        status = self.db.book_get(self.owner, job["id"])
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["charged"], 6250)
        self.assertEqual(self.db.account(self.owner)["reserved"], 0)

    def test_cancel_and_retry_do_not_charge_twice(self):
        job = self.db.confirm(
            self.owner, self.document()["id"], str(uuid.uuid4()), False, "ip", self.s
        )
        self.db.cancel(self.owner, job["id"])
        self.assertEqual(self.db.account(self.owner)["reserved"], 0)
        retry = self.db.retry(self.owner, job["id"], str(uuid.uuid4()), [], self.s)
        same = self.db.retry(self.owner, job["id"], retry["request_id"], [], self.s)
        self.assertEqual(retry["revision"], same["revision"])
        self.assertFalse(same["_new"])

    def test_owner_checks_and_deletion(self):
        doc = self.document()
        job = self.db.confirm(
            self.owner, doc["id"], str(uuid.uuid4()), False, "ip", self.s
        )
        self.assertIsNone(self.db.book_get("other", job["id"]))
        with self.assertRaises(BookError):
            self.db.retry("other", job["id"], str(uuid.uuid4()), [], self.s)
        self.db.delete(self.owner, job["id"])
        self.assertIsNone(self.db.book_get(self.owner, job["id"]))

    def test_refunds_and_order_replay(self):
        self.db.credit(self.owner, "test-order", 100000)
        self.assertEqual(self.db.account(self.owner)["available"], 100000)
        self.db.reverse("test-order", 100000)
        self.db.credit(self.owner, "test-order", 100000)
        self.assertEqual(self.db.account(self.owner)["available"], 0)
        self.db.reverse("test-order", 100000)
        self.assertEqual(self.db.account(self.owner)["available"], 0)

    def test_recovery_and_edit_versions(self):
        key = "recovery-hash"
        self.db.set_recovery(self.owner, key)
        self.assertEqual(self.db.recover(key), self.owner)
        job = self.db.confirm(
            self.owner, self.document(1)["id"], str(uuid.uuid4()), False, "ip", self.s
        )
        self.db.part_start(job["id"], 0)
        self.db.part_finish(job["id"], 0, True)
        self.db.edit(self.owner, job["id"], 1, "corrected text", 0)
        with self.assertRaises(BookError):
            self.db.edit(self.owner, job["id"], 1, "lost update", 0)
        self.assertEqual(self.db.edits(self.owner, job["id"])[0]["version"], 1)


class DocumentTests(unittest.TestCase):
    def pdf(self, pages=4):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from io import BytesIO

        w = PdfWriter()
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        ref = w._add_object(font)
        for i in range(1, pages + 1):
            p = w.add_blank_page(width=300, height=400)
            p[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): ref})}
            )
            s = DecodedStreamObject()
            s.set_data(
                f"BT /F1 12 Tf 20 350 Td (Original source page {i}) Tj ET".encode()
            )
            p[NameObject("/Contents")] = w._add_object(s)
        b = BytesIO()
        w.write(b)
        return b.getvalue()

    def test_text_selected_pages_and_exports(self):
        from web.documents import extract_section, read_part
        from io import BytesIO
        import zipfile

        source = self.pdf()
        inspection = inspect_pdf(source, 1000000, 1000)
        self.assertEqual(inspection["total_pages"], 4)
        blob = extract_section(source, [1, 3], "text")
        payload = read_part(blob)
        self.assertEqual([p["number"] for p in payload["pages"]], [1, 3])
        self.assertIn("page 3", payload["pages"][1]["markdown"])
        job = {
            "parts": [{"number": 0, "pages": [1, 3], "status": "done"}],
            "total": 4,
            "mode": "text",
            "lang": "latin",
            "revision": 1,
            "charged": 2520,
        }
        for layout in ["single", "pages", "sections"]:
            archive = build_archive(
                job,
                source,
                {0: blob},
                [{"page": 3, "text": "Correction preserved", "version": 1}],
                layout,
            )
            with zipfile.ZipFile(BytesIO(archive)) as z:
                manifest = json.loads(z.read("MANIFEST.json"))
                self.assertEqual(manifest["exported_pdf_pages"], [1, 3])
                self.assertEqual(z.read("document.pdf"), source)
                text = "".join(
                    z.read(n).decode() for n in z.namelist() if n.endswith(".md")
                )
                self.assertIn("Correction preserved", text)
                self.assertIn("document.pdf#page=3", text)

    def test_partial_export_does_not_hide_missing_pages(self):
        from web.documents import extract_section
        from io import BytesIO
        import zipfile

        source = self.pdf()
        blob = extract_section(source, [1], "text")
        job = {
            "parts": [
                {"number": 0, "pages": [1], "status": "done"},
                {"number": 1, "pages": [2], "status": "error"},
            ],
            "total": 4,
            "mode": "text",
            "lang": "latin",
            "revision": 1,
            "charged": 2510,
        }
        with zipfile.ZipFile(BytesIO(build_archive(job, source, {0: blob}, []))) as z:
            self.assertEqual(
                json.loads(z.read("MANIFEST.json"))["unavailable_pdf_pages"], [2]
            )

    def test_mineru_mapping_images_and_unknown_blocks(self):
        from web.documents import extract_section, read_part, page_view

        def runner(args, **kwargs):
            out = Path(args[args.index("-o") + 1]) / "document" / "auto"
            out.mkdir(parents=True)
            (out / "images").mkdir()
            (out / "images/p.png").write_bytes(b"image")
            (out / "document.md").write_text("Original raw output\n![](images/p.png)")
            (out / "document_content_list.json").write_text(
                json.dumps(
                    [
                        {
                            "type": "text",
                            "text": "Heading",
                            "text_level": 1,
                            "page_idx": 0,
                        },
                        {
                            "type": "image",
                            "img_path": "images/p.png",
                            "img_caption": ["Caption"],
                            "page_idx": 0,
                        },
                        {"type": "mystery", "text": "Keep me", "page_idx": 1},
                    ]
                )
            )

        blob = extract_section(self.pdf(), [2, 4], "auto", "latin", runner)
        p = read_part(blob)
        self.assertEqual(p["pages"][0]["number"], 2)
        self.assertIn("unhandled_block", [i["kind"] for i in p["pages"][1]["issues"]])
        job = {"parts": [{"number": 0, "status": "done"}]}
        pages, assets, raw = page_view(job, {0: blob}, [])
        self.assertIn(
            "source-extraction/section-0000/images/p.png", pages[0]["markdown"]
        )
        self.assertTrue(assets)
        self.assertTrue(raw)


class LifecycleTests(unittest.TestCase):
    def test_stale_worker_releases_money_and_preserves_other_parts(self):
        with tempfile.TemporaryDirectory() as folder:
            db = BookLedger(folder + "/db")
            owner = "owner"
            s = Settings(Fernet.generate_key().decode())
            db.credit(owner, "order", 50000)
            d = db.prepare(
                owner,
                "r",
                "hash",
                "b.pdf",
                30,
                list(range(1, 31)),
                "auto",
                "latin",
                s.retention_seconds,
            )
            j = db.confirm(owner, d["id"], "c", False, "ip", s)
            db.part_start(j["id"], 0)
            with db.db() as sql:
                sql.execute(
                    "UPDATE parts SET deadline=0 WHERE job=? AND number=0", (j["id"],)
                )
            self.assertEqual(db.stale(), 1)
            db.cancel(owner, j["id"])
            self.assertEqual(db.account(owner)["reserved"], 0)
            self.assertEqual(db.account(owner)["available"], 50000)

    def test_discard_unused_source_and_force_ocr_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            db = BookLedger(folder + "/db")
            s = Settings(Fernet.generate_key().decode())
            db.credit("o", "order", 50000)
            d = db.prepare(
                "o",
                "r",
                "hash",
                "b.pdf",
                2,
                [1, 2],
                "auto",
                "latin",
                s.retention_seconds,
            )
            j = db.confirm("o", d["id"], "c", False, "ip", s)
            with self.assertRaises(BookError):
                db.discard_document("o", d["id"])
            db.part_start(j["id"], 0)
            db.part_finish(j["id"], 0, True)
            r = db.retry("o", j["id"], "repair", [1], s, mode="ocr")
            parts = db.book_internal(j["id"])["parts"]
            self.assertEqual(parts[-1]["pages"], [1])
            self.assertEqual(parts[-1]["mode"], "ocr")
            self.assertEqual(r["quoted"], 2050)
            db.cancel("o", j["id"])
            deleted = db.delete("o", j["id"])
            self.assertTrue(deleted["source_unused"])

    def test_full_1000_page_selection_native_processing(self):
        from web.documents import extract_section, read_part

        source = DocumentTests().pdf(1000)
        info = inspect_pdf(source, 64 * 1024 * 1024, 1000)
        self.assertEqual(info["total_pages"], 1000)
        with tempfile.TemporaryDirectory() as folder:
            db = BookLedger(folder + "/db")
            s = Settings(Fernet.generate_key().decode())
            db.credit("o", "order", 100000)
            d = db.prepare(
                "o",
                "r",
                "hash",
                "1000-pages.pdf",
                1000,
                list(range(1, 1001)),
                "text",
                "latin",
                s.retention_seconds,
            )
            j = db.confirm("o", d["id"], "c", False, "ip", s)
            self.assertEqual(len(db.book_internal(j["id"])["parts"]), 50)
            # Real extraction at the last section tests source-page indices at the bound.
            blob = extract_section(source, list(range(981, 1001)), "text")
            p = read_part(blob)
            self.assertIn("page 1000", p["pages"][-1]["markdown"])
            self.assertEqual(p["pages"][-1]["number"], 1000)
            db.cancel("o", j["id"])
            self.assertEqual(db.account("o")["reserved"], 0)


class SafetyAndExportTests(unittest.TestCase):
    def test_attempt_limit_rolls_back_reserved_credit(self):
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as folder:
            db = BookLedger(folder + "/db")
            s = replace(Settings(Fernet.generate_key().decode()), gpu_daily_pages=1)
            db.credit("o", "order", 50000)
            d = db.prepare(
                "o",
                "r",
                "hash",
                "b.pdf",
                2,
                [1, 2],
                "auto",
                "latin",
                s.retention_seconds,
            )
            with self.assertRaises(BookError):
                db.confirm("o", d["id"], "c", False, "ip", s)
            self.assertEqual(db.account("o")["available"], 50000)
            self.assertEqual(db.account("o")["reserved"], 0)
            self.assertEqual(db.book_list("o"), [])

    def test_retry_replay_after_completion_returns_original_quote(self):
        with tempfile.TemporaryDirectory() as folder:
            db = BookLedger(folder + "/db")
            s = Settings(Fernet.generate_key().decode())
            db.credit("o", "order", 50000)
            d = db.prepare(
                "o", "r", "hash", "b.pdf", 1, [1], "auto", "latin", s.retention_seconds
            )
            j = db.confirm("o", d["id"], "c", False, "ip", s)
            db.part_start(j["id"], 0)
            db.part_finish(j["id"], 0, False)
            r = db.retry("o", j["id"], "repeat", [1], s)
            db.part_start(j["id"], 1)
            db.part_finish(j["id"], 1, True)
            prior = db.retry_existing("o", j["id"], "repeat")
            self.assertEqual(prior["quoted"], r["quoted"])
            self.assertFalse(prior["_new"])

    def test_refund_cancels_queued_work_and_releases_holds(self):
        with tempfile.TemporaryDirectory() as folder:
            db = BookLedger(folder + "/db")
            s = Settings(Fernet.generate_key().decode())
            db.credit("o", "order", 50000)
            d = db.prepare(
                "o",
                "r",
                "hash",
                "b.pdf",
                20,
                list(range(1, 21)),
                "auto",
                "latin",
                s.retention_seconds,
            )
            j = db.confirm("o", d["id"], "c", False, "ip", s)
            db.reverse("order", 50000)
            self.assertEqual(db.account("o")["reserved"], 0)
            self.assertEqual(db.account("o")["available"], 0)
            self.assertEqual(db.book_get("o", j["id"])["status"], "cancelled")

    def test_bookmark_export_uses_actual_pdf_destinations(self):
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        from web.documents import extract_section
        import zipfile

        writer = PdfWriter(clone_from=PdfReader(BytesIO(DocumentTests().pdf())))
        writer.add_outline_item("First section", 0)
        writer.add_outline_item("Second section", 2)
        buffer = BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
        blob = extract_section(data, [1, 2, 3, 4], "text")
        job = {
            "parts": [{"number": 0, "status": "done", "pages": [1, 2, 3, 4]}],
            "total": 4,
            "mode": "text",
            "lang": "latin",
            "revision": 1,
            "charged": 2540,
        }
        with zipfile.ZipFile(
            BytesIO(build_archive(job, data, {0: blob}, [], layout="bookmarks"))
        ) as archive:
            self.assertIn("bookmark-section-0001.md", archive.namelist())
            self.assertIn("bookmark-section-0003.md", archive.namelist())
            self.assertIn("page 4", archive.read("bookmark-section-0003.md").decode())
