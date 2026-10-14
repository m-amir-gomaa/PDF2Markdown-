"""Local development fixture: seeded credits, real PDF text extraction, no payments/GPU.
Never imported by the deployed app. UI defaults to text-only when testing this fixture.
"""

import asyncio
from pathlib import Path
from cryptography.fernet import Fernet
from web.books import BookLedger
from web.config import Settings
from web.documents import extract_section
from web.studio import create_app

root = Path(".local/books-preview")
root.mkdir(parents=True, exist_ok=True)
key = root / "session-key"
if not key.exists():
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
settings = Settings(key.read_text())
db = BookLedger(root / "db.sqlite")


async def store(operation, args):
    if operation == "account":
        db.credit(args["owner"], "local-fixture:" + args["owner"], 100000)
    return getattr(db, operation)(**args)


async def storage(operation, args):
    if operation == "put_source":
        (root / (args["document"] + ".pdf")).write_bytes(args["data"])
    elif operation == "source":
        return (root / (args["document"] + ".pdf")).read_bytes()
    elif operation == "parts":
        return {
            n: (root / (args["job"] + f"-{n}.zip")).read_bytes()
            for n in args["numbers"]
        }
    elif operation == "delete_source":
        (root / (args["document"] + ".pdf")).unlink(missing_ok=True)
    elif operation == "delete_job":
        for p in root.glob(args["job"] + "-*.zip"):
            p.unlink()


async def dispatch(jid):
    async def run():
        job = db.book_internal(jid)
        for part in job["parts"]:
            if not db.part_start(jid, part["number"]):
                continue
            try:
                if job["mode"] != "text":
                    raise ValueError(
                        "Local fixture only runs real text-layer extraction. Cloud MinerU is verified separately."
                    )
                data = (root / (job["document"] + ".pdf")).read_bytes()
                blob = await asyncio.to_thread(
                    extract_section, data, part["pages"], "text"
                )
                (root / (jid + f"-{part['number']}.zip")).write_bytes(blob)
                db.part_finish(jid, part["number"], True)
            except Exception:
                db.part_finish(
                    jid,
                    part["number"],
                    False,
                    "Local fixture only supports text-only mode; choose text extraction.",
                )

    asyncio.create_task(run())


app = create_app(settings, store, storage, dispatch)
