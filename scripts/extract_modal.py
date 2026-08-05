import modal
import os
import zipfile
from io import BytesIO

app = modal.App("mineru-pdf-extractor")

# The cloud container image: Debian + GPU libs + MinerU
mineru_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "libgl1", "libglib2.0-0", "libsm6", "libxext6", "libxrender-dev")
    .pip_install("mineru[pipeline]", "accelerate", "transformers", "torch", "torchvision", "six")
)


@app.function(image=mineru_image, gpu="A10G", timeout=3600)
def extract_pdf(pdf_bytes: bytes, base_name: str) -> bytes:
    """
    Runs MinerU on the provided PDF bytes inside the Modal GPU container.
    Returns a zip of the output folder (markdown + images).
    """
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
        ("sources/comparative_linguistics/frequency_dictionary_german.pdf",     "frequency_dictionary_german.md"),
        ("sources/comparative_linguistics/rosenberg_german_speak_write.pdf",    "rosenberg_german_speak_write.md"),
        ("sources/comparative_linguistics/bodmer_loom_of_language.pdf",         "bodmer_loom_of_language.md"),
        ("sources/grammar/fagan_german_linguistic_intro.pdf",                   "fagan_german_linguistic_intro.md"),
        ("sources/grammar/beck_gergel_english_german_syntax.pdf",               "beck_gergel_english_german_syntax.md"),
        ("sources/grammar/hammers_german_grammar.pdf",                          "hammers_german_grammar.md"),
        ("sources/grammar/fox_structure_of_german.pdf",                         "fox_structure_of_german.md"),
        ("sources/grammar/hammers_german_workbook.pdf",                         "hammers_german_workbook.md"),
        ("sources/grammar/rankin_handbuch_grammatik.pdf",                       "rankin_handbuch_grammatik.md"),
        ("sources/reading/duden_band_2.pdf",                                    "duden_band_2.md"),
        ("sources/reading/lonely_planet_phrasebook.pdf",                        "lonely_planet_phrasebook.md"),
        ("sources/methodology/Fluent Forever _ How to Learn Any Language Fast and Never Forget It.pdf", "fluent_forever_book.md"),
        ("sources/ipa/modern_german_pronunciation.pdf",                         "modern_german_pronunciation.md"),
    ]

    base_dir = os.path.dirname(os.path.abspath(__file__))

    print("=== DeutschKern Modal Extraction ===")
    for rel_pdf_path, target_md in PDFS:
        pdf_path   = os.path.join(base_dir, rel_pdf_path)
        target_dir = os.path.dirname(pdf_path)
        # MinerU outputs into an 'auto/' subdirectory
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
        print(f"[UPLOAD] {base_name} ...")

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
