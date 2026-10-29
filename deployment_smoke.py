"""Offline representative chain: original -> local checks -> source-bound report.

Synthetic data only. This is not model quality, actual API, SVN or Word testing.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
from tempfile import TemporaryDirectory


def run(http_startup=False):
    from docx import Document
    from new_paper_checks import read_docx, extract_paragraphs
    from paper_locations import annotate_paragraphs
    from expanded_paper_checks import check_expanded_rules
    from docx_comment_reader import extract_docx
    from nanobot.agent.tools.registry import ToolRegistry
    from independent_evaluation import create_registry
    from rule_governance import build_review_package
    import new_paper_agent
    import web_backend.server
    with TemporaryDirectory(prefix='thesis_deployment_smoke_') as temp:
        source = Path(temp) / 'synthetic.docx'
        doc = Document()
        doc.add_heading('Introduction', 1)
        doc.add_paragraph('见图4-8。This is a synthetic test, not student work.')
        doc.save(source)
        document, styles, sha, image_count = read_docx(source)
        paragraphs = annotate_paragraphs(extract_paragraphs(document, styles))
        findings, coverage = check_expanded_rules(document, paragraphs, [
            {'id': 'NP010', 'title': 'References', 'family': 'object_references',
             'enabled': True, 'scope': 'synthetic only', 'teacher_evidence': []}], source_sha256=sha)
        comments = extract_docx(source, {'sha256': sha})
        empty_package = build_review_package({'comments': [], 'rules': []}, [])
        registry = create_registry({'comments': []})
        ToolRegistry()
        if not findings or not coverage:
            raise AssertionError('synthetic object-reference check did not run')
        result = {'status': 'offline_representative_chain_passed',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'python': __import__('sys').version, 'source_sha256': sha,
                'paragraphs': len(paragraphs), 'coverage': coverage,
                'findings': len(findings), 'image_objects': image_count,
                'comment_count': len(comments['comments']),
                'service_and_agent_imported': True,
                'module_sources': {'agent': str(Path(new_paper_agent.__file__).resolve()),
                                   'server': str(Path(web_backend.server.__file__).resolve()),
                                   'nanobot_tools': str(Path(__import__('sys').modules[ToolRegistry.__module__].__file__).resolve())},
                'rule_package_status': empty_package['status'],
                'registry_status': registry['status'],
                'package_versions': {p: importlib.metadata.version(p) for p in
                                     ['lxml', 'openai', 'Pillow', 'PyMuPDF', 'python-docx', 'pydantic']},
                'llm_requests': 0, 'svn_requests': 0, 'word_requests': 0,
                'independent_accuracy_test': False,
                'limits': ['No external API/Word/SVN/network performed; optional localhost HTTP only',
                           'Historical hard-coded demo paths require separate migration',
                           'Representative local parsing chain does not certify the full historical workspace']}
        if http_startup:
            result['empty_install_http'] = verify_empty_install_http()
        return result


def verify_empty_install_http():
    """Launch the real entry point from a source copy without private data."""
    from http.client import HTTPConnection
    import os
    import shutil
    import socket
    import subprocess
    import sys
    import time
    origin = Path(__file__).resolve().parent
    with TemporaryDirectory(prefix='thesis_empty_http_') as folder:
        target = Path(folder)
        for source in origin.glob('*.py'):
            shutil.copy2(source, target / source.name)
        shutil.copytree(origin / 'web_backend', target / 'web_backend',
                        ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(origin / 'web_frontend/dist', target / 'web_frontend/dist')
        # Explicitly omit private data, credentials and development config.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        log = target / 'http_startup.log'
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        for name in ('DEEPSEEK_API_KEY', 'THESIS_WORKCOPY', 'THESIS_SVN_EXECUTABLE'):
            env.pop(name, None)
        guarded_entry = (
            "import socket,sys,runpy; "
            "sys.path.insert(0,sys.argv[1]); "
            "original=socket.socket.connect; "
            "socket.socket.connect=lambda self,address: original(self,address) "
            "if isinstance(address,tuple) and address[0] in {'127.0.0.1','localhost','::1'} "
            "else (_ for _ in ()).throw(RuntimeError('External network disabled during startup audit')); "
            "entry=sys.argv[1]+'/run_agent_ui.py'; "
            "sys.argv=[entry,'--port',sys.argv[2]]; runpy.run_path(entry,run_name='__main__')"
        )
        with log.open('w', encoding='utf-8') as output:
            process = subprocess.Popen([sys.executable, '-c', guarded_entry, str(target), str(port)],
                                       cwd=target, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            def request(path, method='GET', body=None, headers=None):
                connection = HTTPConnection('127.0.0.1', port, timeout=5)
                try:
                    connection.request(method, path, body=body, headers=headers or {})
                    response = connection.getresponse()
                    data = response.read()
                    return response.status, data
                finally:
                    connection.close()
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        health_status, health_bytes = request('/api/health')
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise AssertionError('Empty-install HTTP startup failed: ' + log.read_text(encoding='utf-8'))
                        time.sleep(0.1)
                assert health_status == 200
                health = json.loads(health_bytes)
                assert health['startup_status'] == 'configuration_required'
                assert not health['new_paper_reviews']
                statuses = {'/api/health': health_status}
                workspace = None
                for path in ('/', '/api/workspace', '/api/rule-library', '/api/thesis-workflow', '/api/new-papers'):
                    status, data = request(path)
                    assert status == 200, (path, status, data)
                    statuses[path] = status
                    if path == '/api/workspace':
                        workspace = json.loads(data)
                        assert workspace['startup']['mode'] == 'empty_workspace'
                        assert workspace['students'] == workspace['findings'] == workspace['actions'] == []
                    elif path == '/api/rule-library':
                        assert json.loads(data)['summary']['enabled_check_count'] == 0
                    elif path == '/api/new-papers':
                        assert not json.loads(data)['capabilities']['checks_enabled']
                token = workspace['session_token']
                upload_status, error = request('/api/new-papers', 'POST', b'not-a-paper',
                    {'Content-Type': 'application/octet-stream', 'X-Session-Token': token})
                assert upload_status == 400 and '尚未配置可执行' in json.loads(error)['error']
                for path, body in (('/api/jobs', {'action': 'replay'}),
                                   ('/api/thesis-workflow/check-initials', {}),
                                   ('/api/new-papers/unknown/review', {})):
                    status, data = request(path, 'POST', json.dumps(body),
                        {'Content-Type': 'application/json', 'X-Session-Token': token})
                    assert status == 400, (path, status, data)
                assert not list((target / 'data/new_papers').glob('paper_*'))
                assert not list((target / 'data/ui_jobs').glob('job_*'))
                assert not list((target / 'data/thesis_workflow/jobs').glob('job_*'))
                return {'status': 'empty_install_http_passed', 'endpoints': statuses,
                        'uploads_rejected_before_storage': True, 'fixed_actions_disabled': True,
                        'private_history_copied': False, 'paper_reports_created': 0,
                        'external_socket_connections_blocked': True,
                        'llm_requests': 0, 'svn_requests': 0, 'word_requests': 0,
                        'scope': 'Source-copy first HTTP startup; target-machine installation and external integrations remain separate acceptance items'}
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--http-startup', action='store_true', help='Verify real HTTP startup from a source copy without private history')
    args = parser.parse_args()
    result = run(http_startup=args.http_startup)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result['status'])
