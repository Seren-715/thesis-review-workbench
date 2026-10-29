"""Produce evidence-bound rule completion drafts; never approve or enable rules.

Pure builder plus read-only local CLI. No SVN, Word, or model requests. The
disposition denominator is unique retained comment IDs, never family links.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from rule_governance import build_review_package, canonical_sha

VERSION = 'rule_completion_plan_v2_teacher_evidence_bound'


def semantic_pending_composition(library):
    """Lexical work triage only; these buckets are not semantic decisions."""
    rows = []
    action = re.compile(r'应该|应当|需要|必须|不要|不能|要加|要给|要写|要说明|要描述|要有|要列|请|建议|改为|改成|增加|补充|单独|给出|介绍|描述|说明|分析|放到|放在|不应|缺少|缺失')
    for comment in library['comments']:
        if comment.get('status') != 'semantic_review_required':
            continue
        text = re.sub(r'\s+', '', comment.get('text', ''))
        if text and set(text) <= {'?', '？'}:
            bucket = 'question_mark_only'
        elif action.search(text):
            bucket = 'action_word_instruction_candidate'
        elif len(text) <= 24 and ('?' in text or '？' in text):
            bucket = 'short_question_without_action_word'
        else:
            bucket = 'other_or_context_dependent'
        rows.append({'comment_id': comment['id'], 'bucket': bucket,
                     'original_text': comment.get('text'),
                     'semantic_decision': 'not_performed'})
    return {'method': 'mutually_exclusive_lexical_triage_not_semantic_classification',
            'question_limit_characters': 24, 'action_pattern': action.pattern,
            'counts': dict(Counter(row['bucket'] for row in rows)), 'items': rows}


def context_recovery_examples(library):
    """Read five complete-anchor same-source excerpts, without reclassifying."""
    from new_paper_checks import read_docx, extract_paragraphs
    selectors = [('这里要给出建模的效果描述', '结论建模效果说明'),
                 ('最终提示词是什么样子要给出', '最终提示词设计说明'),
                 ('任务没有说明清楚', '任务目标与预测对象说明'),
                 ('模块设计，这里可以', '系统模块功能划分'),
                 ('应该介绍你对数据的分析', '可视化结果的具体数据解读')]
    result = []
    for needle, potential in selectors:
        comment = next((c for c in library['comments'] if c.get('status') == 'semantic_review_required' and needle in c.get('text', '')), None)
        if comment is None:
            continue
        anchor = comment.get('anchor', {})
        row = {k: deepcopy(comment.get(k)) for k in ['id', 'student_name', 'revision', 'text',
                'source_path', 'source_sha256', 'classification_reason', 'anchor']}
        row.update(proposed_topic=potential, proposed_topic_status='investigation_only_not_new_family',
                   scope='仅此教师批注所指章节/对象，不推广为通用规范')
        if anchor.get('status') != 'complete' or not anchor.get('paragraph_indices'):
            row['context_read_status'] = 'refused_incomplete_anchor'
        else:
            document, styles, sha, _ = read_docx(comment['source_path'])
            if sha != comment['source_sha256']:
                raise ValueError('Context source hash mismatch')
            paragraphs = extract_paragraphs(document, styles)
            # docx_comment_reader indices are one-based (line 261); the
            # checker extractor publishes zero-based indices. Convert once.
            indices = [i-1 for i in anchor['paragraph_indices']]
            row['index_conversion'] = 'comment_reader_one_based_to_checker_zero_based'
            first, last = min(indices), max(indices)
            local = paragraphs[max(0, first-3):last+4]
            # Extractor view differences must not silently misbind paragraph IDs.
            original = re.sub(r'\s+', '', anchor.get('text', ''))
            bound = re.sub(r'\s+', '', ''.join(p['text'] for p in paragraphs[first:last+1]))
            if original and original not in bound:
                row['context_read_status'] = 'refused_extractor_anchor_mismatch'
            else:
                row['context_read_status'] = 'same_source_neighbour_paragraphs_verified'
                row['additional_context'] = [dict(index=p['index'], text=p['text']) for p in local]
                row['context_complete_chapter'] = False
        result.append(row)
    return result

# Implementation proposals, not new checks or school-wide requirements.
PROPOSALS = {
    'references': ('按照教师原话逐项整理外文参考文献要求，读取完整参考文献后识别语言和数量；与NP008数字引用一致性单独核查。', '数字引用一致性不是外文文献数量要求的实现；须确认数量阈值及文献语言判据。', '英文题名的中文作者文献不必然是外文文献，不能仅因英文字符计入。'),
    'headings': ('根据教师实际标题修改要求明确层级和编号对象，再核查真实大纲及继承样式；不能把NP009重复编号当作完整实现。', '须核对原批注是标题名称、层级、顺序还是编号要求；目录和正文不得充当真实标题。', '正文列举数字不是标题编号，目录重复显示也不是章节重复。'),
    'object_references': ('核对教师批注真实所指图表对象、引用及题注，明确局部查找要求；NP010补充引用匹配检查单独保留。', '“在哪里”之类疑问必须结合批注锚点和原对象核实，不自动归为对象不存在或编号错误。', '对象在文本框、图片题注或后续页时，当前解析未找到不能认定论文缺对象。'),
    'english_abstract_length': ('读取完整中英文摘要并记录字数及版面长度；仅在模板或教师提供明确阈值后比较。', '缺少篇幅阈值时只提供统计，不以“较长”判错。', '中文摘要较短而英文正常展开，不应仅按长度比判错。'),
    'font_layout': ('解析段落及运行文字的直接属性、样式继承和文档默认值，生成局部字体差异记录。', '须确认该批注针对的对象及字体字号要求；继承未完整解析时拒判。', '代码、公式、标题字体与正文不同可以符合各自样式要求。'),
    'formula_font': ('读取OMML数学字体与上下文，区分数学变量、说明文字和非原生公式。', '需要批准的公式字体要求及对象绑定，图片公式须视觉证据。', '公式变量斜体与说明文字正体不能按混用字体直接判错。'),
    'cover_layout': ('将模板字段和版式要求转成封面字段表，逐项对比封面原文和页面证据。', '没有本校本年度适用模板时不套用历史样例。', '另一模板合法的封面字段或布局差异不能判错。'),
    'code_layout': ('可靠识别代码段，检查是否使用受要求支持的呈现形式，并复用NP018/NP020局部属性。', '代码图片、代码清单及正文代码片段的适用要求须分别确认。', '行内函数名不能当整段程序代码要求改成代码清单。'),
    'implementation_claims': ('抽取全文实现声明及互相矛盾的描述；文稿一致性与软件实际实现分别输出。', '核验实际功能须取得代码、运行结果或实验记录，不能从论文推断未实现。', '论文未详细说明某功能不等于该功能没有实现。'),
    'architecture_fidelity': ('绑定架构图、图中文字及完整设计/实现章节，比较文稿内组件和接口叙述。', '真实系统一致性需要项目代码或运行架构；看不到图或上下文时拒判。', '通用背景架构图已明确标注为背景知识，不应当成项目实际架构。'),
    'figure_layout': ('绑定图对象、图题和前后完整说明，比较文档顺序与真实分页。', '多图组合和同页布局需页面证据，不能只看XML邻接。', '同一复合图内多个子图不能按多张独立图片缺说明判错。'),
    'citation_validity': ('建立引用条目与相应正文论断的映射；取得来源原文后核对支持范围。', '只有参考文献题名不能证明来源支持论断，无法取得来源时保留缺口。', '相关主题的真实文献不必支持正文的所有具体论断。'),
    'citation_placement': ('定位数字引用标记与所在句、引用对象，按教师局部要求比较位置。', '应区分直接引用、间接引述和多句共用引用，不按固定句尾模板泛化。', '连续多句共同论述一个来源时，引用位置合法性需结合全文。'),
    'citation_leading_position': ('追踪同一资料在章节中的首次介绍位置和后续复用，提供引用前移候选。', '首次提及和首次实质使用不等同，需批准适用条件。', '先提到资料名称但后文才介绍内容，不应直接判为缺引用。'),
    'source_citation_missing': ('识别明确来自外部的资料/图/数据，查找就近来源说明及参考文献对应项。', '自行实验产生的数据、常识、未能识别来源者不得强制补外部引用。', '作者自行采集并交代方法的数据不需要虚构第三方来源。'),
    'text_color': ('解析可见正文的颜色、主题色及样式继承；根据已声明模板要求判断。', '表题、超链接、修订、代码及图内文字需独立范围；未解析主题色时拒判。', '合法超链接、代码高亮或修订颜色不等于正文字体颜色错误。'),
    'blank_pages': ('在真实Word/PDF页面证据上识别仅有页眉页脚的页面，关联分页符和分节符。', '双面章节起始页、模板要求空白页和隐藏批注布局须排除。', '模板明确要求章节从奇数页开始时，偶数空白页可能是正确版式。'),
    'image_page_bounds': ('取同源页面版心和图表实际边界，比较页面坐标，覆盖内嵌与浮动对象。', '仅OOXML尺寸无法确定浮动对象最终版面；横向分节及跨栏须排除。', '横向分节中合法宽表不能按上一节纵向版心判越界。'),
    'chart_axes': ('先识别数据坐标图，再读取图像轴标题、刻度、单位和相邻说明，判断缺失。', '用例图/架构图不具有坐标轴；无法识别图中文字时拒判。', '流程图没有坐标轴是正常情况；单位已在图题说明时不得重复判缺失。'),
    'model_comparison': ('绑定任务、基线、比较指标及完整实验章节，核查文稿是否说明模型选择与对照。', '未写对照不等于未做对照；没有数据不得编造性能差异或最佳模型。', '纯系统实现论文未声明模型优化任务时不能强制要求消融实验。'),
    'data_provenance': ('读取数据来源、采集范围、时间、清洗及划分章节，建立字段化来源说明。', '取决于论文类型及实际数据材料；不得以关键词未命中认定没有来源。', '来源信息分布在方法、实验和附录时应合并读取后判断。'),
    'database_explanation': ('读取数据库设计、表结构及实现章节，核查字段含义、关系和功能叙述对应性。', '无数据库的论文不适用；关系需真实对象及上下文，表内短字段名不是错。', '非关系型存储或文件存储方案不能强制采用关系型外键表述。'),
    'table_caption_name': ('绑定表格和可靠题注，区分缺题名、题注解析失败及非数据展示框。', '文本框、图片表及不规范题注需扩大读取；没有匹配题注不等于没有题注。', '提示词展示框、单格排版框不应当成数据表强制加表题。'),
    'keyword_specificity': ('结合题名、完整摘要和任务内容审阅关键词的对象/技术辨识度。', '缺少教师明确要求或语境时不按词长和宽泛词黑名单直接判错。', '领域通用术语在论文任务中具有明确含义时不应仅因常见而判不具体。'),
    # Families from the 2026-10-12 semantic review; criteria cite the school writing standard.
    'caption_content': ('绑定图表对象与题注，检出只有编号无名称的题注；图内自带标题与题注不一致时交多模态复核。', '题名是否“具体、贴切”需结合图表内容判断，仅缺名称可直接检出。', '复合图的子图标签、续表省略表题不能按缺题名判错。'),
    'document_order': ('读取真实标题大纲，核对结论、参考文献、谢辞、附录的有无与先后顺序（撰写规范第四节）。', '标题须为真实正文标题而非目录行；附录按需设置，缺附录不判错。', '章内小结或“总结”作为结论章标题时应按结论处理。'),
    'page_header': ('读取各节页眉，按撰写规范第六节第9条核对：第1章起设置，奇数页为论文题目，偶数页为学校固定文字。', '须区分奇偶页页眉及分节继承；无法取得分节信息时拒判。', '封面、声明、摘要、目录不设页眉不是错误。'),
    'chinese_abstract_length': ('读取完整中文摘要正文（不含题目和关键词）计字，按撰写规范500～800字左右且限一页比较。', '“左右”须留余量，超出一页需页面证据。', '英文摘要篇幅另由english_abstract_length处理。'),
    'evaluative_wording': ('定位“先进、领先、最新、显著”等评价性词语及空泛模板句，交语义审阅判断是否有对比依据。', '撰写规范要求避免带感情色彩的非学术性词语；是否空泛需语义判断，不按词表直接判错。', '有实验对比数据支撑的“优于”“提升”等表述不应判错。'),
    'requirement_classification': ('读取需求分析章节结构，核对是否区分功能性需求与非功能性需求，以及非功能需求分类名称。', '分类标准以教师批注为依据（易用性、性能、安全性、兼容性等），学校规范未逐项规定。', '算法研究类论文没有系统需求章节时不适用。'),
    'uml_modeling': ('定位用例图、活动图、流程图及E-R图，交多模态审阅粒度、角色和画法。', '图形语义须读图判断，不能仅凭图题判错。', '概要示意图不按完整UML规范要求。'),
}


def observed_checker_rules(library, root):
    """Read current checker metadata without calling the mutable library cache."""
    from new_paper_checks import load_teacher_rules
    rows = load_teacher_rules(library=library)
    families = {r['id']: r for r in library['rules']}
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate checker ID')
    if any(r['family'] not in families and r.get('enabled', True)
           and r.get('teacher_evidence') and r.get('policy') != 'supplemental_structural_check'
           for r in rows):
        raise ValueError('Checker family absent from current library')
    return rows


def build_completion_plan(library, checker_rules):
    """Return mutually exclusive dispositions and unsigned family proposals."""
    comments = library['comments']
    identities = [c['id'] for c in comments]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate retained comment identity')
    physical = {}
    for rule in checker_rules:
        if rule.get('family'):
            physical.setdefault(rule['family'], []).append(rule)
    # A code-family association alone does not implement the teacher's actual
    # request. Require enabled, same-family, source-bound teacher evidence.
    supported = [r for r in checker_rules if r.get('enabled', True)
                 and r.get('teacher_evidence')
                 and r.get('policy') != 'supplemental_structural_check']
    package = build_review_package(library, supported)
    linked = {r['id'] for r in package['rules'] if r['checker_ids']}
    known = {r['id'] for r in package['rules']}
    bound = {}
    for rule in supported:
        for evidence in rule['teacher_evidence']:
            identity = (evidence.get('source_sha256'), str(evidence.get('comment_id')))
            bound.setdefault(identity, set()).add(rule.get('family'))
    dispositions = []
    for comment in comments:
        families = list(dict.fromkeys(comment.get('rule_ids', [])))
        if not set(families) <= known:
            raise ValueError('Comment references unknown family')
        direct = set(families) & bound.get((comment.get('source_sha256'), str(comment.get('comment_id'))), set())
        if not comment.get('text', '').strip():
            state = 'empty_or_media_evidence_pending'
        elif not families:
            state = 'semantic_disposition_pending'
        elif direct:
            state = 'linked_checker_candidate_not_approved'
        else:
            state = 'classified_without_direct_checker'
        row = deepcopy(comment)
        row.update(disposition=state, direct_checker_families=sorted(direct),
                   unlinked_families=sorted(set(families) - direct),
                   reliability_review='pending', automatic_error_judgment_enabled=False,
                   count_unit='one_unique_comment_id')
        dispositions.append(row)
    specs = []
    for source in package['rules']:
        row = deepcopy(source)
        row['supplemental_code_associations'] = [dict(id=m['id'], policy=m.get('policy'),
                                                   teacher_evidence_count=len(m.get('teacher_evidence', [])))
                                              for m in physical.get(row['id'], []) if m not in supported]
        proposal = PROPOSALS.get(row['id'])
        row['status'] = 'unsigned_specification_draft'
        row['existing_checker_scope_is_not_full_family_coverage'] = True
        row['decision_criteria'] = {
            'status': 'proposed_not_approved',
            'implementation': proposal[0] if proposal else '按现有明确局部范围实施：' + '；'.join(str(m.get('scope') or '') for m in row['checker_scopes']) + '。不以候选关联判断教师所有要求均已满足。',
            'evidence_gate': proposal[1] if proposal else '逐项来源哈希与教师引文可核验；对象、章节及版面完整；适用要求明确；语义和视觉项需实际完成对应审阅。',
            'rule_outcome': ['suggested_change_with_quoted_evidence', 'complies_in_checked_scope', 'insufficient_evidence'],
        }
        row['negative_examples'] = {'status': 'synthetic_boundary_proposal_not_ground_truth',
                                    'items': [{'text': proposal[2] if proposal else '同一术语或对象出现在不同章节、引用、代码或致谢等范围时，不能未经范围核对沿用局部要求。',
                                               'label': None, 'origin': 'synthetic_boundary_description'}]}
        row['completion_gate'] = ['来源及批注语义核对', '批准明确适用范围与拒判条件',
                                  '独立人工正反例和误报边界', '执行相应检查并测定准确率及召回率',
                                  '人工签审及规则版本冻结']
        row['next_step'] = ('implement_missing_direct_checker' if proposal else 'validate_existing_scoped_checker')
        row['entry_sha256'] = canonical_sha({k: v for k, v in row.items() if k != 'entry_sha256'})
        specs.append(row)
    counts = Counter(row['disposition'] for row in dispositions)
    missing = [r for r in specs if not r['checker_ids']]
    missing.sort(key=lambda r: (-r['distinct_supporting_students'], -len(r['teacher_evidence']), r['id']))
    result = {
        'schema_version': VERSION, 'status': 'completion_draft_not_rule_approval',
        'library_sha256': canonical_sha(library),
        'source_fingerprint': library.get('source_fingerprint'),
        'summary': {'unique_comments': len(comments), 'text_comments': sum(bool(c.get('text', '').strip()) for c in comments),
                    'family_count': len(specs), 'direct_linked_families': len(linked),
                    'no_direct_checker_families': len(missing), 'dispositions': dict(counts),
                    'supplemental_structural_checker_count': sum(r.get('policy') == 'supplemental_structural_check' for r in checker_rules),
                    'human_signed_rules': 0, 'reliability_validated_families': 0},
        'comment_dispositions': dispositions, 'rule_specification_drafts': specs,
        'missing_checker_priority': [{'id': r['id'], 'title': r['title'],
                                      'supporting_students': r['distinct_supporting_students'],
                                      'proposal': r['decision_criteria']} for r in missing],
        'limitations': ['已归类和关联检查不等于可靠规则化完成。',
                        '相同批注关联多类仍只计一条；主关联必须匹配实际检查的教师来源SHA和批注ID，不能只按类别名称关联。',
                        '缺少同类教师原话的补充结构检查不计作对应教师要求已实现；来源绑定的方法与补充方法分别记录。',
                        '教师批注锚点是观察素材，不是独立真值；合成反例描述未被人工标注。',
                        '不把全部待复核条目认定为错误，也不伪造教师批准或自动启用。'],
        'external_requests': {'llm': 0, 'svn': 0, 'word': 0},
    }
    result['plan_sha256'] = canonical_sha(result)
    return result


def write_completion_plan(output, plan):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'rule_completion_plan.json'
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = plan['summary']
    text = ['# 教师批注规则化完成清单', '', '本文件是来源绑定的实施草案，不是规则审批或规则可靠性验收。', '',
            f"唯一批注 {summary['unique_comments']} 条；文字批注 {summary['text_comments']} 条。",
            f"候选类别 {summary['family_count']} 类，直接关联教师支持检查 {summary['direct_linked_families']} 类，未直接关联教师支持检查 {summary['no_direct_checker_families']} 类。",
            '可靠性验收与人工签审均未完成；关联数量不作为可靠规则完成数量。', '',
            '## 互斥处置状态', '', '| 状态 | 批注数 |', '| --- | ---: |']
    labels = {'linked_checker_candidate_not_approved': '已归类并匹配教师支持的局部检查，未审定',
              'classified_without_direct_checker': '已归类，该批注未匹配教师支持的直接检查',
              'semantic_disposition_pending': '文字语义处置待核对',
              'empty_or_media_evidence_pending': '空文字或媒体证据待处理'}
    for key, count in summary['dispositions'].items():
        text.append(f'| {labels[key]} | {count} |')
    text.extend(['', '每条批注仅进入一个状态；多个类别关联不会重复计数。关联须匹配实际检查所用教师证据的源 SHA 和批注 ID，而不是仅匹配类别名称。',
                 'NP008、NP009、NP010 为 3 项补充结构检查，不计入教师要求直接实现的主数量。即使某类别有局部检查，也不能把该类全部教师批注计为覆盖。', '',
            f"## {summary['no_direct_checker_families']} 类未直接关联教师支持检查的实施优先次序", '',
                 '顺序按支持学生人数及教师证据数排列；频次只用于实施优先级。', ''])
    for row in plan['missing_checker_priority']:
        text.extend([f"### {row['title']} · {row['id']}", '',
                     f"支持学生：{row['supporting_students']}；{row['proposal']['implementation']}", '',
                     f"证据限制：{row['proposal']['evidence_gate']}", ''])
    text.extend(['## 逐条批注处置', ''])
    for i, row in enumerate(plan['comment_dispositions'], 1):
        text.extend([f"### {i}. {row.get('student_name')} · r{row.get('revision')} · 批注 {row.get('comment_id')}", '',
                     f"标识：`{row['id']}`", '', f"处置：{labels[row['disposition']]}；类别：{', '.join(row['rule_ids']) or '尚未确定'}。", '',
                     '教师批注原文：', '', row.get('text') or '（无可提取文字，媒体信息见 JSON）', '',
                     f"来源：{row.get('source_path')}", ''])
    (output / '批注处置与实施优先级.md').write_text('\n'.join(text), encoding='utf-8')
    specs = ['# 44 类检查项目规格草案', '', '所有条目未签审，未启用可靠自动判错。完整来源、教师原话及观察锚点保存在 JSON；观察素材不作为独立正例真值。', '']
    for row in plan['rule_specification_drafts']:
        specs.extend([f"## {row['title']} · {row['id']}", '',
                      f"现有教师支持检查：{', '.join(row['checker_ids']) or '未直接关联'}。", '',
                      f"补充结构代码关联（不计教师要求实现）：{', '.join(r['id'] for r in row['supplemental_code_associations']) or '无'}。", '',
                      f"适用范围草案：{row['scope']}", '',
                      f"实施判据草案：{row['decision_criteria']['implementation']}", '',
                      f"缺证据/拒判条件：{row['decision_criteria']['evidence_gate']}", '',
                      f"合成误报边界（非人工真值）：{row['negative_examples']['items'][0]['text']}", '',
                      '现有局部检查范围：', ''])
        for checker in row['checker_scopes']:
            specs.extend([f"- {checker['id']}：{checker['scope']}"])
        specs.extend(['', '教师原话与来源：', ''])
        for evidence in row['teacher_evidence']:
            specs.extend([f"- {evidence.get('student_name')} · r{evidence.get('revision')} · 批注 {evidence.get('comment_id')}：{str(evidence.get('quote', '')).replace(chr(10), ' / ')}；SHA256 `{evidence.get('source_sha256')}`"])
        specs.extend(['', '审批/独立正反例：待补；完成条件为来源核对、适用范围确认、独立真值验证和签审冻结。', ''])
    (output / '检查项目规格草案.md').write_text('\n'.join(specs), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    library_path = args.root / 'data/teacher_rule_library/library.json'
    library_bytes = library_path.read_bytes()
    library_hash = hashlib.sha256(library_bytes).hexdigest()
    library = json.loads(library_bytes.decode('utf-8'))
    for document in library['documents']:
        path = Path(document['source_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != document['sha256']:
            raise ValueError('Teacher source hash mismatch: ' + str(path))
    rules = observed_checker_rules(library, args.root)
    plan = build_completion_plan(library, rules)
    if hashlib.sha256(library_path.read_bytes()).hexdigest() != library_hash:
        raise ValueError('Library changed during read-only generation')
    plan['source_verification'] = {'library_file_sha256': library_hash,
                                 'verified_teacher_documents': len(library['documents']),
                                 'text_evidence_only_comments': sum(bool(c.get('text_evidence_only')) for c in library['comments']),
                                 'incomplete_anchor_comments': sum(c.get('anchor', {}).get('status') != 'complete' for c in library['comments']),
                                 'comments_with_media': sum(bool(c.get('comment_media')) for c in library['comments']),
                                 'formal_library_unchanged_during_generation': True,
                                 'mode': 'read_only_local'}
    plan['plan_sha256'] = canonical_sha({k: v for k, v in plan.items() if k != 'plan_sha256'})
    write_completion_plan(args.output, plan)
    recovery = {'library_sha256': plan['library_sha256'],
                'composition': semantic_pending_composition(library),
                'examples': context_recovery_examples(library),
                'external_requests': {'llm': 0, 'svn': 0, 'word': 0},
                'status': 'context_material_not_classification_or_approval'}
    (args.output / 'semantic_context_recovery.json').write_text(
        json.dumps(recovery, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(plan['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
