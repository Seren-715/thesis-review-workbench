"""Offline counterexamples for completion accounting and source identity."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
import json

from teacher_task_coverage import build_task_coverage, result_is_complete
from thesis_workflow import ThesisWorkflow, save


class TaskCoverageTests(TestCase):
    def test_completed_automatic_review_keeps_manual_verification_separate(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, path, _ = self.fixture(root, 'partial', 'agent')
            report = json.loads(path.read_text(encoding='utf8'))
            report['summary']['result_groups'] = {'unreviewed': 0, 'pending': 1}
            report['run'].update(progress={'remaining':0,'blocked':0},completion={'status':'completed_with_manual'})
            save(path, report)
            result = build_task_coverage(root, [])
            self.assertEqual(result['initials']['summary']['automatic_review_complete_students'], 1)
            self.assertEqual(result['initials']['summary']['fully_completed_students'], 0)
            source.write_bytes(b'changed')
            self.assertEqual(build_task_coverage(root, [])['initials']['summary']['automatic_review_complete_students'], 0)

    def fixture(self, root, status='partial', mode='local'):
        source = root/'source.docx'
        source.write_bytes(b'original')
        digest = sha256(source.read_bytes()).hexdigest()
        save(root/'data/teacher_rule_library/library.json', {'rules': [], 'comments': []})
        save(root/'data/thesis_workflow/discovery.json', {'snapshot_revision': 30,
            'students': [{'id': '123456_甲', 'name': '甲', 'initial_revision': 7},
                         {'id': '234567_乙', 'name': '乙', 'initial_revision': 8}]})
        report = root/'data/thesis_workflow/initial_checks/123456_甲/job_a/review/report.json'
        save(report, {'source_sha256': digest, 'status': status, 'coverage': [{'rule_id': 'NP001', 'status': 'partial' if status == 'partial' else 'checked'}],
            'summary': {'result_groups': {'unreviewed': 1 if status == 'partial' else 0, 'pending': 0}},
            'run': {'mode': mode, 'request_attempts': 0 if mode == 'local' else 1}})
        row = {'student_id': '123456_甲', 'revision': 7, 'report_json_path': str(report),
               'source_path': str(source), 'source_sha256': digest, 'status': status}
        job = {'id': 'job_a', 'created_at': '2020', 'options': {'use_agent': mode == 'agent'},
               'result_summary': {'students': [row]}}
        save(root/'data/thesis_workflow/jobs/job_a.json', job)
        save(root/'data/thesis_workflow/runs/job_a/run.json', job)
        return source, report, job

    def test_duplicate_jobs_and_orphan_report_do_not_inflate_student_count(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            value = build_task_coverage(root, [])
            self.assertEqual(value['initials']['summary']['report_students'], 1)
            self.assertEqual(value['initials']['summary']['fully_completed_students'], 0)
            self.assertEqual(value['initials']['summary']['not_checked_students'], 1)
            self.assertEqual(value['initials']['students'][0]['status'], 'local_only')

    def test_modified_source_is_invalid_not_completed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, _, _ = self.fixture(root, 'completed', 'agent')
            source.write_bytes(b'modified')
            value = build_task_coverage(root, [])
            self.assertEqual(value['initials']['students'][0]['status'], 'source_invalid')
            self.assertEqual(value['initials']['summary']['fully_completed_students'], 0)
            self.assertTrue(value['issues'])

    def test_new_rules_do_not_erase_local_only_mode_accounting(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            value = build_task_coverage(root, [{'id': 'NP024', 'title': '新检查项', 'enabled': True}])
            self.assertEqual(value['initials']['students'][0]['status'], 'incomplete')
            self.assertEqual(value['initials']['summary']['local_only_students'], 1)
            self.assertFalse(value['initials']['students'][0]['agent_enabled'])

    def test_no_recent_job_limit_and_mismatched_revision_does_not_count(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            for i in range(35):
                save(root/f'data/thesis_workflow/jobs/z{i}.json', {'id': f'z{i}', 'created_at': '2025', 'result_summary': {}})
            value = build_task_coverage(root, [])
            self.assertEqual(value['initials']['summary']['report_students'], 1)
            discovery = json.loads((root/'data/thesis_workflow/discovery.json').read_text(encoding='utf-8'))
            discovery['students'][0]['initial_revision'] = 9
            save(root/'data/thesis_workflow/discovery.json', discovery)
            self.assertEqual(build_task_coverage(root, [])['initials']['summary']['report_students'], 0)

    def test_missing_or_malformed_catalog_is_unknown_not_zero_success(self):
        with TemporaryDirectory() as folder:
            value = build_task_coverage(Path(folder), [])
            self.assertIsNone(value['rules']['summary']['candidate_families'])
            self.assertIsNone(value['initials']['summary']['fully_completed_students'])
            self.assertEqual(value['status'], 'needs_review')

    def test_completed_requires_coverage_and_no_remaining_objects(self):
        self.assertFalse(result_is_complete({'status': 'completed'}))
        self.assertFalse(result_is_complete({'status': 'completed', 'coverage': [{'status': 'checked'}],
                                            'summary': {'result_groups': {'unreviewed': 1}}}))
        self.assertTrue(result_is_complete({'status': 'completed', 'coverage': [{'status': 'checked'}]}))

    def test_parent_job_partial_when_child_report_is_partial(self):
        with TemporaryDirectory() as folder:
            workflow = ThesisWorkflow(Path(folder))
            job = {'id': 'job_test', 'action': 'check-initials', 'label': '核查', 'created_at': '2020',
                   'options': {}, 'status': 'queued'}
            workflow.jobs[job['id']] = job
            try:
                with patch.object(workflow, 'check_initials', return_value={'checked': 1, 'failed_or_unavailable': 0,
                        'students': [{'status': 'partial', 'summary': {'result_groups': {'unreviewed': 3}}}]}):
                    workflow._run(job, None)
                self.assertEqual(job['status'], 'partial')
            finally:
                workflow.close()

    def test_get_endpoint_is_read_only_and_returns_missing_data_as_unknown(self):
        from web_backend.server import Workspace, create_handler
        with TemporaryDirectory() as folder:
            workflow = ThesisWorkflow(Path(folder))
            workspace = Workspace(lambda: ({'title': 'test'}, {}))
            workspace.refresh()
            class Jobs:
                def recent(self): return []
            handler = create_handler(workspace, Jobs(), 0, thesis_workflow=workflow)
            class Quiet(handler):
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
            live = create_handler(workspace, Jobs(), server.server_port, thesis_workflow=workflow)
            class QuietLive(live):
                def log_message(self, *args): pass
            server.RequestHandlerClass = QuietLive
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection('127.0.0.1', server.server_port)
                connection.request('GET', '/api/task-coverage',
                                   headers={'X-Session-Token': workspace.session_token})
                response = connection.getresponse()
                value = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertIsNone(value['initials']['summary']['fully_completed_students'])
                self.assertFalse((Path(folder)/'data/teacher_rule_library/library.json').exists())
            finally:
                server.shutdown(); server.server_close(); thread.join(); workflow.close()

    def test_contextual_classification_excludes_previously_reviewed_ids(self):
        self.contextual_classification_receipt_case(matching_contract=True)

    def test_changed_context_contract_does_not_skip_previous_review(self):
        self.contextual_classification_receipt_case(matching_contract=False)

    def contextual_classification_receipt_case(self, matching_contract):
        from teacher_rule_library import classification_contract
        with TemporaryDirectory() as folder:
            workflow = ThesisWorkflow(Path(folder))
            job = {'id': 'job_test', 'action': 'reclassify', 'label': '复核', 'created_at': '2020',
                   'options': {'max_requests': 4, 'max_reserved_tokens': 24000}, 'status': 'queued',
                   'llm_requests': 0, 'known_total_tokens': 0}
            workflow.jobs[job['id']] = job
            library = {'source_fingerprint': 'original_source',
                       'comments': [{'id': 'old', 'text': '修改摘要', 'anchor_text': '摘要第一段',
                                     'anchor': {'context_paragraphs': [{'text': '摘要第二段'}]}}],
                       'summary': {'total_comments': 1, 'rule_count': 1, 'unclassified_comments': 0},
                       'classification_run': {'status': 'completed', 'still_uncertain_comment_ids': ['old']}}
            contract = classification_contract(library)
            if not matching_contract:
                old_library = json.loads(json.dumps(library))
                old_library['comments'][0]['anchor']['context_paragraphs'] = []
                contract = classification_contract(old_library)
                self.assertNotEqual(contract, classification_contract(library))
            save(Path(folder)/'data/thesis_workflow/runs/old/classification/classification_run.json',
                 {'review_existing': True, 'classified_comment_ids': ['old'], 'classification_contract': contract})
            try:
                with patch('teacher_rule_library.get_library', return_value=library), \
                     patch('teacher_rule_library.classify_library', return_value=library) as classify, \
                     patch('teacher_rule_library.publish_classification', return_value=library), \
                     patch('openai.OpenAI', return_value=object()):
                    workflow._run(job, 'test_key')
                self.assertTrue(classify.call_args.kwargs['review_existing'])
                self.assertEqual(classify.call_args.kwargs['reviewed_comment_ids'], ['old'] if matching_contract else [])
                self.assertEqual(job['result_summary']['still_uncertain_comments'], 1)
                self.assertEqual(job['status'], 'partial')
            finally:
                workflow.close()

    def test_all_student_cli_continues_after_valid_partial_batch(self):
        import paper_workflow
        class Workflow:
            root = Path('D:/svn_agent')
            def active(self): return False
            def snapshot(self): return {'discovery': {'students': [{'id': str(i)} for i in range(17)]}}
            def close(self): pass
        with patch.object(paper_workflow, 'ThesisWorkflow', return_value=Workflow()), \
             patch.object(paper_workflow, 'save'), \
             patch.object(paper_workflow, 'wait_job', return_value={'status': 'partial',
                 'result_summary': {'checked': 10, 'failed_or_unavailable': 0}}) as wait, \
             patch('sys.argv', ['paper_workflow.py', 'check-initials', '--all-students']):
            self.assertEqual(paper_workflow.main(), 1)
            self.assertEqual(wait.call_count, 2)
            self.assertEqual(len(wait.call_args_list[0].args[2]['student_ids']), 10)
            self.assertEqual(len(wait.call_args_list[1].args[2]['student_ids']), 7)
