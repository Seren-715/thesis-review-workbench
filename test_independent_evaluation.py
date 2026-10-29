from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from independent_evaluation import create_registry, register_material, ground_truth_template, evaluate


class IndependentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'independent.docx'
        from docx import Document
        doc = Document()
        doc.add_paragraph('We implemented the system.')
        doc.add_paragraph('The system has no tests.')
        doc.add_paragraph('The system runs.')
        doc.save(self.source)
        self.registry = create_registry({'comments': [{'student_id': 'dev1', 'source_sha256': 'x'}]})
        self.att = {'reviewer': 'human', 'signed_at': '2026-10-10', 'not_used_in_rule_or_prompt_development': True}
        self.material = register_material(self.registry, 'T001', 'new1', self.source, 'independent_test', self.att)
        self.truth = ground_truth_template(self.material, ['NP001'])
        self.truth.update(human_signoff={'reviewer': 'human', 'signed_at': '2026-10-10'},
                          rule_coverage=[{'rule_id': 'NP001', 'applicable': True, 'exhaustive_for_rule': True}],
                          cases=[{'id': 'G001', 'rule_id': 'NP001', 'paragraph_index': 0,
                                  'quote': 'We implemented', 'label': 'issue'},
                                 {'id': 'G002', 'rule_id': 'NP001', 'paragraph_index': 1,
                                  'quote': 'no tests', 'label': 'issue'},
                                 {'id': 'G003', 'rule_id': 'NP001', 'paragraph_index': 2,
                                  'quote': 'system runs', 'label': 'not_issue'}])
        self.report = {'source_sha256': self.material['source_sha256'], 'findings': [
            {'id': 'F001', 'rule_id': 'NP001', 'paragraph_index': 0,
             'semantic_review': {'decision': 'suggested_revision', 'validated_quote': True, 'quote': 'We implemented'}},
            {'id': 'F002', 'rule_id': 'NP001', 'paragraph_index': 2,
             'semantic_review': {'decision': 'suggested_revision', 'validated_quote': True, 'quote': 'system runs'}},
            {'id': 'F003', 'rule_id': 'NP001', 'paragraph_index': 1, 'status': 'needs_review'}]}
        self.package = {'rules': [{'id': 'first_person', 'checker_ids': ['NP001'],
                                  'human_signoff': {'status': 'approved', 'reviewer': 'human',
                                                    'signed_at': '2026-10-10', 'approved_scope': 'scope'}}]}

    def test_case_metrics_with_independent_human_truth(self):
        result = evaluate(self.registry, 'T001', self.truth, self.report, self.package)
        self.assertEqual(result['precision'], .5)
        self.assertEqual(result['recall'], .5)
        self.assertEqual(result['unconfirmed_finding_count'], 1)
        self.assertTrue(result['independent_accuracy_test'])

    def test_development_exposure_cannot_be_relabelled_independent(self):
        with self.assertRaises(ValueError):
            register_material(self.registry, 'T002', 'dev1', self.source, 'independent_test', self.att)

    def test_student_folder_alias_cannot_bypass_exposure(self):
        registry = create_registry({'comments': [{'student_id': '211706315', 'source_sha256': 'old'}]})
        for alias in ['211706315_黄发平', r'D:\SVN虚拟助教\211706315_黄发平']:
            with self.assertRaises(ValueError):
                register_material(registry, 'T002', alias, self.source, 'independent_test', self.att)

    def test_same_student_new_version_cannot_bypass_exposure(self):
        other = Path(self.tmp.name) / 'different.docx'
        other.write_bytes(b'different version')
        with self.assertRaises(ValueError):
            register_material(self.registry, 'T002', 'new1', other, 'development')

    def test_unknown_history_requires_signed_human_attestation(self):
        registry = create_registry({'comments': []})
        with self.assertRaises(ValueError):
            register_material(registry, 'T002', 'new2', self.source, 'independent_test')

    def test_candidate_rules_or_incomplete_truth_produce_no_accuracy(self):
        self.package['rules'][0]['human_signoff']['status'] = 'pending'
        self.truth['rule_coverage'][0]['exhaustive_for_rule'] = False
        result = evaluate(self.registry, 'T001', self.truth, self.report, self.package)
        self.assertIsNone(result['precision'])
        self.assertIsNone(result['recall'])
        self.assertFalse(result['independent_accuracy_test'])

    def test_tampered_source_refused(self):
        self.source.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            evaluate(self.registry, 'T001', self.truth, self.report, self.package)

    def test_hallucinated_truth_and_prediction_refused(self):
        self.truth['cases'][0]['quote'] = 'not present'
        with self.assertRaises(ValueError):
            evaluate(self.registry, 'T001', self.truth, self.report, self.package)
        self.truth['cases'][0]['quote'] = 'We implemented'
        self.report['findings'][0]['semantic_review']['quote'] = 'not present'
        with self.assertRaises(ValueError):
            evaluate(self.registry, 'T001', self.truth, self.report, self.package)

    def test_empty_predictions_precision_is_null_and_recall_zero(self):
        self.report['findings'] = []
        result = evaluate(self.registry, 'T001', self.truth, self.report, self.package)
        self.assertIsNone(result['precision'])
        self.assertEqual(result['recall'], 0)

    def test_visual_rules_cannot_silently_enter_text_quality_denominator(self):
        self.truth['rule_coverage'].append({'rule_id': 'NP007', 'applicable': True, 'exhaustive_for_rule': True})
        result = evaluate(self.registry, 'T001', self.truth, self.report, self.package)
        self.assertFalse(result['independent_accuracy_test'])
        self.assertEqual(result['unsupported_declared_rule_ids'], ['NP007'])
        self.assertIsNone(result['precision'])


if __name__ == '__main__': unittest.main()
