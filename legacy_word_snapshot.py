"""Validate an original-DOC Word snapshot and create an in-memory text carrier.

Never writes a fake DOCX. This carrier deliberately contains no reconstructed
image, formula, field or pagination claims; control characters remain unchanged.
"""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(snapshot_path, original_path):
    snapshot_path, original_path = Path(snapshot_path).resolve(), Path(original_path).resolve()
    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8-sig'))
    if snapshot.get('status') != 'read_only_snapshot_collected' or snapshot.get('errors') or snapshot.get('cleanup_errors'):
        raise ValueError('原DOC只读快照或Word清理状态未通过核验')
    if snapshot.get('source_sha256_unchanged') is not True or snapshot.get('derived_sha256_unchanged') is not True:
        raise ValueError('只读快照读取前后文件哈希未通过核验')
    source = snapshot.get('source', {})
    if source.get('read_only') is not True or Path(source.get('path', '')).resolve() != original_path or _sha(original_path) != source.get('sha256'):
        raise ValueError('快照原DOC路径或来源哈希不匹配')
    raw = source.get('raw_body_text')
    if not isinstance(raw, str) or hashlib.sha256(raw.encode('utf-8')).hexdigest() != source.get('body_utf8_sha256'):
        raise ValueError('完整源正文快照哈希不匹配')
    rows = source.get('paragraphs', [])
    if not isinstance(rows, list) or len(rows) > 20000 or ''.join(r.get('raw_text', '') for r in rows) != raw:
        raise ValueError('段落快照不能完整重构原DOC正文')
    prior_start = -1
    for index, row in enumerate(rows):
        if row.get('index') != index or not isinstance(row.get('raw_text'), str):
            raise ValueError('段落快照索引或文字不可信')
        start, end = row.get('range_start'), row.get('range_end')
        if not isinstance(start, int) or not isinstance(end, int) or end < start or start < prior_start:
            raise ValueError('段落Word范围无效或逆序')
        prior_start = start
        level = row.get('heading_level')
        if level is not None and (type(level) is not int or level < 1 or level > 9):
            raise ValueError('标题大纲级别不在Word有效范围')
    return snapshot, _sha(snapshot_path)


def build_parsed(snapshot_path, original_path):
    """Return ``((document, styles, source_sha256, 0), snapshot_info)``.

Image count 0 is an unknown sentinel; info explicitly marks it unknown. Consumers
must not infer no pictures or full coverage, and must skip layout/object checks.
The snapshot's paragraph evidence carries original Word ranges for traceability.
"""
    snapshot, snapshot_sha = validate(snapshot_path, original_path)
    document = ET.Element(W + 'document')
    body = ET.SubElement(document, W + 'body')
    styles = ET.Element(W + 'styles')
    for row in snapshot['source']['paragraphs']:
        parent = body
        if row.get('in_table'):
            # Only paragraph membership is known: separate carriers deliberately
            # avoid claiming actual table grouping, captions, border or layout.
            parent = ET.SubElement(ET.SubElement(ET.SubElement(body, W + 'tbl'), W + 'tr'), W + 'tc')
        p = ET.SubElement(parent, W + 'p')
        props = ET.SubElement(p, W + 'pPr')
        if row.get('heading_level'):
            ET.SubElement(props, W + 'outlineLvl', {W + 'val': str(row['heading_level'] - 1)})
        name = row.get('style_name', '')
        if name.lower().startswith(('toc', '目录')):
            # Existing extractor's actual style evidence recognises TOC names.
            style_id = 'SnapshotTOC'
            ET.SubElement(props, W + 'pStyle', {W + 'val': style_id})
            if not any(s.get(W + 'styleId') == style_id for s in styles):
                style = ET.SubElement(styles, W + 'style', {W + 'styleId': style_id, W + 'type': 'paragraph'})
                ET.SubElement(style, W + 'name', {W + 'val': 'TOC 1'})
        ET.SubElement(ET.SubElement(p, W + 'r'), W + 't').text = row['raw_text']
    info = {'artifact_role': 'original_legacy_doc_read_only_snapshot', 'snapshot_path': str(Path(snapshot_path).resolve()),
            'snapshot_sha256': snapshot_sha, 'original_source_path': str(Path(original_path).resolve()),
            'source_sha256': snapshot['source']['sha256'], 'original_body_utf8_sha256': snapshot['source']['body_utf8_sha256'],
            'original_paragraphs': snapshot['source']['paragraphs'], 'image_count_known': False,
            'native_object_xml_available': False, 'layout_available': False, 'docx_conversion_eligible': False,
            'word_snapshot_requests_previously_recorded': snapshot.get('word_launches', 0),
            'snapshot_paragraphs_complete': True, 'text_control_characters_preserved': True,
            'limitations': ['原DOC正文范围只读快照；纯内存树仅供文字规则调用，不是原DOC的XML或保真DOCX。',
                            '图片数量未知；公式、表格结构、脚注、字段及分页不由该内存树推断。']}
    return (document, styles, snapshot['source']['sha256'], 0), info
