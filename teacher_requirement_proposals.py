"""Explicit missing-vocabulary proposals, never approvals or automatic errors."""
import hashlib
import re

SPECS = (
    ('model_effect_explanation', '建模效果说明', r'这里要给出建模的效果描述',
     '模型结果和效果评价范围', '核查本项目建模结果、评价或效果说明；不强制增加未要求的对照实验'),
    ('prompt_specification', '本项目提示词设计说明', r'最终提示词是什么样子要给出|当前任务的不同提示词是什么样子的',
     '提示词设计与实现范围', '核查项目实际提示词及用途；通用框架介绍不等于项目设计；不要求展示敏感信息'),
    ('prediction_target', '预测对象与目标说明', r'预测对象是谁',
     '任务定义和模型输入输出范围', '核查预测对象、目标及含义；不能根据泛称猜测对象不存在'),
    ('module_inventory', '系统模块组成说明', r'系统分为几个模块',
     '系统总体设计范围', '核查模块组成和职责说明；不规定所有系统采用相同数量的模块'),
    ('data_analysis_explanation', '本项目数据分析过程说明', r'介绍你对数据的分析',
     '数据分析范围', '核查针对本项目数据开展的分析和结果；不能将背景知识等同于实际分析'),
)


def build_proposals(library):
    rows = []
    for comment in library.get('comments', []):
        if comment.get('status') != 'semantic_review_required':
            continue
        for family, title, pattern, scope, criterion in SPECS:
            match = re.search(pattern, comment.get('text', ''))
            if not match:
                continue
            quote = match.group()
            rows.append(dict(id=hashlib.sha256((comment['id']+family).encode()).hexdigest(),
                             family=family, title=title, comment_id=comment['id'], quote=quote,
                             student_id=comment.get('student_id'), student_name=comment.get('student_name'),
                             revision=comment.get('revision'), source_sha256=comment.get('source_sha256'),
                             source_path=comment.get('source_path'), anchor=comment.get('anchor'),
                             scope=scope, criterion_draft=criterion,
                             status='explicit_requirement_draft_semantic_validation_pending',
                             basis='exact_directive_pattern_in_teacher_quote',
                             enabled=False, automatic_rule_approval=False,
                             refusal_conditions=['教师适用范围尚未确认', '未读取相关章节全文', '仅图片包含关键内容且尚未完成审图'],
                             validation_required=['核验教师原话与锚点', '独立正反例验证', '规则适用范围签审']))
    counts = {}
    for family, _, _, _, _ in SPECS:
        supporting = {r['student_id'] for r in rows if r['family'] == family and r['student_id']}
        counts[family] = dict(distinct_students=len(supporting),
                             recurrence='common_candidate' if len(supporting) >= 3 else 'cross_student_candidate' if len(supporting) >= 2 else 'single_student_feedback' if supporting else 'no_support')
    return dict(schema_version=1, automatic_rule_approval=False, proposals=rows, recurrence=counts)
