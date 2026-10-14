"""Build an original, permission-cleared example with the actual CPU exporter."""

from io import BytesIO
import json
from pathlib import Path
import zipfile
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from web.documents import build_archive, extract_section, inspect_pdf


def main():
    sections = [
        (
            "Survey plan",
            [
                "Field notes: vegetation survey",
                "This is an original demonstration document.",
                "Goal: compare plant counts at two sites.",
                "Use the same observation area at each site.",
            ],
        ),
        (
            "Observations",
            [
                "Observations",
                "Site A: 12 plants in the observation area.",
                "Site B: 18 plants in the observation area.",
                "These invented values are examples, not research results.",
            ],
        ),
        (
            "Interpretation",
            [
                "Interpretation and next steps",
                "The observed difference is six plants.",
                "Repeat observations before drawing a conclusion.",
                "Keep the original source beside your notes.",
            ],
        ),
    ]
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for index, (title, lines) in enumerate(sections):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        text = (
            "BT /F1 14 Tf 50 730 Td 24 TL "
            + " T* ".join(
                "("
                + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                + ") Tj"
                for line in lines
            )
            + " ET"
        )
        stream.set_data(text.encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_outline_item(title, index)
    buffer = BytesIO()
    writer.write(buffer)
    source = buffer.getvalue()
    inspection = inspect_pdf(source, 1024 * 1024, 1000)
    part = extract_section(source, [1, 2, 3], "text")
    job = {
        "id": "public-original-example",
        "filename": "field-notes.pdf",
        "digest": inspection["sha256"],
        "selected": [1, 2, 3],
        "total": 3,
        "mode": "text",
        "lang": "latin",
        "revision": 1,
        "charged": 0,
        "parts": [
            {
                "number": 0,
                "revision": 1,
                "status": "done",
                "pages": [1, 2, 3],
                "mode": "text",
            }
        ],
    }
    archive = build_archive(
        job, source, {0: part}, [], layout="single", include_pdf=True
    )
    root = Path("static/examples")
    root.mkdir(parents=True, exist_ok=True)
    (root / "document.pdf").write_bytes(source)
    (root / "field-notes.zip").write_bytes(archive)
    with zipfile.ZipFile(BytesIO(archive)) as z:
        (root / "field-notes.md").write_bytes(z.read("document.md"))
        review = json.loads(z.read("REVIEW.json"))
        if review["unavailable_pages"]:
            raise RuntimeError("Example pages missing")
    print(
        "Original PDF, Markdown and portable archive built with actual text extraction."
    )


if __name__ == "__main__":
    main()
