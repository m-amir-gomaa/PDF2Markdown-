"""Owner-authenticated verification through deployed functions, never a second writer.
Explicit internal credit fixture; this does not test a customer payment.
"""

import argparse, json, uuid, time
from pathlib import Path
import modal
from web.config import Settings
from web.documents import inspect_pdf, parse_pages, build_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--mode", choices=["auto", "ocr", "text"], default="text")
    parser.add_argument("--pages", default="all")
    args = parser.parse_args()
    store = modal.Function.from_name("mineru-pdf-extractor", "book_store")
    run = modal.Function.from_name("mineru-pdf-extractor", "run_book")
    volume = modal.Volume.from_name("document-studio-results-v1")
    data = Path(args.pdf).read_bytes()
    s = Settings("unused-probe-key")
    info = inspect_pdf(data, s.paid_bytes, s.paid_pages)
    chosen = parse_pages(args.pages, info["total_pages"])
    owner = str(uuid.uuid4())
    store.remote(
        "credit",
        {"owner": owner, "order_id": "internal-verification:" + owner, "units": 100000},
    )
    doc = store.remote(
        "prepare",
        {
            "owner": owner,
            "request_id": str(uuid.uuid4()),
            "digest": info["sha256"],
            "filename": "original-verification.pdf",
            "total": info["total_pages"],
            "pages": chosen,
            "mode": args.mode,
            "lang": "latin",
            "retention": s.retention_seconds,
        },
    )
    with volume.batch_upload(force=True) as upload:
        upload.put_file(args.pdf, "/sources/" + doc["id"] + ".pdf")
    job = store.remote(
        "confirm",
        {
            "owner": owner,
            "document": doc["id"],
            "request_id": str(uuid.uuid4()),
            "trial": False,
            "ip": "internal-verification",
            "settings": s,
        },
    )
    run.remote(job["id"])
    job = store.remote("book_get", {"owner": owner, "job_id": job["id"]})
    if job["status"] != "done":
        raise RuntimeError("Cloud verification failed: " + job["status"])
    blobs = {
        p["number"]: b"".join(
            volume.read_file("books/" + job["id"] + "/" + str(p["number"]) + ".zip")
        )
        for p in job["parts"]
        if p["status"] == "done"
    }
    archive = build_archive(job, data, blobs, [])
    out = Path("artifacts")
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    (out / ("cloud-book-" + args.mode + "-" + stamp + ".zip")).write_bytes(archive)
    (out / ("cloud-book-" + args.mode + "-" + stamp + ".json")).write_text(
        json.dumps(
            {
                "id": job["id"],
                "mode": args.mode,
                "selected": chosen,
                "completed": job["completed_pages"],
                "charged_units": job["charged"],
                "archive_bytes": len(archive),
                "credit": "internal verification fixture, not payment",
            },
            indent=2,
        )
    )
    print(
        "Deployed book passed:", args.mode, len(chosen), "pages", len(archive), "bytes"
    )


if __name__ == "__main__":
    main()
