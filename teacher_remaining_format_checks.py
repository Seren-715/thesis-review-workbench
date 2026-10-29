"""Local formatting diagnostics when teacher feedback omits a usable standard.

This is an evidence collection capability, not an enabled error checker. It
never invents a font, point size, code style or text color and emits no finding
cards. Full context and source properties can be inspected before a teacher or
template supplies the missing requirements. No file, Word, SVN or model I/O.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import unicodedata

from teacher_extended_checks import collect_extended_scope
from content_review_context import CHUNK_CHARACTERS
from teacher_format_checks import CODE, Properties, W, M

VERSION = 'remaining_format_diagnostics_v1'
FAMILIES = ('font_layout', 'code_layout', 'formula_font', 'text_color')
HIDDEN = {W+'del', W+'moveFrom', W+'txbxContent'}
CODE_EXTRA = re.compile(r'^\s*(?:</?[A-Za-z][\w:-]*(?:\s|>)|(?:const|let|var)\s+\w+\s*=|//|\w+\([^\n]*\)\s*\{)', re.M)


def load_remaining_format_capabilities(library):
    """Keep a diagnostic capability distinct from a reliable rule association."""
    rows = {r['id']: r for r in library.get('rules', [])}
    result = []
    for family in FAMILIES:
        evidence = deepcopy(rows.get(family, {}).get('teacher_evidence', []))
        text_evidence, object_evidence = [], []
        for item in evidence:
            anchor = item.get('anchor', {})
            quote = item.get('quote', '')
            image_request = bool(re.search(r'白底|黑底|底纹|用例.*上色|其他的图.*上色', quote))
            # A caption anchor often points to a nearby image. Its own text
            # color is not the target of a request about the image background.
            image_request |= bool(anchor.get('media')) and not re.search(r'字(?:体)?(?:的)?颜色', quote)
            (object_evidence if image_request else text_evidence).append(item)
        result.append(dict(family=family, title=rows.get(family, {}).get('title', family),
            capability='source_property_and_complete_context_collection',
            capability_available=bool(evidence), enabled=False,
            reliable_rule_association=False, automatic_error_judgment_enabled=False,
            standard_status='missing_explicit_parameters', teacher_evidence=evidence,
            text_teacher_evidence=text_evidence, object_teacher_evidence=object_evidence,
            limitation='教师原话未给出目标字号、字体、颜色或可执行的代码排版标准；属性差异不能直接判错。'))
    return result


def _hidden(node, parents):
    current = node
    while current is not None:
        if current.tag in HIDDEN:
            return True
        current = parents.get(current)
    return False


def _text(node, parents):
    values = []
    for child in node.iter():
        if _hidden(child, parents):
            continue
        if child.tag in {W+'t', M+'t'}:
            values.append(child.text or '')
        elif child.tag == W+'tab':
            values.append('\t')
        elif child.tag in {W+'br', W+'cr'}:
            values.append('\n')
    return ''.join(values)


def _properties(properties, p, run):
    output = {}
    for name, route, attribute in (
        ('font_size_half_points', W+'sz', 'val'),
        ('font_ascii', W+'rFonts', 'ascii'), ('font_east_asia', W+'rFonts', 'eastAsia'),
        ('font_ascii_theme', W+'rFonts', 'asciiTheme'),
        ('font_east_asia_theme', W+'rFonts', 'eastAsiaTheme'),
        ('color', W+'color', 'val'), ('color_theme', W+'color', 'themeColor'),
    ):
        value = properties.resolve(p, run, route, attribute)
        if value is not None:
            output[name] = value
    # Theme slots require the package theme and a renderer. A fallback RGB or
    # font in XML must not be presented as its proven effective display value.
    output['theme_dependent'] = any(k in output for k in ('font_ascii_theme', 'font_east_asia_theme', 'color_theme'))
    output['effective_display_verified'] = False
    return output


def _complete_context(paragraphs, family, source_sha256):
    # The prompts selector is intentionally subject-specific and can exclude
    # code under headings such as "用户管理模块". Formatting evidence instead
    # retains the complete visible body plus appendix, without keyword gating.
    selected, scope = collect_extended_scope(paragraphs, 'body', family, source_sha256)
    appendix, appendix_scope = collect_extended_scope(paragraphs, 'prompts', family, source_sha256)
    combined = {p['index']: p for p in selected + appendix}
    selected = [combined[i] for i in sorted(combined)]
    headings = {h['paragraph_index']: h for h in scope['headings'] + appendix_scope['headings']}
    chunks = []
    for p in selected:
        for start in range(0, len(p['text']), CHUNK_CHARACTERS):
            end = min(start + CHUNK_CHARACTERS, len(p['text']))
            chunks.append(dict(id=f'{family}-p{p["index"]}-{start}-{end}', paragraph_index=p['index'],
                start=start, end=end, text=p['text'][start:end], location=p.get('location'),
                role='heading' if p['index'] in headings else 'table_cell' if p.get('in_table') else 'body'))
    scope.update(selector='complete_visible_body_and_appendix', chunks=chunks, total_chunks=len(chunks),
        paragraph_indices=[p['index'] for p in selected], total_paragraphs=len(selected),
        total_characters=sum(len(p['text']) for p in selected),
        headings=[headings[i] for i in sorted(headings)], heading_indices=sorted(headings),
        scope_sha256=hashlib.sha256('\n'.join(f'{p["index"]}:{p["text"]}' for p in selected).encode('utf-8')).hexdigest())
    return selected, scope


def collect_remaining_format_diagnostics(document, styles, paragraphs, library, source_sha256):
    """Return serializable capabilities, scopes, property records and coverage.

    Paragraph indices must be those of the current source XML. There are no
    synthetic NP rules and no candidate/manual-review findings. Integrators may
    save the result separately from enabled checker items and their counts.
    """
    nodes = list(document.iter(W+'p'))
    if len(nodes) != len(paragraphs) or any(p.get('index') != i for i, p in enumerate(paragraphs)):
        raise ValueError('格式属性采集段落索引与当前原件不一致')
    if not isinstance(source_sha256, str) or not source_sha256:
        raise ValueError('格式属性采集必须绑定当前原件来源哈希')
    parents = {child: parent for parent in document.iter() for child in parent}
    props = Properties(styles)
    capabilities = load_remaining_format_capabilities(library)
    sources, coverage = {}, []
    for capability in capabilities:
        family = capability['family']
        if not capability['capability_available']:
            coverage.append(dict(family=family, status='not_applicable', detail='没有同类教师反馈，未采集或新增格式标准。'))
            continue
        selected, scope = _complete_context(paragraphs, family, source_sha256)
        records = []
        for p in selected:
            if p.get('heading_level') is not None:
                continue
            node = nodes[p['index']]
            if _hidden(node, parents):
                continue
            code = bool(CODE.search(p['text']) or CODE_EXTRA.search(p['text']) or re.search(r'code|代码', str(p.get('style_id', '')), re.I))
            maths = [n for n in node.iter(M+'oMath') if not _hidden(n, parents)]
            if family in {'font_layout', 'code_layout'} and not code:
                continue
            if family == 'formula_font' and not maths:
                continue
            if family == 'text_color' and not capability['text_teacher_evidence']:
                continue
            targets = list(node.iter(W+'r')) if family != 'formula_font' else [r for math in maths for r in math.iter(M+'r')]
            runs = []
            for run in targets:
                text = _text(run, parents)
                if not text.strip():
                    continue
                values = _properties(props, node, run)
                if family == 'formula_font':
                    values['math_style'] = {n.tag.split('}')[-1]: n.get(M+'val') for n in run.findall(M+'rPr/*')}
                    values['unicode_math_characters'] = [dict(character=c, code_point=f'U+{ord(c):04X}', name=unicodedata.name(c, 'UNKNOWN'))
                        for c in text if unicodedata.name(c, '').startswith('MATHEMATICAL') or '\u2100' <= c <= '\u214f']
                runs.append(dict(text=text, properties=values))
            if not runs:
                continue
            paragraph_properties = {}
            for key, route, attribute in (
                ('alignment', W+'jc', 'val'), ('first_line_indent', W+'ind', 'firstLine'),
                ('left_indent', W+'ind', 'left'), ('line_spacing', W+'spacing', 'line'),
                ('line_spacing_rule', W+'spacing', 'lineRule'), ('space_after', W+'spacing', 'after'),
            ):
                value = props.resolve(node, None, route, attribute)
                if value is not None:
                    paragraph_properties[key] = value
            records.append(dict(paragraph_index=p['index'], text=p['text'], location=p.get('location'),
                object_kind='native_math' if maths and family == 'formula_font' else 'code' if code else 'text',
                in_table=bool(p.get('in_table')), native_formula_count=len(maths),
                paragraph_properties=paragraph_properties, runs=runs, error_judgment=False))
        sources[family] = dict(content_scope=scope, source_property_records=records,
            property_records_sha256=hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(',',':')).encode('utf-8')).hexdigest(),
            metadata_only=True, teacher_requirement_not_resolved=True)
        coverage.append(dict(family=family, title=capability['title'], status='standard_not_specified',
            source_scope_complete=True, property_record_count=len(records),
            reliable_rule_association=False, automatic_error_judgment_enabled=False,
            detail=f'已采集{len(records)}个可定位对象的原文及样式属性，并保留完整后文；缺少教师目标参数，不按差异判错，不新增逐对象人工核验卡片。'))
    return dict(version=VERSION, source_sha256=source_sha256, capabilities=capabilities,
                sources=sources, coverage=coverage, findings=[], llm_requests=0, svn_requests=0, word_requests=0)
