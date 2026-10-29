import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory

from teacher_semantic_content_checks import (load_semantic_content_rules,
    check_semantic_content, bind_uml_vision_context, DEFINITIONS)


def library():
    return {'rules': [dict(id=row[2], teacher_evidence=[dict(
        quote='归类片段', comment_text='完整教师要求\n后续限定：不要机械判错。',
        anchor={'text': '原始批注定位'}, canonical_identity=['student', '/paper', 1, 'sha', '1'])])
        for row in DEFINITIONS]}


class SemanticTeacherTests(unittest.TestCase):
    def test_original_full_comments_and_anchors_preserved_without_library_mutation(self):
        lib = library(); before = deepcopy(lib)
        rules = load_semantic_content_rules(lib)
        self.assertEqual(lib, before)
        self.assertTrue(all(r['teacher_evidence'][0]['quote'].startswith('完整教师要求\n') for r in rules))
        self.assertTrue(all(r['teacher_evidence'][0]['anchor']['text'] == '原始批注定位' for r in rules))
        self.assertTrue(all(not r['automatic_error_judgment_enabled'] for r in rules))
        self.assertTrue(all(r['teacher_signoff_status'] == 'pending' for r in rules))

    def test_no_source_or_no_text_produces_no_empty_manual_cards(self):
        findings, coverage = check_semantic_content([], load_semantic_content_rules({'rules': []}), 'sha')
        self.assertEqual(findings, [])
        self.assertEqual({c['status'] for c in coverage}, {'not_applicable'})
        findings, coverage = check_semantic_content([], load_semantic_content_rules(library()), 'sha')
        self.assertEqual(findings, [])
        self.assertTrue(all(c['status'] == 'partial' for c in coverage))

    def test_complete_sections_include_later_evidence_tables_and_exclude_acknowledgments(self):
        rows = [dict(index=0, text='3 需求分析', heading_level=1),
                dict(index=1, text='先进的系统仅此介绍。'),
                dict(index=2, text='3.1 功能需求', heading_level=2),
                dict(index=3, text='用户上传论文。'),
                dict(index=4, text='3.2 非功能需求', heading_level=2),
                dict(index=5, text='兼容性要求', in_table=True),
                dict(index=6, text='后文列出对比实验依据：耗时降低20%。'),
                dict(index=7, text='致谢', heading_level=1),
                dict(index=8, text='先进的导师')]
        findings, _ = check_semantic_content(rows, load_semantic_content_rules(library()), 'sha')
        self.assertEqual(len(findings), 3)
        for f in findings:
            self.assertIn(6, f['content_scope']['paragraph_indices'])
            self.assertIn(5, f['content_scope']['paragraph_indices'])
            self.assertNotIn(8, f['content_scope']['paragraph_indices'])
            self.assertEqual(f['status'], 'candidate')
            self.assertNotIn('semantic_review', f)

    def test_long_scope_runs_existing_queue_through_all_chunks(self):
        from content_context_agent import ContextReviewQueue
        from test_content_context_agent import FakeBudget
        rows = [dict(index=0, text='3 需求分析', heading_level=1)] + [
            dict(index=i, text=('具体内容兼容性说明。' * 120)) for i in range(1, 10)]
        rules = load_semantic_content_rules(library())[:1]
        findings, coverage = check_semantic_content(rows, rules, 'a'*64)
        report = dict(source_sha256='a'*64, findings=findings, rules=rules,
                      coverage=coverage, limitations=[])
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            budget = FakeBudget(queue.state)
            self.assertTrue(queue.step(budget))
            self.assertNotIn('semantic_review', findings[0])
            while queue.has_pending():
                self.assertTrue(queue.step(budget))
            f = findings[0]
            self.assertEqual(set(f['content_read_progress']['read_chunk_ids']),
                {c['id'] for c in f['content_scope']['chunks']})
            self.assertTrue(f['semantic_review']['validated_scope'])
            self.assertEqual(budget.packets[-1]['mode'], 'synthesize')
            self.assertIn('后续限定：不要机械判错。', budget.packets[-1]['teacher_quotes'][0])

    def test_uml_caption_and_stale_readability_never_become_uml_proof(self):
        rows = [dict(index=0, text='3 系统设计', heading_level=1),
                dict(index=1, text='典型业务由双方参与。'),
                dict(index=2, text='图3-1 咨询用例图')]
        rules = load_semantic_content_rules(library())
        findings, _ = check_semantic_content(rows, rules, 'sha')
        findings.append(dict(id='NP007-0001', rule_id='NP007', paragraph_index=2,
            figure_evidence=dict(source_sha256='sha', preview_sha256='pixels',
                caption='图3-1 咨询用例图', reviewable=True),
            vision_review=dict(validated_image=True, source_sha256='sha',
                preview_sha256='old-pixels', context_key='stale', observations=['图很清晰'])))
        report = dict(findings=findings, rules=rules, source_sha256='sha')
        bind_uml_vision_context(report)
        plan = findings[2]['vision_review_plan']
        self.assertEqual(plan['figures'][0]['decision'], 'needs_review')
        self.assertFalse(plan['figures'][0]['readability_review_validated'])
        self.assertFalse(plan['semantic_image_review_complete'])
        self.assertNotIn('readability_context', plan['figures'][0])

    def test_valid_readability_is_only_context_and_no_matching_pixels_is_explicit(self):
        from paper_vision_review import context_key
        rows = [dict(index=0, text='3 系统设计', heading_level=1), dict(index=1, text='文字场景说明。')]
        rules = load_semantic_content_rules(library())
        findings, _ = check_semantic_content(rows, rules, 'sha')
        report = dict(findings=findings, rules=rules, source_sha256='sha')
        bind_uml_vision_context(report)
        self.assertEqual(findings[2]['vision_review_plan']['missing_evidence'], 'matching_figure_pixels')
        figure = dict(id='NP007-0001', rule_id='NP007', paragraph_index=1,
                      figure_evidence=dict(source_sha256='sha', preview_sha256='pixels'))
        figure['vision_review'] = dict(validated_image=True, source_sha256='sha',
            preview_sha256='pixels', context_key=context_key(figure, {}), visible_text=['用户'])
        findings.append(figure)
        bind_uml_vision_context(report)
        row = findings[2]['vision_review_plan']['figures'][0]
        self.assertTrue(row['readability_review_validated'])
        self.assertEqual(row['readability_context']['visible_text'], ['用户'])
        self.assertFalse(row['semantic_image_review_complete'])


if __name__ == '__main__':
    unittest.main()
