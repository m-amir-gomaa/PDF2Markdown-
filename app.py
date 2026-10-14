import asyncio
from pathlib import Path
import modal
from modal_fn import app, base, result_volume


@app.function(
    image=base,
    volumes={"/results": result_volume},
    secrets=[
        modal.Secret.from_name("document-studio-session"),
        modal.Secret.from_name("document-studio-payments"),
    ],
    max_containers=2,
    timeout=180,
    memory=2048,
)
@modal.concurrent(max_inputs=4)
@modal.asgi_app()
def web_app():
    from web.studio import create_app
    from web.config import Settings
    from modal_fn import book_store, run_book

    async def store(operation, arguments):
        return await book_store.remote.aio(operation, arguments)

    async def dispatch(job_id):
        call = await run_book.spawn.aio(job_id)
        return call.object_id

    async def storage(operation, arguments):
        def execute():
            import shutil

            result_volume.reload()
            root = Path("/results")
            if operation == "put_source":
                path = root / "sources" / (arguments["document"] + ".pdf")
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(arguments["data"])
                    result_volume.commit()
                return
            if operation == "delete_source":
                (root / "sources" / (arguments["document"] + ".pdf")).unlink(
                    missing_ok=True
                )
                result_volume.commit()
                return
            if operation == "source":
                return (
                    root / "sources" / (arguments["document"] + ".pdf")
                ).read_bytes()
            if operation == "parts":
                paths = {
                    n: root / "books" / arguments["job"] / f"{n}.zip"
                    for n in arguments["numbers"]
                }
                if sum(p.stat().st_size for p in paths.values()) > 200 * 1024 * 1024:
                    raise ValueError(
                        "Selected export is too large. Export fewer pages."
                    )
                return {n: p.read_bytes() for n, p in paths.items()}
            if operation == "delete_job":
                shutil.rmtree(root / "books" / arguments["job"], ignore_errors=True)
                result_volume.commit()
                return
            raise ValueError("Unknown storage operation")

        return await asyncio.to_thread(execute)

    return create_app(Settings.from_env(), store, storage, dispatch)


@app.local_entrypoint()
def probe(pdf: str):
    from modal_fn import extract_pdf

    result = extract_pdf.remote(Path(pdf).read_bytes(), "verification")
    target = Path("artifacts/verification-result.zip")
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(result)
    print("Private extraction probe wrote", target, len(result), "bytes")
