"""Bind captioned figures to verified, actual Word PDF pages, read only."""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def compact(value):
    return re.sub(r'\s+', '', value)

def prefer_verified_word_pages(source, output, report, layout):
    if not layout or layout.get('source_unchanged') is not True or layout.get('source',{}).get('sha256') != report['source_sha256']:
        return 0
    pdf = layout.get('pdf',{})
    path = Path(pdf.get('path','')).resolve()
    if not path.is_relative_to(ROOT/'data') or not path.is_file() or path.stat().st_size > 100_000_000 or digest(path) != pdf.get('sha256') or digest(source) != report['source_sha256']:
        return 0
    import fitz
    count = 0
    with fitz.open(path) as document:
        if document.page_count > 300:return 0
        lines = []
        for page in document:
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    text = ''.join(span['text'] for span in line['spans'])
                    lines.append((page.number, compact(text),line['bbox']))
        for finding in report.get('findings',[]):
            if finding['rule_id'] != 'NP007':continue
            evidence = finding.get('figure_evidence',{})
            caption = evidence.get('caption',{}).get('text') or finding.get('text','')
            matches = [row for row in lines if row[1] == compact(caption)]
            if len(matches) != 1:continue
            number, _, box = matches[0]
            page = document[number]
            scale = 1600 / max(page.rect.width,page.rect.height)
            preview = Path(output)/f"{finding['id']}_word_page.png"
            page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).save(preview)
            evidence.update(original_binding_status=evidence.get('binding_status'),binding_status='verified_word_page_caption',
                reviewable=True,evidence_basis='word_page',pdf_sha256=pdf['sha256'],physical_page=number+1,
                caption_bbox=list(box),page_height_pt=page.rect.height,caption_y_fraction=(box[1]+box[3])/2/page.rect.height,
                preview_path=str(preview),preview_sha256=digest(preview),preview_mime='image/png',
                source_image_sha256=digest(preview),note='来源哈希一致的 Word 实际排版整页；以指定图题定位审阅，包含复合对象。')
            finding['figure_evidence']=evidence
            count += 1
    if digest(path)!=pdf['sha256'] or digest(source)!=report['source_sha256']:raise ValueError('排版证据在读取期间改变。')
    return count
