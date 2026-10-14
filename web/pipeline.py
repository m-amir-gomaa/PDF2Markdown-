"""Bounded PDF checks and isolated unmodified MinerU CLI invocation."""
from io import BytesIO
import json
import hashlib
import re
from pathlib import Path
import subprocess
import tempfile
import zipfile
from pypdf import PdfReader

MAX_RESULT=50*1024*1024


def validate_pdf(data,max_bytes,max_pages):
    if not data or len(data)>max_bytes or not data.startswith(b'%PDF-'):
        raise ValueError('Choose a PDF within the file-size limit.')
    try:
        doc=PdfReader(BytesIO(data),strict=True)
        if doc.is_encrypted:
            raise ValueError('Remove the PDF password before uploading.')
        count=len(doc.pages)
        if not 1<=count<=max_pages:
            raise ValueError(f'This upload supports 1–{max_pages} PDF pages.')
        return count
    except ValueError:
        raise
    except Exception:
        raise ValueError('This PDF could not be opened. Check the original file.') from None


def should_keep(path):
    p=Path(path)
    return p.suffix.lower() in ('.md','.png','.jpg','.jpeg','.webp') or (
        p.name.endswith(('_content_list.json','_content_list_v2.json')) or p.name in ('content_list.json','content_list_v2.json'))


def convert_pdf(data,seconds,runner=subprocess.run,max_result=MAX_RESULT):
    with tempfile.TemporaryDirectory(prefix='document-studio-') as folder:
        root=Path(folder);source=root/'document.pdf';source.write_bytes(data);out=root/'out'
        runner(['mineru','-p',str(source),'-o',str(out),'-b','pipeline','-m','auto','-l','latin'],
               check=True,timeout=max(1,seconds-15),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        markdown=sorted(out.rglob('*.md'))
        if len(markdown)!=1 or not markdown[0].read_text(encoding='utf-8').strip():
            raise ValueError('Extraction returned no usable Markdown.')
        base=markdown[0].parent
        artifacts=sorted(p for p in base.rglob('*') if p.is_file() and should_keep(p))
        if any(p.is_symlink() or not p.resolve().is_relative_to(base.resolve()) for p in artifacts):
            raise ValueError('Unsafe extraction artifact.')
        if sum(p.stat().st_size for p in artifacts)>250*1024*1024:
            raise ValueError('Expanded result is too large.')
        pages=len(PdfReader(BytesIO(data)).pages)
        source_map=[]
        for candidate in artifacts:
            if candidate.name.endswith('_content_list.json') or candidate.name=='content_list.json':
                try:
                    items=json.loads(candidate.read_text())
                    if isinstance(items,list):
                        for index,item in enumerate(items):
                            if not isinstance(item,dict):continue
                            page=item.get('page_idx')
                            if type(page) is int and 0<=page<pages:
                                source_map.append({'pdf_page':page+1,'block_index':index,
                                    'type':str(item.get('type','unknown')),'excerpt':str(item.get('text',''))[:180]})
                except (ValueError,UnicodeError):pass
                break
        text=markdown[0].read_text()
        references=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text)
        missing=[ref for ref in references if '://' not in ref and not (base/ref).is_file()]
        review={'source_sha256':hashlib.sha256(data).hexdigest(),'source_pdf_pages':pages,
                'engine':'MinerU','engine_version':'2.7.6','replacement_characters':text.count('\ufffd'),
                'missing_image_references':missing,'source_mapped_pages':sorted({i['pdf_page'] for i in source_map}),
                'source_mapping_status':'available' if source_map else 'unavailable',
                'linguistic_or_layout_correctness':'not_certified',
                'notes':['Flags detect specific symptoms; they do not detect every wrong word, symbol or table.']}
        navigation=['# Source navigation','',
            'PDF page numbers refer to the included document.pdf, not printed page labels. Source mapping comes from MinerU metadata and requires review.','']
        for page in sorted({i['pdf_page'] for i in source_map}):
            navigation.append(f'- [PDF page {page}](document.pdf#page={page})')
        if not source_map:navigation.append('No usable page metadata was returned; no page links were invented.')
        target=root/'result.zip'
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
            for p in artifacts:archive.write(p,str(p.relative_to(base)))
            archive.writestr('document.pdf',data)
            archive.writestr('SOURCE-MAP.json',json.dumps(source_map,ensure_ascii=False,indent=2))
            archive.writestr('REVIEW.json',json.dumps(review,ensure_ascii=False,indent=2))
            archive.writestr('SOURCE-NAVIGATION.md','\n'.join(navigation)+'\n')
            archive.writestr('DOCUMENT-STUDIO.txt' ,'Extracted with unmodified MinerU 2.7.6. Check symbols, tables and reading order before relying on this output. Source rights remain with their owners.\n')
        if target.stat().st_size>max_result:
            raise ValueError('Result exceeds the download limit.')
        return target.read_bytes()
