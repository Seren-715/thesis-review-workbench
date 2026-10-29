"""Regression tests for missing continuation and chapter-outline false scopes."""
import unittest

from content_review_context import collect_content_scope
from test_expanded_paper_checks import execute, p


def row(index, text, level=None, **flags):
    return {'index': index, 'text': text, 'heading_level': level,
            'location': {'heading_path': ['第五章 系统实现'], 'section': '第五章 系统实现',
                         'paragraph_in_section': index, 'label': f'正文 {index}'}, **flags}


class CompleteContentScopeTests(unittest.TestCase):
    def test_abstract_following_paragraph_is_preserved_and_keywords_excluded(self):
        rows = [row(0, '摘要'), row(1, 'Linux背景与任务。'),
                row(2, '系统实现在线实验、学习进度追踪，采用容器隔离，测试证明减少维护时间。'),
                row(3, '关键词：Linux；实训'), row(4, '1 绪论', 1), row(5, '后续正文。')]
        selected, scope = collect_content_scope(rows, 'abstract', 'abc')
        self.assertEqual([p['index'] for p in selected], [1, 2])
        self.assertEqual(scope['excluded_paragraph_indices'], [3])
        self.assertEqual(scope['source_sha256'], 'abc')
        self.assertIn('测试证明', scope['chunks'][1]['text'])

    def test_large_abstract_all_characters_reconstruct_without_truncation(self):
        text = '开源系统训练说明。' * 350 + '结尾真正的效果说明。'
        _, scope = collect_content_scope([row(0, '摘要'), row(1, text)], 'abstract')
        self.assertEqual(''.join(c['text'] for c in scope['chunks']), text)
        self.assertTrue(all(len(c['text']) <= 1000 for c in scope['chunks']))
        self.assertTrue(scope['complete'])
        for chunk in scope['chunks']:
            self.assertEqual(text[chunk['start']:chunk['end']], chunk['text'])

    def test_keywords_end_chinese_abstract_before_english_title(self):
        rows = [row(0, '摘要'), row(1, '中文背景。'), row(2, '中文方法与效果。'),
                row(3, '关键词：Linux；实训'), row(4, 'Linux Practical Teaching System'),
                row(5, 'Abstract'), row(6, 'English abstract body.')]
        selected, _ = collect_content_scope(rows, 'abstract')
        self.assertEqual([p['index'] for p in selected], [1, 2])

    def test_new_chinese_abstract_title_reopens_after_keywords(self):
        rows = [row(0, '摘要'), row(1, '第一份摘要。'), row(2, '关键词：Linux'),
                row(3, '英文题目'), row(4, '中文摘要'), row(5, '第二份中文摘要。')]
        selected, _ = collect_content_scope(rows, 'abstract')
        self.assertEqual([p['index'] for p in selected], [1, 5])

    def test_keywords_embedded_in_body_do_not_delete_body_or_continuation(self):
        rows = [row(0, '摘要'), row(1, '系统实现训练管理并减少部署时间。关键词：Linux'),
                row(2, '测试效果说明。')]
        selected, _ = collect_content_scope(rows, 'abstract')
        self.assertEqual([p['index'] for p in selected], [1, 2])

    def test_inferred_false_implementation_path_does_not_create_real_scope(self):
        rows = [row(0, '1 绪论', 1), row(1, '1.4 论文结构安排', 2),
                row(2, '第五章介绍系统实现的过程。'), row(3, '最后是论文对本课题进行总结。')]
        selected, scope = collect_content_scope(rows, 'implementation')
        self.assertEqual(selected, [])
        self.assertEqual(scope['total_characters'], 0)

    def test_real_implementation_subsections_tables_code_and_empty_heading_map(self):
        rows = [row(0, '5 系统实现', 1), row(1, '5.1 训练模块', 2), row(2, '读取数据库记录。'),
                row(3, '用户ID与练习记录', in_table=True), row(4, 'for record in data: train(record)'),
                row(5, '5.2 结束模块', 2), row(6, '6 总结与展望', 1), row(7, '成果总结。')]
        selected, scope = collect_content_scope(rows, 'implementation')
        self.assertEqual([p['index'] for p in selected], [2, 3, 4])
        self.assertEqual(scope['heading_indices'], [0, 1, 5])
        self.assertEqual(scope['chunks'][1]['role'], 'table_cell')
        self.assertIn('for record', scope['chunks'][2]['text'])

    def test_deleted_toc_textbox_and_thanks_never_contaminate_scope(self):
        rows = [row(0, '摘要'), row(1, '真实摘要'), row(2, '不纳入删除', in_deleted=True),
                row(3, '不纳入目录', in_toc=True), row(4, '不纳入文本框', in_textbox=True),
                row(5, '致谢', 1), row(6, '我感谢导师')]
        selected, _ = collect_content_scope(rows, 'abstract')
        self.assertEqual([p['text'] for p in selected], ['真实摘要'])

    def test_conclusion_nested_paragraphs_all_available_until_next_real_chapter(self):
        rows = [row(0, '6 总结与展望', 1), row(1, '成果。'), row(2, '6.1 不足', 2),
                row(3, '未覆盖大量并发用户。'), row(4, '参考文献', 1), row(5, '[1] 资料')]
        selected, _ = collect_content_scope(rows, 'conclusion')
        self.assertEqual([p['index'] for p in selected], [1, 3])

    def test_implementation_chapter_summary_is_not_whole_paper_conclusion(self):
        rows = [row(0, '5 系统实现', 1), row(1, '5.4 本章总结', 2), row(2, '本章说明功能实现。')]
        self.assertEqual(collect_content_scope(rows, 'conclusion')[0], [])
        self.assertEqual([p['index'] for p in collect_content_scope(rows, 'implementation')[0]], [2])

    def test_unstyled_conclusion_and_recommendations_preserves_both_subsections(self):
        rows = [row(0, '英文摘要'), row(1, 'English abstract.'), row(2, '目录'),
                row(3, '第5章 结论与建议', in_toc=True),
                row(4, '第1章 绪论'), row(5, '真实正文。'),
                row(6, '第5章 结论与建议'), row(7, '5.1 研究总结'),
                row(8, '实际成果及不足的完整说明。'), row(9, '5.2 未来研究方向'),
                row(10, '未来模型、数据和解释性改进。'), row(11, '参考文献'),
                row(12, '[1] 文献内容。'), row(13, '致谢'), row(14, '感谢教师。'),
                row(15, '附录'), row(16, '附录原文。')]
        selected, scope = collect_content_scope(rows, 'conclusion', 'sha')
        self.assertEqual([p['index'] for p in selected], [8, 10])
        self.assertEqual(scope['heading_indices'], [6, 7, 9])
        self.assertFalse(scope['heading_compliance_evidence'])
        self.assertEqual(next(h['evidence_role'] for h in scope['headings'] if h['paragraph_index']==7), 'read_boundary_only')

    def test_unstyled_data_and_model_subsections_supply_content_not_compliance_evidence(self):
        rows = [row(0, '英文摘要'), row(1, 'English text.'), row(2, '目录'),
                row(3, '第3章 研究方法'), row(4, '3.1 数据来源'), row(5, '数据来自真实公开来源。'),
                row(6, '3.2 数据处理'), row(7, '实际清洗和划分说明。'),
                row(8, '3.3 模型选择'), row(9, '实际模型与对照设置。'),
                row(10, '第4章 实验结果'), row(11, '真实实验结果说明。'),
                row(12, '参考文献'), row(13, '数据来源的参考条目。')]
        data, data_scope = collect_content_scope(rows, 'data_provenance')
        model, model_scope = collect_content_scope(rows, 'model_comparison')
        self.assertEqual([p['index'] for p in data], [5])
        self.assertEqual([p['index'] for p in model], [9, 11])
        self.assertFalse(data_scope['heading_compliance_evidence'])
        self.assertFalse(model_scope['heading_compliance_evidence'])

    def test_plain_next_chapter_ends_abstract_even_without_keyword_marker(self):
        rows = [row(0, '摘要'), row(1, '摘要背景。'), row(2, '摘要方法与效果。'),
                row(3, '第1章 绪论'), row(4, '正文背景，不属于摘要。')]
        selected, _ = collect_content_scope(rows, 'abstract')
        self.assertEqual([p['index'] for p in selected], [1, 2])

    def test_terminal_separator_in_unstyled_data_heading_keeps_original_text(self):
        rows = [row(0, '第3章 研究方法'), row(1, '3.1. 数据集介绍\t'),
                row(2, '数据来源为腾讯股票，包含十年每日交易数据。'),
                row(3, '3.2. 数据预处理步骤'), row(4, '真实数据清洗、划分及标准化说明。')]
        selected, scope = collect_content_scope(rows, 'data_provenance')
        self.assertEqual([p['index'] for p in selected], [2, 4])
        self.assertEqual(scope['headings'][0]['text'], '3.1. 数据集介绍\t')
        self.assertTrue(all(h['evidence_role']=='read_boundary_only' for h in scope['headings']))

    def test_unstyled_organization_sentence_does_not_create_implementation_scope(self):
        rows = [row(0, '第1章 绪论'), row(1, '1.4 论文结构安排'),
                row(2, '第五章介绍系统实现的过程。'), row(3, '最后是论文对本课题进行总结。'),
                row(4, '第5章 是实现部分，介绍了系统的功能。')]
        self.assertEqual(collect_content_scope(rows, 'implementation')[0], [])

    def test_postbody_numbered_lists_do_not_open_semantic_ranges(self):
        rows = [row(0, '致谢'), row(1, '5 系统实现'), row(2, '感谢系统实现指导。'),
                row(3, '参考文献'), row(4, '3.3 模型选择'), row(5, '书名内容。')]
        self.assertEqual(collect_content_scope(rows, 'implementation')[0], [])
        self.assertEqual(collect_content_scope(rows, 'model_comparison')[0], [])

    def test_full_scope_hash_changes_on_later_effects_evidence(self):
        a = [row(0, '摘要'), row(1, '背景。'), row(2, '效果好。')]
        b = [row(0, '摘要'), row(1, '背景。'), row(2, '效果未验证。')]
        self.assertNotEqual(collect_content_scope(a, 'abstract')[1]['scope_sha256'],
                            collect_content_scope(b, 'abstract')[1]['scope_sha256'])

    def test_expanded_checker_exposes_full_scope_independent_of_display_preview(self):
        library = {'rules': [{'id': 'abstract_keywords', 'teacher_evidence': [{'quote': '说明背景功能技术效果'}]}]}
        findings, coverage = execute(p('摘要') + p('背景。' * 700) + p('真正效果位于后文。') + p('1 绪论', 1), library)
        f = next(f for f in findings if f['rule_id'] == 'NP011')
        self.assertTrue(f['section_complete'])
        self.assertFalse(f['preview_complete'])
        self.assertIn('真正效果', f['content_scope']['chunks'][-1]['text'])
        c = next(c for c in coverage if c['rule_id'] == 'NP011')
        self.assertTrue(c['source_scope_complete'])
        self.assertFalse(c['semantic_review_complete'])


if __name__ == '__main__':
    unittest.main()
