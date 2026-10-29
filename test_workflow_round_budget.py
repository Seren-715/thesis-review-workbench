"""Cross-batch and crash-safe budget guards; all review runners are offline."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import paper_workflow
from atomic_json import read_json
from thesis_workflow import ThesisWorkflow, digest, review_budget_charge, save


class WorkflowRoundBudgetTests(TestCase):
    def cli(self, rows):
        with TemporaryDirectory() as directory:
            class FakeWorkflow:
                root = Path(directory)
                def active(self): return False
                def snapshot(self): return {'discovery': {'students': [{'id': str(i)} for i in range(17)]}}
                def close(self): pass
            with patch.object(paper_workflow, 'ThesisWorkflow', return_value=FakeWorkflow()), \
                 patch.object(paper_workflow, 'wait_job', side_effect=rows) as wait, \
                 patch('sys.argv', ['paper_workflow.py', 'check-initials', '--all-students', '--agent']), \
                 redirect_stdout(StringIO()):
                code = paper_workflow.main()
            ledger = read_json(next((Path(directory)/'data/thesis_workflow/cli_rounds').glob('*.json')))
            return code, wait.call_args_list, ledger

    def row(self, reserved=0, known=0, charged=None, unknown=0, **extra):
        return {'id': 'job', 'status': 'partial', 'reserved_token_bound': reserved,
                'budget_charge_token_bound': reserved if charged is None else charged,
                'known_total_tokens': known, 'unknown_cost_attempts': unknown,
                'result_summary': extra}

    def test_all_student_batches_share_one_reserved_cap(self):
        code, calls, ledger = self.cli([self.row(18000, 1500, 1500), self.row(6000, 700, 700)])
        self.assertEqual(code, 1)
        self.assertEqual(calls[0].args[2]['max_reserved_tokens'], 24000)
        self.assertEqual(calls[1].args[2]['max_reserved_tokens'], 6000)
        self.assertEqual(ledger['reserved_token_bound'], 24000)
        self.assertEqual(ledger['known_total_tokens'], 2200)
        self.assertEqual(ledger['remaining_token_bound'], 0)

    def test_unknown_cost_never_funds_next_batch(self):
        _, calls, ledger = self.cli([self.row(1000, unknown=1), self.row()])
        self.assertEqual(calls[1].args[2]['max_reserved_tokens'], 0)
        self.assertEqual(ledger['unknown_cost_attempts'], 1)

    def test_one_unavailable_student_does_not_omit_later_batch(self):
        _, calls, _ = self.cli([self.row(failed_or_unavailable=1), self.row()])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[1].args[2]['student_ids']), 7)

    def test_invalid_usage_cannot_be_misread_as_free(self):
        row = self.row(); row['known_total_tokens'] = None
        _, calls, ledger = self.cli([row])
        self.assertEqual(len(calls), 1)
        self.assertEqual(ledger['status'], 'stopped_invalid_usage')

    def test_verified_usage_releases_only_effective_cost_not_round_reserve(self):
        charge, unknown = review_budget_charge({'known_total_tokens': 100, 'reserved_token_bound': 7000,
            'unknown_cost_attempts': 1, 'requests': [
                {'reserved_upper_bound': 4000, 'usage': {'total_tokens': 100}},
                {'reserved_upper_bound': 3000, 'usage': None}]})
        self.assertEqual((charge, unknown), (3100, 1))
        with self.assertRaises(ValueError):
            review_budget_charge({'known_total_tokens': 100, 'reserved_token_bound': 7000, 'requests': []})

    def fixture(self, root, runner):
        workflow = ThesisWorkflow(root, review_runner=runner)
        original = root/'data/original.docx'; original.write_bytes(b'original')
        students = [{'id': f's{i}', 'name': f'学生{i}', 'initial_selection': 'earliest_available_pre_feedback_candidate',
                     'initial': {'revision': 1, 'repository_path': '/paper.docx'}} for i in range(2)]
        source = {'revision': 1, 'repository_path': '/paper.docx', 'exported_path': str(original), 'sha256': digest(original)}
        workflow._discovery = lambda: {'students': students}
        workflow._reusable_initial = lambda student: (original, source, None, None, None, None)
        job = {'id': 'job_test', 'created_at': '2026', 'status': 'running', 'options': {'student_ids': ['s0', 's1'],
               'chapter_policy': False, 'use_agent': True}, 'max_reserved_token_bound': 24000,
               'reserved_token_bound': 0, 'known_total_tokens': 0, 'unknown_cost_attempts': 0,
               'word_launches': 0, 'llm_requests': 0, 'log_tail': ''}
        return workflow, job

    def report(self, reserved, known):
        return {'status': 'partial', 'limitations': [], 'coverage': [{'rule_id': 'test', 'status': 'partial'}],
                'summary': {}, 'run': {'request_attempts': 1 if reserved else 0, 'known_total_tokens': known,
                    'reserved_token_bound': reserved, 'unknown_cost_attempts': 0, 'word_launches': 0}}

    def test_students_in_one_job_share_reserved_budget_and_live_progress(self):
        limits = []; progress = []
        with TemporaryDirectory() as directory:
            def runner(path, output, **options):
                limits.append(options['max_total_bound'])
                if len(limits) == 2:
                    progress.append(read_json(root/'data/thesis_workflow/jobs/job_test.json')['result_summary']['checked'])
                return self.report(24000 if len(limits) == 1 else 0, 500 if len(limits) == 1 else 0)
            root = Path(directory); workflow, job = self.fixture(root, runner)
            try:
                with patch('paper_report.render_report', return_value='offline mock report'):
                    result = workflow.check_initials(job, 'mock-key')
                self.assertEqual(limits, [24000, 0])
                self.assertEqual(progress, [1])
                self.assertEqual(result['checked'], 2)
                self.assertEqual(job['known_total_tokens'], 500)
                self.assertTrue(result['budget_exhausted'])
            finally: workflow.close()

    def test_durable_usage_survives_exception_after_api(self):
        limits = []
        with TemporaryDirectory() as directory:
            def runner(path, output, **options):
                limits.append(options['max_total_bound'])
                if len(limits) == 1:
                    save(output/'run.json', self.report(23000, 500)['run'])
                    raise OSError('derived report disk failure')
                return self.report(0, 0)
            root = Path(directory); workflow, job = self.fixture(root, runner)
            try:
                with patch('paper_report.render_report', return_value='offline mock report'):
                    result = workflow.check_initials(job, 'mock-key')
                self.assertEqual(limits, [24000, 1000])
                self.assertEqual(job['known_total_tokens'], 500)
                self.assertEqual(result['failed_or_unavailable'], 1)
                self.assertEqual(result['checked'], 1)
            finally: workflow.close()

    def test_missing_usage_after_exception_stops_paid_operations(self):
        limits = []
        with TemporaryDirectory() as directory:
            def runner(path, output, **options):
                limits.append(options['max_total_bound'])
                if len(limits) == 1: raise OSError('no durable usage')
                return self.report(0, 0)
            root = Path(directory); workflow, job = self.fixture(root, runner)
            try:
                with patch('paper_report.render_report', return_value='offline mock report'):
                    result = workflow.check_initials(job, 'mock-key')
                self.assertEqual(limits, [24000, 0])
                self.assertTrue(result['cost_unknown_stop'])
                self.assertEqual(result['remaining_token_bound'], 0)
            finally: workflow.close()

    def test_bad_optional_budget_rejected_before_dispatch(self):
        with TemporaryDirectory() as directory:
            workflow = ThesisWorkflow(Path(directory))
            workflow.snapshot = lambda: {'discovery': {'students': [{'id': 's'}]}}
            try:
                for bad in (None, -1, 24001, True):
                    with self.assertRaises(ValueError):
                        workflow.start('check-initials', {'student_ids': ['s'], 'use_agent': True,
                            'chapter_policy': False, 'max_reserved_tokens': bad}, api_key='mock-key')
            finally: workflow.close()

    def test_zero_budget_does_not_require_a_model_credential(self):
        with TemporaryDirectory() as directory:
            workflow = ThesisWorkflow(Path(directory))
            workflow.snapshot = lambda: {'discovery': {'students': [{'id': 's'}]}}
            try:
                with patch.dict('os.environ', {}, clear=True), patch.object(workflow.executor, 'submit'):
                    job = workflow.start('check-initials', {'student_ids': ['s'], 'use_agent': True,
                        'chapter_policy': False, 'max_reserved_tokens': 0})
                self.assertEqual(job['max_reserved_token_bound'], 0)
                self.assertEqual(job['llm_requests'], 0)
            finally: workflow.close()
