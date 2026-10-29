"""Source-bound content scopes; absence of a word never constitutes an error."""
from content_review_context import collect_content_scope

DEFINITIONS = (
    ('NP024', '数据来源、选择理由与数据集说明', 'data_provenance',
     ['按所附教师原话核查数据来源、选择理由及数据内容；未要求的规模、期限或划分方式不强加',
      '区分论文未说明、图片不可读与实际数据不存在；不能从正文缺少关键词直接判错']),
    ('NP025', '数据库表用途、字段与关系说明', 'database_explanation',
     ['按教师原话核查相关表用途、字段、外键或实体关系，结合全部原生表格文字与叙述',
      '实际关系可能在图片中；未审图不能断言关系不存在；不要求非数据库型论文建立数据库']),
    ('NP026', '模型选择及优化前后对照说明', 'model_comparison',
     ['按教师原话核查模型选择理由和可比较的评分、耗时或优化前后对照',
      '不得虚构基准或实验数字；只有一种模型且没有比较或优化主张时不强加多模型对照']),
)


def load_content_rules(library):
    families = {r['id']: r for r in library.get('rules', [])}
    result = []
    for rid, title, family, dimensions in DEFINITIONS:
        source = families.get(family, {})
        evidence = source.get('teacher_evidence', [])
        result.append(dict(id=rid, title=title, family=family, checker='complete_section_semantics',
                           scope='仅真实标题识别的相关章节及其下级正文和原生表格文字；依所附教师原话审阅，图片内容与论文外实现不由文字证明。',
                           check_dimensions=dimensions, teacher_evidence=evidence, enabled=bool(evidence),
                           policy='teacher_specific_not_universal',
                           recurrence_status=source.get('recurrence_status', 'not_evaluated'),
                           supporting_students=source.get('supporting_students', []),
                           automatic_error_judgment_enabled=False))
    return result


def check_content_rules(paragraphs, rules, source_sha256):
    findings, coverage = [], []
    for rule in rules:
        if rule.get('id') not in {r[0] for r in DEFINITIONS} or not rule.get('enabled'):
            continue
        selected, scope = collect_content_scope(paragraphs, rule['family'], source_sha256)
        row = dict(rule_id=rule['id'], title=rule['title'], status='partial', semantic_review_complete=False)
        if not selected:
            row['detail'] = '尚未从真实标题识别适用内容范围；本项未作缺失判断，不新增逐段人工核验记录。'
            coverage.append(row)
            continue
        first = selected[0]
        end = min(len(first['text']), 1000)
        findings.append(dict(id=rule['id']+'-0001', rule_id=rule['id'], title=rule['title']+'待语义审阅',
                             status='candidate', paragraph_index=first['index'], location=first.get('location'),
                             text=first['text'], start=0, end=end, semantic_required=True,
                             context=[], check_dimensions=rule['check_dimensions'], section_complete=True,
                             text_scope='identified_section_complete', content_scope=scope,
                             teacher_evidence=rule['teacher_evidence'], policy=rule['policy'],
                             reason='已保存相关章节的全部可提取原文及表格文字；尚未形成内容质量结论。',
                             suggestion='智能体应继续读取完整范围，结合教师原话形成有逐字引文的判断；不能把未读内容转交人工。'))
        row.update(detail=f'完整保留 {scope["total_paragraphs"]} 个段落、{scope["total_characters"]} 字符、{scope["total_chunks"]} 个可续读块，等待实际语义审阅。',
                   total_paragraphs=scope['total_paragraphs'], total_characters=scope['total_characters'],
                   total_chunks=scope['total_chunks'], source_scope_complete=True)
        coverage.append(row)
    return findings, coverage
