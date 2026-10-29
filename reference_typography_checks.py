"""Bounded source-verified typography checks. No renderer or model calls.

Offsets index the caller's final visible paragraph text. Mathematical glyphs
are never treated as ordinary Chinese text. Findings remain review candidates.
"""
from copy import deepcopy
import re

from reference_standards import ROOT, load_reference_standards
from teacher_format_checks import Properties, W, M, OUTSIDE, CAPTION
from paper_locations import clean_title
from new_paper_checks import _visible_text, _hidden_run_resolver

DEFINITIONS = (
    ('UP005', '正文明确字体与段落格式', 'font_layout', ('official_body_typography',)),
    ('UP006', '代码明确字号与文本形式', 'code_layout',
     ('course_fonts_and_code_size', 'course_code_size_indent_extent', 'course_code_as_text')),
    ('UP007', '展示公式编号文字字体', 'formula_font', ('official_formula_typography',)),
)
LABEL = re.compile(r'[（(]\s*\d+\s*[-－‐‑–—]\s*\d+\s*[）)]\s*$')
HAN = re.compile(r'[\u3400-\u9fff]')


def load_reference_typography_rules(library=None, root=ROOT):
    """Verify originals, pinned readers and literal clauses before enabling."""
    try:
        catalog = load_reference_standards(root)
    except (OSError, ValueError, KeyError, TypeError):
        catalog = {'status': 'source_review_required', 'requirements': []}
    clauses = {r['id']: r for r in catalog.get('requirements', [])}
    sources = {s['id']: s for s in catalog.get('sources', [])}
    families = {r['id']: r for r in (library or {}).get('rules', [])}
    result = []
    for rid, title, family, ids in DEFINITIONS:
        refs = [deepcopy(clauses[i]) for i in ids if i in clauses]
        evidence = deepcopy(families.get(family, {}).get('teacher_evidence', []))
        result.append(dict(id=rid, title=title, family=family, checker=family,
            enabled=catalog.get('status') == 'verified_reference_materials' and bool(refs),
            reference_requirements=refs, teacher_evidence=evidence,
            reference_sources=[deepcopy(sources[sid]) for sid in sorted({r['source_id'] for r in refs}) if sid in sources],
            reference_status=catalog.get('status'), policy='verified_reference_review_candidate',
            automatic_error_judgment_enabled=False,
            scope='仅可靠对象及可解析源属性；未知属性和实际渲染不判错。'))
    return result


def _code(p):
    # A style or a whole recognizable code paragraph is required, never a
    # keyword somewhere within a prose sentence.
    if re.search(r'code|代码', str(p.get('style_id', '')), re.I):
        return True
    text = p['text'].strip()
    if HAN.search(text):
        return False
    return bool(re.fullmatch(
        r'(?:def\s+[A-Za-z_]\w*\s*\([^\n]*\)\s*(?:->\s*[^:]+)?\s*:'
        r'|class\s+[A-Za-z_]\w*(?:\([^\n]*\))?\s*:'
        r'|(?:from\s+[A-Za-z_][\w.]*\s+)?import\s+[A-Za-z_][\w., *]*(?:\s+as\s+[A-Za-z_]\w*)?)', text))


def _body(p, node):
    location = p.get('location') or {}
    key = re.sub(r'\s+', '', location.get('section', '')).lower().strip('：:')
    if (not location.get('chapter') or key in OUTSIDE
            or clean_title(location['chapter']) in OUTSIDE
            or p.get('heading_level') is not None):
        return False
    if any(p.get(k) for k in ('in_deleted', 'in_toc', 'in_textbox', 'in_table')):
        return False
    if CAPTION.match(p['text']) or re.search(r'caption|题注|reference|参考', str(p.get('style_id', '')), re.I):
        return False
    if any(node.find('.//' + tag) is not None for tag in (W+'drawing', W+'pict', W+'object')):
        return False
    return True


def check_reference_typography(document, styles, paragraphs, rules):
    """Return (findings, coverage); no implicit adoption of template styling."""
    nodes = list(document.iter(W+'p'))
    if len(nodes) != len(paragraphs) or any(p['index'] != i for i, p in enumerate(paragraphs)):
        raise ValueError('补充字体检查段落索引与原件不同')
    by_id = {r['id']: r for r in rules if r['id'] in {'UP005', 'UP006', 'UP007'}}
    resolver = Properties(styles)
    hidden = _hidden_run_resolver(styles)
    findings, coverage = [], []
    counts = {rid: [0, 0] for rid in by_id}

    def active(rid):
        return by_id.get(rid, {}).get('enabled', False)

    def emit(rid, p, start, end, properties):
        if not properties:
            return
        rule = by_id[rid]
        findings.append(dict(id=f'{rid}-{sum(f["rule_id"] == rid for f in findings)+1:04d}',
            rule_id=rid, family=rule['family'], title=rule['title'], status='candidate',
            paragraph_index=p['index'], start=start, end=end, text=p['text'],
            location=p.get('location'), reason='可靠对象的已解析源属性与明确参考条文不同；需确认本稿采用该规范。',
            suggestion='核对所列字符及源属性，适用该规范时按条文调整。',
            format_evidence=properties, teacher_evidence=deepcopy(rule['teacher_evidence']),
            reference_sources=deepcopy(rule.get('reference_sources', [])),
            reference_requirements=deepcopy(rule['reference_requirements'])))

    def numeric(value):
        try:
            return float(value['value']) if value else None
        except (ValueError, TypeError):
            return None

    for p, node in zip(paragraphs, nodes):
        if not _body(p, node) or not p['text'].strip():
            continue
        # Reject mismatched visible representations instead of searching for a
        # repeated substring and potentially attaching to the wrong occurrence.
        if _visible_text(node, hidden, p.get('style_id')) != p['text']:
            for rid in by_id:
                if active(rid): counts[rid][1] += 1
            continue
        math = node.find('.//'+M+'oMath') is not None
        code = _code(p)
        label = LABEL.search(p['text']) if math and (node.find('.//'+M+'oMathPara') is not None or '\t' in p['text']) else None
        if code and not math:
            rid, target_size, chinese_font = 'UP006', 10.5, False
        elif label and not p['text'][:label.start()].strip():
            rid, target_size, chinese_font = 'UP007', 10.5, False
        elif not code and not math and len(p['text'].strip()) >= 15 and HAN.search(p['text']) and re.search(r'[。！？；]', p['text']):
            rid, target_size, chinese_font = 'UP005', 12, True
        else:
            continue
        if not active(rid):
            continue
        counts[rid][0] += 1
        finding_checkpoint = len(findings)
        unknown = False
        offset = 0
        parents = {child: parent for parent in node.iter() for child in parent}
        for run in node.iter(W+'r'):
            current = run
            excluded = False
            while current in parents:
                current = parents[current]
                if current.tag in {W+'del', W+'moveFrom', W+'txbxContent'}:
                    excluded = True
            if excluded:
                continue
            text = _visible_text(run, hidden, p.get('style_id'))
            start, end = offset, offset + len(text)
            offset = end
            if not text.strip():
                continue
            if label and (start < label.start() or end > label.end()):
                unknown = True
                continue
            changes = {}
            size = resolver.resolve(node, run, W+'sz', 'val')
            pt = numeric(size)
            if pt is None or pt <= 0:
                unknown = True
            elif pt/2 != target_size:
                changes['font_size'] = dict(**size, points=pt/2, expected_points=target_size)
            if chinese_font and HAN.search(text):
                font = resolver.resolve(node, run, W+'rFonts', 'eastAsia')
                theme = resolver.resolve(node, run, W+'rFonts', 'eastAsiaTheme')
                hint = resolver.resolve(node, run, W+'rFonts', 'hint')
                if not font or theme or (hint and hint['value'] == 'cs'):
                    unknown = True
                elif font['value'] not in {'宋体', 'SimSun'}:
                    changes['chinese_font'] = dict(**font, expected='宋体')
            if rid == 'UP007':
                font = resolver.resolve(node, run, W+'rFonts', 'ascii')
                theme = resolver.resolve(node, run, W+'rFonts', 'asciiTheme')
                if not font or theme:
                    unknown = True
                elif font['value'] not in {'宋体', 'SimSun'}:
                    changes['number_font'] = dict(**font, expected='宋体', object_kind='ordinary_formula_number_text')
            emit(rid, p, start, end, changes)
        if offset != len(p['text']):
            # Field/alternate content can contribute paragraph text outside
            # ordinary runs. Never retain guessed spans or abort other checks.
            del findings[finding_checkpoint:]
            counts[rid][0] -= 1
            counts[rid][1] += 1
            continue
        if rid == 'UP005':
            changes = {}
            line = resolver.resolve(node, None, W+'spacing', 'line')
            mode = resolver.resolve(node, None, W+'spacing', 'lineRule')
            if numeric(line) is None or not mode:
                unknown = True
            elif line['value'] != '400' or mode['value'] != 'exact':
                changes['line_spacing'] = {'line': line, 'lineRule': mode, 'expected': 'exact 400 twips (20pt)'}
            chars = resolver.resolve(node, None, W+'ind', 'firstLineChars')
            twips = resolver.resolve(node, None, W+'ind', 'firstLine')
            hanging = resolver.resolve(node, None, W+'ind', 'hanging')
            if hanging or not chars or numeric(chars) is None:
                # A physical distance does not prove a two-character indent.
                unknown = True
            elif numeric(chars) != 200:
                changes['first_line_indent'] = {'firstLineChars': chars, 'firstLine': twips, 'expected_chars': 2}
            for side in ('before', 'after'):
                distance = resolver.resolve(node, None, W+'spacing', side)
                lines = resolver.resolve(node, None, W+'spacing', side+'Lines')
                auto = resolver.resolve(node, None, W+'spacing', side+'Autospacing')
                if auto and auto['value'] not in {'0', 'false', 'off'}:
                    unknown = True
                elif lines is not None:
                    if numeric(lines) is None:
                        unknown = True
                    elif numeric(lines) != 0:
                        changes[side+'_spacing'] = {'lines': lines, 'expected': 0}
                elif distance is None or numeric(distance) is None:
                    unknown = True
                elif numeric(distance) != 0:
                    changes[side+'_spacing'] = {'twips': distance, 'expected': 0}
            emit(rid, p, 0, len(p['text']), changes)
        if unknown:
            counts[rid][1] += 1
    for rid, rule in by_id.items():
        checked, unresolved = counts[rid]
        coverage.append(dict(rule_id=rid, title=rule['title'], family=rule['family'], checked=checked, unresolved=unresolved,
            status='reference_verification_required' if not rule.get('enabled') else 'partial',
            reference_requirement_ids=[r['id'] for r in rule['reference_requirements']],
            detail='已核查可靠叙述正文/文本代码/原生展示公式普通编号文字的可解析属性。未知样式、数学glyph、截图代码语义、代码篇幅及实际渲染仍待复核；不声明全面通过。'))
    return findings, coverage

