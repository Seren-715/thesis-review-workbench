import unittest
import xml.etree.ElementTree as ET

from content_context_agent import ReadContentScopeTool
from teacher_extended_checks import (
    W, DEFINITIONS, load_extended_rules, collect_extended_scope, check_extended_rules,
)


def row(i, text, level=None, **flags):
    return dict(index=i, text=text, heading_level=level, location={'section': '系统设计'}, **flags)


def rule(family):
    rules = load_extended_rules({'rules': [dict(id=family, teacher_evidence=[dict(
        quote='要添加2篇英文文献' if family == 'references' else '教师明确要求',
        student_name='测试学生', source_sha256='teacher-sha', comment_id='7')])]})
    return next(r for r in rules if r['family'] == family)


def table_document(caption=None, single=False):
    root = ET.Element(W+'document')
    body = ET.SubElement(root, W+'body')
    rows = []
    def paragraph(parent, text, table=False):
        p = ET.SubElement(parent, W+'p')
        ET.SubElement(ET.SubElement(p, W+'r'), W+'t').text = text
        rows.append(row(len(rows), text, in_table=table))
    if caption:
        paragraph(body, caption)
    tbl = ET.SubElement(body, W+'tbl')
    for texts in ([['提示词说明']] if single else [['字段名', '数据类型'], ['id', 'int']]):
        tr = ET.SubElement(tbl, W+'tr')
        for text in texts:
            paragraph(ET.SubElement(tr, W+'tc'), text, True)
    return root, rows


class ExtendedTeacherMethodTests(unittest.TestCase):
    def test_missing_evidence_never_enables_teacher_norm(self):
        rules = load_extended_rules({'rules': []})
        self.assertEqual(len(rules), 15)
        self.assertTrue(all(not r['enabled'] for r in rules))
        findings, coverage = check_extended_rules(ET.Element(W+'document'), [], rules, 'sha')
        self.assertEqual(findings, [])
        self.assertTrue(all(c['status'] == 'not_applicable' for c in coverage))

    def test_new_families_only_enable_when_source_is_present(self):
        for family in ('model_effect_explanation', 'prompt_specification', 'prediction_target',
                       'module_inventory', 'data_analysis_explanation'):
            self.assertTrue(rule(family)['enabled'])
            self.assertFalse(rule(family)['automatic_error_judgment_enabled'])

    def test_bibliography_example_is_not_language_requirement(self):
        rules = load_extended_rules({'rules': [dict(id='references', teacher_evidence=[dict(
            quote='Blei D M. Latent Dirichlet Allocation[J]. 2003.')])]})
        self.assertFalse(next(r for r in rules if r['family'] == 'references')['enabled'])

    def test_quoted_requirement_and_original_source_id_preserved(self):
        r = rule('references')
        self.assertEqual(r['teacher_evidence'][0]['quote'], '要添加2篇英文文献')
        self.assertEqual(r['teacher_evidence'][0]['comment_id'], '7')
        self.assertEqual(r['teacher_evidence'][0]['source_sha256'], 'teacher-sha')

    def test_large_late_context_is_lossless(self):
        text = '真实实现说明。'*950 + '最后的测试说明与性能目标相符。'
        paragraphs = [row(0, '1 绪论', 1), row(1, '设计目标。'),
                      row(2, '5 系统实现', 1), row(3, text), row(4, '致谢'), row(5, '无关致谢。')]
        selected, scope = collect_extended_scope(paragraphs, 'body', 'implementation_claims', 'sha')
        self.assertEqual([p['index'] for p in selected], [0, 1, 2, 3])
        self.assertEqual(''.join(c['text'] for c in scope['chunks'] if c['paragraph_index'] == 3), text)
        self.assertTrue(all(len(c['text']) <= 1000 for c in scope['chunks']))
        self.assertEqual(scope['source_sha256'], 'sha')

    def test_inferred_navigation_never_changes_actual_range(self):
        paragraphs = [row(0, '1 绪论', 1), row(1, '本文最后说明模块。'),
                      row(2, '致谢'), row(3, '系统设计说明但实际位于致谢。')]
        selected, scope = collect_extended_scope(paragraphs, 'design', 'module_inventory', 'sha')
        self.assertEqual([p['index'] for p in selected], [0, 1])
        self.assertEqual(scope['scope_fallback'], 'complete_visible_body')

    def test_prompt_appendix_and_table_cells_are_readable(self):
        paragraphs = [row(0, '4 系统实现', 1), row(1, '任务说明。'), row(2, '附录'),
                      row(3, '最终提示词：仅依据资料回答。', in_table=True), row(4, '附录后文。')]
        selected, scope = collect_extended_scope(paragraphs, 'prompts', 'prompt_specification', 'sha')
        self.assertEqual([p['index'] for p in selected], list(range(5)))
        self.assertEqual(next(c['role'] for c in scope['chunks'] if c['paragraph_index'] == 3), 'table_cell')

    def test_citation_scope_includes_following_explanation_and_every_reference(self):
        paragraphs = [row(0, '1 引言', 1), row(1, '前文说明。'), row(2, '方法来源[2]。'),
                      row(3, '后文限定范围。'), row(4, '参考文献'),
                      row(5, '[1] 文献一。'), row(6, '[2] 文献二。'), row(7, '致谢'), row(8, '无关。')]
        selected, scope = collect_extended_scope(paragraphs, 'citations', 'citation_placement', 'sha')
        self.assertEqual([p['index'] for p in selected], [1, 2, 3, 4, 5, 6])
        self.assertFalse(scope['external_sources_read'])

    def test_keyword_checks_read_complete_abstract_not_only_first_paragraph(self):
        paragraphs = [row(0, '摘要'), row(1, '研究背景。'), row(2, '实现功能、方法与效果。'),
                      row(3, '关键词：Linux；实训'), row(4, '1 绪论', 1), row(5, '课题的具体范围。')]
        selected, _ = collect_extended_scope(paragraphs, 'keywords', 'keyword_specificity', 'sha')
        self.assertEqual([p['index'] for p in selected], list(range(6)))

    def test_missing_actual_headings_still_supplies_all_readable_context(self):
        findings, coverage = check_extended_rules(ET.Element(W+'document'),
            [row(0, '模块有三个，后文介绍功能。')], [rule('module_inventory')], 'sha')
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]['semantic_required'])
        self.assertTrue(findings[0]['content_scope']['body_boundary_unresolved'])
        self.assertEqual(findings[0]['content_scope']['chunks'][0]['text'], '模块有三个，后文介绍功能。')
        self.assertEqual(coverage[0]['status'], 'partial')

    def test_mixed_unstyled_chapters_do_not_remain_inside_english_abstract_or_toc(self):
        # Reproduces r194: front matter has clear titles, but only 1.1 uses a
        # real heading style. Previously NP028 retained six abstract paragraphs
        # and silently dropped all implementation/model/result paragraphs.
        paragraphs = [row(0, '摘要'), row(1, '摘要中的模型效果主张。'),
                      row(2, 'ABSTRACT'), row(3, 'English abstract prose.'),
                      row(4, '目录'), row(5, '第1章 绪论', in_toc=True),
                      row(6, '第1章 绪论'), row(7, '1.1 研究背景', 2),
                      row(8, '实际研究背景。'), row(9, '第2章 模型训练'),
                      row(10, '真实模型训练方法。'), row(11, '第3章 实验结果'),
                      row(12, '实际实验数据、指标及结果。'), row(13, '参考文献'),
                      row(14, '[1] 文献原文。'), row(15, '致谢'), row(16, '感谢指导。')]
        selected, scope = collect_extended_scope(paragraphs, 'body', 'implementation_claims', 'sha')
        self.assertEqual([p['index'] for p in selected], [0, 1, 6, 7, 8, 9, 10, 11, 12])
        self.assertEqual(scope['inferred_read_heading_indices'], [6, 9, 11])
        self.assertFalse(scope['heading_compliance_evidence'])
        inferred = [h for h in scope['headings'] if h['paragraph_index'] in (6, 9, 11)]
        self.assertTrue(all(h['evidence_role'] == 'read_boundary_only' for h in inferred))
        self.assertTrue(scope['complete'])

    def test_styled_subchapter_can_exit_frontmatter_without_parent_chapter(self):
        paragraphs = [row(0, '英文摘要'), row(1, 'English text.'),
                      row(2, '1.1 研究背景', 2), row(3, '研究问题的完整说明。'),
                      row(4, '2.1 系统实现', 2), row(5, '实现使用的实际方法。')]
        selected, _ = collect_extended_scope(paragraphs, 'body', 'implementation_claims', 'sha')
        self.assertEqual([p['index'] for p in selected], [2, 3, 4, 5])

    def test_numbered_organization_prose_and_postmatter_lists_do_not_reopen_body(self):
        paragraphs = [row(0, '第1章 绪论'), row(1, '第五章是实现部分，最后是论文对本课题进行总结。'),
                      row(2, '第5章 是实现部分，最后是论文对本课题进行总结。'),
                      row(3, '致谢'), row(4, '1 指导教师'), row(5, '感谢教师。'),
                      row(6, '2 同学'), row(7, '感谢同学。')]
        selected, scope = collect_extended_scope(paragraphs, 'body', 'implementation_claims', 'sha')
        self.assertEqual([p['index'] for p in selected], [0, 1, 2])
        self.assertEqual(scope['inferred_read_heading_indices'], [0])

    def test_plain_numeric_table_cell_is_never_a_chapter_boundary(self):
        paragraphs = [row(0, '第4章 系统设计'), row(1, '4.1 总体架构', in_table=True),
                      row(2, '真实正文说明。')]
        selected, scope = collect_extended_scope(paragraphs, 'design', 'architecture_fidelity', 'sha')
        self.assertEqual([p['index'] for p in selected], [0, 1, 2])
        self.assertEqual(scope['inferred_read_heading_indices'], [0])
        self.assertEqual(next(c['role'] for c in scope['chunks'] if c['paragraph_index'] == 1), 'table_cell')

    def test_terminal_separator_title_is_a_read_boundary_not_a_style_judgment(self):
        paragraphs = [row(0, '英文摘要'), row(1, 'English abstract.'),
                      row(2, '3.1. 数据集介绍'), row(3, '真实数据说明。')]
        selected, scope = collect_extended_scope(paragraphs, 'body', 'implementation_claims', 'sha')
        self.assertEqual([p['index'] for p in selected], [2, 3])
        self.assertFalse(scope['heading_compliance_evidence'])
        self.assertEqual(scope['headings'][0]['text'], '3.1. 数据集介绍')

    def test_toc_textbox_deleted_cannot_supply_evidence(self):
        paragraphs = [row(0, '4 系统设计', 1), row(1, '真实模块说明。'),
                      row(2, '错误目录', in_toc=True), row(3, '错误文本框', in_textbox=True),
                      row(4, '删除内容', in_deleted=True)]
        selected, _ = collect_extended_scope(paragraphs, 'design', 'module_inventory', 'sha')
        self.assertEqual([p['index'] for p in selected], [0, 1])

    def test_native_named_table_has_no_missing_name_candidate(self):
        document, paragraphs = table_document('表4-1 用户信息')
        findings, _ = check_extended_rules(document, paragraphs, [rule('table_caption_name')], 'sha')
        self.assertEqual(findings, [])

    def test_bare_table_number_is_candidate_not_human_confirmed(self):
        document, paragraphs = table_document('表4-1')
        findings, coverage = check_extended_rules(document, paragraphs, [rule('table_caption_name')], 'sha')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['status'], 'candidate')
        self.assertEqual(findings[0]['text'], '表4-1')
        self.assertNotIn('human_confirmed', findings[0])

    def test_missing_adjacent_caption_is_uncertain_not_automatic_error(self):
        document, paragraphs = table_document()
        findings, _ = check_extended_rules(document, paragraphs, [rule('table_caption_name')], 'sha')
        self.assertEqual(findings[0]['status'], 'needs_review')

    def test_single_cell_display_box_exempt(self):
        document, paragraphs = table_document(single=True)
        findings, coverage = check_extended_rules(document, paragraphs, [rule('table_caption_name')], 'sha')
        self.assertEqual(findings, [])
        self.assertEqual(coverage[0]['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()
