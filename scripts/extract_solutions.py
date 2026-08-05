import modal
import os
import zipfile
from io import BytesIO

app = modal.App("mineru-pdf-extractor")

mineru_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "libgl1", "libglib2.0-0", "libsm6", "libxext6", "libxrender-dev")
    .pip_install("mineru[pipeline]", "accelerate", "transformers", "torch", "torchvision", "six")
)

@app.function(image=mineru_image, gpu="A10G", timeout=3600)
def extract_pdf(pdf_bytes: bytes, base_name: str) -> bytes:
    import os
    import subprocess
    import shutil
    import zipfile

    pdf_path = f"/tmp/{base_name}.pdf"
    out_dir = f"/tmp/mineru_out_{base_name}"

    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    os.makedirs(out_dir, exist_ok=True)

    print(f"[modal] Running MinerU on {base_name}.pdf ...")
    subprocess.run(["mineru", "-p", pdf_path, "-o", out_dir, "-b", "pipeline"], check=True)

    result_folder = os.path.join(out_dir, base_name)
    if not os.path.isdir(result_folder):
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
                zipf.write(file_path, arcname)

    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    os.remove(pdf_path)
    os.remove(zip_path)
    shutil.rmtree(out_dir)

    return zip_bytes

@app.local_entrypoint()
def main():
    PDFS = [
        (
            "sources/math/strang_linear_algebra_solutions.pdf",
            "strang_linear_algebra_solutions.md",
        ),
    ]

    base_dir = os.path.dirname(os.path.abspath(__file__))

    print("=== Strang Linear Algebra Solutions Modal Extraction ===")
    for rel_pdf_path, target_md in PDFS:
        pdf_path   = os.path.join(base_dir, rel_pdf_path)
        target_dir = os.path.dirname(pdf_path)
        auto_dir   = os.path.join(target_dir, "auto")
        target_md_path_direct = os.path.join(target_dir, target_md)
        target_md_path_auto   = os.path.join(auto_dir,   target_md)

        if not os.path.exists(pdf_path):
            print(f"[SKIP] Missing PDF: {rel_pdf_path}")
            continue

        if os.path.exists(target_md_path_direct) or os.path.exists(target_md_path_auto):
            print(f"[SKIP] Already extracted: {target_md}")
            continue

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        print(f"[UPLOAD] {base_name} ({os.path.getsize(pdf_path) / 1024 / 1024:.1f} MB) ...")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        try:
            zip_bytes = extract_pdf.remote(pdf_bytes, base_name)

            os.makedirs(auto_dir, exist_ok=True)
            print(f"[UNPACK] Received results for {base_name}, unpacking into auto/ ...")
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zipf:
                zipf.extractall(target_dir)

            print(f"[DONE]  {target_md}")

        except Exception as e:
            print(f"[ERROR] {base_name}: {e}")

    print("=== Extraction complete ===")
