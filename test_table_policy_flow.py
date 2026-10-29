"""Verify explicit table-format policy across HTTP, service, and agent boundaries.

All runners, model clients, and Word collectors in this suite are fake. Tests
never launch Word, contact SVN, or make a paid model request.
"""
from copy import deepcopy
from hashlib import sha256
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

from atomic_json import read_json, write_json
from new_paper_agent import run_review
from web_backend import server
from web_backend.new_paper_service import NewPapers
from web_backend.test_new_paper_service import docx_bytes, FakeJobs, wait_complete


INVALID_POLICIES = (None, 0, 1, 'true', [], {})


def report_fixture(source, student_name='测试学生'):
    return {
        'status': 'local_checks_completed', 'student_name': student_name,
        'file_name': source.name, 'source_sha256': sha256(source.read_bytes()).hexdigest(),
        'findings': [], 'rules': [], 'coverage': [], 'paragraphs': [],
        'limitations': [], 'summary': {}, 'table_count': 0, 'image_count': 0,
    }


class TablePolicyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.calls = []

        def runner(source, output, **options):
            self.assertTrue(callable(options['cancel_check']))
            self.calls.append({**deepcopy({k:v for k,v in options.items() if k!='cancel_check'}),'cancel_check':options['cancel_check']})
            report = report_fixture(source, options['student_name'])
            report['run'] = {'known_total_tokens': 0, 'request_attempts': 0,
                             'three_line_tables': options['three_line_tables']}
            write_json(output/'report.json', report)
            write_json(output/'run.json', report['run'])
            (output/'report.md').write_text('本地测试报告', encoding='utf-8')
            return report

        self.service = NewPapers(Path(self.temp.name)/'papers', runner)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def upload(self, **options):
        created = self.service.upload(docx_bytes(), '测试论文.docx', '测试学生', **options)
        return wait_complete(self.service, created['id'])

    def test_upload_defaults_to_unconfirmed_policy(self):
        paper = self.upload()
        self.assertIs(paper['three_line_tables'], False)
        self.assertIs(self.calls[0]['three_line_tables'], False)
        saved = read_json(self.service.folder(paper['id'])/'paper.json')
        self.assertIs(saved['three_line_tables'], False)

    def test_explicit_policy_is_forwarded_and_full_review_can_disable_it(self):
        paper = self.upload(three_line_tables=True)
        self.assertIs(paper['three_line_tables'], True)
        self.assertIs(self.calls[0]['three_line_tables'], True)
        previous = self.service.folder(paper['id'])/paper['review_id']/'report.json'
        previous_bytes = previous.read_bytes()
        source_bytes = (self.service.folder(paper['id'])/'source.docx').read_bytes()
        fresh = self.service.review(paper['id'], three_line_tables=False)
        fresh = wait_complete(self.service, fresh['id'])
        self.assertIs(fresh['three_line_tables'], False)
        self.assertIs(self.calls[-1]['three_line_tables'], False)
        self.assertIn('prior_report', self.calls[-1])
        self.assertEqual(previous.read_bytes(), previous_bytes)
        self.assertEqual((self.service.folder(paper['id'])/'source.docx').read_bytes(), source_bytes)

    def test_invalid_upload_policy_rejected_before_key_file_or_runner(self):
        with patch.dict('os.environ', {}, clear=True):
            for value in INVALID_POLICIES:
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, '三线表'):
                        self.service.upload(b'invalid-document', '论文.docx',
                                            use_agent=True, three_line_tables=value)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.service.root.iterdir()), [])

    def test_invalid_review_policy_keeps_existing_revision_and_metadata(self):
        paper = self.upload(three_line_tables=True)
        saved = self.service.folder(paper['id'])/'paper.json'
        before = saved.read_bytes()
        with patch.dict('os.environ', {}, clear=True):
            for value in INVALID_POLICIES:
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, '三线表'):
                        self.service.review(paper['id'], use_agent=True, three_line_tables=value)
        self.assertEqual(saved.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(list(self.service.folder(paper['id']).glob('review_*'))), 1)

    def test_figures_only_review_preserves_saved_true_and_false_policies(self):
        for original in (True, False):
            with self.subTest(original=original):
                paper = self.upload(three_line_tables=original, chapter_policy=original)
                fresh = self.service.review(paper['id'], use_agent=True, api_key='test-key',
                    review_scope='figures', chapter_policy=not original,
                    three_line_tables=not original)
                fresh = wait_complete(self.service, fresh['id'])
                self.assertIs(fresh['three_line_tables'], original)
                self.assertIs(fresh['chapter_policy'], original)
                self.assertIs(self.calls[-1]['three_line_tables'], original)
                self.assertEqual(self.calls[-1]['review_scope'], 'figures')

    def test_same_source_cache_retained_when_local_policy_changes(self):
        first = self.upload(three_line_tables=True)
        second = self.upload(three_line_tables=False)
        self.assertFalse(self.calls[-1]['use_agent'])
        self.assertEqual(self.calls[-1]['prior_report']['source_sha256'], first['sha256'])
        self.assertEqual(len(self.calls[-1]['additional_prior_reports']), 1)
        self.assertEqual(second['same_source_evidence_candidates'][0]['paper_id'], first['id'])
        self.assertEqual(second['report']['run']['known_total_tokens'], 0)
        self.assertEqual(second['report']['run']['request_attempts'], 0)


class TablePolicyHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(server, 'ROOT', self.root)
        self.root_patch.start()
        self.options = []

        def runner(source, output, **options):
            self.assertTrue(callable(options['cancel_check']))
            self.options.append({**deepcopy({k:v for k,v in options.items() if k!='cancel_check'}),'cancel_check':options['cancel_check']})
            report = report_fixture(source, options['student_name'])
            report['run'] = {'known_total_tokens': 0, 'request_attempts': 0}
            write_json(output/'report.json', report)
            write_json(output/'run.json', report['run'])
            return report

        self.service = NewPapers(self.root/'data/new_papers', runner)
        self.workspace = server.Workspace(lambda: ({'documents': []}, {}))
        self.workspace.refresh()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0),
            server.create_handler(self.workspace, FakeJobs(), 0, self.service))
        self.port = self.http.server_port
        base = server.create_handler(self.workspace, FakeJobs(), self.port, self.service)

        class QuietHandler(base):
            def log_message(self, *args):
                pass

        self.http.RequestHandlerClass = QuietHandler
        self.thread = Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.service.close()
        self.root_patch.stop()
        self.temp.cleanup()

    def request(self, path, payload, content_type='application/json', headers=None):
        auth = {'Content-Type': content_type, 'X-Session-Token': self.workspace.session_token}
        auth.update(headers or {})
        conn = HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('POST', path, body=payload, headers=auth)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def upload(self, policy='absent'):
        headers = {'X-File-Name': quote('论文.docx'), 'X-Student-Name': quote('测试学生')}
        if policy != 'absent':
            headers['X-Three-Line-Tables'] = policy
        return self.request('/api/new-papers', docx_bytes(), 'application/octet-stream', headers)

    def review(self, paper_id, **options):
        payload = {'use_agent': False, 'chapter_policy': False, **options}
        return self.request('/api/new-papers/'+paper_id+'/review', json.dumps(payload))

    def test_upload_header_default_and_explicit_values(self):
        for header, expected in (('absent', False), ('true', True), ('false', False)):
            with self.subTest(header=header):
                status, paper = self.upload(header)
                self.assertEqual(status, 202)
                completed = wait_complete(self.service, paper['id'])
                self.assertIs(completed['three_line_tables'], expected)
                self.assertIs(self.options[-1]['three_line_tables'], expected)

    def test_invalid_upload_header_creates_nothing(self):
        for value in ('True', '1', '', 'undefined'):
            with self.subTest(value=value):
                status, body = self.upload(value)
                self.assertEqual(status, 400)
                self.assertIn('true 或 false', body['error'])
        self.assertEqual(self.options, [])
        self.assertEqual(list(self.service.root.iterdir()), [])

    def test_review_json_explicit_true_and_default_false(self):
        status, paper = self.upload('false')
        self.assertEqual(status, 202)
        wait_complete(self.service, paper['id'])
        for options, expected in (({'three_line_tables': True}, True), ({}, False)):
            with self.subTest(options=options):
                status, fresh = self.review(paper['id'], **options)
                self.assertEqual(status, 202)
                completed = wait_complete(self.service, fresh['id'])
                self.assertIs(completed['three_line_tables'], expected)
                self.assertIs(self.options[-1]['three_line_tables'], expected)

    def test_invalid_review_json_does_not_start_runner(self):
        status, paper = self.upload('true')
        self.assertEqual(status, 202)
        paper = wait_complete(self.service, paper['id'])
        previous_id = paper['review_id']
        for value in INVALID_POLICIES:
            with self.subTest(value=value):
                status, body = self.review(paper['id'], three_line_tables=value)
                self.assertEqual(status, 400)
                self.assertIn('三线表', body['error'])
        self.assertEqual(len(self.options), 1)
        self.assertEqual(self.service.get(paper['id'])['review_id'], previous_id)

    def test_figures_json_does_not_change_existing_policy(self):
        status, paper = self.upload('true')
        self.assertEqual(status, 202)
        wait_complete(self.service, paper['id'])
        options = {'use_agent': True, 'chapter_policy': False,
                   'review_scope': 'figures', 'three_line_tables': False}
        status, paper = self.request('/api/new-papers/'+paper['id']+'/review',
                                    json.dumps(options), headers={'X-DeepSeek-Key': 'test-key'})
        self.assertEqual(status, 202)
        paper = wait_complete(self.service, paper['id'])
        self.assertIs(paper['three_line_tables'], True)
        self.assertIs(self.options[-1]['three_line_tables'], True)


class TablePolicyAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'source.docx'
        self.source.write_bytes(docx_bytes())
        self.report = report_fixture(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def run_local(self, **options):
        with (patch('new_paper_checks.execute_checks', return_value=deepcopy(self.report)) as checks,
              patch('paper_report.render_report', return_value='本地测试报告')):
            result = run_review(self.source, self.root/'result', collect_word=False, **options)
        return result, checks

    def test_default_policy_reaches_checker_as_false(self):
        result, checks = self.run_local()
        self.assertIs(checks.call_args.kwargs['three_line_tables'], False)
        self.assertIs(result['run']['three_line_tables'], False)
        self.assertEqual(result['requests'], {'llm': 0, 'svn': 0, 'word': 0})

    def test_explicit_policy_reaches_checker_as_true(self):
        result, checks = self.run_local(three_line_tables=True)
        self.assertIs(checks.call_args.kwargs['three_line_tables'], True)
        self.assertIs(result['run']['three_line_tables'], True)

    def test_invalid_policy_precedes_directory_check_and_model(self):
        with patch('new_paper_checks.execute_checks') as checker, patch('openai.OpenAI') as client:
            for index, value in enumerate(INVALID_POLICIES):
                with self.subTest(value=value):
                    output = self.root/f'invalid-{index}'
                    with self.assertRaisesRegex(ValueError, '三线表'):
                        run_review(self.root/'missing.docx', output, use_agent=True, three_line_tables=value)
                    self.assertFalse(output.exists())
        checker.assert_not_called()
        client.assert_not_called()

    def test_policy_forwarded_again_after_fake_word_layout(self):
        report = deepcopy(self.report)
        report['table_count'] = 1
        layout = {'status': 'native_table_layout_collected_visual_pending',
                  'source': {'sha256': report['source_sha256']}, 'source_unchanged': True,
                  'pdf': {'path': str(self.root/'fake.pdf'), 'sha256': 'fake-test-hash'},
                  'tables': []}
        with (patch('new_paper_checks.execute_checks', side_effect=[deepcopy(report), deepcopy(report)]) as checker,
              patch('new_paper_layout.collect_layout', return_value=layout) as word,
              patch('paper_report.render_report', return_value='本地测试报告')):
            result = run_review(self.source, self.root/'result', three_line_tables=True)
        self.assertEqual(checker.call_count, 2)
        self.assertTrue(all(call.kwargs['three_line_tables'] is True for call in checker.call_args_list))
        word.assert_called_once()
        self.assertEqual(result['requests']['llm'], 0)

    def test_cached_semantic_evidence_kept_during_local_policy_change_without_model(self):
        finding = {'id': 'NP001-0001', 'rule_id': 'NP001', 'status': 'candidate',
                   'semantic_required': True, 'text': '我们设计了系统。', 'start': 0, 'end': 1,
                   'title': '第一人称表述', 'reason': '正文我字线索', 'suggestion': ''}
        self.report['findings'] = [finding]
        self.report['rules'] = [{'id': 'NP001', 'checker': 'first_person', 'enabled': True,
                                 'scope': '正文', 'teacher_evidence': []}]
        previous = deepcopy(self.report)
        from review_contract import review_identity
        previous['review_contract']=review_identity()
        previous['findings'][0]['semantic_review'] = {
            'id': finding['id'], 'decision': 'suggested_revision', 'quote': finding['text'],
            'reason': '作者叙述使用第一人称。', 'suggestion': '改为本研究设计了系统。',
            'validated_quote': True}
        model = Mock()
        model.chat.completions.create.side_effect = AssertionError('A cached local refresh must not call a model')
        result, _ = self.run_local(three_line_tables=False, prior_report=previous, client=model)
        self.assertEqual(result['run']['semantic_evidence_reused'], 1)
        self.assertTrue(result['findings'][0]['semantic_review']['validated_quote'])
        self.assertEqual(result['findings'][0]['semantic_review']['decision'], 'suggested_revision')
        self.assertEqual(result['requests']['llm'], 0)
        self.assertEqual(result['run']['known_total_tokens'], 0)
        model.chat.completions.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
