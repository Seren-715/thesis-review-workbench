"""Teacher-bound layout diagnostics using verified Word-exported PDF evidence.

No Word/provider/SVN calls. XML dimensions alone never prove rendered layout.
Pixels bind PDF raster objects to current DOCX media; captions are bound only by
explicit drawing metadata, never a nearby numbered string. Remaining visual or
table clauses remain uncovered and are counted in coverage, not as errors.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
CAPTION = re.compile(r'图\s*\d+\s*[-－–—]\s*\d+')


def load_remaining_layout_rules(library):
    families = {r['id']: r for r in library.get('rules', [])}
    definitions = (
        ('NP047', '实际空白页核查', 'blank_pages', r'空白页'),
        ('NP048', '图像实际版心边界核查', 'image_page_bounds', r'图片?.*(?:边界|过界)|图表.*(?:边界|过界)'),
        ('NP049', '独立图像排列及重叠核查', 'figure_layout', r'同一行|叠在一起'),
    )
    result = []
    for rid, title, family, pattern in definitions:
        all_evidence = families.get(family, {}).get('teacher_evidence', [])
        selected = [e for e in all_evidence if re.search(pattern, e.get('quote', ''))]
        result.append(dict(id=rid, title=title, family=family, enabled=bool(selected),
            checker='verified_pdf_layout', teacher_evidence=selected,
            excluded_evidence_ids=[e.get('evidence_id') for e in all_evidence if e not in selected],
            automatic_error_judgment_enabled=False, policy='teacher_specific_not_universal',
            scope='当前原件对应的真实Word PDF；空页仅完全无可见内容页，图像须通过媒体像素绑定；不以XML排版尺寸或邻近图题推定真实布局。'))
    return result


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _verified_pdf(layout, sha):
    if not layout or layout.get('source', {}).get('sha256') != sha or not layout.get('source_unchanged'):
        raise ValueError('没有当前原件SHA绑定的真实Word分页证据')
    if layout.get('status') != 'native_table_layout_collected_visual_pending':
        raise ValueError('实际Word PDF采集状态未经核验')
    source = Path(layout.get('source', {}).get('path', ''))
    pdf = Path(layout.get('pdf', {}).get('path', ''))
    if not source.is_file() or _hash(source.read_bytes()) != sha:
        raise ValueError('原件文件缺失或SHA已改变')
    if not pdf.is_file() or _hash(pdf.read_bytes()) != layout.get('pdf', {}).get('sha256'):
        raise ValueError('实际PDF缺失或SHA不符')
    return source, pdf


def _media_bindings(source, paragraphs, expected_sha256=None):
    """Only main-story nondeleted raster drawings; no guesses for OLE/SVG/alpha."""
    from PIL import Image
    from new_paper_checks import read_docx, _xml
    checked_document, _, actual_sha256, _ = read_docx(source)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ValueError('读取绘图对象时原件SHA已改变')
    with ZipFile(source) as archive:
        doc = checked_document
        relationships = _xml(archive.read('word/_rels/document.xml.rels'))
        if 'word/settings.xml' in archive.namelist():
            settings = _xml(archive.read('word/settings.xml'))
            if any(settings.find(W+tag) is not None for tag in ('mirrorMargins','gutterAtTop','bookFoldPrinting','bookFoldRevPrinting')):
                doc.set('layout_bounds_unsupported','true')
        targets = {n.get('Id'): n.get('Target') for n in relationships if n.get('TargetMode') != 'External'}
        nodes = list(doc.iter(W+'p'))
        if len(nodes) != len(paragraphs):
            raise ValueError('当前原件段落结构与核查输入不一致')
        result, unread = [], 0
        for node, paragraph in zip(nodes, paragraphs):
            if any(paragraph.get(k) for k in ('in_deleted','in_toc','in_textbox')):
                continue
            for drawing in node.iter(W+'drawing'):
                blips = list(drawing.iter(A+'blip'))
                if len(blips) != 1:
                    unread += 1; continue
                target = targets.get(blips[0].get(R+'embed'))
                if not target or '..' in target or target.startswith('/'):
                    unread += 1; continue
                try:
                    image = Image.open(io.BytesIO(archive.read('word/'+target)))
                    if image.mode not in ('RGB', 'L', 'RGBA', 'P') or getattr(image, 'n_frames', 1) != 1:
                        raise ValueError('Transparent/vector/animated media require visual review')
                    if image.mode in ('RGBA','P') and image.convert('RGBA').getchannel('A').getextrema() != (255,255):
                        raise ValueError('Transparent media requires compositing evidence')
                    image = image.convert('RGB')
                    fingerprint = _hash(image.tobytes())
                except Exception:
                    unread += 1; continue
                metadata = ' '.join((n.get('title','')+' '+n.get('descr','')) for n in drawing.iter(WP+'docPr'))
                labels = set(re.sub(r'\s+', '', m.group()).replace('－','-').replace('–','-').replace('—','-') for m in CAPTION.finditer(metadata))
                result.append(dict(pixel_sha256=fingerprint, paragraph=paragraph,
                    caption=next(iter(labels)) if len(labels) == 1 else None,
                    media_target=target, image_dimensions=list(image.size)))
        # VML and native drawing/shapes are not silently considered inspected.
        unread += sum(1 for _ in doc.iter(W+'pict'))
    return doc, result, unread


def _rendered_images(pdf, bindings):
    import fitz
    result, unbound = [], 0
    by_digest = {}
    for item in bindings:
        by_digest.setdefault(item['pixel_sha256'], []).append(item)
    for page_number, page in enumerate(pdf, 1):
        for info in page.get_image_info(xrefs=True):
            xref = info.get('xref', 0)
            if not xref:
                unbound += 1; continue
            try:
                pix = fitz.Pixmap(pdf, xref)
                if pix.alpha:
                    raise ValueError('Mask/alpha rendering not uniquely bound')
                if pix.colorspace.n != 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                matches = by_digest.get(_hash(pix.samples), [])
                if len(matches) != 1:
                    raise ValueError('Media binding is absent or ambiguous')
                result.append(dict(matches[0], physical_page=page_number, bbox=list(info['bbox'])))
            except Exception:
                unbound += 1
    # Repeated PDF use of a media object cannot be mapped to a single XML drawing.
    counts = {}
    for item in result:
        counts[item['pixel_sha256']] = counts.get(item['pixel_sha256'], 0)+1
    unbound += sum(counts[i['pixel_sha256']] != 1 for i in result)
    result = [i for i in result if counts[i['pixel_sha256']] == 1]
    return result, unbound


def _body_rect(document, page):
    """Margins require one explicit section and actual PDF size agreement."""
    sections = list(document.iter(W+'sectPr'))
    if len(sections) != 1 or page.rotation:
        return None
    if document.get('layout_bounds_unsupported') or sections[0].find(W+'rtlGutter') is not None:
        return None
    size, margins = sections[0].find(W+'pgSz'), sections[0].find(W+'pgMar')
    if size is None or margins is None:
        return None
    try:
        width, height = [int(size.get(W+k))/20 for k in ('w','h')]
        if abs(width-page.rect.width)>1 or abs(height-page.rect.height)>1:
            return None
        left, right, top, bottom = [int(margins.get(W+k))/20 for k in ('left','right','top','bottom')]
        if int(margins.get(W+'gutter','0')) or min(left,right,top,bottom)<0:
            return None
        if document.find('.//'+W+'mirrorMargins') is not None:
            return None
        return [left, top, width-right, height-bottom]
    except (TypeError, ValueError):
        return None


def _finding(rule, title, reason, suggestion, paragraph=None, **evidence):
    paragraph = paragraph or {}
    return dict(rule_id=rule['id'], title=title, status='candidate',
        text=paragraph.get('text',''), paragraph_index=paragraph.get('index'),
        start=0, end=len(paragraph.get('text','')), location=paragraph.get('location'),
        reason=reason, suggestion=suggestion, teacher_evidence=rule['teacher_evidence'],
        policy=rule['policy'], physical_pages=[evidence['physical_page']] if evidence.get('physical_page') else [],
        layout_evidence=evidence)


def check_remaining_layout_rules(document, paragraphs, rules, source_sha256, layout_result=None):
    import fitz
    findings, coverage = [], []
    selected = [r for r in rules if r['id'] in {'NP047','NP048','NP049'}]
    try:
        source, path = _verified_pdf(layout_result, source_sha256)
        pdf = fitz.open(path)
    except (ValueError, OSError, fitz.FileDataError) as error:
        for rule in selected:
            coverage.append(dict(rule_id=rule['id'],title=rule['title'],
                status='partial' if rule.get('enabled') else 'not_applicable',
                detail=f'{error}；没有执行视觉对象核查，不判断存在版面错误。'))
        return findings, coverage
    try:
        try:
            actual_document, media, unread = _media_bindings(source, paragraphs, source_sha256)
            images, unbound = _rendered_images(pdf, media)
        except (ValueError, KeyError, OSError) as error:
            actual_document, images, unread, unbound = document, [], 1, 0
        for rule in selected:
            rid = rule['id']
            row = dict(rule_id=rid,title=rule['title'],status='partial')
            if not rule.get('enabled'):
                row.update(status='not_applicable',detail='所附教师引文没有可执行的本项要求，未启用。')
            elif rid == 'NP047':
                blank = []
                uninspected = 0
                for number, page in enumerate(pdf,1):
                    # Full rendered page, not OCR/text absence. Vector diagrams,
                    # white images with ink and scan pages cannot become blanks.
                    if page.rect.width*page.rect.height>4_000_000:
                        uninspected += 1; continue
                    if page.get_text().strip() or page.get_images() or page.get_drawings() or list(page.annots() or []):
                        continue
                    pix = page.get_pixmap(matrix=fitz.Matrix(1,1),colorspace=fitz.csRGB,alpha=False)
                    if pix.samples and min(pix.samples) == 255:
                        blank.append(number)
                        findings.append(_finding(rule,'实际PDF存在完全空白页',
                            f'当前原件绑定的PDF物理页{number}全页渲染为纯白，没有可见内容；所附教师明确要求不留空白页。',
                            '检查是否为多余分页符或分节造成的空页；若用于规定的章节奇偶起始或模板留页，保留并说明适用例外。',
                            physical_page=number,pdf_sha256=layout_result['pdf']['sha256']))
                row.update(status='partial' if uninspected else 'checked',physical_pages_scanned=len(pdf)-uninspected,blank_pages=blank,
                    detail=f'检查{len(pdf)-uninspected}个实际物理页的完整渲染，仅识别无文本、绘图、图像或批注且全白的页；{uninspected}页尺寸超出本机渲染限制。空行、局部留白、仅页眉页脚页不由本项判断。')
            elif rid == 'NP048':
                margins_missing = 0
                for image in images:
                    page = pdf[image['physical_page']-1]
                    rect = _body_rect(actual_document,page)
                    if rect is None:
                        margins_missing += 1; continue
                    box = image['bbox']
                    # One-point tolerance is extraction precision, not a style threshold.
                    outside = box[0]<rect[0]-1 or box[1]<rect[1]-1 or box[2]>rect[2]+1 or box[3]>rect[3]+1
                    if outside:
                        findings.append(_finding(rule,'实际图像范围超出版心',
                            f'媒体像素与当前原件唯一绑定；实际PDF物理页{image["physical_page"]}图像范围{box}超出当前节页边距确定的版心{rect}。',
                            '核对是否有明确允许的出血或跨版心排版；否则调整此图位置或尺寸。不以此结论判断表格、水纹或图像内容错误。',
                            image['paragraph'],physical_page=image['physical_page'],bbox=box,body_bbox=rect,
                            media_target=image['media_target'],pixel_sha256=image['pixel_sha256']))
                row.update(status='partial',bound_images=len(images),unread_images=unread+unbound,
                    detail=f'唯一媒体绑定{len(images)}张实际PDF图像，{margins_missing}张缺少可靠节边界；{unread+unbound}个对象未绑定。表格、矢量图、透明图及水纹条款未由本项覆盖。')
            else:
                certified = [i for i in images if i.get('caption')]
                pairs = 0
                for index, first in enumerate(certified):
                    for second in certified[index+1:]:
                        if first['physical_page'] != second['physical_page'] or first['caption']==second['caption']:
                            continue
                        pairs += 1
                        a,b=first['bbox'],second['bbox']
                        overlap = min(a[2],b[2])-max(a[0],b[0])>1 and min(a[3],b[3])-max(a[1],b[1])>1
                        same_row = abs(a[1]-b[1])<=1 and (a[2]<=b[0]+1 or b[2]<=a[0]+1)
                        relevant = [e for e in rule['teacher_evidence'] if (overlap and '叠在一起' in e.get('quote','')) or (same_row and '同一行' in e.get('quote',''))]
                        if relevant:
                            scoped = dict(rule,teacher_evidence=relevant)
                            findings.append(_finding(scoped,'独立图像实际重叠' if overlap else '独立图像排列在同一行',
                                f'{first["caption"]}与{second["caption"]}由各自绘图元数据明确命名且媒体像素唯一绑定；实际PDF显示两者'+('存在重叠。' if overlap else '上缘一致并在同一行并列。'),
                                '核对本论文是否适用该教师针对独立图像的排列要求；子图组合不据此判错。适用时分别排列，避免互相覆盖。',
                                first['paragraph'],physical_page=first['physical_page'],captions=[first['caption'],second['caption']],bboxes=[a,b]))
                row.update(status='partial',bound_captioned_images=len(certified),checked_pairs=pairs,
                    detail=f'核查{pairs}对绘图元数据明确命名且媒体唯一绑定的独立图像；没有对象元数据绑定不使用邻近题注代替。未命名、矢量或复用对象仍未覆盖，不生成逐图待核验记录。')
            coverage.append(row)
    finally:
        pdf.close()
    for index, finding in enumerate(findings,1):
        finding['id']=f'{finding["rule_id"]}-{index:04d}'
    return findings, coverage
