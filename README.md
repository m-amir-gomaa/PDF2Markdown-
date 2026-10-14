# Document Studio

Hosted PDF-to-Markdown conversion using MinerU on Amir's Modal account. Users receive portable files for their own reading, editing and LLM workflows. The product does not generate study materials or teach the document's content.

[Public limited trial](https://m-amir-gomaa--mineru-pdf-extractor-web-app.modal.run) · [Conversion specification](docs/CONVERSION-PRODUCT.md) · [Pricing research](docs/PRICING-AND-DISCOVERY.md)

## Current release

- One-page PDF trial, up to 1 MiB, with persistent per-session/IP and global quotas.
- Cloud MinerU 2.7.6 conversion to Markdown/images.
- Download ZIP includes the original PDF, available block/page metadata, PDF-page navigation and a report of detectable replacement glyphs and broken image references.
- Session-owned job status/downloads and duplicate-submission suppression.
- Results expire after 48 hours; hourly cleanup deletes expired archives.
- Planned $1/month access plus separately paid processing. **Payments and processing credits are not live.** The old included-compute subscription verifier is disabled draft code, not the selected billing design.

A real public synthetic PDF upload/conversion/download passed. The trial is not a verified full-book service. Source-page metadata and absence of replacement glyphs do not certify extraction accuracy. Specialized symbols, formulas, tables, reading order and missing content need comparison with the source.

## Architecture

```text
Browser: encrypted session + CSRF, upload/status/download
   |
   v
Modal CPU FastAPI web service
   | reserve job before dispatch     | owned archive download
   v                                v
Single-writer ledger function       Private result volume
   | SQLite on ledger volume          ^
   | transactional quotas             | ZIP
   v                                  |
Modal A10G worker -> bounded MinerU CLI -> source-aware export
   |
   +-> persistent model cache

Hourly cleanup -> expired jobs + archives
```

The browser accepts no Modal tokens. The worker is invoked privately through Modal RPC; the former unauthenticated `/upload` route is gone. One serialized ledger container owns all SQLite writes. This is a bounded small-service architecture, not demonstrated high-volume scalability. Polling replaces the previous SSE/log stream.

## Runtime and deployment

Python 3.13 and Modal SDK 1.5.3 are used locally; the GPU image uses Python 3.12. CPU/GPU direct package versions are defined in modal_fn.py. Transitive package/model revisions are not all pinned, so a fresh future image is not guaranteed byte-identical. No local GPU or model download is needed.

```sh
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Use existing owner Modal authentication or the official Modal login. Store `DOCUMENT_STUDIO_SESSION_KEY` as a valid Fernet key in the named Modal secret `document-studio-session`; never log or commit it. Reuse an existing key to preserve sessions. No payment secret is required for the bounded trial.

```sh
.venv/bin/python -m modal deploy app.py
.venv/bin/python -m modal app logs mineru-pdf-extractor --tail 100
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
```

Deployment replaces the existing app of the same name. Private owner extraction remains available as `mineru-pdf-extractor.extract_pdf` for authenticated preparation scripts; its larger bounds do not describe public trial capacity. Historical scripts/test_e2e.py targets the retired SSE/BYO interface; use tests/ for the current implementation.

## Evidence and limits

22 automated tests cover real SQLite concurrency/persistence, sessions/CSRF/ownership, PDF bounds, archive traversal, idempotency, simulated MinerU output and mocked subscription-provider validation. Provider mocks do not establish payment delivery or an activated seller account.

On 14 October 2026, a real original one-page PDF converted on Modal; the public upload/status/download path produced a 2,295-byte ZIP, a repeated request returned the same job, and another session was denied access. System Chromium browser checks passed at widths 320, 390, 768 and 1440 with its sandbox enabled: no horizontal overflow or page errors, oversize/invalid-PDF error handling, retry control and keyboard disclosure. Phone/desktop screenshots were inspected. Private verification artifacts are ignored. This is one synthetic document, not a comparative academic-document benchmark or Safari/physical-device certification.

Trial quotas protect owner spending but can reject legitimate users sharing an IP. Results are private through session ownership, not a claim of zero provider retention or encryption at rest. Hard worker termination or indeterminate dispatch currently needs owner review before retrying; no automatic billed retry occurs. Hourly cleanup has a small scheduled usage cost. Storage and cache also count as usage; hosting is not guaranteed literally free when idle.

Paid release requires a transactional processing-credit ledger, verified payment events/refunds, explicit tariffs/limits and failure-cost rules, usable account recovery, and a real approved seller/product. Subscription entitlement alone must not authorize unpaid GPU processing. Full-book/range processing, selective retries and repair tools are specification candidates in docs/CONVERSION-PRODUCT.md, not shipped features.

## Source and dependencies

[Upstream MinerU 2.7.6](https://github.com/opendatalab/MinerU/tree/mineru-2.7.6-released) is used unmodified through a subprocess. Its release license is AGPL-3.0; see [dependency notes](docs/THIRD-PARTY.md). Do not apply current upstream master licensing retroactively. New source books, extracted collections, authentication and local environments are ignored and are not added by this change. Historical tracked sources already exist in this repository; ignore rules do not remove them or rewrite history.
