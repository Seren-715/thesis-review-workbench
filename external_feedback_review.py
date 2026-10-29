"""Route explicit dependencies on unavailable teacher instructions for review.

No model, SVN, Word, network or publication operation is performed. General
citations, data-source requests and optional offers of help are not dependencies.
An independently stated requirement is preserved without approving a rule.
"""
from __future__ import annotations

from copy import deepcopy
import re

VERSION = 'explicit_external_teacher_instruction_v1'

_CHANNEL = re.compile(
    r'(?:看(?:一下|看)?|参见|参考|详见|根据|按照|按|见)\s*'
    r'(?:(?:我|老师)(?:发的|给的|的)?\s*)?'
    r'(?:QQ|微信|邮件)(?:中的|里(?:面)?的?|上的|的)?\s*(?:留言|消息|说明|要求|通知)', re.I)
_SLIDES = re.compile(
    r'(?:看(?:一下|看)?|参见|参考|详见|根据|按照|按|见)'
    r'[^。！？；;\n]{0,30}?(?<![A-Za-z])PPT(?![A-Za-z])', re.I)
_DIRECT = re.compile(
    r'(?:(?:晚上|今晚|明天|课后|下课后|当面|打电话|电话(?:里)?|稍后)\s*(?:再|来)?\s*'
    r'(?:联系我|找我|问我|和我(?:联系|讨论|沟通))|'
    r'(?:联系我|找我)(?:吧|一下)?(?:，|,)?\s*(?:当面|电话)(?:讨论|沟通|说明))')
_REPORT = re.compile(
    r'(?:论文一点通[^。！？；;\n]{0,60}(?:报告|检测)|'
    r'(?:提交|提供|发给我|给我)[^。！？；;\n]{0,25}(?:检测通过|格式检测|检测)(?:的)?报告)')

_KINDS = {
    'teacher_message': ('教师留言或消息原文', '补充教师所指的留言或消息及对应适用范围。'),
    'instructional_slides': ('教师指定PPT、规范或讲座资料', '补充所指PPT或规范的相关页面，注明适用模板与要求。'),
    'teacher_conversation': ('教师后续沟通说明', '取得教师对该对象的具体说明，并记录文字结论。'),
    'format_detection_report': ('教师要求的外部格式检测报告', '补充对应论文版本的格式检测报告；不能以本地检查代替已要求的报告。'),
}


def _optional_help(text, matched):
    """An offer to answer questions is not evidence that a criterion is missing."""
    start = matched.start()
    prefix = text[max(0, start-22):start]
    return bool(re.search(r'如(?:果)?(?:有|还|不懂)|有问题(?:可以|可)|不(?:明白|懂)(?:可以|可)|有疑问(?:可以|可)', prefix))


def review_external_feedback(comment):
    """Return an explicit manual route or None; leave the supplied record intact.

    Source fields bind this decision to a stored comment. They do not assert a
    fresh filesystem hash verification or that a teacher requirement is approved.
    """
    text = str(comment.get('text') or '')
    dependencies = []
    for kind, pattern in [('teacher_message', _CHANNEL), ('instructional_slides', _SLIDES),
                          ('teacher_conversation', _DIRECT), ('format_detection_report', _REPORT)]:
        for match in pattern.finditer(text):
            if kind == 'teacher_conversation' and _optional_help(text, match):
                continue
            dependencies.append({'kind': kind, 'quote': match.group(), 'start': match.start(),
                                 'end': match.end(), 'material': _KINDS[kind][0]})
    if not dependencies:
        return None
    # Preserve explicit self-contained clauses without deriving missing details
    # from the anchor or silently treating these clauses as approved standards.
    fragments = []
    for match in re.finditer(r'[^。！？；;\n]+', text):
        fragment = match.group()
        overlaps = [d for d in dependencies if d['start'] < match.end() and d['end'] > match.start()]
        if overlaps:
            fragment = fragment[:max(0, min(d['start'] for d in overlaps)-match.start())]
        fragment = fragment.strip(' ，,：:')
        explicit = re.search(r'需要|应该|应当|必须|不要|不能|(?:^|[，,：:])\s*画(?:UML|用例|图)|同一页|修改|独立|补充', fragment)
        if fragment and explicit and not re.search(r'(?:放到|放在|根据|按照|参照|参考|采用|使用)$', fragment):
            fragments.append({'quote': fragment, 'status': 'explicit_fragment_not_rule_approval',
                              'needs_scope_review': True})
    kinds = list(dict.fromkeys(d['kind'] for d in dependencies))
    source_fields = {key: deepcopy(comment.get(key)) for key in
                     ('id', 'evidence_id', 'source_sha256', 'source_path', 'comment_id', 'student_id',
                      'student_name', 'revision', 'repository_path', 'original_source_sha256', 'original_source_path')}
    return {'schema_version': 1, 'version': VERSION, 'status': 'manual_external_information',
            'classification_action': 'skip_llm_external_dependency',
            'rule_approval': False, 'evidence': {**source_fields, 'comment_text': text},
            'missing_materials': [_KINDS[k][0] for k in kinds],
            'dependencies': dependencies,
            'reason': '教师批注明确指向论文外的说明或报告；当前批注及选区不足以还原这些外部依据。',
            'next_step': ' '.join(_KINDS[k][1] for k in kinds),
            'independent_requirement_fragments': fragments,
            'independent_fragment_policy': '可单独保留明确原话进行范围复核；仍依赖外部的细节不进入自动归纳或判错。'}


def audit_external_feedback(library):
    matches = [route for c in library.get('comments', []) if (route := review_external_feedback(c))]
    return {'schema_version': 1, 'version': VERSION, 'status': 'external_dependencies_audited',
            'source_fingerprint': library.get('source_fingerprint'),
            'comment_count': len(library.get('comments', [])), 'matching_comments': len(matches),
            'items': matches, 'llm_requests': 0, 'svn_requests': 0, 'word_requests': 0,
            'limitations': ['只识别教师原话明确指向的外部材料，未假设材料已经提供。',
                            '人工路由不批准规则；独立要求与外部依赖应分开保留。']}
