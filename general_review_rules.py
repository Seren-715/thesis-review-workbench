"""Separate reusable checking requirements from historical case evidence.

No historical quotation is promoted to a universal requirement. Specific
numeric format checks remain governed by their verified reference policy.
"""
from copy import deepcopy

VERSION = 'general_requirements_only_v1'
POLICY = ('只检查当前论文适用的通用要求；历史个案的题目、功能、技术、数据集、'
          '示范答案和修改后的段落均不构成新论文必须内容。适用性应由本稿原文证明；'
          '不适用则说明不适用。格式数值仅采用已核验的学校规范或用户明确要求。'
          '外部实现或资料不能由论文确认时提示需要外部资料确认。')

CORE = {
    'first_person': ['规范要求客观表述的正文应采用客观叙述；结合上下文区分作者自称、引用和术语，致谢中的第一人称不计为正文问题'],
    'chapter_structure': ['仅在当前论文明确适用分章要求时，需求分析与系统设计应分别成章；依据真实标题层级判断，不以正文关键词代替章节'],
    'table_continuation': ['仅对实际跨页表格核查续表标识和重复表头；单页表格不需要续表标识，须依据真实分页'],
    'formula_numbering': ['公式编号应与全文引用一致，核查重复和逆序；未解析的图片公式不能据此判定缺失'],
    'figure_numbering': ['图题编号应唯一并与正文引用一致；核查真实题注，不能仅依赖相似文字'],
    'table_caption_position': ['采用本校表格规范时表题置于表格上方；核查真实表格与题名对应关系'],
    'native_table': ['明确采用三线表规范且该对象是适用数据表时，检查表顶、表头分隔和表底横线；布局表、代码表及其他特殊表格不机械套用'],
    'abstract_keywords': ['摘要说明本研究背景与目的、主要工作、采用的方法及结果或成果；结合摘要全部段落判断',
                          '方法、功能和成果须针对当前论文，不能照搬历史示例；不编造未提供的效果'],
    'conclusion_writing': ['结论归纳本研究实际完成的工作与成果，结合文内结果说明不足及后续方向',
                           '不以章节目录复述代替成果总结，不要求采用其他论文的系统或技术'],
    'implementation_explanation': ['结合当前论文实际任务，核查实现流程、关键技术与数据处理的说明',
                                  '没有采用某种技术或数据任务时不强制增加；未说明不等于实际未实现'],
    'data_provenance': ['对实际使用的数据说明来源、选择理由与数据集内容，不要求固定网站、规模、时段或划分比例'],
    'model_comparison': ['存在模型选型、优化或优越性主张时，核查理由、比较条件与文内结果支持，不强制其他模型'],
    'database_explanation': ['对本稿实际涉及的数据库表说明用途、主要字段及关系；无数据库任务时不适用'],
    'image_readability': ['结合实际排版和图内文字判断清晰度；清晰的图不要求放大，不套用其他图的局部意见'],
    'text_color': ['字体颜色要求须有明确适用的规范；未明确时不自动指定颜色，提示需要外部资料确认'],
    'caption_content': ['图表题名应准确对应实际对象内容；没有审阅对象内容时不推断题名错误'],
    'chinese_abstract_length': ['中文摘要约占一页，可以略少，不得超过一页；判断须依据当前文档真实分页'],
}


def attach_general_requirements(rules):
    """Project existing curated checker definitions, retaining literal provenance."""
    for rule in rules:
        family = rule.get('family', '')
        dimensions = list(CORE.get(family) or rule.get('check_dimensions') or [rule.get('title', rule['id'])])
        rule['general_requirements'] = {
            'version': VERSION, 'requirements': deepcopy(dimensions), 'application_policy': POLICY,
            'evidence_role': 'historical_provenance_only_not_new_paper_requirements',
            'reference_requirement_ids': [r.get('id') for r in rule.get('reference_requirements', [])],
            'case_specific_transfer_enabled': False,
        }
    return rules


def apply_general_packet(packet, rule):
    general = rule.get('general_requirements')
    if not isinstance(general, dict) or general.get('version') != VERSION:
        return packet
    packet['teacher_quotes'] = []  # Raw comments remain in the local evidence library.
    packet['general_requirements'] = deepcopy(general)
    packet['policy'] = POLICY
    return packet
