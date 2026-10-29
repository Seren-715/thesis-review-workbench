"""Protect reachable local-file, request-origin and job-action boundaries."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from web_backend.server import ROOT, Workspace, create_handler, file_hash


class FakeJobs:
    def __init__(self):
        from threading import Lock
        self.started = []; self.lock = Lock(); self.jobs = {}
    def active(self): return False
    def recent(self): return []
    def get(self, job_id): raise KeyError('Unknown job')
    def start(self, action):
        self.started.append(action)
        return {'id': 'test-job', 'status': 'queued', 'action': action}


class LocalServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix='http_test_', dir=ROOT/'data')
        cls.file = Path(cls.temp.name)/'source.pdf'
        cls.file.write_bytes(b'%PDF-test-evidence')
        cls.expected = cls.file.read_bytes()
        cls.workspace = Workspace(lambda: ({'title': 'Evidence test'}, {'fixed': {
            'path': str(cls.file), 'sha256': file_hash(cls.file), 'kind': 'pdf', 'label': 'test'}}))
        cls.workspace.refresh(); cls.jobs = FakeJobs()
        from docx import Document
        cls.docx_file = Path(cls.temp.name)/'registered.docx'
        document = Document(); document.add_paragraph('用于网页定位的原文。'); document.save(cls.docx_file)
        cls.docx_expected = cls.docx_file.read_bytes()
        cls.workspace.assets['verifieddoc'] = {'path':str(cls.docx_file), 'sha256':file_hash(cls.docx_file),
                                               'kind':'document', 'label':'网页核验学生'}
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(cls.workspace, cls.jobs, 0))
        cls.port = cls.server.server_port
        base = create_handler(cls.workspace, cls.jobs, cls.port)
        class QuietHandler(base):
            def log_message(self, *args): pass
        cls.server.RequestHandlerClass = QuietHandler
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def request(self, path, method='GET', payload=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.port)
        body = json.dumps(payload).encode() if payload is not None else None
        options = {'Content-Type': 'application/json'} if body is not None else {}
        if method == 'GET': options['X-Session-Token'] = self.workspace.session_token
        options.update(headers or {})
        connection.request(method, path, body=body, headers=options)
        response = connection.getresponse(); data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close(); return result

    def test_private_get_requires_session_even_with_valid_host(self):
        for route in ['/api/workspace', '/api/assets/fixed', '/api/documents/verifieddoc', '/api/jobs/test', '/api/new-papers']:
            for token in ['', 'invalid']:
                status, _, body = self.request(route, headers={'X-Session-Token': token})
                self.assertEqual(status, 403, route)
                self.assertNotIn(self.workspace.session_token.encode(), body)
        self.assertEqual(self.request('/api/health', headers={'X-Session-Token': ''})[0], 200)

    def test_browser_session_exchange_and_cookie_asset_access(self):
        self.assertEqual(self.request('/api/session', 'POST')[0], 403)
        status, headers, _ = self.request('/api/session', 'POST', headers={'X-Session-Token': self.workspace.session_token})
        self.assertEqual(status, 204)
        cookie = headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        auth = {'X-Session-Token': '', 'Cookie': cookie.split(';')[0]}
        self.assertEqual(self.request('/api/workspace', headers=auth)[0], 200)
        self.assertEqual(self.request('/api/assets/fixed', headers=auth)[0], 200)
        self.assertEqual(self.request('/api/workspace', headers={**auth, 'Origin':'https://external.example'})[0], 403)

    def test_health_identity_proof_is_bound_to_challenge_and_port(self):
        import hmac
        challenge = 'fresh-random-challenge'
        status, _, body = self.request('/api/health', headers={'X-Health-Challenge':challenge, 'X-Session-Token':''})
        self.assertEqual(status, 200)
        proof = json.loads(body)['identity_proof']
        self.assertEqual(proof, hmac.new(self.workspace.session_token.encode(), f'{challenge}:{self.port}'.encode(), 'sha256').hexdigest())
        self.assertNotIn(self.workspace.session_token.encode(), body)

    def test_workspace_same_origin(self):
        status, _, body = self.request('/api/workspace')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['session_token'], self.workspace.session_token)
        self.assertEqual(self.request('/api/workspace', headers={'Origin': 'https://external.example'})[0], 403)
        self.assertEqual(self.request('/api/workspace', headers={'Host': 'external.example'})[0], 403)

    def test_read_registered_asset_and_range(self):
        status, headers, data = self.request('/api/assets/fixed', headers={'Range': 'bytes=0-3'})
        self.assertEqual((status, data), (206, b'%PDF'))
        self.assertIn('bytes 0-3/', headers['Content-Range'])
        self.assertIn("frame-ancestors 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/api/assets/fixed', headers={'Range': 'bytes=-3'})[2], b'nce')
        self.assertEqual(self.request('/api/assets/fixed', headers={'Range': 'bytes=0-9999'})[2], self.expected)
        self.assertEqual(self.request('/api/assets/fixed', headers={'Range': 'bytes=999-'})[0], 416)
        self.assertEqual(self.request('/api/assets/unregistered')[0], 404)

    def test_changed_asset_rejected(self):
        self.file.write_bytes(b'changed')
        try: self.assertEqual(self.request('/api/assets/fixed')[0], 409)
        finally: self.file.write_bytes(self.expected)

    def test_document_metadata_only_reads_verified_registered_docx(self):
        asset_id = 'verifieddoc'
        status, _, body = self.request('/api/documents/'+asset_id)
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value['asset_id'], asset_id)
        self.assertEqual(value['file_name'], self.docx_file.name)
        self.assertEqual(value['sha256'], file_hash(self.docx_file))
        self.assertEqual(value['comments'], [])
        self.assertEqual(self.request('/api/documents/unregistered')[0], 404)
        self.assertEqual(self.request('/api/documents/fixed')[0], 409)
        self.docx_file.write_bytes(b'changed')
        try: self.assertEqual(self.request('/api/documents/'+asset_id)[0], 409)
        finally: self.docx_file.write_bytes(self.docx_expected)

    def test_no_arbitrary_command_or_missing_token(self):
        self.assertEqual(self.request('/api/jobs', 'POST', {'action': 'replay'})[0], 403)
        auth = {'X-Session-Token': self.workspace.session_token}
        self.assertEqual(self.request('/api/jobs', 'POST', {'action': 'run'}, auth)[0], 400)
        self.assertEqual(self.request('/api/jobs', 'POST', {'action': 'replay', 'cmd': 'anything'}, auth)[0], 400)
        self.assertEqual(self.request('/api/jobs', 'POST', {'action': 'replay'}, auth)[0], 202)
        self.assertEqual(self.jobs.started, ['replay'])

    def test_static_traversal_and_unknown_api(self):
        self.assertEqual(self.request('/assets/%2e%2e/%2e%2e/%2e%2e/secret.txt')[0], 403)
        self.assertEqual(self.request('/api/unknown')[0], 404)


if __name__ == '__main__': unittest.main()
