from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
from thesis_workflow import ThesisWorkflow, reserved_evaluation_students, save


class EvaluationCollectionIsolationTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workflow = ThesisWorkflow(self.root, invoke=lambda *a, **kw: self.fail('no SVN allowed'))
        self.sid = '123456_未见学生'
        self.discovery = {'students': [{'id': self.sid, 'name': '未见学生', 'teacher_version_count': 2,
            'documents': [{'teacher_versions': [{'revision': 9}]}]}], 'total_students': 1}
        save(self.root/'data/thesis_workflow/discovery.json', self.discovery)
        self.discovery_patch = patch.object(self.workflow, '_discovery', return_value=self.discovery)
        self.discovery_patch.start()
        self.job = {'id': 'job_isolation', 'action': 'collect', 'status': 'completed', 'created_at': '2026',
            'options': {'student_ids': [self.sid], 'max_versions': 2}, 'log_tail': ''}
    def tearDown(self):
        self.discovery_patch.stop()
        self.workflow.close()
        self.temp.cleanup()
    def candidate(self, value):
        path = self.root/'data/independent_evaluation/arbitrary_candidate/prospective_candidate.json'
        save(path, value)
        return path
    def test_unsigned_pending_candidate_never_exports_reads_or_refreshes(self):
        self.candidate({'student_id': '123456', 'split': 'unsigned_prospective_candidate',
            'ground_truth_status': 'pending', 'independence_attestation': None})
        with patch.object(self.workflow, '_export', side_effect=AssertionError('no export')), \
             patch('teacher_rule_library.get_library', side_effect=AssertionError('no library scan')):
            result = self.workflow.collect(self.job)
        self.assertEqual(result['students'][0]['status'], 'skipped_reserved_for_independent_evaluation')
        self.assertEqual(result['processed_students'], 0)
        self.assertEqual(result['skipped_reserved_students'], 1)
        self.assertEqual(result['failed_count'], 0)
        self.assertEqual(result['exported_versions'], 0)
        self.assertIsNone(result['library_summary'])
        self.assertFalse((self.root/'data/thesis_workflow/exports').exists())
        student = self.workflow.snapshot()['discovery']['students'][0]
        self.assertFalse(student['teacher_collection_allowed'])
        self.assertEqual(student['teacher_collection_status'], 'reserved_for_independent_evaluation')
    def test_registry_independent_holdout_reserved_but_development_allowed(self):
        save(self.root/'data/independent_evaluation/registry.json', {'materials': [
            {'student_id': self.sid, 'split': 'independent_test'},
            {'student_id': '987654_开发', 'split': 'development'}]})
        reserved = reserved_evaluation_students(self.root)
        self.assertEqual(set(reserved), {'123456'})
        with patch('teacher_rule_library.get_library', side_effect=AssertionError('no refresh')):
            self.assertEqual(self.workflow.collect(self.job)['skipped_reserved_students'], 1)
    def test_corrupt_candidate_blocks_entire_teacher_collection(self):
        path = self.candidate({'student_id': '123456'})
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError): self.workflow.collect(self.job)
        self.assertTrue(self.workflow.snapshot()['discovery']['teacher_collection_disabled'])
        self.assertFalse((self.root/'data/thesis_workflow/exports').exists())
    def test_missing_id_or_unknown_registry_group_does_not_fail_open(self):
        self.candidate({'split': 'unsigned_prospective_candidate'})
        with self.assertRaises(ValueError): self.workflow.collect(self.job)
        with TemporaryDirectory() as other:
            root = Path(other)
            save(root/'data/independent_evaluation/registry.json', {'materials': [{'student_id': '123456', 'split': 'unknown'}]})
            with self.assertRaises(ValueError): reserved_evaluation_students(root)


if __name__ == '__main__': main()
