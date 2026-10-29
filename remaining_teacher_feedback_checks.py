"""Remaining teacher feedback with bounded facts and complete read scopes.

No color target, word-count minimum, or image content is inferred. This module
does not perform network, renderer, Word or SVN calls.
"""
from copy import deepcopy
import re

from teacher_extended_checks import collect_extended_scope
from teacher_format_checks import Properties, W

DEFINITIONS = (
    ('NP058', '文字与图像颜色要求', 'text_color'),
    ('NP059', '图表题名与所述内容对应', 'caption_content'),
    ('NP060', '中文摘要篇幅与信息完整性', 'chinese_abstract_length'),
)


def load_remaining_feedback_rules(library):
    families = {r['id']: r for r in library.get('rules', [])}
    result = []
    for rid, title, family in DEFINITIONS:
        evidence = deepcopy(families.get(family, {}).get('teacher_evidence', []))
        for e in evidence:
            if e.get('comment_text'):
                e['classification_quote'] = e.get('quote')
                e['quote'] = e['comment_text']
            if rid == 'NP058':
                anchor = e.get('anchor') or {}
                e['color_requirement_scope'] = ('figure' if
                    re.search(r'白底|黑底|底纹|用例|其他的图', e.get('quote', '')) or
                    re.match(r'^\s*图\s*\d', anchor.get('text', '')) or anchor.get('media')
                    else 'text_unspecified_target')
        result.append(dict(id=rid, title=title, family=family, enabled=bool(evidence),
            checker='bounded_remaining_teacher_feedback', teacher_evidence=evidence,
            policy='teacher_specific_not_universal', automatic_error_judgment_enabled=False,
            teacher_signoff_status='pending',
            scope='完整阅读对应正文；原批注适用范围、源属性及图像证据分别保留，不猜测未明确标准。'))
        if rid == 'NP060':
            from reference_standards import load_reference_standards
            try:
                catalog = load_reference_standards()
                if catalog.get('status') == 'verified_reference_materials':
                    result[-1]['reference_requirements'] = [deepcopy(r) for r in
                        catalog.get('requirements', []) if r['id'] == 'official_chinese_abstract_page_extent']
            except (OSError, ValueError, KeyError, TypeError):
                result[-1]['reference_requirements'] = []
    return result


def _finding(rule, p, **extra):
    return dict(id=rule['id']+'-0001', rule_id=rule['id'], title=rule['title'],
        status='candidate', paragraph_index=p['index'], text=p['text'], start=0,
        end=len(p['text']), location=p.get('location'),
        teacher_evidence=deepcopy(rule['teacher_evidence']), policy=rule['policy'],
        teacher_signoff_status='pending', **extra)


def check_remaining_feedback(document, styles, paragraphs, rules, source_sha256):
    """Return findings and coverage; NP060 reuses UP001 for actual page limit."""
    selected, scope = collect_extended_scope(paragraphs, 'body', 'remaining_feedback', source_sha256)
    active = {r['id']: r for r in rules if r.get('id') in {d[0] for d in DEFINITIONS}}
    findings, coverage = [], []
    for rid, rule in active.items():
        if not rule.get('enabled'):
            coverage.append(dict(rule_id=rid, title=rule['title'], status='not_applicable',
                detail='没有该类教师原批注来源，未启用。'))
            continue
        narrative = [p for p in selected if p['text'].strip() and p.get('heading_level') is None]
        if not narrative:
            coverage.append(dict(rule_id=rid, title=rule['title'], status='partial',
                detail='没有可绑定原文范围，不推断对象缺失。'))
            continue
        if rid == 'NP058':
            color_runs, unknown = _colors(document, styles, paragraphs, selected)
            if color_runs:
                p = next(p for p in paragraphs if p['index'] == color_runs[0]['paragraph_index'])
                f = _finding(rule, p, status_override='needs_review',
                    reason='记录当前文字的具体颜色属性；教师批注没有给出统一正文目标颜色。',
                    suggestion='需要外部资料确认：取得本稿明确颜色标准后，再对照这些源属性判断。',
                    color_evidence=color_runs, external_material_required=True,
                    external_material_reason='正文目标颜色标准', semantic_required=False)
                f.pop('status_override'); f['status'] = 'needs_review'
                findings.append(f)
            coverage.append(dict(rule_id=rid, title=rule['title'], status='partial',
                parsed_color_runs=len(color_runs), unknown_color_runs=unknown,
                target_text_color=None, detail='仅记录明确颜色和主题引用，不将非黑色判错；图像白底要求需对应像素审阅。',
                figure_requirement_evidence=[deepcopy(e) for e in rule['teacher_evidence']
                    if e.get('color_requirement_scope') == 'figure']))
        elif rid == 'NP059':
            captions = [p for p in selected if re.match(r'^\s*[图表]\s*\d+\s*[-－‐‑–—]\s*\d+', p['text'])]
            tables = [p for p in selected if p.get('in_table') and p['text'].strip()]
            nodes = [] if document is None else list(document.iter(W+'p'))
            objects = any(0 <= p['index'] < len(nodes) and
                any(nodes[p['index']].find('.//'+W+tag) is not None for tag in ('drawing', 'pict'))
                for p in selected)
            if not captions and not tables and not objects:
                coverage.append(dict(rule_id=rid, title=rule['title'], status='not_applicable',
                    detail='当前可见正文没有图表题名、原生表格文字或图表对象，本项不创建语义审阅卡片。'))
                continue
            f = _finding(rule, narrative[0], semantic_required=True,
                content_scope=deepcopy(scope), section_complete=True,
                check_dimensions=[
                    '结合完整正文、图表引用、题名及实际表格文字核查命名与所述内容是否对应；标题措辞差异本身不构成错误',
                    '不能把图题或正文描述当作图内内容证明；涉及图形类别或图内内容须有当前像素语义证据，没有时明确证据缺口'],
                reason='保留全部正文及原生表格文字，需综合判断题名是否确实与所述对象矛盾。',
                suggestion='引用具体题名及相应对象内容；图像内容未审阅时不得断言图名错误。',
                vision_review_plan=dict(status='pending', source_sha256=source_sha256,
                    semantic_image_review_complete=False, related_figure_rule_id='NP007', figures=[]))
            findings.append(f)
            coverage.append(dict(rule_id=rid, title=rule['title'], status='partial',
                source_scope_complete=True, semantic_review_complete=False,
                detail='完整正文和表格文字已进入语境队列；图内题名对应仍依赖像素语义证据。'))
        else:
            abstract = _abstract(paragraphs)
            if not abstract:
                coverage.append(dict(rule_id=rid, title=rule['title'], status='partial',
                    detail='未取得有标题及关键词边界的完整中文摘要，不猜测字数或摘要缺失。'))
                continue
            f = _finding(rule, abstract[0], semantic_required=True,
                content_scope=deepcopy(scope), section_complete=True,
                abstract_evidence=dict(paragraph_indices=[p['index'] for p in abstract],
                    nonwhitespace_characters=sum(len(re.sub(r'\s', '', p['text'])) for p in abstract),
                    minimum_word_count=None, hard_word_threshold=False, page_limit_rule_id='UP001'),
                reference_requirements=deepcopy(rule.get('reference_requirements', [])),
                check_dimensions=[
                    '结合完整中文摘要及后文研究内容核查目的、方法、结果、结论是否有足够具体说明，不能用固定字数下限判错',
                    '500至800字左右仅为约略参考；按用户要求可略少，实际一页上限由UP001真实分页独立核查，不能从文字长度估算页数',
                    '教师要求缩减或充实时须结合具体摘要内容判定，简短而完整的摘要不因字数少机械判错'],
                reason='保留完整摘要及后文；字数是统计事实，不代表篇幅或质量结论。',
                suggestion='按具体缺失或冗余内容提出建议；实际页数采用UP001证据，不设硬性字数下限。')
            findings.append(f)
            coverage.append(dict(rule_id=rid, title=rule['title'], status='partial',
                semantic_review_complete=False, page_limit_rule_id='UP001',
                detail='摘要完整性进入全文语境队列；实际不超过一页由UP001核查，没有硬性字数下限。'))
    return findings, coverage


def _abstract(paragraphs):
    visible = [p for p in paragraphs if not any(p.get(k) for k in
        ('in_deleted', 'in_toc', 'in_textbox', 'in_table'))]
    starts = [i for i, p in enumerate(visible) if re.sub(r'\s', '', p['text']) in {'摘要', '中文摘要'}]
    if len(starts) != 1:
        return []
    rows = []
    for p in visible[starts[0]+1:]:
        if re.match(r'^\s*关\s*键\s*词\s*[:：]', p['text']):
            return rows
        if p.get('heading_level') is not None or re.sub(r'\s', '', p['text']).lower() in {'abstract', '英文摘要', '目录'}:
            return []
        if p['text'].strip():
            rows.append(p)
    return []


def _colors(document, styles, paragraphs, selected):
    from new_paper_checks import _visible_text, _hidden_run_resolver
    if document is None:
        return [], len(selected)
    nodes = list(document.iter(W+'p'))
    if len(nodes) != len(paragraphs) or any(p['index'] != i for i, p in enumerate(paragraphs)):
        raise ValueError('颜色检查段落索引与原件不同')
    resolver, hidden = Properties(styles), _hidden_run_resolver(styles)
    rows, unknown = [], 0
    for p in selected:
        if any(p.get(k) for k in ('in_deleted', 'in_toc', 'in_textbox')):
            continue
        node = nodes[p['index']]
        for run in node.iter(W+'r'):
            text = _visible_text(run, hidden, p.get('style_id'))
            if not text.strip():
                continue
            values = {attr: resolver.resolve(node, run, W+'color', attr)
                      for attr in ('val', 'themeColor', 'themeTint', 'themeShade')}
            values = {k: v for k, v in values.items() if v}
            if values:
                rows.append(dict(paragraph_index=p['index'], text=text, location=p.get('location'),
                    properties=values, rendered_color_resolved=False, source_only=True))
            else:
                unknown += 1
    return rows, unknown


def bind_remaining_figure_context(report):
    """Bind current image receipts without promoting readability to correctness."""
    from paper_vision_review import context_key
    rules = {r['id']: r for r in report.get('rules', [])}
    figures = [f for f in report.get('findings', []) if f.get('rule_id') == 'NP007']
    for f in report.get('findings', []):
        if f.get('rule_id') != 'NP059':
            continue
        scope_ids = set(f['content_scope']['paragraph_indices'])
        rows = []
        for figure in figures:
            if figure.get('paragraph_index') not in scope_ids:
                continue
            ev, receipt = figure.get('figure_evidence', {}), figure.get('vision_review', {})
            valid = bool(receipt.get('validated_image') and receipt.get('source_sha256') ==
                report.get('source_sha256') == ev.get('source_sha256') and
                receipt.get('preview_sha256') == ev.get('preview_sha256') and
                receipt.get('context_key') == context_key(figure, rules.get('NP007', {})))
            row = dict(finding_id=figure['id'], caption=ev.get('caption'),
                source_sha256=ev.get('source_sha256'), preview_sha256=ev.get('preview_sha256'),
                readability_review_validated=valid, semantic_image_review_complete=False)
            if valid:
                row['readability_context'] = {k: deepcopy(receipt.get(k)) for k in
                    ('visible_text', 'observations', 'image_kind', 'reason')}
            rows.append(row)
        f['vision_review_plan'].update(figures=rows, semantic_image_review_complete=False,
            missing_evidence='caption_specific_pixel_semantics' if rows else 'matching_figure_pixels')
    return report
