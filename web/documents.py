"""PDF selection, source-aware page rendering and portable book exports."""

import hashlib
import json
import re
from io import BytesIO
from pathlib import PurePosixPath
import zipfile
from pypdf import PdfReader, PdfWriter
from web.pipeline import convert_pdf, validate_pdf

LANGUAGES = {
    "latin": "Latin scripts",
    "en": "English",
    "ch": "Chinese",
    "chinese_cht": "Traditional Chinese",
    "japan": "Japanese",
    "korean": "Korean",
    "arabic": "Arabic",
    "east_slavic": "East Slavic",
    "cyrillic": "Cyrillic",
    "devanagari": "Devanagari",
    "el": "Greek",
    "ta": "Tamil",
    "te": "Telugu",
    "ka": "Georgian",
    "th": "Thai",
}


def parse_pages(value, total):
    if not value or value.strip().lower() == "all":
        return list(range(1, total + 1))
    if len(value) > 5000:
        raise ValueError("Page selection is too long.")
    pages = set()
    for item in value.split(","):
        item = item.strip()
        selection = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", item)
        if not selection:
            raise ValueError("Use PDF page numbers such as 1-10, 15, 20-25.")
        start = int(selection[1])
        end = int(selection[2] or start)
        if not 1 <= start <= end <= total:
            raise ValueError(f"Choose PDF pages between 1 and {total}.")
        pages.update(range(start, end + 1))
    return sorted(pages)


def inspect_pdf(data, max_bytes, max_pages):
    count = validate_pdf(data, max_bytes, max_pages)
    pdf = PdfReader(BytesIO(data), strict=True)
    outline = []

    def walk(items):
        for item in items:
            if isinstance(item, list):
                walk(item)
            else:
                try:
                    page = pdf.get_destination_page_number(item)
                    if page is not None and 0 <= page < count:
                        outline.append(
                            {"title": str(item.title)[:180], "pdf_page": page + 1}
                        )
                except Exception:
                    pass

    walk(pdf.outline)
    sample = []
    for idx in sorted({0, count // 2, count - 1}):
        try:
            text = pdf.pages[idx].extract_text() or ""
        except Exception:
            text = ""
        sample.append(
            {
                "pdf_page": idx + 1,
                "text_characters": len(text),
                "replacement_characters": text.count("\ufffd"),
            }
        )
    return {
        "total_pages": count,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "outline": outline[:500],
        "text_layer_sample": sample,
        "text_only_note": "Text-only extraction is cheaper but excludes image extraction, OCR and table/layout reconstruction.",
    }


def quote(pages, mode, settings, repair=False):
    base = (
        settings.repair_base_units
        if repair
        else settings.text_base_units
        if mode == "text"
        else settings.base_units
    )
    per = settings.text_page_units if mode == "text" else settings.page_units
    return {
        "units": base + len(pages) * per,
        "base_units": base,
        "page_units": per,
        "selected_pages": len(pages),
        "currency": "USD",
        "tariff": "2026-10-14-v1",
        "note": "A fixed service price, not a claim of exact Modal invoice pass-through. Failed sections release their allowance; the service fee applies once a section succeeds.",
    }


def _symptoms(text, source_text="", mapped=True):
    issues = []
    if "\ufffd" in text:
        issues.append(
            {
                "kind": "replacement_glyph",
                "count": text.count("\ufffd"),
                "detail": "One or more characters were not decoded. Check the source.",
            }
        )
    if not text.strip():
        issues.append(
            {
                "kind": "empty_output",
                "detail": "No Markdown text was extracted. This can be an image-only or blank page; inspect the source.",
            }
        )
    if (
        len(source_text.strip()) > 300
        and len(text.strip()) < len(source_text.strip()) * 0.3
    ):
        issues.append(
            {
                "kind": "short_output",
                "detail": "Output is much shorter than the source text layer. Check for missing content; the source text layer can also be unreliable.",
            }
        )
    if not mapped:
        issues.append(
            {
                "kind": "unmapped",
                "detail": "No usable block-to-page mapping. Raw section Markdown is preserved separately.",
            }
        )
    return issues


def extract_section(data, pages, mode="auto", lang="latin", runner=None):
    if lang not in LANGUAGES or mode not in ("auto", "ocr", "text"):
        raise ValueError("Choose a supported extraction mode/language.")
    pdf = PdfReader(BytesIO(data))
    records = []
    assets = {}
    raw = {}
    if mode == "text":
        for n in pages:
            text = pdf.pages[n - 1].extract_text() or ""
            records.append(
                {
                    "number": n,
                    "markdown": text,
                    "issues": _symptoms(text),
                    "mapped": True,
                }
            )
        raw["text-layer.txt"] = "\n\n".join(r["markdown"] for r in records).encode()
    else:
        writer = PdfWriter()
        for n in pages:
            writer.add_page(pdf.pages[n - 1])
        stream = BytesIO()
        writer.write(stream)
        kwargs = {"lang": lang, "method": mode}
        if runner is not None:
            kwargs["runner"] = runner
        result = convert_pdf(stream.getvalue(), 180, **kwargs)
        with zipfile.ZipFile(BytesIO(result)) as z:
            if sum(i.file_size for i in z.infolist()) > 250 * 1024 * 1024:
                raise ValueError("Section artifacts exceed limits.")
            raw = {
                name: z.read(name)
                for name in z.namelist()
                if name
                not in (
                    "document.pdf",
                    "SOURCE-NAVIGATION.md",
                    "SOURCE-MAP.json",
                    "REVIEW.json",
                    "DOCUMENT-STUDIO.txt",
                )
            }
            assets = {
                name: body
                for name, body in raw.items()
                if PurePosixPath(name).suffix.lower()
                in (".png", ".jpg", ".jpeg", ".webp")
            }
            candidates = [
                name
                for name in raw
                if name.endswith("_content_list.json") or name == "content_list.json"
            ]
            items = json.loads(raw[candidates[0]]) if candidates else []
            if not isinstance(items, list):
                items = []
            grouped = {i: [] for i in range(len(pages))}
            unknown = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                idx = item.get("page_idx")
                if type(idx) is int and idx in grouped:
                    grouped[idx].append(item)
                else:
                    unknown.append(item)
            for idx, n in enumerate(pages):
                blocks = []
                extra = []
                for item in grouped[idx]:
                    kind = item.get("type")
                    text = item.get("text", "")
                    if kind == "text":
                        level = item.get("text_level", 0)
                        blocks.append(
                            (
                                "#" * min(6, level) + " "
                                if type(level) is int and level > 0
                                else ""
                            )
                            + str(text)
                        )
                    elif kind == "equation":
                        if text:
                            blocks.append(
                                str(text)
                                if str(text).strip().startswith("$$")
                                else "$$\n" + str(text) + "\n$$"
                            )
                        elif item.get("img_path") in assets:
                            blocks.append("![](" + item["img_path"] + ")")
                            extra.append(
                                {
                                    "kind": "equation_image",
                                    "detail": "A formula is preserved as an image because no LaTeX text was extracted.",
                                }
                            )
                        else:
                            extra.append(
                                {
                                    "kind": "missing_equation",
                                    "detail": "A formula could not be represented. Inspect the source and raw extraction.",
                                }
                            )
                    elif kind in ("image", "table"):
                        image = item.get("img_path")
                        table = item.get("table_body")
                        if kind == "table" and isinstance(table, str) and table.strip():
                            blocks.append(table)
                        elif isinstance(image, str):
                            path = PurePosixPath(image)
                            if (
                                path.is_absolute()
                                or ".." in path.parts
                                or image not in assets
                            ):
                                extra.append(
                                    {
                                        "kind": "missing_image",
                                        "detail": "An image reference could not be packaged.",
                                    }
                                )
                            else:
                                blocks.append("![](" + image + ")")
                        for field in (
                            ("table_caption", "table_footnote")
                            if kind == "table"
                            else (
                                "image_caption",
                                "image_footnote",
                                "img_caption",
                                "img_footnote",
                            )
                        ):
                            value = item.get(field, [])
                            blocks.extend(
                                [str(s) for s in value]
                                if isinstance(value, list)
                                else [str(value)]
                                if value
                                else []
                            )
                    elif kind == "list" and isinstance(item.get("list_items"), list):
                        blocks.extend("- " + str(t) for t in item["list_items"])
                    elif kind == "code":
                        blocks.append("```\n" + str(text) + "\n```")
                    else:
                        if text:
                            blocks.append(str(text))
                        extra.append(
                            {
                                "kind": "unhandled_block",
                                "detail": "A source block is not fully represented by this Markdown renderer; inspect source-extraction/.",
                            }
                        )
                text = "\n\n".join(blocks).strip()
                mapped = bool(grouped[idx])
                try:
                    original = pdf.pages[n - 1].extract_text() or ""
                except Exception:
                    original = ""
                issues = _symptoms(text, original, mapped) + extra
                if unknown:
                    issues.append(
                        {
                            "kind": "unmapped_blocks",
                            "detail": "Some section blocks have no usable PDF page index. Raw extraction retains them.",
                        }
                    )
                records.append(
                    {"number": n, "markdown": text, "issues": issues, "mapped": mapped}
                )
    payload = {
        "pages": records,
        "mode": mode,
        "language": lang,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "correctness": "requires_source_review",
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("PART.json", json.dumps(payload, ensure_ascii=False))
        for name, body in assets.items():
            z.writestr(name, body)
        for name, body in raw.items():
            if name not in assets:
                z.writestr("RAW/" + name, body)
    return buffer.getvalue()


def read_part(blob):
    with zipfile.ZipFile(BytesIO(blob)) as z:
        return json.loads(z.read("PART.json"))


def page_view(job, blobs, edits):
    pages = {}
    assets = {}
    raw = {}
    for part in job["parts"]:
        if part["status"] != "done" or part["number"] not in blobs:
            continue
        with zipfile.ZipFile(BytesIO(blobs[part["number"]])) as z:
            payload = json.loads(z.read("PART.json"))
            prefix = f"source-extraction/section-{part['number']:04d}/"
            for name in z.namelist():
                if name.startswith("RAW/"):
                    raw[prefix + name[4:]] = z.read(name)
                elif PurePosixPath(name).suffix.lower() in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                ):
                    assets[prefix + name] = z.read(name)
            for page in payload["pages"]:
                text = page["markdown"]
                for name in z.namelist():
                    if not name.startswith("RAW/") and PurePosixPath(
                        name
                    ).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        text = text.replace(
                            "](" + name + ")", "](" + prefix + name + ")"
                        )
                pages[page["number"]] = {
                    **page,
                    "markdown": text,
                    "extracted_markdown": text,
                    "section": part["number"],
                    "version": 0,
                    "edited": False,
                }
    for edit in edits:
        if edit["page"] in pages:
            pages[edit["page"]].update(
                markdown=edit["text"], version=edit["version"], edited=True
            )
    return [pages[n] for n in sorted(pages)], assets, raw


def build_archive(job, source, blobs, edits, layout="single", include_pdf=True):
    if layout not in ("single", "pages", "sections", "bookmarks"):
        raise ValueError("Choose single, pages, sections or PDF-bookmark export.")
    pages, assets, raw = page_view(job, blobs, edits)
    parts = []
    reports = []
    for page in pages:
        n = page["number"]
        body = (
            f"<!-- Original PDF page {n} -->\n\n[Source PDF page {n}](document.pdf#page={n})\n\n"
            + page["markdown"]
            + "\n"
        )
        parts.append((n, body))
        reports.append(
            {
                "pdf_page": n,
                "issues": page["issues"],
                "edited": page["edited"],
                "edit_version": page["version"],
                "source_mapping_available": page["mapped"],
            }
        )
    wanted = sorted({n for p in job["parts"] for n in p["pages"]})
    missing = sorted(set(wanted) - {n for n, _ in parts})
    manifest = {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_pdf_pages": job["total"],
        "selected_pdf_pages": wanted,
        "exported_pdf_pages": [n for n, _ in parts],
        "unavailable_pdf_pages": missing,
        "mode": job["mode"],
        "language": job["lang"],
        "engine": "pypdf text layer" if job["mode"] == "text" else "MinerU 2.7.6",
        "layout": layout,
        "source_pdf_included": include_pdf,
        "revision": job["revision"],
        "charged_usd": job["charged"] / 10000,
        "correctness": "not_certified",
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        if include_pdf:
            z.writestr("document.pdf", source)
        if layout == "single":
            z.writestr("document.md", "\n\n".join(body for _, body in parts))
        elif layout == "pages":
            for n, body in parts:
                z.writestr(f"page-{n:04d}.md", body)
        elif layout == "bookmarks":
            from bisect import bisect_right

            outline = inspect_pdf(source, len(source) + 1, job["total"])["outline"]
            starts = sorted({1, *[item["pdf_page"] for item in outline]})
            groups = {}
            for n, body in parts:
                start = starts[max(0, bisect_right(starts, n) - 1)]
                groups.setdefault(start, []).append(body)
            for start, bodies in groups.items():
                z.writestr(f"bookmark-section-{start:04d}.md", "\n\n".join(bodies))
            manifest["bookmark_sections"] = outline
            manifest["section_basis"] = (
                "PDF bookmark page positions; bookmark labels are not independently verified chapters."
            )
        else:
            # Stable bounded sections, not invented semantic chapters.
            for offset in range(0, len(parts), 20):
                selected = parts[offset : offset + 20]
                z.writestr(
                    f"section-{selected[0][0]:04d}-{selected[-1][0]:04d}.md",
                    "\n\n".join(b for _, b in selected),
                )
        engine = []
        for name, body in {**assets, **raw}.items():
            z.writestr(name, body)
            if name.endswith(".md"):
                prefix = str(PurePosixPath(name).parent) + "/"
                text = body.decode("utf-8")
                for asset in assets:
                    if asset.startswith(prefix):
                        text = text.replace(
                            "](" + asset[len(prefix) :] + ")", "](" + asset + ")"
                        )
                engine.append(text)
        if engine:
            z.writestr("engine-output.md", "\n\n".join(engine))
        z.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr(
            "REVIEW.json",
            json.dumps(
                {
                    "pages": reports,
                    "unavailable_pages": missing,
                    "notice": "Flags detect symptoms, not every wrong character, number or missing passage.",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        z.writestr(
            "SOURCE-NAVIGATION.md",
            "# Source pages\n\n"
            + "\n".join(f"- [PDF page {n}](document.pdf#page={n})" for n, _ in parts)
            + "\n",
        )
        z.writestr(
            "README.txt",
            "Plain Markdown with local image paths. Some tables use HTML and equations use LaTeX. Inspect REVIEW.json, original source pages and source-extraction/ for fidelity. If document.pdf is omitted, place your original PDF beside these files under that name. Sections are PDF-page groups, not verified chapters. User corrections are retained and are not certified.\n",
        )
    if buffer.tell() > 250 * 1024 * 1024:
        raise ValueError(
            "Export exceeds the archive limit. Select fewer pages or omit the source PDF."
        )
    return buffer.getvalue()
