"""Fresh authenticated MinerU jobs using the existing deployed Modal worker.

No public redeployment, dotenv loading, DeepSeek or credential logging.
Run with the project's Modal 1.5.3 environment and Python 3.13.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import stat
import threading
import time
import zipfile
import modal

ALLOWED = {'.md', '.png', '.jpg', '.jpeg', '.webp', '.json', '.txt', '.pdf'}


def safe_unpack(blob, destination, max_bytes=1024 * 1024 * 1024):
    """Validate the whole archive before writing; no traversal/symlink/bombs."""
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        infos = archive.infolist()
        if len(infos) > 30000 or sum(i.file_size for i in infos) > max_bytes:
            raise ValueError('archive exceeds extraction bounds')
        names = set()
        for info in infos:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if (path.is_absolute() or '..' in path.parts or '\\' in info.filename
                    or not path.parts or ':' in path.parts[0] or stat.S_ISLNK(mode)
                    or info.filename in names):
                raise ValueError('unsafe or duplicate archive member')
            names.add(info.filename)
            if not info.is_dir() and path.suffix.lower() not in ALLOWED:
                raise ValueError('unexpected artifact format')
        for info in infos:
            dest = destination / info.filename
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open('xb') as stream:
                    stream.write(archive.read(info))
    markdown = sorted(p for p in destination.rglob('*.md') if p.name != 'SOURCE-NAVIGATION.md')
    if len(markdown) != 1 or not markdown[0].read_text(encoding='utf-8').strip():
        raise ValueError('expected one nonempty Markdown artifact')
    return markdown[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--book', help='Optional original PDF filename for the first acceptance job')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.workers <= 3:
        parser.error('workers must be 1..3')
    root = args.sources.resolve(strict=True)
    pdfs = sorted(p for p in root.rglob('*.pdf') if not any(part in ('auto', 'fresh-2026-10-14') for part in p.relative_to(root).parts))
    if args.book:
        pdfs = [p for p in pdfs if p.name == args.book]
    if not pdfs:
        parser.error('no matching original PDFs')
    if args.output.exists() and not args.resume:
        parser.error('choose a new output directory or resume an existing run')
    args.output.mkdir(parents=True, mode=0o700, exist_ok=args.resume)
    fn = modal.Function.from_name('mineru-pdf-extractor', 'extract_pdf')
    fn.hydrate()  # authenticated lookup before any billable work
    lock = threading.Lock()
    manifest_path = args.output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'worker': 'mineru-pdf-extractor.extract_pdf', 'modal_sdk': modal.__version__,
        'status': 'running', 'books': {}, 'deepseek_requests': 0,
        'publication_rights': 'unverified', 'semantic_review': 'not_performed'}

    def checkpoint():
        temp = manifest_path.with_suffix('.tmp')
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        temp.replace(manifest_path)

    def job(source):
        rel = str(source.relative_to(root))
        blob = source.read_bytes()
        sha = hashlib.sha256(blob).hexdigest()
        with lock:
            old = manifest['books'].get(rel, {})
            if old.get('status') == 'extracted' and old.get('source_sha256') == sha:
                md = args.output / old['markdown']
                if md.is_file() and hashlib.sha256(md.read_bytes()).hexdigest() == old['markdown_sha256']:
                    print('Already verified:', source.name, flush=True)
                    return True
        # Worker receives a strictly safe name, never a user path or arbitrary filename.
        name = 'book_' + sha[:16]
        dest = args.output / source.parent.relative_to(root) / source.stem
        start = time.monotonic()
        try:
            with lock:
                record = manifest['books'].get(rel, {})
                call_id = record.get('function_call_id') if record.get('source_sha256') == sha else None
            if call_id:
                call = modal.FunctionCall.from_id(call_id)
                print('Retrieving existing call:', source.name, flush=True)
            else:
                call = fn.spawn(blob, name)
                with lock:
                    manifest['books'][rel] = {'status': 'submitted', 'source_sha256': sha,
                                             'function_call_id': call.object_id}
                    checkpoint()
                print('Submitted:', source.name, flush=True)
            result = call.get(timeout=3900)
            if not isinstance(result, bytes):
                raise ValueError('worker did not return ZIP bytes')
            dest.mkdir(parents=True, exist_ok=False)
            md = safe_unpack(result, dest)
            record = {'status': 'extracted', 'source_sha256': sha,
                      'function_call_id': call.object_id, 'elapsed_seconds': round(time.monotonic() - start, 2),
                      'markdown': str(md.relative_to(args.output)),
                      'markdown_sha256': hashlib.sha256(md.read_bytes()).hexdigest(),
                      'markdown_characters': len(md.read_text()),
                      'replacement_characters': md.read_text().count('\ufffd'),
                      'images': sum(p.suffix.lower() in {'.png','.jpg','.jpeg','.webp'} for p in dest.rglob('*')),
                      'zip_bytes': len(result), 'linguistic_review': 'not_performed'}
            with lock:
                manifest['books'][rel] = record
                checkpoint()
            print('Extracted:', source.name, 'chars:', record['markdown_characters'], flush=True)
            return True
        except Exception as exc:
            with lock:
                record = manifest['books'].setdefault(rel, {'source_sha256': sha})
                record.update(status='failed', error_type=type(exc).__name__)
                checkpoint()
            print('Extraction requires attention:', source.name, type(exc).__name__, flush=True)
            return False

    manifest['status'] = 'running'
    checkpoint()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(job, pdfs))
    manifest['status'] = 'extracted' if all(results) else 'incomplete'
    checkpoint()
    return int(not all(results))

if __name__ == '__main__':
    raise SystemExit(main())
