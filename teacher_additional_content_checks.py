"""Explicit additional content requirements, without invented format standards."""
from teacher_extended_checks import collect_extended_scope

DEFINITIONS = (
    ('NP051','技术介绍与项目实施内容的位置','section_content_placement','body',
     ['结合真实章节和完整上下文区分通用技术介绍与本项目的数据采集、实施内容',
      '仅按教师局部要求提出章节调整；不得因介绍项目实现就一律要求移段']),
    ('NP052','本项目采用的模型与技术说明','method_specification','body',
     ['核对全文中本项目实际采用的模型、技术及其作用说明',
      '通用模型介绍与本项目选用说明分开；未采用的模型不得强制新增']),
    ('NP053','方案概述与整体处理流程','solution_overview','body',
     ['结合完整方案、数据来源、预测和推荐范围核查总体思路及流程',
      '示意图属于原话建议时不直接强制；非推荐或预测任务不强加相应步骤']),
    ('NP054','模型或系统结果展示','result_demonstration','results',
     ['结合方法、实现和结果核查是否有本项目输入输出或运行结果的实际展示',
      '教师所举词向量或相似度示例不泛化为所有论文要求；文字、表格和图像证据分别说明']),
)


def load_additional_content_rules(library):
    families={r['id']:r for r in library.get('rules',[])}
    return [dict(id=rid,title=title,family=family,scope_selector=selector,
                 check_dimensions=dimensions,enabled=bool(families.get(family,{}).get('teacher_evidence')),
                 teacher_evidence=families.get(family,{}).get('teacher_evidence',[]),
                 checker='complete_additional_teacher_content',automatic_error_judgment_enabled=False,
                 policy='teacher_specific_not_universal',
                 scope='依据所附教师原话，读取真实章节和本项完整范围；未实际读取和综合判断不算审阅完成。')
            for rid,title,family,selector,dimensions in DEFINITIONS]


def check_additional_content(paragraphs,rules,sha):
    findings,coverage=[],[]
    for rule in rules:
        if rule['id'] not in {r[0] for r in DEFINITIONS}:continue
        if not rule.get('enabled'):
            coverage.append(dict(rule_id=rule['id'],title=rule['title'],status='not_applicable',
                                 detail='没有本项教师来源证据，未启用。'))
            continue
        selected,scope=collect_extended_scope(paragraphs,rule['scope_selector'],rule['family'],sha)
        text=[p for p in selected if p.get('heading_level') is None and p['text'].strip()]
        if not text:
            coverage.append(dict(rule_id=rule['id'],title=rule['title'],status='partial',
                                 detail='未建立适用原文范围，不推断内容缺失。'))
            continue
        p=text[0]
        findings.append(dict(id=rule['id']+'-0001',rule_id=rule['id'],title=rule['title']+'待完整范围审阅',
            status='candidate',paragraph_index=p['index'],text=p['text'],start=0,end=len(p['text']),
            location=p.get('location'),semantic_required=True,content_scope=scope,
            check_dimensions=rule['check_dimensions'],section_complete=True,text_scope='all_selected_text_complete',
            teacher_evidence=rule['teacher_evidence'],policy=rule['policy'],
            reason='保留本项完整可提取文字与来源范围；读取并综合之后才能判断是否需要修改。',
            suggestion='核查全文已有说明和教师具体要求，建议应引用实际原文；资料不足与实际未实现分开处理。'))
        coverage.append(dict(rule_id=rule['id'],title=rule['title'],status='partial',
            semantic_review_complete=False,total_chunks=scope['total_chunks'],total_paragraphs=scope['total_paragraphs'],
            detail=f'完整保留{scope["total_paragraphs"]}段、{scope["total_characters"]}字符；尚未实际完成语境判断。'))
    return findings,coverage
