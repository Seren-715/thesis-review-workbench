"""Teacher-bound language, requirement and diagram review plans.

Local detection only prepares complete read scopes. It neither identifies AI
authorship nor infers UML correctness from captions or approves teacher rules.
"""
from copy import deepcopy

from teacher_extended_checks import collect_extended_scope

DEFINITIONS = (
    ('NP055', '评价性表述与具体内容', 'evaluative_wording', 'body', (
        '结合全部正文、摘要和后文结果，核查先进等比较性评价是否有明确对象和文内依据；有支持的比较不因用词判错',
        '核查所述研究、技术和效果是否有本项目具体内容；模型生成感不是可验证的作者身份或生成检测结论',
    )),
    ('NP056', '功能性与非功能性需求分类', 'requirement_classification', 'design', (
        '先确认论文是否涉及系统需求；适用时结合完整需求及设计，核查用户功能与非功能性需求是否分开描述，不凭标题词表判错',
        '依据教师原话核查易用性、兼容性、性能、安全性的具体归类；示例不是所有系统都必须实现的固定清单',
        '安全性说明须结合实际约束核查；用户名密码、备份和加密均不按关键词机械判定，不能据文内说明断言实际实现',
    )),
    ('NP057', '用例与业务流程建模', 'uml_modeling', 'design', (
        '结合本系统完整需求、角色和典型场景核查用例粒度及参与角色，历史咨询师学生案例不得强加到其他系统',
        '结合场景核查图名与图型说明；教师允许文字、活动图或流程图的场景不统一强制画图',
        '图内用例、连线、角色、泳道及实体复杂度必须有对应像素的语义审阅；只有图题或可读性审阅时为uncertain，不断言画法错误',
    )),
)


def load_semantic_content_rules(library):
    """Keep full canonical comments, anchors and lineage, without corpus writes."""
    families = {r['id']: r for r in library.get('rules', [])}
    rules = []
    for rid, title, family, selector, dimensions in DEFINITIONS:
        source = families.get(family, {})
        evidence = deepcopy(source.get('teacher_evidence', []))
        for e in evidence:
            # A classification excerpt must not hide qualifications in the
            # original multi-line comment. Preserve both for auditability.
            if isinstance(e.get('comment_text'), str) and e['comment_text'].strip():
                e['classification_quote'] = e.get('quote')
                e['quote'] = e['comment_text']
        rules.append(dict(id=rid, title=title, family=family,
            scope_selector=selector, check_dimensions=list(dimensions),
            enabled=bool(evidence), teacher_evidence=evidence,
            checker='complete_teacher_semantic_scope',
            policy='teacher_specific_not_universal',
            scope='依据完整教师原话及对应anchor核查适用范围，读取全部所选原文后综合；禁止按关键词或图题判错，非系统论文不强加需求/UML要求；未提供图像和外部事实标记uncertain。',
            recurrence_status=source.get('recurrence_status', 'not_evaluated'),
            same_category_feedback_reviewed=False,
            automatic_error_judgment_enabled=False,
            teacher_signoff_status='pending'))
    return rules


def check_semantic_content(paragraphs, rules, source_sha256):
    findings, coverage = [], []
    ids = {r[0] for r in DEFINITIONS}
    for rule in rules:
        if rule.get('id') not in ids:
            continue
        rid = rule['id']
        if not rule.get('enabled'):
            coverage.append(dict(rule_id=rid, title=rule['title'], status='not_applicable',
                detail='没有本项教师来源证据，未启用。'))
            continue
        selected, scope = collect_extended_scope(paragraphs, rule['scope_selector'],
                                                 rule['family'], source_sha256)
        narrative = [p for p in selected if p.get('heading_level') is None and p['text'].strip()]
        if not narrative:
            coverage.append(dict(rule_id=rid, title=rule['title'], status='partial',
                semantic_review_complete=False, detail='没有取得适用文字范围；不推断需求或图形缺失，不生成空人工卡片。'))
            continue
        p = narrative[0]
        finding = dict(id=rid+'-0001', rule_id=rid, title=rule['title']+'待完整范围审阅',
            status='candidate', paragraph_index=p['index'], text=p['text'], start=0,
            end=len(p['text']), location=p.get('location'), semantic_required=True,
            content_scope=scope, check_dimensions=deepcopy(rule['check_dimensions']),
            section_complete=True, text_scope='all_selected_text_complete',
            teacher_evidence=deepcopy(rule['teacher_evidence']), policy=rule['policy'],
            teacher_signoff_status='pending',
            reason='保留真实章节中全部适用文字，等待逐段读取及证据绑定判断；定位不是错误结论。',
            suggestion='结合后文已有依据及教师原话判断，未提供图像或文外事实明确保留证据缺口。')
        if rid == 'NP057':
            finding['vision_review_plan'] = dict(version='teacher_uml_review_plan_v1',
                status='pending', source_sha256=source_sha256,
                related_figure_rule_id='NP007', paragraph_indices=scope['paragraph_indices'],
                read_all_matching_figures=True, semantic_image_review_complete=False,
                readability_receipt_is_uml_proof=False,
                dimensions=deepcopy(rule['check_dimensions']),
                missing_image_decision='needs_review', figures=[])
        findings.append(finding)
        coverage.append(dict(rule_id=rid, title=rule['title'], status='partial',
            source_scope_complete=True, semantic_review_complete=False,
            total_paragraphs=scope['total_paragraphs'], total_chunks=scope['total_chunks'],
            total_characters=scope['total_characters'], teacher_signoff_status='pending',
            detail=f'完整保留{scope["total_paragraphs"]}段文字；语义审阅及教师签审仍待完成。'))
    return findings, coverage


def bind_uml_vision_context(report):
    """Link scoped NP007 objects; validated readability is context, not UML proof.

    The caller may pass this plan to a diagram-specific vision reviewer. Nothing
    here converts existing readability receipts into completed UML semantics.
    """
    from paper_vision_review import context_key
    rules = {r['id']: r for r in report.get('rules', [])}
    for f in report.get('findings', []):
        if f.get('rule_id') != 'NP057' or not f.get('vision_review_plan'):
            continue
        plan = f['vision_review_plan']
        selected = set(f['content_scope']['paragraph_indices'])
        rows = []
        for figure in report.get('findings', []):
            if figure.get('rule_id') != 'NP007' or figure.get('paragraph_index') not in selected:
                continue
            e = figure.get('figure_evidence', {})
            receipt = figure.get('vision_review', {})
            valid = bool(receipt.get('validated_image')
                and receipt.get('source_sha256') == report.get('source_sha256') == e.get('source_sha256')
                and receipt.get('preview_sha256') == e.get('preview_sha256')
                and receipt.get('context_key') == context_key(figure, rules.get('NP007', {})))
            row = dict(finding_id=figure['id'], paragraph_index=figure['paragraph_index'],
                caption=e.get('caption'), reviewable=bool(e.get('reviewable')),
                preview_sha256=e.get('preview_sha256'),
                source_sha256=e.get('source_sha256'),
                readability_review_validated=valid, semantic_image_review_complete=False,
                decision='needs_review')
            if valid:
                row['readability_context'] = {k: deepcopy(receipt.get(k)) for k in
                    ('image_kind', 'readable', 'observations', 'visible_text', 'reason')}
            rows.append(row)
        plan.update(figures=rows, status='pending', semantic_image_review_complete=False,
                    missing_evidence='diagram_specific_pixel_semantics' if rows else 'matching_figure_pixels')
    return report
