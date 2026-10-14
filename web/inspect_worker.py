"""CPU/time/address-space-bounded PDF inspection, outside the HTTP process."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def bounded_inspect(data, max_bytes, max_pages):
    if len(data) > max_bytes:
        raise ValueError("PDF exceeds the upload size limit.")
    with tempfile.TemporaryDirectory(prefix="studio-inspection-") as directory:
        source = Path(directory) / "input.pdf"
        source.write_bytes(data)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "web.inspect_worker",
                    str(source),
                    str(max_bytes),
                    str(max_pages),
                ],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
                env={
                    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                    "LANG": "C.UTF-8",
                },
            )
        except subprocess.TimeoutExpired:
            raise ValueError(
                "PDF inspection exceeded its safety time limit. Try a smaller PDF."
            ) from None
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise ValueError(
                "PDF could not be inspected within the safety limits. Check that it is valid and unencrypted, or try a smaller PDF."
            )
        return json.loads(result.stdout)


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    from web.documents import inspect_pdf

    result = inspect_pdf(
        Path(sys.argv[1]).read_bytes(), int(sys.argv[2]), int(sys.argv[3])
    )
    print(json.dumps(result))
