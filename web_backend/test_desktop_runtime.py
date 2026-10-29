"""Offline desktop identity, isolated controls and worker-safety checks."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from threading import Event, Lock, Thread
import unittest
from unittest.mock import patch

from web_backend.desktop_runtime import DesktopRuntime, active_task_count, workspace_identity
from web_backend.server import Workspace, create_handler


class Service:
    def __init__(self, records=None, papers=False):
        self.lock = Lock()
        if papers:
            self.papers = records or {}
        else:
            self.jobs = records or {}
        self.refresh_calls = 0

    def active(self):
        self.refresh_calls += 1
        return any(row.get('status') in {'queued', 'running', 'cancelling'}
                   for row in getattr(self, 'papers', getattr(self, 'jobs', {})).values())

    def recent(self): return []


def make_http_service(root, services):
    workspace = Workspace(lambda: ({'startup': {'mode': 'empty_workspace',
        'status': 'configuration_required', 'checks_enabled': False}}, {}))
    workspace.refresh()
    runtime = DesktopRuntime(*services)
    server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(workspace, services[0], 0,
        desktop_runtime=runtime))
    handler = create_handler(workspace, services[0], server.server_port, desktop_runtime=runtime)
    class QuietHandler(handler):
        def log_message(self, *args): pass
    server.RequestHandlerClass = QuietHandler
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, runtime


def request(server, path, method='GET'):
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    connection.request(method, path)
    result = connection.getresponse()
    status, body = result.status, result.read()
    connection.close()
    return status, json.loads(body)


class DesktopRuntimeTest(unittest.TestCase):
    def test_identity_is_canonical_and_separates_workspaces(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            identity = workspace_identity(root)
            self.assertEqual(identity, workspace_identity(root / 'inside' / '..'))
            self.assertNotEqual(identity['workspace_fingerprint'],
                                workspace_identity(root / 'other')['workspace_fingerprint'])
            self.assertEqual(len(identity['workspace_fingerprint']), 64)

    def test_counts_all_services_including_cancelling_and_refreshes(self):
        jobs = Service({'a': {'status': 'queued'}, 'b': {'status': 'completed'}})
        papers = Service({'a': {'status': 'running'}, 'b': {'status': 'cancelling'}}, papers=True)
        workflow = Service({'a': {'status': 'running'}})
        self.assertEqual(active_task_count(jobs, papers, workflow, None), 4)
        self.assertEqual(workflow.refresh_calls, 1)

    def test_busy_preserves_worker_then_idle_shutdown_only_own_server(self):
        work = Service({'worker': {'status': 'running'}})
        own, own_thread, runtime = make_http_service(None, [work])
        foreign, foreign_thread, _ = make_http_service(None, [Service()])
        finished = Event()
        executor = ThreadPoolExecutor(max_workers=1)
        def worker():
            finished.wait(5)
            with work.lock: work.jobs['worker']['status'] = 'completed'
            return 'result-preserved'
        future = executor.submit(worker)
        try:
            reply = runtime.handle({'type': 'shutdown', 'force': False}, own)
            self.assertEqual(reply, {'type': 'shutdown', 'status': 'busy', 'active_tasks': 1})
            self.assertFalse(future.done())
            self.assertTrue(runtime.accepting)
            finished.set()
            self.assertEqual(future.result(timeout=3), 'result-preserved')
            self.assertEqual(runtime.handle({'type': 'shutdown', 'force': False}, own)['status'], 'accepted')
            own_thread.join(3)
            self.assertFalse(own_thread.is_alive())
            self.assertEqual(request(foreign, '/api/health')[0], 200)
            with runtime.lock:
                with self.assertRaisesRegex(RuntimeError, '安全退出'): runtime.require_accepting()
        finally:
            finished.set(); executor.shutdown(wait=True)
            if own_thread.is_alive(): own.shutdown(); own_thread.join(3)
            own.server_close()
            foreign.shutdown(); foreign_thread.join(3); foreign.server_close()

    def test_http_health_identity_preserves_existing_fields_and_no_shutdown_api(self):
        server, thread, runtime = make_http_service(None, [Service()])
        try:
            with TemporaryDirectory() as folder, patch('web_backend.server.ROOT', Path(folder)):
                status, health = request(server, '/api/health')
                self.assertEqual(status, 200)
                self.assertEqual(health['service'], 'paper-evidence-workbench')
                self.assertEqual(health['status'], 'ready')
                self.assertEqual(health['active_tasks'], 0)
                self.assertEqual(health['workspace_root'], str(Path(folder).resolve()))
                self.assertFalse(health['new_paper_reviews'])
                self.assertEqual(request(server, '/api/desktop/shutdown')[0], 403)
                self.assertEqual(request(server, '/api/desktop/shutdown', 'POST')[0], 403)
        finally:
            server.shutdown(); thread.join(3); server.server_close()

    def test_stdio_bad_messages_and_eof_do_not_shutdown(self):
        class Server:
            calls = 0
            def shutdown(self): self.calls += 1
        server = Server()
        runtime = DesktopRuntime(Service())
        output = io.StringIO()
        runtime.read_stdio(io.StringIO('bad json\n{"type":"shutdown","force":true}\n'
                                     '{"type":"status"}\n'), output, server)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([reply['status'] for reply in replies], ['rejected', 'rejected', 'ready'])
        self.assertEqual(server.calls, 0)
        self.assertTrue(runtime.accepting)

    def test_stdio_accepted_reply_is_flushed(self):
        shutdown = Event()
        class Server:
            def shutdown(self): shutdown.set()
        output = io.StringIO()
        runtime = DesktopRuntime(Service())
        runtime.read_stdio(io.StringIO('{"type":"shutdown","force":false}\n'), output, Server())
        self.assertTrue(shutdown.wait(3))
        self.assertEqual(json.loads(output.getvalue())['status'], 'accepted')

    def test_actual_cli_empty_copy_health_and_graceful_exit(self):
        # No source papers/config are copied; every ledger stays in this fixture.
        origin = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(prefix='desktop_cli_') as folder:
            target = Path(folder)
            for source in origin.glob('*.py'):
                shutil.copy2(source, target / source.name)
            shutil.copytree(origin / 'web_backend', target / 'web_backend',
                            ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(origin / 'web_frontend/dist', target / 'web_frontend/dist')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PAPER_DESKTOP_SESSION_TOKEN='isolated-desktop-session-secret')
            for name in ('DEEPSEEK_API_KEY', 'THESIS_WORKCOPY', 'THESIS_SVN_EXECUTABLE', 'PYTHONPATH'):
                env.pop(name, None)
            guarded_entry = (
                "import socket,sys,runpy; "
                "sys.path.insert(0,sys.argv[1]); "
                "original=socket.socket.connect; "
                "socket.socket.connect=lambda self,address: original(self,address) "
                "if isinstance(address,tuple) and address[0] in {'127.0.0.1','localhost','::1'} "
                "else (_ for _ in ()).throw(RuntimeError('External network disabled')); "
                "entry=sys.argv[1]+'/run_agent_ui.py'; "
                "sys.argv=[entry,'--port',sys.argv[2],'--desktop-stdio']; "
                "runpy.run_path(entry,run_name='__main__')"
            )
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', guarded_entry,
                                        str(target), str(port)], cwd=target, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 30
                while True:
                    connection = HTTPConnection('127.0.0.1', port, timeout=2)
                    try:
                        connection.request('GET', '/api/health')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        health = json.loads(response.read())
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail('Isolated desktop backend did not start')
                        time.sleep(0.1)
                    finally:
                        connection.close()
                import hmac
                connection = HTTPConnection('127.0.0.1', port, timeout=3)
                connection.request('GET', '/api/health', headers={'X-Health-Challenge':'integration-nonce'})
                response = connection.getresponse()
                verified = json.loads(response.read())
                self.assertEqual(verified['identity_proof'], hmac.new(b'isolated-desktop-session-secret', f'integration-nonce:{port}'.encode(), 'sha256').hexdigest())
                connection.request('GET', '/api/workspace')
                response = connection.getresponse(); self.assertEqual(response.status,403); response.read()
                connection.request('POST', '/api/session', headers={'X-Session-Token':'isolated-desktop-session-secret'})
                response = connection.getresponse(); self.assertEqual(response.status,204)
                cookie = response.getheader('Set-Cookie').split(';')[0]; response.read()
                connection.close()
                connection = HTTPConnection('127.0.0.1', port, timeout=3)
                connection.request('GET', '/api/workspace', headers={'Cookie':cookie})
                response = connection.getresponse(); self.assertEqual(response.status,200); response.read()
                connection.close()
                self.assertEqual(health['workspace_root'], str(target.resolve()))
                self.assertEqual(health['startup_status'], 'configuration_required')
                self.assertEqual(health['active_tasks'], 0)
                stdout, _ = process.communicate('{"type":"shutdown","force":false}\n', timeout=15)
                self.assertEqual(process.returncode, 0, stdout)
                replies = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
                self.assertIn({'type': 'shutdown', 'status': 'accepted', 'active_tasks': 0}, replies)
            finally:
                # Own fixture only, no model/Word/SVN work can have been dispatched.
                if process.poll() is None:
                    process.kill(); process.communicate(timeout=10)


if __name__ == '__main__': unittest.main()
