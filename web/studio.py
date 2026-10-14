"""Source-owned book conversion flow; no client-controlled payment entitlement."""

import asyncio, hashlib, hmac, json, secrets, uuid
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from web.main import BodyLimit
from web.books import BookError
from web.billing import BillingError
from web.credits import CreditProvider
from web.documents import (
    parse_pages,
    quote,
    LANGUAGES,
    build_archive,
    page_view,
)
from web.inspect_worker import bounded_inspect

ROOT = Path(__file__).resolve().parents[1]


def create_app(settings, store, storage, dispatcher, provider=None):
    provider = provider or CreditProvider(settings)
    cipher = Fernet(settings.session_key.encode())
    app = FastAPI(
        title="Document Studio", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(BodyLimit, limit=settings.paid_bytes + 65536)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.exception_handler(json.JSONDecodeError)
    async def invalid_json(request, error):
        return JSONResponse(
            {"detail": "Invalid request body. Refresh and try again."}, 400
        )

    async def payload(request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a request object.")
        return body

    def identity(v):
        return hmac.new(
            settings.session_key.encode(), v.encode(), hashlib.sha256
        ).hexdigest()

    def session(request):
        try:
            v = json.loads(
                cipher.decrypt(
                    request.cookies.get("studio_session", "").encode(), ttl=30 * 86400
                )
            )
            uuid.UUID(v["uid"])
            if not isinstance(v["csrf"], str):
                raise ValueError()
            return v
        except (InvalidToken, KeyError, ValueError, TypeError):
            raise HTTPException(
                401, "Refresh the page to start a secure session."
            ) from None

    def cookie(response, value):
        response.set_cookie(
            "studio_session",
            cipher.encrypt(json.dumps(value).encode()).decode(),
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=30 * 86400,
            path="/",
        )

    def csrf(request, v):
        if (
            request.headers.get("origin")
            and request.headers["origin"] != str(request.base_url).rstrip("/")
        ) or not hmac.compare_digest(
            request.headers.get("x-csrf-token", ""), v["csrf"]
        ):
            raise HTTPException(403, "Refresh the page and try again.")

    async def call(operation, args):
        try:
            return await store(operation, args)
        except BookError as e:
            raise HTTPException(409, str(e)) from None

    def address(request):
        # Modal's proxy supplies the last hop. IP hashes are rate-limit keys,
        # not identity verification; global caps remain necessary.
        value = request.headers.get("x-forwarded-for", "").split(",")[-1].strip() or (
            request.client.host if request.client else "unknown"
        )
        return identity("rate:" + value)

    async def throttle(request, bucket):
        try:
            await store(
                "admit_request", {"bucket": bucket, "identity": address(request)}
            )
        except BookError as e:
            raise HTTPException(429, str(e), headers={"Retry-After": "60"}) from None

    def require_live():
        if not settings.billing_ready:
            raise HTTPException(
                402,
                "Paid conversion opens after the seller account and processing packs are activated.",
            )

    def idvalue(value):
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Invalid request identifier.") from None

    async def owned(request, jid):
        job = await call(
            "book_get", {"owner": session(request)["uid"], "job_id": idvalue(jid)}
        )
        if not job:
            raise HTTPException(404, "Conversion not found or expired.")
        return job

    def public_job(j):
        return {
            k: j[k]
            for k in (
                "id",
                "filename",
                "status",
                "total",
                "selected",
                "completed_pages",
                "parts",
                "expires",
                "charged",
                "revision",
                "trial",
                "mode",
                "cancelled",
            )
        }

    async def dispatch(job):
        if job.pop("_new", False):
            try:
                await dispatcher(job["id"])
            except Exception:
                return {
                    "id": job["id"],
                    "submission_uncertain": True,
                    "detail": "Submission is uncertain. Check this job before retrying; credits remain reserved until reconciliation.",
                }
        return {"id": job["id"], "submission_uncertain": False}

    @app.middleware("http")
    async def headers(request, next):
        try:
            r = await next(request)
        except Exception:
            try:
                await store("event", {"kind": "server_error"})
            except Exception:
                print("document_studio error_metric_write_failed")
            raise
        path = request.url.path
        kind = None
        if request.method == "GET" and (path == "/" or path.startswith("/guides/")):
            kind = "landing"
        elif request.method == "POST":
            kind = {
                "/api/documents": "inspection",
                "/api/jobs": "confirmation",
                "/api/checkout": "checkout",
                "/api/access": "purchase_restore",
            }.get(path)
            if path.endswith("/retry"):
                kind = "retry"
        elif request.method == "GET" and path.endswith("/download"):
            kind = "export"
        if r.status_code >= 500:
            kind = "server_error"
        elif kind and r.status_code >= 400:
            kind = (
                "payment_error"
                if settings.billing_ready
                and path in ("/api/checkout", "/api/access", "/api/payment-event")
                else "request_error"
            )
        if kind:
            try:
                await store(
                    "event",
                    {
                        "kind": kind,
                        "source": request.query_params.get("utm_source", "direct")
                        if path == "/"
                        else "direct",
                    },
                )
            except Exception:
                print("document_studio metric_write_failed")
        r.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://m-amir-gomaa.github.io; base-uri 'none'; form-action 'self'; object-src 'none'",
            }
        )
        return r

    @app.get("/")
    async def root():
        return FileResponse(ROOT / "web/templates/index.html", media_type="text/html")

    @app.get("/guides/pdf-to-markdown")
    async def guide():
        return FileResponse(
            ROOT / "web/templates/pdf-to-markdown.html", media_type="text/html"
        )

    @app.get("/robots.txt")
    async def robots():
        return Response(
            "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /health\nSitemap: https://m-amir-gomaa--mineru-pdf-extractor-web-app.modal.run/sitemap.xml\n",
            media_type="text/plain",
        )

    @app.get("/sitemap.xml")
    async def sitemap():
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://m-amir-gomaa--mineru-pdf-extractor-web-app.modal.run/</loc></url><url><loc>https://m-amir-gomaa--mineru-pdf-extractor-web-app.modal.run/guides/pdf-to-markdown</loc></url></urlset>',
            media_type="application/xml",
        )

    @app.get("/health")
    async def health():
        return {
            "service": "document-studio",
            "status": "ok",
            "billing_enabled": settings.billing_ready,
            "commercial_model": "prepaid_conversion",
        }

    @app.get("/api/session")
    async def get_session(request: Request):
        await throttle(request, "session")
        try:
            v = session(request)
        except HTTPException:
            v = {"uid": str(uuid.uuid4()), "csrf": secrets.token_urlsafe(32)}
        account = await call("account", {"owner": v["uid"]})
        r = JSONResponse(
            {
                "csrf": v["csrf"],
                "account": account,
                "billing_enabled": settings.billing_ready,
                "max_bytes": settings.paid_bytes,
                "max_pages": settings.paid_pages,
                "retention_hours": settings.retention_seconds // 3600,
                "languages": LANGUAGES,
                "base_units": settings.base_units,
                "page_units": settings.page_units,
                "text_base_units": settings.text_base_units,
                "text_page_units": settings.text_page_units,
            }
        )
        cookie(r, v)
        return r

    @app.post("/api/recovery")
    async def recovery(request: Request):
        v = session(request)
        csrf(request, v)
        key = secrets.token_urlsafe(40)
        await call(
            "set_recovery", {"owner": v["uid"], "key": identity("recover:" + key)}
        )
        return {
            "recovery_key": key,
            "notice": "Keep this private. Generating another key invalidates the previous key.",
        }

    @app.post("/api/restore")
    async def restore(request: Request):
        v = session(request)
        csrf(request, v)
        body = await payload(request)
        key = body.get("recovery_key", "")
        if not isinstance(key, str) or not 30 <= len(key) <= 100:
            raise HTTPException(400, "Enter a recovery key.")
        owner = await call("recover", {"key": identity("recover:" + key)})
        if not owner:
            raise HTTPException(401, "Recovery key not found.")
        v = {"uid": owner, "csrf": secrets.token_urlsafe(32)}
        r = JSONResponse({"restored": True})
        cookie(r, v)
        return r

    @app.post("/api/logout")
    async def logout(request: Request):
        v = session(request)
        csrf(request, v)
        r = JSONResponse({"signed_out": True})
        r.delete_cookie("studio_session", path="/")
        return r

    @app.post("/api/checkout")
    async def checkout(request: Request):
        v = session(request)
        csrf(request, v)
        body = await payload(request)
        require_live()
        await throttle(request, "payment")
        try:
            url = await provider.checkout(v["uid"], body.get("pack"))
        except BillingError as e:
            raise HTTPException(402, str(e)) from None
        return {"url": url}

    @app.post("/api/access")
    async def access(request: Request):
        v = session(request)
        csrf(request, v)
        body = await payload(request)
        require_live()
        await throttle(request, "payment")
        try:
            purchase = await provider.key(body.get("license_key"))
        except BillingError as e:
            raise HTTPException(402, str(e)) from None
        owner = await call(
            "credit",
            {
                "owner": v["uid"],
                "order_id": purchase["order_id"],
                "units": purchase["units"],
                "revoked": purchase["revoked"],
                "verified": True,
            },
        )
        v = {"uid": owner, "csrf": secrets.token_urlsafe(32)}
        r = JSONResponse({"restored": True})
        cookie(r, v)
        return r

    @app.post("/api/payment-event")
    async def payment_event(request: Request):
        require_live()
        body = await request.body()
        if len(body) > 65536:
            raise HTTPException(413, "Event too large.")
        try:
            event = provider.webhook(body, request.headers.get("x-signature", ""))
            if event:
                purchase = await provider.order(event["order_id"])
                if event["owner"]:
                    owner = idvalue(event["owner"])
                    await call(
                        "credit",
                        {
                            "owner": owner,
                            "order_id": purchase["order_id"],
                            "units": purchase["units"],
                            "revoked": purchase["revoked"],
                            "verified": True,
                        },
                    )
                elif purchase["revoked"]:
                    await call(
                        "reverse",
                        {
                            "order_id": purchase["order_id"],
                            "units": purchase["revoked"],
                        },
                    )
        except BillingError as e:
            raise HTTPException(400, str(e)) from None
        return {"received": True}

    @app.post("/api/documents")
    async def upload(
        request: Request,
        file: UploadFile = File(...),
        request_id: str = Form(...),
        pages: str = Form("all"),
        mode: str = Form("auto"),
        language: str = Form("latin"),
    ):
        v = session(request)
        csrf(request, v)
        await throttle(request, "inspect")
        rid = idvalue(request_id)
        if mode not in ("auto", "ocr", "text") or language not in LANGUAGES:
            raise HTTPException(
                400, "Choose a supported extraction mode and OCR language."
            )
        raw = bytearray()
        try:
            while part := await file.read(1048576):
                raw.extend(part)
                if len(raw) > settings.paid_bytes:
                    raise HTTPException(413, "PDF exceeds the 64 MiB upload limit.")
        finally:
            await file.close()
        data = bytes(raw)
        try:
            info = await asyncio.to_thread(
                bounded_inspect, data, settings.paid_bytes, settings.paid_pages
            )
            selected = parse_pages(pages, info["total_pages"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        filename = "".join(
            c
            for c in (file.filename or "document.pdf")
            .replace("\\", "/")
            .rsplit("/", 1)[-1][:150]
            if ord(c) >= 32
        )
        doc = await call(
            "prepare",
            {
                "owner": v["uid"],
                "request_id": rid,
                "digest": info["sha256"],
                "filename": filename,
                "total": info["total_pages"],
                "pages": selected,
                "mode": mode,
                "lang": language,
                "retention": settings.retention_seconds,
            },
        )
        await storage("put_source", {"document": doc["id"], "data": data})
        return {
            "id": doc["id"],
            "filename": filename,
            "inspection": info,
            "selected": selected,
            "quote": quote(selected, mode, settings),
            "trial_pages": selected[: settings.trial_pages],
        }

    @app.get("/api/documents")
    async def retained_documents(request: Request):
        rows = await call("documents_list", {"owner": session(request)["uid"]})
        return [
            {
                "id": r["id"],
                "filename": r["filename"],
                "inspection": {"total_pages": r["total"]},
                "selected": json.loads(r["pages"]),
                "mode": r["mode"],
                "lang": r["lang"],
                "quote": quote(json.loads(r["pages"]), r["mode"], settings),
            }
            for r in rows
        ]

    @app.delete("/api/documents/{did}")
    async def discard(did: str, request: Request):
        v = session(request)
        csrf(request, v)
        await call("discard_document", {"owner": v["uid"], "document": idvalue(did)})
        await storage("delete_source", {"document": idvalue(did)})
        return {"deleted": True}

    @app.post("/api/jobs")
    async def confirm(request: Request):
        v = session(request)
        csrf(request, v)
        body = await payload(request)
        did = idvalue(body.get("document"))
        rid = idvalue(body.get("request_id"))
        trial = body.get("trial") is True
        doc = await call("document_get", {"owner": v["uid"], "document": did})
        if not doc:
            raise HTTPException(404, "Upload not found or expired.")
        cost = quote(json.loads(doc["pages"]), doc["mode"], settings)
        if not trial and body.get("expected_units") != cost["units"]:
            raise HTTPException(
                409, "Quote changed. Upload again to review the current price."
            )
        address = request.headers.get("x-forwarded-for", "").split(",")[-1].strip() or (
            request.client.host if request.client else "unknown"
        )
        job = await call(
            "confirm",
            {
                "owner": v["uid"],
                "document": did,
                "request_id": rid,
                "trial": trial,
                "ip": identity("ip:" + address),
                "settings": settings,
            },
        )
        return await dispatch(job)

    @app.get("/api/jobs")
    async def jobs(request: Request):
        await call("stale", {})
        rows = await call("book_list", {"owner": session(request)["uid"]})
        return [public_job(j) for j in rows]

    @app.get("/api/jobs/{jid}")
    async def status(jid: str, request: Request):
        await call("stale", {})
        return public_job(await owned(request, jid))

    @app.post("/api/jobs/{jid}/cancel")
    async def cancel(jid: str, request: Request):
        v = session(request)
        csrf(request, v)
        await call("cancel", {"owner": v["uid"], "job_id": idvalue(jid)})
        return {
            "cancelled": True,
            "notice": "Queued work is released. A section already running may finish and be charged.",
        }

    @app.post("/api/jobs/{jid}/retry-quote")
    async def retry_quote(jid: str, request: Request):
        v = session(request)
        csrf(request, v)
        job = await owned(request, jid)
        body = await payload(request)
        try:
            selected = (
                parse_pages(body["pages"], job["total"])
                if body.get("pages")
                else sorted(
                    set(n for p in job["parts"] for n in p["pages"])
                    - set(job["completed_pages"])
                )
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if not selected or not set(selected) <= set(job["selected"]):
            raise HTTPException(400, "Choose pages from this conversion.")
        mode = body.get("mode") or job["mode"]
        if mode not in ("auto", "ocr", "text"):
            raise HTTPException(400, "Unsupported reprocessing mode.")
        cost = quote(
            selected,
            mode,
            settings,
            repair=bool(set(selected) & set(job["completed_pages"])),
        )
        if not set(selected) & set(job["completed_pages"]) and job["charged"]:
            cost["units"] -= cost["base_units"]
            cost["base_units"] = 0
        return {"pages": selected, "quote": cost}

    @app.post("/api/jobs/{jid}/retry")
    async def retry(jid: str, request: Request):
        v = session(request)
        csrf(request, v)
        body = await payload(request)
        job = await owned(request, jid)
        chosen = body.get("pages", [])
        if not isinstance(chosen, list) or any(type(n) is not int for n in chosen):
            raise HTTPException(400, "Invalid retry pages.")
        existing = await call(
            "retry_existing",
            {
                "owner": v["uid"],
                "job_id": job["id"],
                "request_id": idvalue(body.get("request_id")),
            },
        )
        if existing:
            if (
                body.get("expected_units") != existing["quoted"]
                or (chosen and sorted(set(chosen)) != existing["pages"])
                or (body.get("mode") and body["mode"] != existing["mode"])
            ):
                raise HTTPException(
                    409, "This retry request belongs to different options."
                )
            return await dispatch(existing)
        effective = chosen or sorted(
            set(n for p in job["parts"] for n in p["pages"])
            - set(job["completed_pages"])
        )
        mode = body.get("mode") or job["mode"]
        if mode not in ("auto", "ocr", "text"):
            raise HTTPException(400, "Unsupported reprocessing mode.")
        cost = quote(
            effective,
            mode,
            settings,
            repair=bool(set(effective) & set(job["completed_pages"])),
        )
        if not set(effective) & set(job["completed_pages"]) and job["charged"]:
            cost["units"] -= cost["base_units"]
        if body.get("expected_units") != cost["units"]:
            raise HTTPException(409, "Review the current retry price first.")
        result = await call(
            "retry",
            {
                "owner": v["uid"],
                "job_id": job["id"],
                "request_id": idvalue(body.get("request_id")),
                "pages": chosen,
                "settings": settings,
                "mode": mode,
            },
        )
        return await dispatch(result)

    async def content(request, jid, page=None):
        job = await owned(request, jid)
        try:
            blobs = await storage(
                "parts",
                {
                    "job": job["id"],
                    "numbers": [
                        p["number"]
                        for p in job["parts"]
                        if p["status"] == "done"
                        and (page is None or page in p["pages"])
                    ],
                },
            )
        except ValueError:
            raise HTTPException(
                413,
                "This result is too large for a combined export. Select fewer source pages.",
            ) from None
        except OSError:
            raise HTTPException(
                503,
                "Result files are temporarily unavailable. Try again or contact support.",
            ) from None
        edits = await call("edits", {"owner": job["owner"], "job_id": job["id"]})
        return job, blobs, edits

    @app.get("/api/jobs/{jid}/pages")
    async def review(jid: str, request: Request, page: int | None = None):
        job, blobs, edits = await content(request, jid, page)
        pages, _, _ = page_view(job, blobs, edits)
        return {
            "pages": [p for p in pages if page is None or p["number"] == page],
            "notice": "Review flags do not certify accurate or complete extraction.",
        }

    @app.post("/api/jobs/{jid}/pages/{page}")
    async def edit(jid: str, page: int, request: Request):
        v = session(request)
        csrf(request, v)
        body = await payload(request)
        if type(body.get("version")) is not int:
            raise HTTPException(400, "Reload this page before saving.")
        version = await call(
            "edit",
            {
                "owner": v["uid"],
                "job_id": idvalue(jid),
                "page": page,
                "text": body.get("text"),
                "version": body["version"],
            },
        )
        return {"version": version}

    @app.get("/api/jobs/{jid}/pages/{page}/history")
    async def history(jid: str, page: int, request: Request):
        return await call(
            "history",
            {"owner": session(request)["uid"], "job_id": idvalue(jid), "page": page},
        )

    @app.get("/api/jobs/{jid}/source")
    async def source(jid: str, request: Request):
        job = await owned(request, jid)
        data = await storage("source", {"document": job["document"]})
        return Response(
            data,
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="document.pdf"'},
        )

    @app.get("/api/jobs/{jid}/download")
    async def download(
        jid: str, request: Request, layout: str = "single", include_pdf: bool = True
    ):
        job, blobs, edits = await content(request, jid)
        if not blobs:
            raise HTTPException(409, "No completed pages are available yet.")
        data = await storage("source", {"document": job["document"]})
        try:
            archive = await asyncio.to_thread(
                build_archive, job, data, blobs, edits, layout, include_pdf
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return Response(
            archive,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="document-studio-{job["id"]}.zip"'
            },
        )

    @app.delete("/api/jobs/{jid}")
    async def delete(jid: str, request: Request):
        v = session(request)
        csrf(request, v)
        deleted = await call("delete", {"owner": v["uid"], "job_id": idvalue(jid)})
        await storage("delete_job", {"job": idvalue(jid)})
        if deleted["source_unused"]:
            await storage("delete_source", {"document": deleted["document"]})
        return {
            "deleted": True,
            "notice": "Job output and edits deleted. Shared source files are removed when no retained conversion needs them or at expiry.",
        }

    return app
