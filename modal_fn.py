import modal
import os
import subprocess
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

# The cloud container image: Debian + GPU libs + MinerU
mineru_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "libgl1", "libglib2.0-0", "libsm6", "libxext6", "libxrender-dev")
    .pip_install("mineru[pipeline]", "accelerate", "transformers", "torch", "torchvision", "six")
)

KEEP_EXTENSIONS = {".md", ".png", ".jpg", ".jpeg", ".webp", ".svg"}
KEEP_JSON_FILES = {"content_list.json", "content_list_v2.json"}
DISCARD_NAMES = {"layout.pdf", "span.pdf", "model.json", "layout.json"}

def should_keep(rel_path: str) -> bool:
    p = Path(rel_path)
    if p.name in DISCARD_NAMES:
        return False
    if p.suffix == ".json" and p.name not in KEEP_JSON_FILES:
        return False
    if p.suffix == ".pdf":
        return False
    return p.suffix in KEEP_EXTENSIONS or p.suffix == ".json"

# We don't define the app here. We expect it to be imported and used by app.py
# But we need a stub app so we can define the function here, OR we can define
# the function on a stub app and then include it in the main app.
# The recommended Modal pattern is to define the app in app.py and import it,
# OR define the App object here and import it in app.py.
# Let's define the App object in a central place? 
# No, Modal handles this nicely. We can just define the Image and the function
# here by accepting an app object, or creating a stub. Let's just create an app object here
# and import it in app.py.

app = modal.App("mineru-pdf-extractor")

@app.function(image=mineru_image, gpu="A10G", timeout=3600)
def extract_pdf(pdf_bytes: bytes, base_name: str) -> bytes:
    """
    Runs MinerU on the provided PDF bytes inside the Modal GPU container.
    Filters the output to remove debug artifacts, and returns a zip of the
    clean output folder (markdown + images + content_list.json).
    """
    pdf_path = f"/tmp/{base_name}.pdf"
    out_dir = f"/tmp/mineru_out_{base_name}"

    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    os.makedirs(out_dir, exist_ok=True)

    print(f"[modal] Running MinerU on {base_name}.pdf ...")
    subprocess.run(["mineru", "-p", pdf_path, "-o", out_dir, "-b", "pipeline"], check=True)

    # MinerU writes output into a subdirectory named after the file
    result_folder = os.path.join(out_dir, base_name)
    if not os.path.isdir(result_folder):
        # Fallback: search for any directory inside out_dir
        subdirs = [d for d in os.listdir(out_dir) if os.path.isdir(os.path.join(out_dir, d))]
        if subdirs:
            result_folder = os.path.join(out_dir, subdirs[0])
        else:
            raise RuntimeError(f"MinerU produced no output directory in {out_dir}")

    zip_path = f"/tmp/{base_name}_result.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(result_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, result_folder)
                
                # Filter out garbage
                if should_keep(arcname):
                    zipf.write(file_path, arcname)

    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    os.remove(pdf_path)
    os.remove(zip_path)
    shutil.rmtree(out_dir)

    return zip_bytes
