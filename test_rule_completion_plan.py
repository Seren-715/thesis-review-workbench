import copy
import unittest
from unittest.mock import patch

from rule_completion_plan import build_completion_plan, context_recovery_examples, semantic_pending_composition, observed_checker_rules


def fixture():
    evidence = dict(source_sha256='a'*64, comment_id='1', student_id='S1',
                    quote='原话', source_path='source.docx', anchor={'text': '观察原文'})
    rules = [dict(id=f, title=f, scope='local', supporting_student_ids=['S1'],
                  teacher_evidence=[evidence]) for f in ['first_person', 'cover_layout']]
    comments = [dict(id='1', text='文字', rule_ids=['first_person', 'cover_layout']),
                dict(id='2', text='文字', rule_ids=['cover_layout']),
                dict(id='3', text='?', rule_ids=[], status='semantic_review_required'),
                dict(id='4', text='', rule_ids=[], status='empty_or_image_comment')]
    for row in comments:
        row.update(source_sha256='a'*64, comment_id=row['id'])
    return dict(rules=rules, comments=comments), [dict(id='NP001', family='first_person', scope='正文',
                                                   teacher_evidence=[evidence], policy='teacher_specific_not_universal')]


class CompletionPlanTests(unittest.TestCase):
    def test_unrepresented_supplemental_and_disabled_families_are_not_teacher_feedback(self):
        library,_=fixture()
        rows=[dict(id='NP009',family='headings',policy='supplemental_structural_check',enabled=True,teacher_evidence=[]),
              dict(id='NP038',family='prediction_target',enabled=False,teacher_evidence=[])]
        with patch('new_paper_checks.load_teacher_rules',return_value=rows):
            self.assertEqual(observed_checker_rules(library,None),rows)
        rows.append(dict(id='invented',family='not_in_library',enabled=True,teacher_evidence=[{'quote':'要求'}]))
        with patch('new_paper_checks.load_teacher_rules',return_value=rows):
            with self.assertRaises(ValueError):observed_checker_rules(library,None)

    def test_unique_dispositions_and_no_false_approval(self):
        library, checkers = fixture()
        original = copy.deepcopy(library)
        plan = build_completion_plan(library, checkers)
        self.assertEqual(library, original)
        self.assertEqual(sum(plan['summary']['dispositions'].values()), 4)
        self.assertEqual(len(set(c['id'] for c in plan['comment_dispositions'])), 4)
        self.assertEqual(plan['summary']['direct_linked_families'], 1)
        self.assertEqual(plan['summary']['reliability_validated_families'], 0)
        self.assertTrue(all(not r['automatic_error_judgment_enabled'] for r in plan['rule_specification_drafts']))
        self.assertEqual(plan['comment_dispositions'][0]['unlinked_families'], ['cover_layout'])

    def test_unknown_family_and_duplicate_identity_rejected(self):
        library, checkers = fixture()
        library['comments'][0]['rule_ids'].append('invented')
        with self.assertRaises(ValueError):
            build_completion_plan(library, checkers)
        library, checkers = fixture()
        library['comments'][1]['id'] = '1'
        with self.assertRaises(ValueError):
            build_completion_plan(library, checkers)

    def test_observed_material_and_synthetic_boundary_not_truth(self):
        plan = build_completion_plan(*fixture())
        for rule in plan['rule_specification_drafts']:
            self.assertEqual(rule['positive_examples']['items'], [])
            self.assertEqual(rule['human_signoff']['status'], 'pending')
            self.assertEqual(rule['negative_examples']['items'][0]['label'], None)
            self.assertIn('not_independent_ground_truth', rule['observed_examples'][0]['status'])
        self.assertEqual(plan['external_requests'], {'llm': 0, 'svn': 0, 'word': 0})

    def test_processed_semantic_row_without_family_is_still_unresolved(self):
        library, checkers = fixture()
        library['comments'][2]['status'] = 'semantic_candidate'
        result = build_completion_plan(library, checkers)
        self.assertEqual(result['comment_dispositions'][2]['disposition'], 'semantic_disposition_pending')
        self.assertEqual(result['summary']['dispositions']['semantic_disposition_pending'], 1)

    def test_complete_anchor_converts_one_based_index_before_binding(self):
        library = {'comments': [dict(status='semantic_review_required', id='x', text='这里要给出建模的效果描述',
                                    source_path='a.docx', source_sha256='abc',
                                    anchor={'status': 'complete', 'paragraph_indices': [2], 'text': '目标原文'})]}
        paragraphs = [{'index': 0, 'text': '前文'}, {'index': 1, 'text': '目标原文'}, {'index': 2, 'text': '后文'}]
        with patch('new_paper_checks.read_docx', return_value=(None, None, 'abc', 0)), patch('new_paper_checks.extract_paragraphs', return_value=paragraphs):
            rows = context_recovery_examples(library)
        self.assertEqual(rows[0]['context_read_status'], 'same_source_neighbour_paragraphs_verified')
        self.assertFalse(rows[0]['context_complete_chapter'])

    def test_incomplete_anchor_does_not_read_document(self):
        library = {'comments': [dict(status='semantic_review_required', id='x', text='最终提示词是什么样子要给出',
                                    anchor={'status': 'incomplete', 'paragraph_indices': [2]})]}
        with patch('new_paper_checks.read_docx') as read:
            result = context_recovery_examples(library)
            read.assert_not_called()
        self.assertEqual(result[0]['context_read_status'], 'refused_incomplete_anchor')

    def test_pending_work_buckets_are_exclusive_not_semantic_labels(self):
        library = {'comments': [dict(id=str(i), text=text, status='semantic_review_required') for i, text in enumerate(['？', '在哪里？', '要给出实验效果', '章节标题'])]}
        result = semantic_pending_composition(library)
        self.assertEqual(sum(result['counts'].values()), 4)
        self.assertTrue(all(v == 1 for v in result['counts'].values()))
        self.assertTrue(all(r['semantic_decision'] == 'not_performed' for r in result['items']))

    def test_supplemental_code_and_unbound_same_family_do_not_count(self):
        library, checkers = fixture()
        checkers.append(dict(id='NP010', family='cover_layout', scope='补充结构',
                             policy='supplemental_structural_check', teacher_evidence=[]))
        library['comments'].append(dict(id='5', comment_id='5', source_sha256='a'*64,
                                       text='另一个范围', rule_ids=['first_person']))
        result = build_completion_plan(library, checkers)
        self.assertEqual(result['summary']['direct_linked_families'], 1)
        self.assertEqual(result['summary']['supplemental_structural_checker_count'], 1)
        self.assertEqual(result['comment_dispositions'][-1]['disposition'], 'classified_without_direct_checker')


if __name__ == '__main__':
    unittest.main()
