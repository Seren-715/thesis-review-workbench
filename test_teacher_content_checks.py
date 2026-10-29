import unittest
from content_review_context import collect_content_scope
from teacher_content_checks import load_content_rules, check_content_rules
from teacher_requirement_proposals import build_proposals
from test_content_review_context import row
from new_paper_agent import build_semantic_units, bind_unit_context, TOOL_RULES


class TeacherContentChecksTests(unittest.TestCase):
    def rules(self):
        return load_content_rules({'rules': [{'id': f, 'teacher_evidence': [{'quote': '教师原话'}]} for f in
            ('data_provenance', 'database_explanation', 'model_comparison')]})

    def test_no_teacher_evidence_disables_new_content_rules(self):
        self.assertTrue(all(not r['enabled'] for r in load_content_rules({'rules': []})))
        self.assertEqual(check_content_rules([], load_content_rules({'rules': []}), 'sha'), ([], []))

    def test_database_scope_preserves_all_table_cells_and_following_explanation(self):
        rows = [row(0, '4 系统设计', 1), row(1, '4.3 数据库设计', 2),
                row(2, '用户表用途及外键说明。'), row(3, 'user_id', in_table=True),
                row(4, '后续关系说明。'), row(5, '5 系统实现', 1), row(6, '不在本范围。')]
        selected, scope = collect_content_scope(rows, 'database_explanation', 'sha')
        self.assertEqual([p['index'] for p in selected], [2, 3, 4])
        self.assertEqual(scope['chunks'][1]['role'], 'table_cell')

    def test_keyword_in_normal_prose_or_inferred_location_cannot_establish_scope(self):
        rows = [row(0, '1 绪论', 1), row(1, '后面将介绍数据库设计、数据来源及模型比较。')]
        for family in ('data_provenance', 'database_explanation', 'model_comparison'):
            self.assertEqual(collect_content_scope(rows, family)[0], [])

    def test_missing_scope_produces_coverage_gap_without_issue_flood(self):
        findings, coverage = check_content_rules([row(0, '1 绪论', 1)], self.rules(), 'sha')
        self.assertEqual(findings, [])
        self.assertEqual(len(coverage), 3)
        self.assertTrue(all(c['status'] == 'partial' for c in coverage))

    def test_data_scope_excludes_thanks_toc_and_textboxes(self):
        rows = [row(0, '3 数据获取', 1), row(1, '合法来源说明。'), row(2, '目录', in_toc=True),
                row(3, '不可定位', in_textbox=True), row(4, '致谢'), row(5, '其他文字。')]
        self.assertEqual([p['index'] for p in collect_content_scope(rows, 'data_provenance')[0]], [1])

    def test_full_late_model_evidence_is_available_to_continuation_tools(self):
        rows = [row(0, '3 模型选择', 1), row(1, '模型背景。'),
                row(2, '中间原文。'*1000), row(3, '最后的模型评分与耗时对照。')]
        rules = self.rules()
        findings, _ = check_content_rules(rows, rules, 'sha')
        finding = next(f for f in findings if f['rule_id'] == 'NP026')
        report = dict(source_sha256='sha', paragraphs=rows)
        units = bind_unit_context(build_semantic_units(finding, rules[2]), finding, report)
        self.assertTrue(units)
        self.assertIn('最后的模型评分', finding['content_scope']['chunks'][-1]['text'])
        self.assertTrue(finding['semantic_required'])
        self.assertEqual(finding['status'], 'candidate')
        self.assertIn('NP026', TOOL_RULES['inspect_data_database_and_models'])

    def test_new_requirement_proposal_is_exact_quote_local_scope_and_disabled(self):
        comment = dict(id='hash:1', text='这里要给出建模的效果描述', status='semantic_review_required',
                       student_id='s1', source_sha256='hash', anchor={'status': 'complete'})
        result = build_proposals({'comments': [comment, dict(comment, id='hash:2')]})
        self.assertEqual(result['recurrence']['model_effect_explanation']['distinct_students'], 1)
        self.assertEqual(len(result['proposals']), 2)
        self.assertTrue(all(not r['enabled'] and r['quote'] in comment['text'] for r in result['proposals']))
        self.assertEqual(comment['status'], 'semantic_review_required')

    def test_generic_question_cannot_create_requirement_proposal(self):
        self.assertEqual(build_proposals({'comments': [{'id': 'x', 'text': '？', 'status': 'semantic_review_required'}]})['proposals'], [])


if __name__ == '__main__':
    unittest.main()
