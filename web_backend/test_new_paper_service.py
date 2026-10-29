"""New upload/review tests use fake runners; no Word, SVN or paid API calls."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote
import zipfile

from web_backend.new_paper_service import NewPapers, MAX_FILE_BYTES, validate_docx, validate_key
from web_backend import server
from atomic_json import write_json


def docx_bytes():
    data = BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        package.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>我们设计了系统。</w:t></w:r></w:p></w:body></w:document>')
    return data.getvalue()


def body_docx_bytes(body=None, *, missing_body=False, styles=None, extra=None):
    data = BytesIO()
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body_xml = '' if missing_body else '<w:body>' + (body or '') + '</w:body>'
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        package.writestr('word/document.xml', f'<w:document xmlns:w="{ns}">{body_xml}</w:document>')
        if styles is not None:
            package.writestr('word/styles.xml', styles)
        for name, value in (extra or {}).items():
            package.writestr(name, value)
    return data.getvalue()


def fake_runner(path, output_dir, **options):
    report = {'status': 'local_checks_completed', 'student_name': options['student_name'], 'findings': [],
              'run': {'known_total_tokens': 0, 'request_attempts': 0}}
    for name, value in [('report.json', report), ('run.json', report['run'])]:
        write_json(output_dir/name, value)
    (output_dir/'report.md').write_text('本地检查报告', encoding='utf-8')
    return report


def wait_complete(service, paper_id):
    for _ in range(100):
        paper = service.get(paper_id)
        if paper['status'] not in {'running', 'queued','cancelling'}:
            return paper
        time.sleep(.01)
    raise AssertionError('Fake runner did not complete')


class NewPaperServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.service = NewPapers(Path(self.temp.name)/'new_papers', fake_runner)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_custom_lower_budget_is_forwarded_and_invalid_budget_creates_no_paper(self):
        seen=[]
        def runner(path,output,**options):
            seen.append(options)
            return fake_runner(path,output,**options)
        self.service.runner=runner
        for options in ({'max_requests':True},{'max_requests':0},{'max_requests':17},
                        {'max_total_bound':999},{'max_total_bound':60001},{'max_total_bound':1.5}):
            with self.assertRaises(ValueError):self.service.upload(docx_bytes(),'budget.docx',**options)
        self.assertEqual(self.service.recent(),[])
        paper=wait_complete(self.service,self.service.upload(docx_bytes(),'budget.docx',max_requests=2,max_total_bound=7000)['id'])
        self.assertEqual(seen[0]['max_requests'],2)
        self.assertEqual(seen[0]['max_total_bound'],7000)
        self.assertTrue(callable(seen[0]['cancel_check']))
        self.assertEqual(paper['max_requests'],2)

    def test_cooperative_cancel_stops_only_selected_task_keeps_progress_and_blocks_archive(self):
        entered=Event()
        def runner(path,output,**options):
            entered.set()
            for _ in range(200):
                if options['cancel_check']():
                    report=fake_runner(path,output,**options)
                    report['run']['status']='cancelled'
                    write_json(output/'report.json',report)
                    return report
                time.sleep(.005)
            raise AssertionError('No cancellation received')
        self.service.runner=runner
        original=docx_bytes()
        paper=self.service.upload(original,'cancel.docx')
        self.assertTrue(entered.wait(2))
        with self.assertRaises(RuntimeError):self.service.archive(paper['id'])
        cancelled=self.service.cancel(paper['id'])
        self.assertIn(cancelled['status'],{'cancelling','cancelled'})
        final=wait_complete(self.service,paper['id'])
        self.assertEqual(final['status'],'cancelled')
        self.assertEqual(final['report']['run']['status'],'cancelled')
        self.assertEqual((self.service.folder(paper['id'])/'source.docx').read_bytes(),original)
        with self.assertRaises(RuntimeError):self.service.cancel(paper['id'])

    def test_history_is_read_only_bound_to_source_and_current_pointer_unchanged(self):
        paper=wait_complete(self.service,self.service.upload(docx_bytes(),'history.docx')['id'])
        old_id=paper['review_id'];old_folder=self.service.folder(paper['id'])/old_id
        old_bytes=(old_folder/'report.json').read_bytes()
        second=wait_complete(self.service,self.service.review(paper['id'])['id'])
        metadata=(self.service.folder(paper['id'])/'paper.json').read_bytes()
        rows=self.service.history(paper['id'])
        self.assertEqual(len(rows),2)
        old=self.service.historical(paper['id'],old_id)
        self.assertTrue(old['historical'])
        self.assertEqual(old['current_review_id'],second['review_id'])
        self.assertEqual(old['review_id'],old_id)
        self.assertEqual((old_folder/'report.json').read_bytes(),old_bytes)
        self.assertEqual((self.service.folder(paper['id'])/'paper.json').read_bytes(),metadata)
        self.assertEqual(self.service.get(paper['id'])['review_id'],second['review_id'])
        with self.assertRaises(ValueError):self.service.historical(paper['id'],'../run.json')
        wrong=dict(old['report'],source_sha256='wrong')
        write_json(old_folder/'report.json',wrong)
        with self.assertRaises(ValueError):self.service.historical(paper['id'],old_id)
        self.assertEqual(len(self.service.history(paper['id'])),1)

    def test_archive_hides_record_retains_source_rounds_and_survives_restart(self):
        original=docx_bytes()
        paper=wait_complete(self.service,self.service.upload(original,'archive.docx')['id'])
        folder=self.service.folder(paper['id'])
        receipt=self.service.archive(paper['id'])
        self.assertTrue(receipt['original_retained'])
        self.assertEqual(self.service.recent(),[])
        self.assertEqual((folder/'source.docx').read_bytes(),original)
        self.assertTrue((folder/paper['review_id']/'report.json').is_file())
        restored=NewPapers(self.service.root,fake_runner)
        try:self.assertEqual(restored.recent(),[])
        finally:restored.close()

    def test_content_scope_requires_agent_and_preserves_existing_policy(self):
        seen=[]
        def runner(path, output, **options):
            seen.append(options)
            return fake_runner(path,output,**options)
        self.service.runner=runner
        paper=wait_complete(self.service,self.service.upload(docx_bytes(),'paper.docx',chapter_policy=True)['id'])
        with self.assertRaises(ValueError):
            self.service.review(paper['id'],use_agent=False,review_scope='content')
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':'test-key'}):
            self.service.review(paper['id'],use_agent=True,chapter_policy=False,review_scope='content')
        updated=wait_complete(self.service,paper['id'])
        self.assertEqual(updated['review_scope'],'content')
        self.assertEqual(seen[-1]['review_scope'],'content')
        self.assertTrue(seen[-1]['chapter_policy'])

    def test_identical_upload_offers_verified_cache_keeps_new_name_and_changed_bytes_do_not(self):
        from hashlib import sha256
        options_seen=[]
        def runner(path,output,**options):
            options_seen.append(options)
            r=fake_runner(path,output,**options);r['source_sha256']=sha256(path.read_bytes()).hexdigest()
            write_json(output/'report.json', r)
            return r
        self.service.runner=runner
        original=docx_bytes()
        first=wait_complete(self.service,self.service.upload(original,'old.docx','甲')['id'])
        second=wait_complete(self.service,self.service.upload(original,'new.docx','乙')['id'])
        self.assertEqual(second['student_name'],'乙')
        self.assertEqual(second['report']['student_name'],'乙')
        self.assertEqual(len(options_seen[1]['additional_prior_reports']),1)
        self.assertEqual(second['same_source_evidence_candidates'][0]['paper_id'],first['id'])
        changed=original+b'changed-zip-comment'
        third=wait_complete(self.service,self.service.upload(changed,'different.docx','丙')['id'])
        self.assertNotIn('additional_prior_reports',options_seen[2])
        self.assertNotIn('same_source_evidence_candidates',third)

    def test_real_docx_and_invalid_zip(self):
        validate_docx(docx_bytes())
        for content in [b'', b'not zip', b'%PDF-other-format']:
            with self.assertRaises(ValueError): validate_docx(content)
        fake = BytesIO()
        with zipfile.ZipFile(fake, 'w') as package:
            package.writestr('random.txt', 'other zip')
        with self.assertRaises(ValueError): validate_docx(fake.getvalue())

    def test_missing_body_and_empty_visible_body_rejected_before_creating_task(self):
        hidden_styles = '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="character" w:styleId="Hidden"><w:rPr><w:vanish/></w:rPr></w:style></w:styles>'
        documents = [
            body_docx_bytes(missing_body=True),
            body_docx_bytes(),
            body_docx_bytes('<w:p/><w:sectPr/>'),
            body_docx_bytes('<w:p><w:r><w:t>  </w:t></w:r></w:p>'),
            body_docx_bytes('<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>隐藏</w:t></w:r></w:p>'),
            body_docx_bytes('<w:p><w:del><w:r><w:t>删除</w:t></w:r></w:del></w:p>'),
            body_docx_bytes('<w:p><w:r><w:rPr><w:rStyle w:val="Hidden"/></w:rPr><w:t>隐藏</w:t></w:r></w:p>', styles=hidden_styles),
            body_docx_bytes('<w:p/>', extra={'word/media/orphan.png': b'unreferenced'}),
        ]
        with patch.object(self.service, 'runner') as runner:
            for document in documents:
                with self.subTest(size=len(document)):
                    with self.assertRaisesRegex(ValueError, '正文'):
                        self.service.upload(document, 'invalid.docx')
            runner.assert_not_called()
        self.assertEqual(self.service.recent(), [])
        self.assertFalse(list(self.service.root.glob('paper_*')))

    def test_upload_retains_native_image_table_and_formula_without_plain_text(self):
        documents = [
            body_docx_bytes('<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>'),
            body_docx_bytes('<w:p><w:r><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml"/></w:pict></w:r></w:p>'),
            body_docx_bytes('<w:p><m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x</m:t></m:r></m:oMath></w:p>'),
        ]
        for document in documents:
            with self.subTest(size=len(document)):
                validate_docx(document)
                paper = wait_complete(self.service, self.service.upload(document, 'objects.docx')['id'])
                self.assertEqual(paper['status'], 'completed')
                self.assertEqual((self.service.folder(paper['id'])/'source.docx').read_bytes(), document)

    def test_filename_traversal_and_key_validation(self):
        for name in ['../test.docx', '..\\test.docx', 'D:test.docx', 'old.doc', quote('../a.docx')]:
            with self.assertRaises(ValueError): self.service.upload(docx_bytes(), name)
        for key in ['\r\nsecret', 'a b', 'x'*513]:
            with self.assertRaises(ValueError): validate_key(key)
        self.assertEqual(self.service.recent(), [])

    def test_async_upload_and_restart_retains_report(self):
        original = docx_bytes()
        paper = self.service.upload(original, quote('张三的论文.docx'), quote('张三'))
        paper = wait_complete(self.service, paper['id'])
        self.assertEqual(paper['status'], 'completed')
        self.assertEqual(paper['file_name'], '张三的论文.docx')
        self.assertEqual(paper['report']['student_name'], '张三')
        second = NewPapers(self.service.root, fake_runner)
        try:
            self.assertEqual(second.get(paper['id'])['report'], paper['report'])
            self.assertEqual((second.folder(paper['id'])/'source.docx').read_bytes(), original)
        finally: second.close()

    def test_identity_update_changes_labels_without_changing_source(self):
        original = docx_bytes()
        paper = self.service.upload(original, '林小滢论文.docx', '李可欣')
        paper = wait_complete(self.service, paper['id'])
        updated = self.service.update_identity(paper['id'], '李小滢')
        self.assertEqual(updated['student_name'], '李小滢')
        self.assertEqual((self.service.folder(paper['id'])/'source.docx').read_bytes(), original)
        self.assertEqual(updated['report']['student_name'], '李小滢')
        self.assertEqual(updated['identity_previous_name'], '李可欣')

    def test_old_report_backfills_excluded_clue_markdown_and_count(self):
        paper = self.service.upload(docx_bytes(), '论文.docx', '测试学生')
        paper = wait_complete(self.service, paper['id'])
        report_path = self.service.folder(paper['id'])/paper['review_id']/'report.json'
        markdown_path = report_path.with_name('report.md')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['findings'] = [{'rule_id': 'NP001'}]
        report['excluded_clues'] = [{'paragraph_index': 3, 'start': 2, 'text': '这是我。', 'reason': '测试排除'}]
        report.pop('raw_first_person_count', None)
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        markdown_path.write_text('# 旧报告\n', encoding='utf-8')
        view = self.service.get(paper['id'])
        self.assertEqual(view['report']['raw_first_person_count'], 2)
        updated_markdown = markdown_path.read_text(encoding='utf-8')
        self.assertIn('这是我。', updated_markdown)
        self.assertEqual(updated_markdown.count('当前自动范围排除或待确认'), 1)

    def test_running_report_is_not_rewritten_by_polling(self):
        started, release = Event(), Event()
        def running_runner(path, output, **options):
            report = fake_runner(path, output, **options)
            report['excluded_clues'] = [{'paragraph_index': 1, 'start': 0, 'text': '我', 'reason': '测试'}]
            write_json(output/'report.json', report)
            started.set()
            release.wait(3)
            return report
        self.service.runner = running_runner
        paper = self.service.upload(docx_bytes(), '论文.docx', '测试学生')
        try:
            self.assertTrue(started.wait(2))
            path = self.service.folder(paper['id'])/paper['review_id']/'report.json'
            previous = path.read_bytes()
            self.assertEqual(self.service.get(paper['id'])['status'], 'running')
            self.assertEqual(path.read_bytes(), previous)
        finally:
            release.set()
        self.assertEqual(wait_complete(self.service, paper['id'])['status'], 'completed')

    def test_missing_key_is_not_silent_local_fallback(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'DeepSeek API Key'):
                self.service.upload(docx_bytes(), 'thesis.docx', use_agent=True)
        self.assertEqual(self.service.recent(), [])

    def test_key_only_memory_and_failure_redaction(self):
        key = 'test-secret-do-not-save'
        def failed_runner(*args, **options):
            raise ValueError('bad '+options['api_key'])
        self.service.runner = failed_runner
        paper = self.service.upload(docx_bytes(), 'thesis.docx', use_agent=True, api_key=key)
        paper = wait_complete(self.service, paper['id'])
        self.assertEqual(paper['status'], 'failed')
        self.assertNotIn(key, json.dumps(paper))
        failure_path=self.service.folder(paper['id'])/paper['review_id']/'failure.json'
        failure=json.loads(failure_path.read_text(encoding='utf-8'))
        self.assertEqual(failure['exception_type'],'ValueError')
        self.assertEqual(failure['automatic_retries'],0)
        self.assertTrue(any(f['function']=='failed_runner' for f in failure['frames']))
        self.assertNotIn('locals',json.dumps(failure))
        for path in self.service.root.rglob('*.json'):
            self.assertNotIn(key, path.read_text(encoding='utf-8'))

    def test_output_directory_failure_releases_queue_even_when_cancelled_before_start(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                with patch.object(self.service.executor, 'submit'):
                    paper = self.service.upload(docx_bytes(), 'thesis.docx')
                identity = (paper['id'], paper['review_id'])
                if cancelled:
                    self.service.cancel_events[identity].set()
                with patch.object(Path, 'mkdir', side_effect=PermissionError('review directory denied')), \
                        patch.object(self.service, 'runner') as runner:
                    self.service.run(*identity, None)
                runner.assert_not_called()
                self.assertFalse(self.service.active())
                self.assertNotIn(identity, self.service.cancel_events)
                saved = self.service.get(paper['id'])
                self.assertEqual(saved['status'], 'failed')
                self.assertEqual(saved['result_status'], 'failed')
                self.assertIn('review directory denied', saved['error'])
                persisted = json.loads((self.service.folder(paper['id'])/'paper.json').read_text(encoding='utf-8'))
                self.assertEqual(persisted['status'], 'failed')
                retried = self.service.review(paper['id'])
                self.assertEqual(wait_complete(self.service, retried['id'])['status'], 'completed')

    def test_initial_and_failure_save_errors_still_leave_memory_terminal(self):
        with patch.object(self.service.executor, 'submit'):
            paper = self.service.upload(docx_bytes(), 'thesis.docx')
        identity = (paper['id'], paper['review_id'])
        secret = 'test-secret-in-storage-error'
        with patch.object(self.service, 'save', side_effect=PermissionError('denied '+secret)), \
                patch.object(self.service, 'runner') as runner:
            self.service.run(*identity, secret)
        runner.assert_not_called()
        self.assertFalse(self.service.active())
        self.assertNotIn(identity, self.service.cancel_events)
        saved = self.service.get(paper['id'])
        self.assertEqual(saved['status'], 'failed')
        self.assertIn('persistence_error', saved)
        self.assertNotIn(secret, json.dumps(saved))
        retried = self.service.review(paper['id'])
        completed = wait_complete(self.service, retried['id'])
        self.assertEqual(completed['status'], 'completed')
        self.assertNotIn('persistence_error', completed)

    def test_path_resolution_failure_cleans_up_before_output_exists(self):
        with patch.object(self.service.executor, 'submit'):
            paper = self.service.upload(docx_bytes(), 'thesis.docx')
        identity = (paper['id'], paper['review_id'])
        with patch.object(self.service, 'folder', side_effect=PermissionError('path inaccessible')), \
                patch.object(self.service, 'runner') as runner:
            self.service.run(*identity, None)
        runner.assert_not_called()
        self.assertFalse(self.service.active())
        self.assertNotIn(identity, self.service.cancel_events)
        self.assertEqual(self.service.get(paper['id'])['status'], 'failed')

    def test_interrupt_and_explicit_review(self):
        paper = self.service.upload(docx_bytes(), 'thesis.docx')
        wait_complete(self.service, paper['id'])
        with self.service.lock:
            self.service.papers[paper['id']]['status'] = 'running'
            self.service.save(self.service.papers[paper['id']])
        reopened = NewPapers(self.service.root, fake_runner)
        try:
            self.assertEqual(reopened.get(paper['id'])['status'], 'interrupted')
            fresh = reopened.review(paper['id'])
            self.assertEqual(wait_complete(reopened, fresh['id'])['status'], 'completed')
        finally: reopened.close()

    def test_mutated_source_stops_before_runner(self):
        paper = self.service.upload(docx_bytes(), 'thesis.docx')
        wait_complete(self.service, paper['id'])
        (self.service.folder(paper['id'])/'source.docx').write_bytes(b'changed')
        paper = self.service.review(paper['id'])
        self.assertIn('哈希', wait_complete(self.service, paper['id'])['error'])


class FakeJobs:
    def recent(self): return []
    def active(self): return False
    def start(self, action): return {'id': 'job', 'action': action, 'status': 'queued'}


class NewPaperHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(server, 'ROOT', self.root)
        self.root_patch.start()
        self.service = NewPapers(self.root/'data/new_papers', fake_runner)
        self.workspace = server.Workspace(lambda: ({'documents': []}, {}))
        self.workspace.refresh()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.create_handler(self.workspace, FakeJobs(), 0, self.service))
        self.port = self.http.server_port
        base = server.create_handler(self.workspace, FakeJobs(), self.port, self.service)
        class QuietHandler(base):
            def log_message(self, *args): pass
        self.http.RequestHandlerClass = QuietHandler
        self.thread = Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.service.close(); self.root_patch.stop(); self.temp.cleanup()

    def request(self, path, method='GET', body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.port, timeout=5)
        options = {'X-Session-Token':self.workspace.session_token} if method == 'GET' else {}
        options.update(headers or {})
        conn.request(method, path, body=body, headers=options)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        conn.close()
        return status, data

    def headers(self):
        return {'Content-Type': 'application/octet-stream', 'X-Session-Token': self.workspace.session_token,
                'X-File-Name': quote('王五论文.docx'), 'X-Student-Name': quote('王五'), 'X-Use-Agent': 'false', 'X-Chapter-Policy': 'false'}

    def test_http_upload_poll_view_refresh_and_assets(self):
        status, body = self.request('/api/new-papers', 'POST', docx_bytes(), self.headers())
        self.assertEqual(status, 202)
        paper = wait_complete(self.service, json.loads(body)['id'])
        status, body = self.request('/api/new-papers/'+paper['id'])
        self.assertEqual(status, 200)
        view = json.loads(body)
        asset_id = view['source_asset_id']
        self.assertEqual(view['document']['role'], 'uploaded')
        self.assertIn('report_asset_id', view)
        self.workspace.refresh()
        status, downloaded = self.request('/api/assets/'+asset_id)
        self.assertEqual(status, 200)
        with zipfile.ZipFile(BytesIO(downloaded)) as actual, zipfile.ZipFile(BytesIO(docx_bytes())) as expected:
            self.assertEqual(sorted(actual.namelist()), sorted(expected.namelist()))
            for name in expected.namelist():
                self.assertEqual(actual.read(name), expected.read(name))
        workspace = json.loads(self.request('/api/workspace')[1])
        self.assertEqual(workspace['documents'][0]['student_name'], '王五')
        self.assertEqual(len(json.loads(self.request('/api/new-papers')[1])['papers']), 1)

    def test_http_missing_or_empty_body_returns_validation_error_without_review_record(self):
        for content in (body_docx_bytes(missing_body=True), body_docx_bytes('<w:p/><w:sectPr/>')):
            with self.subTest(size=len(content)):
                status, body = self.request('/api/new-papers', 'POST', content, self.headers())
                self.assertEqual(status, 400)
                self.assertIn('正文', json.loads(body)['error'])
        self.assertEqual(self.service.recent(), [])
        self.assertFalse(list(self.service.root.glob('paper_*')))

    def test_http_identity_update(self):
        status, body = self.request('/api/new-papers', 'POST', docx_bytes(), self.headers())
        self.assertEqual(status, 202)
        paper_id = json.loads(body)['id']
        wait_complete(self.service, paper_id)
        auth = {'Content-Type': 'application/json', 'X-Session-Token': self.workspace.session_token}
        status, body = self.request('/api/new-papers/'+paper_id+'/identity', 'POST', json.dumps({'student_name': '李小滢'}, ensure_ascii=False).encode('utf-8'), auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['student_name'], '李小滢')

    def test_http_lower_budget_round_history_archive_and_schema(self):
        headers=self.headers();headers.update({'X-Max-Requests':'2','X-Max-Total-Bound':'5000'})
        status,body=self.request('/api/new-papers','POST',docx_bytes(),headers)
        self.assertEqual(status,202)
        paper=wait_complete(self.service,json.loads(body)['id']);paper_id=paper['id'];first_id=paper['review_id']
        auth={'Content-Type':'application/json','X-Session-Token':self.workspace.session_token}
        self.assertEqual(paper['max_total_bound'],5000)
        body=json.dumps({'use_agent':False,'chapter_policy':False,'max_requests':1,'max_total_bound':2000}).encode()
        self.assertEqual(self.request(f'/api/new-papers/{paper_id}/review','POST',body,auth)[0],202)
        second=wait_complete(self.service,paper_id)
        status,data=self.request(f'/api/new-papers/{paper_id}/history')
        self.assertEqual(status,200)
        self.assertEqual(len(json.loads(data)['reviews']),2)
        metadata=(self.service.folder(paper_id)/'paper.json').read_bytes()
        status,data=self.request(f'/api/new-papers/{paper_id}/history/{first_id}')
        self.assertEqual(status,200)
        viewed=json.loads(data)
        self.assertTrue(viewed['historical'])
        self.assertEqual(viewed['review_id'],first_id)
        self.assertNotEqual(viewed['report_asset_id'],json.loads(self.request(f'/api/new-papers/{paper_id}')[1])['report_asset_id'])
        self.assertEqual((self.service.folder(paper_id)/'paper.json').read_bytes(),metadata)
        self.assertEqual(self.service.get(paper_id)['review_id'],second['review_id'])
        self.assertEqual(self.request(f'/api/new-papers/{paper_id}/history/not_a_review')[0],409)
        self.assertEqual(self.request(f'/api/new-papers/{paper_id}/cancel','POST',b'{"extra":1}',auth)[0],400)
        self.assertEqual(self.request(f'/api/new-papers/{paper_id}/archive','POST',b'{}',{'Content-Type':'application/json'})[0],403)
        self.assertEqual(self.request(f'/api/new-papers/{paper_id}/archive','POST',b'{}',auth)[0],200)
        self.assertEqual(json.loads(self.request('/api/new-papers')[1])['papers'],[])
        self.assertTrue((self.service.folder(paper_id)/'source.docx').is_file())

    def test_http_invalid_budgets_are_rejected_before_upload(self):
        headers=self.headers();headers['X-Max-Requests']='false'
        self.assertEqual(self.request('/api/new-papers','POST',docx_bytes(),headers)[0],400)
        headers['X-Max-Requests']='17'
        self.assertEqual(self.request('/api/new-papers','POST',docx_bytes(),headers)[0],400)
        self.assertEqual(self.service.recent(),[])

    def test_privacy_map_cannot_be_registered_or_downloaded(self):
        path=self.root/'data/new_papers/privacy_map.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{"identity":"private"}',encoding='utf-8')
        with self.assertRaises(ValueError):self.workspace.register_output(path,'private mapping')
        with self.workspace.lock:
            self.workspace.assets['private']={'path':str(path),'sha256':server.file_hash(path),'kind':'report','label':'private'}
        self.assertEqual(self.request('/api/assets/private')[0],409)

    def test_failed_word_residue_is_not_verified_pdf_preview(self):
        paper = self.service.upload(docx_bytes(), 'student.docx')
        paper = wait_complete(self.service, paper['id'])
        folder = self.service.folder(paper['id'])/paper['review_id']
        (folder/'layout').mkdir()
        pdf = folder/'layout/preview.pdf'
        pdf.write_bytes(b'%PDF-only-test-residue')
        status, body = self.request('/api/new-papers/'+paper['id'])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['document']['pdf_assets'], [])
        report_path = folder/'report.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report.update(pdf_path=str(pdf), pdf_sha256=server.file_hash(pdf), source_sha256=paper['sha256'])
        report['run']['word_launches'] = 1
        report_path.write_text(json.dumps(report), encoding='utf-8')
        body = self.request('/api/new-papers/'+paper['id'])[1]
        self.assertEqual(len(json.loads(body)['document']['pdf_assets']), 1)
        pdf.write_bytes(b'%PDF-modified')
        body = self.request('/api/new-papers/'+paper['id'])[1]
        self.assertEqual(json.loads(body)['document']['pdf_assets'], [])

    def test_upload_requires_session_origin_and_type(self):
        missing = self.headers(); missing.pop('X-Session-Token')
        self.assertEqual(self.request('/api/new-papers', 'POST', docx_bytes(), missing)[0], 403)
        external = self.headers(); external['Origin'] = 'https://evil.test'
        self.assertEqual(self.request('/api/new-papers', 'POST', docx_bytes(), external)[0], 403)
        wrong = self.headers(); wrong['Content-Type'] = 'application/json'
        self.assertEqual(self.request('/api/new-papers', 'POST', b'{}', wrong)[0], 400)
        self.assertEqual(self.request('/api/new-papers', 'POST', b'not-docx', self.headers())[0], 400)

    def test_image_preview_is_same_source_in_review_folder_and_hash_bound(self):
        paper=wait_complete(self.service,self.service.upload(docx_bytes(),'picture.docx')['id'])
        folder=self.service.folder(paper['id'])/paper['review_id'];(folder/'figure_evidence').mkdir()
        image=folder/'figure_evidence/preview.png';image.write_bytes(b'verified-image')
        evidence={'source_sha256':paper['sha256'],'preview_path':str(image),'preview_sha256':server.file_hash(image)}
        report_path=folder/'report.json';report=json.loads(report_path.read_text(encoding='utf-8'))
        report['findings']=[{'id':'NP007-1','rule_id':'NP007','text':'图1','figure_evidence':evidence}]
        report_path.write_text(json.dumps(report),encoding='utf-8')
        view=json.loads(self.request('/api/new-papers/'+paper['id'])[1]);asset=view['report']['findings'][0]['figure_evidence']['preview_asset_id']
        self.assertEqual(self.request('/api/assets/'+asset)[1],b'verified-image')
        image.write_bytes(b'changed');self.assertEqual(self.request('/api/assets/'+asset)[0],409)
        evidence['source_sha256']='wrong';report_path.write_text(json.dumps(report),encoding='utf-8')
        view=json.loads(self.request('/api/new-papers/'+paper['id'])[1]);self.assertNotIn('preview_asset_id',view['report']['findings'][0]['figure_evidence'])

    def test_image_only_scope_requires_agent_and_rejects_arbitrary_scope(self):
        paper=wait_complete(self.service,self.service.upload(docx_bytes(),'picture.docx')['id'])
        auth={'Content-Type':'application/json','X-Session-Token':self.workspace.session_token}
        for scope in ('figures','content','shell'):
            body=json.dumps({'use_agent':False,'chapter_policy':False,'review_scope':scope}).encode()
            self.assertEqual(self.request('/api/new-papers/'+paper['id']+'/review','POST',body,auth)[0],400)

    def test_oversized_upload_and_bool_schema_rejected(self):
        headers = self.headers(); headers['Content-Length'] = str(MAX_FILE_BYTES + 1)
        self.assertEqual(self.request('/api/new-papers', 'POST', b'', headers)[0], 400)
        headers = self.headers(); headers['X-Use-Agent'] = 'yes'
        self.assertEqual(self.request('/api/new-papers', 'POST', docx_bytes(), headers)[0], 400)

    def test_parallel_review_rejected_and_explicit_retry_contract(self):
        started, release = Event(), Event()
        def blocked_runner(*args, **kwargs):
            started.set(); release.wait(5)
            return fake_runner(*args, **kwargs)
        self.service.runner = blocked_runner
        try:
            status, body = self.request('/api/new-papers', 'POST', docx_bytes(), self.headers())
            self.assertEqual(status, 202)
            paper_id = json.loads(body)['id']
            self.assertTrue(started.wait(2))
            self.assertEqual(self.request('/api/new-papers', 'POST', docx_bytes(), self.headers())[0], 409)
            auth = {'Content-Type': 'application/json', 'X-Session-Token': self.workspace.session_token}
            self.assertEqual(self.request('/api/jobs', 'POST', b'{"action":"replay"}', auth)[0], 409)
        finally: release.set()
        wait_complete(self.service, paper_id)
        status, _ = self.request('/api/new-papers/'+paper_id+'/review', 'POST', b'{"use_agent":false,"chapter_policy":false}', auth)
        self.assertEqual(status, 202)
        wait_complete(self.service, paper_id)
        self.assertEqual(self.request('/api/new-papers/../../review', 'POST', b'{}', auth)[0], 400)


if __name__ == '__main__': unittest.main()
