"""Additional teacher-backed clues. No network, Word, LLM, or rewrites.

These are transferable *review candidates* from one student's feedback, not
proven common errors. XML order does not establish printed layout or readability.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
SOURCE = Path(__file__).resolve().parent / 'data/pipeline_runs/new_batch_20261007_205126_048522/export/P001/teacher_exports/P001_teacher_r247_02.docx'
SOURCE_SHA = '8cb986522ea4724c3d98171489f38a9f4bdad9b8bca776727a5efe66bbcf36db'
NUMBER = r'\d+\s*[-‐‑–—－]\s*\d+'
FORMULA_LABEL = re.compile(r'(?:公式\s*)?[（(]\s*(' + NUMBER + r')\s*[）)]')
CAPTION = re.compile(r'^\s*([图表])\s*(' + NUMBER + r')\s*(\S.*)$')
OUTSIDE = {'致谢', '谢辞', '参考文献', '目录', '独创性声明', '原创性声明', '学术诚信声明'}
MAX_VISUAL_ITEMS = 80


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _visible(node):
    if node.tag in {W + 'del', W + 'moveFrom', W + 'txbxContent'}:
        return ''
    return (node.text or '') if node.tag == W + 't' else ''.join(_visible(child) for child in node)


def load_additional_rules():
    """Validate every quote against pinned original comments.xml, fail closed."""
    if _digest(SOURCE) != SOURCE_SHA:
        raise ValueError('新增规则的教师证据原件哈希不符')
    with zipfile.ZipFile(SOURCE) as archive:
        raw = archive.read('word/comments.xml')
    if len(raw) > 16 * 1024 * 1024 or any(tag in raw.replace(b'\x00', b'').upper() for tag in (b'<!DOCTYPE', b'<!ENTITY')):
        raise ValueError('新增规则的批注XML超出安全读取范围')
    root = ET.fromstring(raw)
    comments = {c.attrib.get(W + 'id'): '\n'.join(_visible(p) for p in c.findall(W + 'p')) for c in root}
    definitions = [
        ('NP004', '公式重复与逆序编号', '仅原生OMML公式附近明确括号编号；图片公式、正文引用、连续性和所有公式是否必须编号未覆盖。',
         [('2', '另外公式是2-1和2-2'), ('4', '应该是按照顺序编号下去')], 'formula_numbering'),
        ('NP005', '图题重复编号', '明确图题、附近有图像对象或题注样式；正文图引用不参加重复统计。正确章号及命名规范仍待确认。',
         [('9', '编号错误')], 'figure_numbering'),
        ('NP006', '表题位于表格上方', '紧邻原生表格的独立表题，仅检查XML内容顺序；真实版面上下位置和图片表仍待视觉核验。',
         [('10', '放在表上面')], 'table_caption_position'),
        ('NP007', '图中文字可读性', '绑定图题和原始图片，按实际图片类型、清晰度及文档显示尺寸进行有限视觉审阅；不因尺寸或教师曾要求放大其他图就判错。',
         [('8', '数据只要展示部分即可，把图片放大。其他的图也是一样')], 'image_readability'),
    ]
    rules = []
    for rid, title, scope, refs, checker in definitions:
        evidence = []
        for cid, quote in refs:
            text = comments.get(cid, '')
            if quote not in text:
                raise ValueError('新增规则教师原话不匹配原件：' + cid)
            evidence.append({'student_name': '黄发平', 'revision': 247, 'comment_text': text,
                             'quote': quote, 'source_sha256': SOURCE_SHA, 'source_path': str(SOURCE),
                             'comment_id': cid, 'evidence_id': f'{SOURCE_SHA}:r247:comment:{cid}',
                             'repository_path': '/211706315_黄发平/论文/211706315_黄发平_基于深度学习的股价预测模型.docx'})
        rules.append({'id': rid, 'title': title, 'scope': scope, 'teacher_evidence': evidence,
                      'policy': 'teacher_specific_not_universal', 'checker': checker,
                      'supporting_students': ['黄发平'], 'recurrence_status': 'single_student_feedback',
                      'same_category_feedback_reviewed': False, 'automatic_error_judgment_enabled': False})
    if _digest(SOURCE) != SOURCE_SHA:
        raise ValueError('新增规则证据原件在读取中发生变化')
    return rules


def _eligible(p):
    if any(p.get(flag) for flag in ('in_deleted', 'in_toc', 'in_textbox', 'in_table')):
        return False
    section = re.sub(r'\s+', '', str(p.get('location', {}).get('section', '')))
    section = re.sub(r'^第[一二三四五六七八九十\d]+章', '', section)
    return section not in OUTSIDE


def _number(text):
    return tuple(int(x) for x in re.split(r'[-‐‑–—－]', re.sub(r'\s+', '', text)))


def check_additional_rules(document, paragraphs, rules):
    """Return findings and coverage using the same indexed paragraphs as caller."""
    by_id = {r['id']: r for r in rules}
    xml_paragraphs = list(document.iter(W + 'p'))
    if len(xml_paragraphs) != len(paragraphs):
        raise ValueError('新增检查的段落索引与DOCX原件不一致，不能生成位置')
    findings, coverage = [], []

    def add(rid, p, title, reason, suggestion, start=None, end=None, status='candidate', **extra):
        row = {'id': f'{rid}-{1 + sum(f["rule_id"] == rid for f in findings):04d}', 'rule_id': rid,
               'title': title, 'status': status, 'paragraph_index': p['index'], 'text': p['text'],
               'start': start, 'end': end, 'reason': reason, 'suggestion': suggestion,
               'location': p.get('location'), 'teacher_evidence': by_id[rid]['teacher_evidence']}
        row.update(extra)
        findings.append(row)

    image_nodes = {i for i, node in enumerate(xml_paragraphs) if node.find('.//' + W + 'drawing') is not None or node.find('.//' + W + 'pict') is not None}
    eligible = {p['index'] for p in paragraphs if _eligible(p)}
    formulas = []
    formula_count = 0
    for p, node in zip(paragraphs, xml_paragraphs):
        if p['index'] not in eligible or node.find('.//' + M + 'oMath') is None:
            continue
        # Inline math appearing in a long explanation is not a displayed formula.
        displayed = node.find('.//' + M + 'oMathPara') is not None or '\t' in p['text']
        if not displayed:
            continue
        formula_count += 1
        for match in FORMULA_LABEL.finditer(p['text']):
            # Only a terminal label beside formula content; references are excluded.
            if p['text'][match.end():].strip(' \t\r\n'):
                continue
            formulas.append((p, match, _number(match.group(1))))
    groups = defaultdict(list)
    for row in formulas:
        groups[row[2]].append(row)
    emitted = set()
    for label, rows in groups.items():
        if len(rows) < 2:
            continue
        for p, match, _ in rows:
            add('NP004', p, '不同公式出现相同编号', f'已绑定OMML的公式编号{label[0]}-{label[1]}出现{len(rows)}次；需确认是否为同一公式重列或独立编号范围。',
                '核对这些公式的身份与章节编号范围；若为同一范围的不同公式，按教师要求调整顺序编号。', match.start(), match.end(),
                related_paragraph_indices=[row[0]['index'] for row in rows])
            emitted.add(p['index'])
    previous = {}
    for p, match, label in formulas:
        chapter = (p.get('location', {}).get('chapter'), label[0])
        prior = previous.get(chapter)
        if prior and label[1] < prior[2][1] and p['index'] not in emitted:
            add('NP004', p, '公式编号出现逆序', f'同一已识别章节中，前一公式编号为{prior[2][0]}-{prior[2][1]}，此处为{label[0]}-{label[1]}。',
                '核对是否确属同一编号范围，再按实际公式顺序修改；不根据跳号直接判缺失。', match.start(), match.end())
        previous[chapter] = (p, match, label)
    coverage.append({'rule_id': 'NP004', 'title': by_id['NP004']['title'], 'status': 'partial' if formula_count or image_nodes else 'not_applicable',
                     'detail': f'识别原生展示公式{formula_count}段，绑定明确编号{len(formulas)}处；已检查重复与逆序。正文引用不计入；图片公式、无编号公式和跳号不作错误判断。'})

    captions = []
    for p in paragraphs:
        if p['index'] not in eligible:
            continue
        match = CAPTION.match(p['text'])
        if not match or len(p['text']) > 100 or re.search(r'[。！？；]', match.group(3)):
            continue
        if match.group(1) == '图':
            neighbors = [i for i in image_nodes if abs(i - p['index']) <= 2 and i in eligible]
            caption_style = str(p.get('style_id', '')).lower() in {'caption', '题注'}
            # No-space "图3-1显示..." is often prose. Require a real caption
            # style or a separator after the number before treating it as a title.
            separated = bool(p['text'][match.end(2):match.start(3)])
            if not caption_style and not separated:
                continue
            if neighbors or caption_style:
                captions.append((p, match, neighbors))
    figure_groups = defaultdict(list)
    for p, match, neighbors in captions:
        figure_groups[(p.get('location', {}).get('chapter'), _number(match.group(2)))].append((p, match))
    for (_, number), rows in figure_groups.items():
        if len(rows) < 2:
            continue
        for p, match in rows:
            add('NP005', p, '图题出现重复编号', f'同一章节范围内，图题编号{number[0]}-{number[1]}出现{len(rows)}次；正文图引用未计入。',
                '核对是否为不同图、续图或同一图的重复展示；确认不同图重号后按教师编号要求修改。', match.start(2), match.end(2),
                related_paragraph_indices=[row[0]['index'] for row in rows])
    coverage.append({'rule_id': 'NP005', 'title': by_id['NP005']['title'], 'status': 'partial' if captions else 'not_applicable',
                     'detail': f'定位明确图题{len(captions)}处，检查同一章节重号；普通正文引用、图中文字和正确章号规范未覆盖。单学生反馈不标记为常见问题。'})

    parents = {child: node for node in document.iter() for child in node}
    table_count, bound_captions = 0, 0
    for table in document.iter(W + 'tbl'):
        parent = parents.get(table)
        if parent is None or parent.tag == W + 'tc':
            continue
        table_count += 1
        siblings = list(parent)
        pos = siblings.index(table)
        nearby = {}
        for direction in (-1, 1):
            cursor = pos + direction
            while 0 <= cursor < len(siblings):
                node = siblings[cursor]
                if node.tag != W + 'p':
                    break
                pi = next((i for i, candidate in enumerate(xml_paragraphs) if candidate is node), None)
                p = paragraphs[pi] if pi is not None else None
                if p is None:
                    break
                if not p['text'].strip():
                    cursor += direction
                    continue
                match = CAPTION.match(p['text'])
                if pi in eligible and match and match.group(1) == '表' and len(p['text']) <= 100 and not re.search(r'[。！？；]', match.group(3)):
                    nearby[direction] = (p, match)
                    bound_captions += 1
                break
        if 1 in nearby and -1 not in nearby:
            p, match = nearby[1]
            add('NP006', p, '表题在XML顺序中位于表格之后', '原生表格之后紧邻该独立表题，上方未检出紧邻表题；XML顺序只是版面位置线索。',
                '在原稿打印布局核对表题位置；确认位于表下后，依教师要求移到表上方。', match.start(), match.end(), status='needs_review')
    coverage.append({'rule_id': 'NP006', 'title': by_id['NP006']['title'], 'status': 'partial' if table_count else 'not_applicable',
                     'detail': f'核对{table_count}张原生表格相邻内容，绑定表题{bound_captions}处；只采集表后标题线索，未确认实际分页位置，未覆盖图片表。'})

    visual = [(p, match, nodes) for p, match, nodes in captions if nodes]
    for p, match, nodes in visual[:MAX_VISUAL_ITEMS]:
        add('NP007', p, '图中文字可读性审阅', '已定位图题和附近图像对象；需绑定原图后实际检查文字、线条与局部清晰度，不能仅由图题或大小判错。',
            '等待实际图片审阅；未送入模型的对象仅标记待审阅，不预先要求放大。',
            match.start(), match.end(), status='needs_review', image_paragraph_indices=nodes)
    coverage.append({'rule_id': 'NP007', 'title': by_id['NP007']['title'], 'status': 'visual_review_required' if visual else 'not_applicable',
                     'located_count':len(visual),'expanded_count':min(len(visual),MAX_VISUAL_ITEMS),
                     'detail': f'定位带图像对象的图题{len(visual)}处，列出前{min(len(visual), MAX_VISUAL_ITEMS)}处待绑定原图和视觉审阅；其余{max(0, len(visual) - MAX_VISUAL_ITEMS)}处未展开。尚无视觉判断；无图题图片不生成问题。'})
    return findings, coverage
