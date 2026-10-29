"""Evidence-bound local checks for new DOCX uploads; never execute document text.

Paragraph indexes and character offsets are zero-based; ``end`` is exclusive.
The report is a clue report under this teacher's policy, not a universal grade.
No SVN, Word, network or LLM calls are made in this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from paper_locations import annotate_paragraphs, enrich_report_locations, declaration_template

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / 'data/svn_exports/cross_student_rules_20261007_121139_462691/reviewed_pilot_20261007_121835_788699/reviewed_rules.json'
CATALOG_SHA256 = '681fb204b7db0d91e395fefbabdf9de16962010fb4a68f322d1e510b10cc7ec0'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MAIN_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
MAX_FILE = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_XML = 16 * 1024 * 1024


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _xml(raw):
    if len(raw) > MAX_XML:
        raise ValueError('XML 内容过大，超出本机安全读取限制')
    # XML parsers must not expand entities or interpret document instructions.
    scan = raw.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan:
        raise ValueError('文档包含禁止的 XML 实体或 DTD')
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError('Word XML 无法解析') from exc


def read_docx(path):
    """Read bounded, non-macro transitional OOXML without extracting its ZIP."""
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.docx':
        raise ValueError('仅支持真实的 .docx；旧 .doc 请先另存为 DOCX')
    if path.stat().st_size > MAX_FILE:
        raise ValueError('文件超过 32 MiB 上传读取限制')
    before = digest(path)
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(i.file_size for i in entries) > MAX_EXPANDED:
                raise ValueError('Word ZIP 内容超出安全限制')
            names = [i.filename for i in entries]
            if len(set(names)) != len(names):
                raise ValueError('ZIP 存在重复成员')
            for entry in entries:
                name = entry.filename.replace('\\', '/')
                if name.startswith('/') or ':' in name or '..' in name.split('/'):
                    raise ValueError('ZIP 包含非法成员路径')
                if entry.flag_bits & 1:
                    raise ValueError('不支持加密 Word 文档')
                if entry.file_size > 40 * 1024 * 1024 or (entry.file_size > 1024 * 1024 and entry.file_size > max(1, entry.compress_size) * 250):
                    raise ValueError('ZIP 成员展开比例或大小超出安全限制')
                if any(marker in name.lower() for marker in ('vbaproject', '/activex/')) or (name.lower().startswith('word/embeddings/') and Path(name).suffix.lower() in {'.exe', '.com', '.bat', '.cmd', '.ps1', '.js', '.vbs', '.scr', '.dll'}):
                    raise ValueError('不支持宏、ActiveX 或嵌入式可执行文件')
            if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
                raise ValueError('ZIP 不是支持的 Word OOXML 文档')
            content = _xml(archive.read('[Content_Types].xml'))
            if any('macroenabled' in n.attrib.get('ContentType', '').lower() or 'vba' in n.attrib.get('ContentType', '').lower() for n in content):
                raise ValueError('不支持带宏文档')
            if not any(n.attrib.get('PartName') == '/word/document.xml' and n.attrib.get('ContentType') == MAIN_TYPE for n in content):
                raise ValueError('不是无宏 DOCX 主文档类型')
            document = _xml(archive.read('word/document.xml'))
            styles = _xml(archive.read('word/styles.xml')) if 'word/styles.xml' in names else None
            validate_document_body(document, styles)
            image_count = len([n for n in names if n.startswith('word/media/') and not n.endswith('/')])
    except (zipfile.BadZipFile, RuntimeError, KeyError) as exc:
        raise ValueError('文件不是可读取的 DOCX') from exc
    if digest(path) != before:
        raise ValueError('读取过程中源文件发生变化')
    return document, styles, before, image_count


BODY_VIEW_VERSION = 'visible_final_no_hidden_runs_v2'


def _on_off(node):
    return node is not None and node.get(W + 'val', 'true').lower() not in {'0', 'false', 'off'}


def _hidden_run_resolver(styles):
    """Resolve vanish toggle styles; direct formatting explicitly overrides them."""
    nodes = {} if styles is None else {s.get(W + 'styleId'): s for s in styles.findall(W + 'style')}
    defaults = [] if styles is None else [s.get(W + 'styleId') for s in styles.findall(W + 'style')
                if s.get(W + 'type') == 'paragraph' and _on_off_attr(s.get(W + 'default', 'false'))]
    default_hidden = _on_off(styles.find(W + 'docDefaults/' + W + 'rPrDefault/' + W + 'rPr/' + W + 'vanish')) if styles is not None else False
    def apply_style(style_id, hidden, seen=()):
        if style_id not in nodes or style_id in seen or len(seen) > 20:
            return hidden
        node = nodes[style_id]
        base = node.find(W + 'basedOn')
        if base is not None:
            hidden = apply_style(base.get(W + 'val'), hidden, (*seen, style_id))
        # In styles, true is a toggle; false leaves inherited state unchanged.
        if _on_off(node.find(W + 'rPr/' + W + 'vanish')):
            hidden = not hidden
        return hidden
    def hidden(run, paragraph_style=None):
        value = apply_style(paragraph_style or (defaults[0] if defaults else None), default_hidden)
        character = run.find(W + 'rPr/' + W + 'rStyle')
        if character is not None:
            value = apply_style(character.get(W + 'val'), value)
        direct = run.find(W + 'rPr/' + W + 'vanish')
        return _on_off(direct) if direct is not None else value
    return hidden


def _on_off_attr(value):
    return str(value).lower() not in {'0', 'false', 'off'}


def validate_document_body(document, styles=None):
    """Require a real body with final-view text or inspectable native objects.

    This is an input-content gate, not a paper-quality verdict. Empty paragraph
    markers, section properties, field instructions and orphan media resources
    are not body content. Image/table/formula-only documents remain eligible
    for their supported object checks; objects are never activated here.
    """
    if document.tag != W + 'document':
        raise ValueError('Word 文档正文格式不正确或命名空间不受支持。')
    bodies = document.findall(W + 'body')
    if len(bodies) != 1:
        raise ValueError('Word 主文档必须包含且仅包含一个正文节点（w:body）。')
    hidden = _hidden_run_resolver(styles)
    math = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'

    def contains_content(node, paragraph_style=None):
        if node.tag in {W + 'del', W + 'moveFrom'}:
            return False
        if node.tag == W + 'p':
            style = node.find(W + 'pPr/' + W + 'pStyle')
            paragraph_style = style.get(W + 'val') if style is not None else None
        if node.tag == W + 'r' and hidden(node, paragraph_style):
            return False
        if node.tag in {W + 't', math + 't'} and (node.text or '').strip():
            return True
        if node.tag == W + 'tbl' and node.find(W + 'tr/' + W + 'tc') is not None:
            return True
        if node.tag in {W + 'drawing', W + 'pict', W + 'object'} and len(node):
            return True
        if node.tag == math + 'oMath' and any(child.tag != math + 'oMathPr' for child in node):
            return True
        return any(contains_content(child, paragraph_style) for child in node)

    if not contains_content(bodies[0]):
        raise ValueError('论文正文不存在可检查内容：未发现可见文字或可检查的表格、图片、公式等对象。请确认文件内容后重新提交。')
    return bodies[0]


def _visible_text(element, hidden_resolver=None, paragraph_style=None):
    result = []
    def visit(node):
        if node.tag in {W + 'del', W + 'moveFrom', W + 'txbxContent'}:
            return
        if node.tag == W + 'r':
            hidden = hidden_resolver(node, paragraph_style) if hidden_resolver is not None else _on_off(node.find(W + 'rPr/' + W + 'vanish'))
            if hidden:
                return
        if node.tag == W + 't':
            result.append(node.text or '')
        elif node.tag == W + 'tab':
            result.append('\t')
        elif node.tag in {W + 'br', W + 'cr'}:
            result.append('\n')
        for child in node:
            visit(child)
    visit(element)
    return ''.join(result)


def _style_levels(styles):
    nodes = {} if styles is None else {s.attrib.get(W + 'styleId'): s for s in styles.findall(W + 'style')}
    defaults = [s.attrib.get(W + 'styleId') for s in nodes.values() if s.attrib.get(W + 'default') == '1' and s.attrib.get(W + 'type') == 'paragraph']
    def resolve(style_id, seen=()):
        if style_id in seen or len(seen) > 20 or style_id not in nodes:
            return None
        node = nodes[style_id]
        outline = node.find(W + 'pPr/' + W + 'outlineLvl')
        if outline is not None:
            try:
                value = int(outline.attrib.get(W + 'val', '9'))
                return value + 1 if 0 <= value < 9 else None
            except ValueError:
                return None
        name = node.find(W + 'name')
        raw_name = name.attrib.get(W + 'val', '') if name is not None else ''
        match = re.fullmatch(r'(?:heading|标题)\s*([1-9])', raw_name, re.I)
        if match:
            return int(match.group(1))
        base = node.find(W + 'basedOn')
        return resolve(base.attrib.get(W + 'val'), seen + (style_id,)) if base is not None else None
    return resolve, defaults[0] if defaults else None


def _toc_style_ids(styles):
    """Resolve TOC paragraph styles by their declared names and inheritance."""
    nodes = {} if styles is None else {s.attrib.get(W + 'styleId'): s for s in styles.findall(W + 'style')}
    resolved = {}

    def resolve(style_id, seen=()):
        if style_id in resolved:
            return resolved[style_id]
        if style_id in seen or len(seen) > 20:
            return False
        node = nodes.get(style_id)
        name = node.find(W + 'name') if node is not None else None
        raw_name = name.attrib.get(W + 'val', '') if name is not None else ''
        is_toc = any(re.fullmatch(r'(?:toc|目录)\s*\d*', value or '', re.I)
                     for value in (style_id, raw_name))
        base = node.find(W + 'basedOn') if node is not None else None
        if not is_toc and base is not None:
            is_toc = resolve(base.attrib.get(W + 'val'), seen + (style_id,))
        resolved[style_id] = bool(is_toc)
        return resolved[style_id]

    return {style_id for style_id in nodes if resolve(style_id)}


def _toc_field_paragraphs(document):
    """Track actual TOC field boundaries, including nested page-reference fields.

    Field instructions and results can span paragraphs. Each complex field has
    its own frame so a nested PAGEREF/HYPERLINK end cannot close the outer TOC.
    Deleted content and text boxes use separate field streams and cannot leak a
    field boundary into the surrounding visible body.
    """
    toc_paragraphs = set()

    def is_toc(instruction):
        return bool(re.match(r'^\s*TOC(?:\s|$)', instruction, re.I))

    def touch(paragraph, stack, simple_toc):
        if paragraph is None:
            return
        if simple_toc or any(frame['toc'] for frame in stack):
            toc_paragraphs.add(paragraph)
        for frame in stack:
            if not frame['separated']:
                frame['paragraphs'].add(paragraph)

    def finish_instruction(frame):
        frame['toc'] = is_toc(''.join(frame['instruction']))
        if frame['toc']:
            toc_paragraphs.update(frame['paragraphs'])
        frame['paragraphs'].clear()
        frame['instruction'].clear()
        frame['separated'] = True

    def visit(node, stack, paragraph=None, simple_toc=False):
        if node.tag in {W + 'del', W + 'moveFrom', W + 'txbxContent'}:
            stack, paragraph, simple_toc = [], None, False
        if node.tag == W + 'p':
            paragraph = node
            touch(paragraph, stack, simple_toc)
        elif node.tag == W + 'fldSimple':
            simple_toc = simple_toc or is_toc(node.attrib.get(W + 'instr', ''))
            touch(paragraph, stack, simple_toc)
        elif node.tag == W + 'fldChar':
            touch(paragraph, stack, simple_toc)
            kind = node.attrib.get(W + 'fldCharType')
            if kind == 'begin':
                stack.append({'instruction': [], 'paragraphs': {paragraph} if paragraph is not None else set(),
                              'toc': False, 'separated': False})
            elif kind == 'separate' and stack:
                if not stack[-1]['separated']:
                    finish_instruction(stack[-1])
            elif kind == 'end' and stack:
                if not stack[-1]['separated']:
                    finish_instruction(stack[-1])
                stack.pop()
        elif node.tag == W + 'instrText' and stack and not stack[-1]['separated']:
            stack[-1]['instruction'].append(node.text or '')
        for child in node:
            visit(child, stack, paragraph, simple_toc)

    visit(document, [])
    return toc_paragraphs


def extract_paragraphs(document, styles=None):
    resolve, default = _style_levels(styles)
    hidden_resolver = _hidden_run_resolver(styles)
    toc_styles = _toc_style_ids(styles)
    toc_fields = _toc_field_paragraphs(document)
    parents = {child: node for node in document.iter() for child in node}
    rows = []
    total_text = 0
    for paragraph in document.iter(W + 'p'):
        if len(rows) >= 20000:
            raise ValueError('段落超过 20000 个，超出小批次检查限制')
        ancestors = []
        current = paragraph
        while current in parents:
            current = parents[current]
            ancestors.append(current)
        props = paragraph.find(W + 'pPr')
        style = props.find(W + 'pStyle') if props is not None else None
        style_id = style.attrib.get(W + 'val', '') if style is not None else default
        outline = props.find(W + 'outlineLvl') if props is not None else None
        level = resolve(style_id)
        if outline is not None:
            try:
                number = int(outline.attrib.get(W + 'val', '9'))
                level = number + 1 if 0 <= number < 9 else None
            except ValueError:
                level = None
        galleries = [g.attrib.get(W + 'val', '').lower() for a in ancestors if a.tag == W + 'sdt'
                     for g in a.findall(W + 'sdtPr/' + W + 'docPartObj/' + W + 'docPartGallery')]
        deleted = any(a.tag in {W + 'del', W + 'moveFrom'} for a in ancestors)
        text = '' if deleted else _visible_text(paragraph, hidden_resolver, style_id)
        total_text += len(text)
        if total_text > 4 * 1024 * 1024:
            raise ValueError('文档可见文字过多，超出小批次检查限制')
        rows.append({'index': len(rows), 'text': text, 'heading_level': level,
                     'style_id': style_id, 'in_table': any(a.tag == W + 'tbl' for a in ancestors),
                     'in_textbox': any(a.tag == W + 'txbxContent' for a in ancestors),
                     'in_deleted': deleted,
                     'in_toc': paragraph in toc_fields or style_id in toc_styles
                               or bool(re.fullmatch(r'(?:toc|目录)\s*\d*', style_id or '', re.I))
                               or any('table of contents' in s for s in galleries)})
    return rows


def load_teacher_rules(library=None):
    """Verify the frozen reviewed catalog and each original comment quotation."""
    from portable_rules import packaged_mode, load_packaged_rules
    if packaged_mode(ROOT):
        if library is not None:
            raise ValueError('可分发规则模式不接受混入历史教师库。')
        return load_packaged_rules(ROOT)
    if digest(CATALOG) != CATALOG_SHA256:
        raise ValueError('已复核教师规则目录发生变化，需要重新审核')
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    titles = {'XR001': ('NP001', '正文第一人称表述'), 'XR002': ('NP002', '需求分析与系统设计分章'), 'XR003': ('NP003', '跨页表格续表标识')}
    rules = []
    for rule in catalog['rules']:
        rule_id, title = titles[rule['rule_id']]
        evidence = []
        for item in rule['sources']:
            source = item['source']
            path = Path(source['document_path'])
            if digest(path) != source['sha256']:
                raise ValueError('教师证据源文件哈希不符')
            with zipfile.ZipFile(path) as archive:
                comments = _xml(archive.read('word/comments.xml'))
            match = next((c for c in comments if c.attrib.get(W + 'id') == str(item['comment_id'])), None)
            text = _visible_text(match) if match is not None else ''
            if item['teacher_quote'] not in text:
                # Preserve paragraph boundaries in comments as extractors do.
                text = '\n'.join(_visible_text(p) for p in match.findall(W + 'p')) if match is not None else ''
            if item['teacher_quote'] not in text:
                raise ValueError('教师原话不匹配批注原件')
            if digest(path) != source['sha256']:
                raise ValueError('教师证据读取过程中源文件发生变化')
            folder = source['repository_path'].strip('/').split('/')[0]
            student_name = folder.split('_', 1)[-1]
            evidence.append({'student_name': student_name, 'revision': source['revision'], 'comment_text': text,
                             'anchor_text': item.get('original_quote', ''), 'quote': item['teacher_quote'],
                             'source_sha256': source['sha256'], 'evidence_id': item['evidence_id'],
                             'source_path': str(path), 'comment_id': str(item['comment_id']),
                             'repository_path': source['repository_path']})
        rules.append({'id': rule_id, 'title': title, 'scope': rule['scope'], 'teacher_evidence': evidence,
                      'policy': 'teacher_specific_not_universal', 'same_category_feedback_reviewed': True})
    from additional_teacher_checks import load_additional_rules
    rules.extend(load_additional_rules())
    from teacher_rule_library import get_library
    from expanded_paper_checks import load_expanded_rules
    if library is None:
        library = get_library(Path(__file__).resolve().parent)
    families = {r['id']: r for r in library['rules']}
    mapping = {'NP001': 'first_person', 'NP002': 'chapter_structure', 'NP003': 'table_continuation',
               'NP004': 'formula_numbering', 'NP005': 'figure_numbering',
               'NP006': 'table_caption_position', 'NP007': 'image_readability'}
    for rule in rules:
        family = families.get(mapping.get(rule['id']))
        if not family:
            continue
        present = {(e.get('source_sha256'), str(e.get('comment_id'))) for e in rule['teacher_evidence']}
        for evidence in family['teacher_evidence']:
            identity = (evidence.get('source_sha256'), str(evidence.get('comment_id')))
            if identity not in present:
                rule['teacher_evidence'].append(evidence)
                present.add(identity)
        rule['recurrence_status'] = family['recurrence_status']
        rule['supporting_students'] = family['supporting_students']
        rule['family'] = family['id']
    rules.extend(load_expanded_rules(library))
    from teacher_policy_checks import load_policy_rules
    rules.extend(load_policy_rules(library))
    from teacher_format_checks import load_format_rules
    rules.extend(load_format_rules(library))
    from teacher_content_checks import load_content_rules
    rules.extend(load_content_rules(library))
    from teacher_extended_checks import load_extended_rules
    rules.extend(load_extended_rules(library))
    from teacher_page_requirements import load_page_rules
    rules.extend(load_page_rules(library))
    from teacher_remaining_layout_checks import load_remaining_layout_rules
    rules.extend(load_remaining_layout_rules(library))
    from teacher_object_presence_checks import load_object_presence_rules
    rules.extend(load_object_presence_rules(library))
    from teacher_additional_content_checks import load_additional_content_rules
    rules.extend(load_additional_content_rules(library))
    from teacher_semantic_content_checks import load_semantic_content_rules
    from reference_structure_rules import load_structure_rules
    from reference_typography_checks import load_reference_typography_rules
    rules.extend(load_semantic_content_rules(library))
    from remaining_teacher_feedback_checks import load_remaining_feedback_rules
    rules.extend(load_remaining_feedback_rules(library))
    try:
        rules.extend(load_structure_rules(library))
    except (OSError, ValueError, KeyError, TypeError) as error:
        # A changed reference must disable its checks without losing unrelated results.
        rules.extend(dict(id=rid, title=title, enabled=False, family=family,
            teacher_evidence=[], reference_evidence=[], checker='reference_structure_ooxml_v1',
            reference_verification_error=str(error), scope='参考来源重新核验后方可使用。')
            for rid, title, family in [('UP002', '正文奇偶页眉与摘要无页眉', 'page_header'),
                ('UP003', '论文组成顺序', 'document_order'), ('UP004', '分类参考文献数量', 'references')])
    rules.extend(load_reference_typography_rules(library))
    from general_review_rules import attach_general_requirements
    return attach_general_requirements(rules)


def _title(text):
    text = re.sub(r'^\s*(?:第[一二三四五六七八九十百\d]+章|\d+(?:\.\d+)*[.、\s]*)\s*', '', text)
    return re.sub(r'\s+', '', text).strip('：:')


def _quoted(text, offset):
    for opening, closing in [('“', '”'), ('「', '」'), ('『', '』'), ('"', '"')]:
        if opening == closing:
            if text[:offset].count(opening) % 2 and closing in text[offset + 1:]:
                return True
        else:
            left = text.rfind(opening, 0, offset)
            previous_close = text.rfind(closing, 0, offset)
            if left > previous_close and closing in text[offset + 1:]:
                return True
    return False


def execute_checks(path, student_name='', chapter_policy=False, layout_result=None, parsed_source=None, three_line_tables=False, paper_type=''):
    if type(three_line_tables) is not bool:
        raise ValueError('three_line_tables 必须为布尔值；仅明确采用三线表要求时启用。')
    document, styles, sha, image_count = parsed_source if parsed_source is not None else read_docx(path)
    if parsed_source is not None and digest(path) != sha:
        raise ValueError('只读Word快照不属于当前原件')
    validate_document_body(document, styles)
    paragraphs = annotate_paragraphs(extract_paragraphs(document, styles))
    if sum(p['text'].count('我') for p in paragraphs) > 1000:
        raise ValueError('本稿“我”字线索超过 1000 处，超出当前小批次逐项核查限制；请按章节拆分后检查。')
    rules = json.loads(json.dumps(load_teacher_rules(), ensure_ascii=False))
    by_id = {r['id']: r for r in rules}
    findings, coverage, excluded = [], [], []
    headings = [p for p in paragraphs if p['heading_level'] is not None and not p['in_toc'] and not p['in_deleted'] and not p['in_textbox'] and not p['in_table'] and p['text'].strip()]
    tables = list(document.iter(W + 'tbl'))
    def finding(rule_id, title, reason, suggestion, paragraph=None, start=None, end=None, status='candidate', **extra):
        row = {'id': f'{rule_id}-{1 + sum(f["rule_id"] == rule_id for f in findings):04d}', 'rule_id': rule_id,
               'title': title, 'status': status, 'paragraph_index': paragraph['index'] if paragraph is not None else None,
               'text': paragraph['text'] if paragraph is not None else '', 'start': start, 'end': end,
               'reason': reason, 'suggestion': suggestion, 'teacher_evidence': by_id[rule_id]['teacher_evidence'],
               'location': paragraph.get('location') if paragraph is not None else None}
        row.update(extra)
        findings.append(row)
    # Declarations at the front often have no following real heading styles.
    # Exclude their explicit template paragraphs only; never let a declaration
    # title exclude the entire paper when its end cannot be established.
    terminal = {'致谢', '谢辞', '参考文献'}
    sections = [(p['index'], _title(p['text']), p['heading_level']) for p in paragraphs if not p['in_toc'] and not p['in_deleted'] and not p['in_table'] and not p['in_textbox'] and (_title(p['text']) in terminal or p in headings)]
    raw_wo = 0
    for p in paragraphs:
        if p['in_deleted']:
            continue
        section = None
        for index, name, level in sections:
            if index > p['index']:
                break
            if name in terminal:
                section = name
            elif section is not None and level == 1:
                section = None
        accepted = []
        for match in re.finditer('我', p['text']):
            raw_wo += 1
            pos = match.start()
            reason = None
            if p['in_toc']:
                reason = '目录文字移出正文语境检查'
            elif p['in_textbox']:
                reason = '文本框文字的正文归属未确认，移出自动正文候选'
            elif section:
                reason = f'位于已识别的{section}范围，移出本轮正文候选'
            elif p['text'][pos:pos + 2] == '我国' or p['text'][max(0, pos - 1):pos + 1] == '自我':
                reason = '国家称谓或自我概念，不作为作者第一人称代词线索'
            elif declaration_template(p['text']):
                reason = '独创性声明模板文字移出正文检查'
            if reason:
                excluded.append({'paragraph_index': p['index'], 'text': p['text'], 'start': pos, 'end': pos + 1, 'reason': reason})
                continue
            accepted.append(pos)
        if not accepted:
            continue
        # One paragraph is one place to rewrite: several 我/我们 in the same
        # paragraph become one finding, so the teacher-facing report stops
        # restating the same suggestion. Every accepted position stays in
        # `occurrences` and only the first one anchors the evidence span.
        first = accepted[0]
        occurrences = [{'start': pos, 'end': pos + 1, 'quoted_context': _quoted(p['text'], pos)} for pos in accepted]
        quoted = any(row['quoted_context'] for row in occurrences)
        reason = '可见正文中检出“我”字；引用原话保留待核对' if quoted else '可见正文中检出“我”字，需要辨别作者自述与引用、概念用语'
        if len(accepted) > 1:
            reason += f'本段共 {len(accepted)} 处，合为一条改写建议。'
        finding('NP001', '第一人称表述待语境核对', reason,
                '若为作者自述且遵循该教师要求，可改为“本研究”“该模型”等客观表述；引用原话不要直接删除。',
                p, first, first + 1, quoted_context=quoted, occurrences=occurrences)
    has_visible_paragraph_text = any(p['text'].strip() for p in paragraphs if not p['in_textbox'])
    coverage.append({'rule_id': 'NP001', 'title': by_id['NP001']['title'],
                     'status': ('partial' if image_count else 'checked') if has_visible_paragraph_text else 'not_applicable',
                     'detail': (f'可提取文字中“我”字 {raw_wo} 处；声明、致谢或概念用语等排除 {len(excluded)} 处，其余为正文语境候选。页眉、页脚、脚注、图片内文字未扫描。'
                                if has_visible_paragraph_text else '当前正文未提取到可核查的段落文字，本项不适用；表格、图片、公式及文本框内容不据此认定合格。')})
    relevant = [p for p in headings if '需求分析' in _title(p['text']) or any(term in _title(p['text']) for term in ('系统设计', '系统总体设计', '总体设计'))]
    if not chapter_policy:
        finding('NP002', '分章要求适用范围待确认', '该规则来自此教师的系统设计类论文反馈，尚未声明新稿采用同一分章要求。',
                '确认适用该教师的分章要求后，再依据真实标题层级核查。', status='needs_review', relevant_headings=relevant)
        coverage.append({'rule_id': 'NP002', 'title': by_id['NP002']['title'], 'status': 'needs_review', 'detail': '尚未确认本稿适用教师分章要求；未把关键词当作章节结论。'})
    elif not relevant:
        finding('NP002', '未建立相关章节结构', '未从标题样式、大纲及样式继承中提取到需求分析或系统设计相关标题。',
                '核对标题样式和真实目录，并确认论文是否属于适用类型。', status='needs_review')
        coverage.append({'rule_id': 'NP002', 'title': by_id['NP002']['title'], 'status': 'needs_review', 'detail': '未识别到可靠相关标题；不从普通正文关键词猜测章级。'})
    else:
        req = [p for p in relevant if '需求分析' in _title(p['text'])]
        design = [p for p in relevant if any(term in _title(p['text']) for term in ('系统设计', '系统总体设计', '总体设计'))]
        independent_req = [p for p in req if p['heading_level'] == 1 and p not in design]
        independent_design = [p for p in design if p['heading_level'] == 1 and p not in req]
        involved, anchors = [], []
        for p in relevant:
            name = _title(p['text'])
            merged = '需求分析' in name and any(term in name for term in ('系统设计', '系统总体设计', '总体设计'))
            subordinate_without_chapter = p['heading_level'] != 1 and ((p in req and not independent_req) or (p in design and not independent_design))
            if (merged and p['heading_level'] == 1) or subordinate_without_chapter:
                anchors.append(p)
                involved.append({'title': name, 'paragraph_index': p['index'], 'heading_level': p['heading_level'],
                                 'kind': 'compound_heading' if merged and p['heading_level'] == 1 else 'subordinate_heading',
                                 'location': p.get('location')})
        problem = bool(involved)
        if involved:
            # The teacher requirement is one remediation task: 需求分析 and
            # 系统设计 must each be their own chapter. Reporting the composite
            # heading plus every subordinate heading separately restates the same
            # suggestion, so they are merged into one finding that lists each
            # heading that failed the requirement.
            detail = '；'.join(
                f"“{row['title']}”为 {row['heading_level']} 级标题" +
                ('且同时包含需求分析与设计' if row['kind'] == 'compound_heading' else '，未独立成章')
                for row in involved)
            finding('NP002', '相关标题疑似合章或处于下级层次',
                    f'共 {len(involved)} 个相关标题不满足分章要求：' + detail + '。',
                    '按本教师要求核对：需求分析与系统设计应分别为独立章；需确认这些标题是否确实分别承担相应章节内容。',
                    anchors[0], heading_level=involved[0]['heading_level'], headings=involved)
        if not req or not design:
            finding('NP002', '相关章节识别不完整', '只识别到需求分析或设计中的一类标题，不能确定另一类不存在。',
                    '核对目录、标题样式及实际章节内容。', status='needs_review', relevant_headings=relevant)
        coverage.append({'rule_id': 'NP002', 'title': by_id['NP002']['title'], 'status': 'checked' if req and design else 'partial',
                         'detail': f'从真实标题层级提取相关标题 {len(relevant)} 个；' + ('存在结构候选，需核对内容。' if problem else '未检出合章/下级标题线索；不等于整篇合格。')})
    valid_layout = bool(layout_result and layout_result.get('status') == 'native_table_layout_collected_visual_pending' and layout_result.get('source_unchanged') is True and layout_result.get('source', {}).get('sha256') == sha)
    if valid_layout:
        results = layout_result.get('table_results', [])
        before = {t.get('index'): t for t in layout_result.get('tables_before', [])}
        if len(results) != len(tables):
            valid_layout = False
        else:
            for table in results:
                if table.get('result') == 'not_applicable_single_page':
                    continue
                content = before.get(table.get('index'), {})
                text = str(content.get('text', '')).replace('\x07', '').replace('\r', '\n')[:1600]
                preceding = str(content.get('preceding_text', '')).replace('\x07', '').replace('\r', '\n').strip()
                excerpt = '\n'.join(part for part in (preceding, text.strip()) if part)[:1600]
                missing = table.get('result') == 'cross_page_no_continuation_text_clue'
                finding('NP003', '跨页表格未检出续表文字' if missing else '跨页表格续表归属待核验',
                        f'Word 实际物理页 {table.get("first_physical_page")}–{table.get("last_physical_page")}；' + ('未检出续表文字，尚需核对页面。' if missing else '检出的标识尚未绑定到该表格。'),
                        '查看导出的续页证据，核对该表的续表标识和表头；确认缺失后补充。',
                        status='candidate' if missing else 'needs_review', table_index=table.get('index'),
                        table_text=text, physical_pages=[table.get('first_physical_page'), table.get('last_physical_page')],
                        source_page_evidence={'first_physical_page': table.get('first_physical_page'),
                                              'last_physical_page': table.get('last_physical_page'),
                                              'source_sha256': sha, 'pdf': layout_result.get('pdf')})
                findings[-1]['text'] = excerpt
            coverage.append({'rule_id': 'NP003', 'title': by_id['NP003']['title'], 'status': 'partial' if tables or image_count else 'not_applicable',
                             'detail': f'已读取 {len(results)} 张原生表格的实际 Word 分页；跨页结果仍待视觉核验；图片表未覆盖。'})
    if not valid_layout:
        finding('NP003', '表格分页和续表标识待版面核验',
                f'读取到 {len(tables)} 张原生表格、{image_count} 个图片资源；没有与当前源哈希一致的完整 Word 分页证据。',
                '使用本机 Word 渲染当前稿，核对实际跨页位置、续表文字与对象归属；不能用 XML 分页符推测。', status='needs_review')
        coverage.append({'rule_id': 'NP003', 'title': by_id['NP003']['title'], 'status': 'needs_review', 'detail': '尚未取得有效的实际分页证据；原生表格为零不证明没有图片表。'})
    if any(r['id'] == 'NP004' for r in rules):
        from additional_teacher_checks import check_additional_rules
        added_findings, added_coverage = check_additional_rules(document, paragraphs, rules)
        findings.extend(added_findings)
        coverage.extend(added_coverage)
    if any(r['id'] == 'NP008' for r in rules):
        from expanded_paper_checks import check_expanded_rules
        added_findings, added_coverage = check_expanded_rules(document, paragraphs, rules, source_sha256=sha)
        findings.extend(added_findings)
        coverage.extend(added_coverage)
    if any(r['id'] == 'NP014' for r in rules):
        from teacher_policy_checks import check_policy_rules
        bound_layout = dict(layout_result, _verified_source_sha256=sha) if valid_layout else None
        added_findings, added_coverage = check_policy_rules(document, paragraphs, rules, bound_layout)
        findings.extend(added_findings)
        coverage.extend(added_coverage)
    if any(r['id'] == 'NP018' for r in rules):
        from teacher_format_checks import check_format_rules
        added_findings, added_coverage = check_format_rules(document, styles, paragraphs, rules, three_line_tables=three_line_tables)
        findings.extend(added_findings)
        coverage.extend(added_coverage)
    if digest(path) != sha:
        raise ValueError('检查期间源文件发生变化')
    from teacher_content_checks import check_content_rules
    added_findings, added_coverage = check_content_rules(paragraphs, rules, sha)
    findings.extend(added_findings)
    coverage.extend(added_coverage)
    from teacher_extended_checks import check_extended_rules
    added_findings, added_coverage = check_extended_rules(document, paragraphs, rules, sha)
    findings.extend(added_findings)
    coverage.extend(added_coverage)
    from teacher_page_requirements import check_page_rules
    added_findings, added_coverage = check_page_rules(document, styles, paragraphs, rules, sha,
                                                   layout_result if valid_layout else None)
    findings.extend(added_findings)
    coverage.extend(added_coverage)
    from teacher_remaining_layout_checks import check_remaining_layout_rules
    added_findings, added_coverage = check_remaining_layout_rules(document, paragraphs, rules, sha,
                                                                 layout_result if valid_layout else None)
    findings.extend(added_findings)
    coverage.extend(added_coverage)
    from teacher_object_presence_checks import check_object_presence
    added_findings, added_coverage = check_object_presence(document, paragraphs, rules, sha)
    findings.extend(added_findings)
    coverage.extend(added_coverage)
    from teacher_remaining_format_checks import collect_remaining_format_diagnostics
    from teacher_additional_content_checks import check_additional_content
    added_findings, added_coverage = check_additional_content(paragraphs, rules, sha)
    findings.extend(added_findings)
    coverage.extend(added_coverage)
    from teacher_semantic_content_checks import check_semantic_content
    from reference_structure_rules import check_structure_rules
    from reference_typography_checks import check_reference_typography
    from remaining_teacher_feedback_checks import check_remaining_feedback
    for check_findings, check_coverage in (
        check_semantic_content(paragraphs, rules, sha),
        check_structure_rules(document, styles, paragraphs, rules, sha, source_path=None if parsed_source is not None else path, paper_type=paper_type),
        check_reference_typography(document, styles, paragraphs, rules),
        check_remaining_feedback(document, styles, paragraphs, rules, sha),
    ):
        findings.extend(check_findings)
        coverage.extend(check_coverage)
    from portable_rules import packaged_mode, BOUNDARY
    if packaged_mode(ROOT):
        format_diagnostics = collect_remaining_format_diagnostics(document, styles, paragraphs, {'rules': []}, sha)
        format_diagnostics['configuration_boundary'] = BOUNDARY
    else:
        from teacher_rule_library import get_library
        format_diagnostics = collect_remaining_format_diagnostics(document, styles, paragraphs, get_library(), sha)
        from reference_standards import attach_reference_parameters, reference_standards_view
        attach_reference_parameters(format_diagnostics, reference_standards_view())
    if digest(path) != sha:
        raise ValueError('检查期间源文件发生变化')
    return enrich_report_locations({'schema_version': 2, 'body_view_version': BODY_VIEW_VERSION,
                                   'body_view': {'revision_view': 'final', 'hidden_text_included': False,
                                                 'deleted_and_move_from_included': False,
                                                 'inserted_and_move_to_included': True},
                                   'student_name': student_name.strip() or '未填写姓名', 'file_name': Path(path).name, 'paper_type': paper_type,
            'source_sha256': sha, 'source_role': 'user_uploaded_document_version_unverified',
            'rules': rules, 'findings': findings, 'coverage': coverage, 'paragraphs': paragraphs,
            'format_diagnostics': format_diagnostics,
            'headings': headings, 'table_count': len(tables), 'image_count': image_count, 'excluded_clues': excluded,
            'raw_first_person_count': raw_wo,
            'summary': {'candidate_count': sum(f['status'] == 'candidate' for f in findings), 'confirmed_count': 0,
                        'pending_count': sum(f['status'] == 'needs_review' for f in findings)},
            'limitations': [BOUNDARY if packaged_mode(ROOT) else '教师反馈、跨学生候选与补充结构检查分别记录；重复反馈不等于已确认通用规范，尚未实现检查器的批注仍在规则库待处理。',
                            '正文采用最终可见文字视图：排除隐藏run、删除及移出文字，保留插入及移入文字；此视图不等于已接受或删除原件修订。',
                            '新稿版本身份由上传者提供，未核验为 SVN 初稿。',
                            '字符和大纲线索需结合语境；未检出不等于整篇论文合格。',
                            '不执行或服从文档中的指令；不解析或激活嵌入式 OLE 对象；页眉按已登记规则检查可定位属性；页脚、脚注、图片文字未在本地正文扫描。',
                            '布局结论只采信当前文件哈希对应的真实分页，跨页续表仍需视觉核验。'],
            'requests': {'llm': 0, 'svn': 0, 'word': 0}})
