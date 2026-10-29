"""Loopback-only evidence UI, fixed jobs and explicit new-paper reviews."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from copy import deepcopy
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Lock
from urllib.parse import unquote, urlsplit

from atomic_json import path_lock, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT/'web_frontend/dist'
BATCH = ROOT/'data/pipeline_runs/new_batch_20261007_205126_048522'
ACTION_LIST = [
    {'id': 'scoped-check', 'label': '重新执行黄发平初稿规则核查', 'description': '重新执行六项已配置检查，范围为黄发平的实验初稿；无法自动判定的项目保留待核验状态。', 'token_cost': '0', 'scope': '黄发平 · 初稿 r194'},
    {'id': 'align-objects', 'label': '定位教师批注在黄发平初稿中的对应内容', 'description': '定位教师批注所指文字、图表或公式在黄发平初稿中的对应内容；完成定位后仍需核验问题是否存在。', 'token_cost': '0', 'scope': '黄发平 · 初稿 r194'},
    {'id': 'replay', 'label': '重新汇总三名学生的已有核查结果', 'description': '核对三名实验学生已保存的文字复核与分页记录，生成结果汇总；该操作不触发模型全文审阅。', 'token_cost': '0', 'scope': '黄发平 / 李晨波 / 周运龙'},
]
COMMANDS = {
    'scoped-check': ['reviewed-check', '--run-dir', str(BATCH)],
    'align-objects': ['align-objects', '--run-dir', str(BATCH)],
    'replay': ['replay'],
}
ALLOWED_ASSETS = {'.png', '.jpg', '.jpeg', '.gif', '.pdf', '.doc', '.docx', '.md', '.json', '.xml', '.wmf', '.emf'}

def private_asset(path):
    return Path(path).name.lower() == 'privacy_map.json'


def file_hash(path):
    h = sha256()
    with path_lock(path):
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


class Workspace:
    def __init__(self, builder):
        self.builder, self.lock, self.data, self.assets = builder, Lock(), None, {}
        self.dynamic_assets = {}
        self.session_token = secrets.token_urlsafe(32)

    def refresh(self):
        data, assets = self.builder()
        for asset in assets.values():
            path = Path(asset['path']).resolve()
            if private_asset(path) or not path.is_relative_to(ROOT) or path.suffix.lower() not in ALLOWED_ASSETS:
                raise ValueError('Unregistered evidence file type or source root')
            if file_hash(path) != asset['sha256']: raise ValueError('Evidence asset changed')
        with self.lock:
            self.data, self.assets = data, {**assets, **self.dynamic_assets}
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            if self.data is None: raise ValueError('Evidence snapshot is not ready')
            # Return a copy so request-specific metadata does not mutate evidence.
            value = dict(self.data)
        value.update(session_token=self.session_token)
        value['actions'] = [] if value.get('startup', {}).get('mode') in {'empty_workspace', 'packaged_workspace'} else ACTION_LIST
        return value

    def asset(self, asset_id):
        with self.lock:
            item = self.assets.get(asset_id)
        if item is None: raise KeyError('Unknown asset')
        path = Path(item['path']).resolve()
        if private_asset(path) or not path.is_relative_to(ROOT) or path.suffix.lower() not in ALLOWED_ASSETS:
            raise ValueError('Asset root/type rejected')
        if file_hash(path) != item['sha256']: raise ValueError('Evidence changed since the last verified snapshot')
        return path, item

    def register_output(self, path, label):
        path = Path(path).resolve()
        if private_asset(path) or not path.is_relative_to(ROOT/'data') or path.suffix.lower() not in {'.md', '.json'}:
            raise ValueError('Job output path is not eligible')
        file_sha = file_hash(path)
        asset_id = sha256((str(path)+file_sha).encode('utf-8')).hexdigest()[:24]
        with self.lock:
            item = {'path': str(path), 'sha256': file_sha, 'kind': 'report', 'label': label}
            self.assets[asset_id] = item
            self.dynamic_assets[asset_id] = item
        return asset_id

    def register_uploaded_asset(self, path, label, display_name=None):
        path = Path(path).resolve()
        if not path.is_relative_to((ROOT/'data/new_papers').resolve()) or path.suffix.lower() not in {'.docx', '.pdf'}:
            raise ValueError('Uploaded asset root/type rejected')
        file_sha = file_hash(path)
        asset_id = sha256((str(path)+file_sha).encode('utf-8')).hexdigest()[:24]
        item = {'path': str(path), 'sha256': file_sha, 'kind': 'document' if path.suffix.lower() == '.docx' else 'pdf',
                'label': label, 'display_file_name': display_name or path.name}
        with self.lock:
            self.dynamic_assets[asset_id] = item
            self.assets[asset_id] = item
        return asset_id


class Jobs:
    def __init__(self):
        self.lock, self.jobs = Lock(), {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='paper-ui-job')
        self.root = ROOT/'data/ui_jobs'
        self.root.mkdir(parents=True, exist_ok=True)
        # Keep the local execution ledger visible after a UI restart; never retry.
        for path in sorted(self.root.glob('job_*.json'))[-20:]:
            try:
                saved = read_json(path)
                if saved.get('id') != path.stem or saved.get('action') not in COMMANDS:
                    continue
                if saved.get('status') not in {'queued', 'running', 'completed', 'failed'}:
                    continue
                if saved['status'] in {'queued', 'running'}:
                    saved.update(status='failed', error='上次服务中断，本次不自动重试。',
                                 finished_at=datetime.now().isoformat())
                    self.save(saved)
                self.jobs[saved['id']] = saved
            except (OSError, ValueError, KeyError):
                continue

    def save(self, job):
        path = self.root/(job['id']+'.json')
        write_json(path, job)

    def start(self, action):
        if action not in COMMANDS: raise ValueError('Unsupported action')
        with self.lock:
            if any(j['status'] in {'queued', 'running'} for j in self.jobs.values()):
                raise RuntimeError('已有检查正在运行，请等待完成。')
            job = {'id': 'job_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(3),
                   'action': action, 'label': next(a['label'] for a in ACTION_LIST if a['id'] == action),
                   'status': 'queued', 'started_at': datetime.now().isoformat(),
                   'finished_at': None, 'log_tail': '', 'error': None,
                   'new_tokens': 0, 'external_requests': 0}
            self.jobs[job['id']] = job
            self.save(job)
        self.executor.submit(self.run, job['id'])
        return self.get(job['id'])

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None: raise KeyError('Unknown job')
            result = dict(job)
        log = self.root/(job_id+'.log')
        if log.exists(): result['log_tail'] = log.read_text(encoding='utf-8', errors='replace')[-12000:]
        return result

    def recent(self):
        with self.lock: ids = list(self.jobs)[-8:][::-1]
        return [self.get(job_id) for job_id in ids]

    def active(self):
        with self.lock:
            return any(job['status'] in {'queued', 'running'} for job in self.jobs.values())

    def run(self, job_id):
        with self.lock:
            job = self.jobs[job_id]; job['status'] = 'running'; self.save(job)
            action = job['action']
        log = self.root/(job_id+'.log')
        environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        try:
            with log.open('w', encoding='utf-8') as output:
                process = subprocess.Popen([sys.executable, str(ROOT/'paper_pipeline_v3.py'), *COMMANDS[action]],
                    cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try: code = process.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                    raise TimeoutError('只读检查超过5分钟，已停止本次程序。')
            text = log.read_text(encoding='utf-8', errors='replace')
            result_dir = None
            for line in text.splitlines():
                if line.startswith('结果目录：'):
                    candidate = Path(line.split('：', 1)[1].strip()).resolve()
                    if candidate.is_relative_to(ROOT/'data') and candidate.is_dir(): result_dir = candidate
            expected = {'scoped-check': 'scoped_checks_completed_review_pending',
                        'align-objects': 'local_object_alignment_completed_scope_review_pending',
                        'replay': 'completed_verified_replay'}[action]
            if code != 0 or result_dir is None:
                raise ValueError('检查未正常完成，请查看运行日志。')
            ledger = read_json(result_dir/'run.json')
            if ledger['status'] != expected: raise ValueError('检查结果状态未通过核验。')
            if action != 'replay':
                if any(ledger[k] != 0 for k in ('new_tokens', 'model_requests', 'svn_requests', 'word_launches')):
                    raise ValueError('只读检查成本记录不一致。')
            elif any(ledger[k] != 0 for k in ('new_api_requests', 'new_known_tokens', 'svn_requests', 'word_launches')):
                raise ValueError('复读流程成本记录不一致。')
            with self.lock:
                job.update(status='completed', result_dir=str(result_dir),
                           finished_at=datetime.now().isoformat(), log_tail=text[-12000:])
                self.save(job)
        except Exception as error:
            with self.lock:
                job.update(status='failed', finished_at=datetime.now().isoformat(), error=str(error))
                self.save(job)


def create_handler(workspace, jobs, port, new_papers=None, thesis_workflow=None, desktop_runtime=None):
    from web_backend.desktop_runtime import DesktopRuntime, workspace_identity
    runtime = desktop_runtime or DesktopRuntime(jobs, new_papers, thesis_workflow)
    mutation_lock = runtime.lock
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format, *args):
            print(self.address_string(), format % args, flush=True)

        def local_request(self):
            host = self.headers.get('Host', '')
            if host not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                self.json_response(403, {'error': '仅支持本机访问。'}); return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+host:
                self.json_response(403, {'error': '不接受来自其他网页的请求。'}); return False
            return True

        def send_common_headers(self, code, content_type, size, extra=None, allow_frame=False):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            ancestor = "'self'" if allow_frame else "'none'"
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors "+ancestor)
            for name, value in (extra or {}).items(): self.send_header(name, value)
            self.end_headers()

        def json_response(self, code, value):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_common_headers(code, 'application/json; charset=utf-8', len(data))
            self.wfile.write(data)

        def authenticated(self):
            token = self.headers.get('X-Session-Token', '')
            if not token:
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get('Cookie', ''))
                    item = cookie.get(f'paper_session_{port}')
                    token = item.value if item else ''
                except Exception:
                    token = ''
            return secrets.compare_digest(token.encode('utf-8'), workspace.session_token.encode('utf-8'))

        def workflow_asset(self, path, label, expected_sha=None, document_metadata=None):
            path = Path(path).resolve()
            if private_asset(path) or not path.is_relative_to((ROOT/'data').resolve()) or path.suffix.lower() not in {'.docx', '.pdf', '.md', '.json'}:
                return None
            if expected_sha and file_hash(path) != expected_sha:
                raise ValueError('规则来源文件已变化，不能打开。')
            actual = file_hash(path)
            asset_id = sha256((str(path)+actual).encode('utf-8')).hexdigest()[:24]
            kind = 'document' if path.suffix.lower() == '.docx' else 'pdf' if path.suffix.lower() == '.pdf' else 'report'
            item = {'path': str(path), 'sha256': actual, 'kind': kind, 'label': label}
            if kind == 'document' and document_metadata:
                item.update({key:value for key,value in document_metadata.items()
                             if key in {'student_name','student_id','revision','document_role'}})
            with workspace.lock:
                workspace.dynamic_assets[asset_id] = item
                workspace.assets[asset_id] = item
            return asset_id

        def library_view(self):
            from teacher_rule_library import get_library
            value = deepcopy(get_library(ROOT))
            from web_backend.portable_startup import require_executable_rules
            startup = workspace.snapshot().get('startup', {})
            executable_rules = [] if startup.get('mode') == 'empty_workspace' else require_executable_rules(ROOT)
            if startup.get('mode') in {'empty_workspace', 'packaged_workspace'}:
                value['startup'] = startup
            if startup.get('mode') == 'packaged_workspace':
                value['rules'] = deepcopy(executable_rules)
                value['limitations'] = [startup['message']]
                value['status'] = 'packaged_general_configuration'
            value['summary']['enabled_check_count'] = sum(r.get('enabled',True) for r in executable_rules)
            for rule in value['rules']:
                rule['implemented_check_ids'] = [r['id'] for r in executable_rules if
                    (r['id']==rule['id'] if startup.get('mode')=='packaged_workspace' else r.get('family')==rule['id'])
                    and r.get('enabled',True)]
                rule['execution_status'] = ('source_configuration_required' if startup.get('mode') == 'empty_workspace'
                                            else 'bounded_checks_available' if rule['implemented_check_ids'] else 'method_pending')
            bound = {}
            for section, evidence_key in (('rules', 'teacher_evidence'), ('comments', None)):
                for item in value.get(section, []):
                    rows = item.get(evidence_key, []) if evidence_key else [item]
                    for evidence in rows:
                        path = evidence.get('source_path') or evidence.get('document_path') or evidence.get('source', {}).get('document_path')
                        sha = evidence.get('source_sha256') or evidence.get('source', {}).get('sha256')
                        if not path or not sha:
                            continue
                        identity = (path, sha)
                        if identity not in bound:
                            student_name=evidence.get('student_name', '学生')
                            revision=evidence.get('revision',evidence.get('source',{}).get('revision',0))
                            bound[identity] = self.workflow_asset(path, f'{student_name} · 教师批改稿 · r{revision}', sha,
                                {'student_name':student_name,'revision':revision,'document_role':'teacher'})
                        if bound[identity]:
                            evidence['source_asset_id'] = bound[identity]
            from reference_standards import reference_standards_view, annotate_local_material_candidates
            catalog = reference_standards_view(ROOT)
            for source in catalog.get('sources', []):
                source['reader_asset_id'] = self.workflow_asset(source['reader_path'],
                    f"{source['title']} · 规范原文", source['reader_sha256'])
            value['reference_standards'] = catalog
            comments_by_id = {c['id']: c for c in value.get('comments', [])}
            for route in value.get('external_manual_reviews', []):
                comment = comments_by_id[route['evidence']['id']]
                route['evidence']['source_asset_id'] = comment.get('source_asset_id')
                route['evidence']['anchor_text'] = comment.get('anchor_text')
                annotate_local_material_candidates(route, catalog)
                for candidate in route.get('available_reference_candidates', []):
                    candidate['reader_asset_id'] = next((s.get('reader_asset_id') for s in catalog['sources']
                                                         if s['id'] == candidate['source_id']), None)
            return value

        def workflow_view(self):
            value = thesis_workflow.snapshot()
            startup = workspace.snapshot().get('startup')
            if startup:
                value['startup'] = startup
                if not startup['checks_enabled']:
                    for capability in value.get('capabilities', []):
                        if capability.get('id') in {'check-initials', 'new-paper'}:
                            capability.update(status='configuration_required', description=startup['message'])
            for job in value['jobs']:
                if job.get('result_dir'):
                    folder = Path(job['result_dir'])
                    for filename, key in (('report.md', 'report_asset_id'), ('run.json', 'ledger_asset_id')):
                        path = folder/filename
                        if path.is_file():
                            job[key] = self.workflow_asset(path, job['label'])
                for row in job.get('result_summary', {}).get('students', []) if isinstance(job.get('result_summary', {}).get('students'), list) else []:
                    for filename, key in (('source_path', 'source_asset_id'), ('report_path', 'report_asset_id'), ('report_json_path', 'ledger_asset_id')):
                        path = row.get(filename)
                        if path:
                            row[key] = self.workflow_asset(path, row.get('student_name', '学生')+' · 初稿检查', row.get('source_sha256') if filename=='source_path' else None,
                                {'student_name':row.get('student_name','学生'),'student_id':row.get('student_id',''),
                                 'revision':row.get('revision',0),'document_role':'initial'} if filename=='source_path' else None)
                            if filename=='source_path':
                                row['source_kind']='document' if Path(path).suffix.lower()=='.docx' else 'report'
            return value

        def job_view(self, job):
            if job['status'] == 'completed' and job.get('result_dir'):
                folder = Path(job['result_dir']).resolve()
                if folder.is_relative_to(ROOT/'data'):
                    for filename, key, label in [('report.md', 'report_asset_id', '本轮检查报告'),
                                                  ('run.json', 'ledger_asset_id', '本轮运行记录')]:
                        path = folder/filename
                        if path.is_file(): job[key] = workspace.register_output(path, label)
            return job

        def workspace_view(self, value):
            value['latest_jobs'] = [self.job_view(job) for job in jobs.recent()]
            if new_papers is not None:
                papers = [self.paper_view(paper) for paper in new_papers.recent()]
                value['new_papers'] = papers
                value['new_paper_capabilities'] = self.paper_capabilities()
                value['documents'] = [*value.get('documents', []), *(paper['document'] for paper in papers)]
            return value

        def paper_capabilities(self):
            value = new_papers.capabilities()
            startup = workspace.snapshot().get('startup')
            if startup:
                value.update(checks_enabled=startup['checks_enabled'],
                             startup_status=startup['status'],
                             configuration_message=startup['message'])
            return value

        def require_review_configuration(self):
            if workspace.snapshot().get('startup'):
                from web_backend.portable_startup import require_executable_rules
                require_executable_rules(ROOT)

        def paper_view(self, paper):
            folder = new_papers.folder(paper['id'])
            display = paper['student_name']+' · 上传论文 · '+paper['file_name']
            source_format = paper.get('source_format', 'docx')
            source = folder/('source.pdf' if source_format == 'pdf' else 'source.docx')
            paper['source_asset_id'] = workspace.register_uploaded_asset(source, display, paper['file_name'])
            pdf_assets = ([{'id': paper['source_asset_id'], 'kind': 'pdf', 'label': 'PDF 原稿'}]
                          if source_format == 'pdf' else [])
            if paper.get('review_id'):
                result = folder/paper['review_id']
                if result.resolve().is_relative_to(folder):
                    for name, key, label in [('report.md', 'report_asset_id', '新论文检查报告'), ('run.json', 'ledger_asset_id', '新论文运行记录')]:
                        path = result/name
                        if path.is_file():
                            paper[key] = workspace.register_output(path, label)
                    pdf = result/'layout/preview.pdf'
                    report = paper.get('report') or {}
                    verified_pdf = (pdf.is_file() and report.get('pdf_path')
                                    and Path(report['pdf_path']).resolve() == pdf.resolve()
                                    and report.get('source_sha256') == paper['sha256']
                                    and (report.get('run', {}).get('word_launches', 0) > 0
                                         or report.get('run', {}).get('layout_evidence_reused') is True)
                                    and report.get('pdf_sha256') == file_hash(pdf))
                    if verified_pdf:
                        asset_id = workspace.register_uploaded_asset(pdf, display+' · 排版预览', Path(paper['file_name']).stem+'_排版预览.pdf')
                        pdf_assets.append({'id': asset_id, 'kind': 'pdf', 'label': 'Word 排版预览'})
                    for finding in report.get('findings',[]):
                        e=finding.get('figure_evidence',{})
                        if e.get('source_sha256')!=paper['sha256']:continue
                        for key,hash_key,asset_key in [('preview_path','preview_sha256','preview_asset_id'),('display_preview_path','display_preview_sha256','display_preview_asset_id')]:
                            image=Path(e.get(key,'')).resolve()
                            if not image.is_relative_to((result/'figure_evidence').resolve()) or image.suffix.lower()!='.png' or not image.is_file() or file_hash(image)!=e.get(hash_key):continue
                            asset_id=sha256((str(image)+e[hash_key]).encode()).hexdigest()[:24]
                            asset={'path':str(image),'sha256':e[hash_key],'kind':'image','label':finding.get('text','图片证据')}
                            with workspace.lock:
                                workspace.assets[asset_id]=asset;workspace.dynamic_assets[asset_id]=asset
                            e[asset_key]=asset_id
                    # Reference candidates reuse only image assets already verified
                    # against this source and this receipt; no candidate path is served.
                    figure_rows = {f.get('paragraph_index'):f.get('figure_evidence',{}) for f in report.get('findings',[]) if f.get('rule_id')=='NP007'}
                    for finding in report.get('findings',[]):
                        diagnosis = finding.get('reference_diagnostic', {})
                        if diagnosis.get('source_sha256') != paper['sha256']:
                            continue
                        for target in diagnosis.get('candidates', []):
                            target.pop('preview_asset_id', None)
                            evidence = figure_rows.get(target.get('paragraph_index'), {})
                            if evidence.get('source_sha256') == paper['sha256'] and evidence.get('preview_asset_id'):
                                target['preview_asset_id'] = evidence['preview_asset_id']
                                target['caption_y_fraction'] = evidence.get('caption_y_fraction')
            paper['document'] = {'asset_id': paper['source_asset_id'], 'student_id': paper['id'],
                'student_name': paper['student_name'], 'revision': 0, 'role': 'uploaded', 'display_name': display,
                'file_name': paper['file_name'], 'sha256': paper['sha256'], 'comments': [], 'pdf_assets': pdf_assets,
                'source_format': source_format,
                'page_count': (paper.get('report') or {}).get('pdf_pages', {}).get('page_count')
                              if source_format == 'pdf' else None}
            if isinstance(paper.get('report'), dict):
                from reference_standards import reference_standards_view, attach_reference_parameters, attach_report_reference_evidence
                catalog = reference_standards_view(ROOT)
                for source in catalog.get('sources', []):
                    source['reader_asset_id'] = self.workflow_asset(source['reader_path'],
                        f"{source['title']} · 规范原文", source['reader_sha256'])
                diagnostic = paper['report'].get('format_diagnostics')
                attach_report_reference_evidence(paper['report'], catalog)
                if isinstance(diagnostic, dict):
                    attach_reference_parameters(diagnostic, catalog)
                from teacher_rule_library import get_library
                trusted_sources = {(str(Path(c['source_path']).resolve()), c['source_sha256'])
                                   for c in get_library(ROOT).get('comments', [])
                                   if c.get('source_path') and c.get('source_sha256')}
                # Only bind already registered teacher originals with the same hash.
                # Paths from a report never become arbitrary file-serving authority.
                with workspace.lock:
                    registered = dict(workspace.assets)
                by_source = {(str(Path(asset['path']).resolve()), asset['sha256']): asset_id
                             for asset_id, asset in registered.items()}
                checked = {}
                for section in ('rules', 'findings'):
                    for item in paper['report'].get(section, []):
                        for evidence in item.get('teacher_evidence', []):
                            source_path, source_hash = evidence.get('source_path'), evidence.get('source_sha256')
                            if not source_path or not source_hash: continue
                            identity = (str(Path(source_path).resolve()), source_hash)
                            asset_id = by_source.get(identity)
                            if asset_id is None and identity in trusted_sources:
                                student_name=evidence.get('student_name','学生')
                                revision=evidence.get('revision',0)
                                asset_id = self.workflow_asset(source_path, f'{student_name} · 教师批改稿 · r{revision}', source_hash,
                                    {'student_name':student_name,'revision':revision,'document_role':'teacher'})
                            if asset_id is None: continue
                            if asset_id not in checked:
                                try:
                                    workspace.asset(asset_id)
                                    checked[asset_id] = True
                                except (KeyError, ValueError, OSError): checked[asset_id] = False
                            if checked[asset_id]: evidence['source_asset_id'] = asset_id
            return paper

        def file_response(self, path, attachment=False, display_name=None):
            from urllib.parse import quote
            json_bytes = None
            if path.suffix.lower() == '.json':
                # Close mutable JSON before socket writes: a slow download must
                # not hold a Windows file handle and block review persistence.
                with path_lock(path):
                    json_bytes = path.read_bytes()
                size = len(json_bytes)
            else:
                size = path.stat().st_size
            start, end = 0, size-1
            partial = False
            range_header = self.headers.get('Range')
            if range_header:
                import re
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
                if not match or not any(match.groups()):
                    self.json_response(416, {'error': '不支持此文件范围。'}); return
                if match.group(1):
                    start = int(match.group(1)); end = int(match.group(2)) if match.group(2) else size-1
                else:
                    start = max(0, size-int(match.group(2))); end = size-1
                if start >= size or end < start:
                    self.json_response(416, {'error': '文件范围越界。'}); return
                end = min(end, size-1)
                partial = True
            kind = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if path.suffix.lower() in {'.md', '.json', '.xml', '.wmf', '.emf'}: attachment = True
            extra = {'Accept-Ranges': 'bytes', 'Content-Disposition':
                     ('attachment' if attachment else 'inline')+"; filename*=UTF-8''"+quote(display_name or path.name)}
            if partial: extra['Content-Range'] = f'bytes {start}-{end}/{size}'
            self.send_common_headers(206 if partial else 200, kind, end-start+1, extra,
                                     allow_frame=path.suffix.lower()=='.pdf')
            if json_bytes is not None:
                self.wfile.write(json_bytes[start:end+1])
                return
            with path.open('rb') as stream:
                stream.seek(start); remaining = end-start+1
                while remaining:
                    chunk = stream.read(min(remaining, 256*1024))
                    if not chunk: break
                    self.wfile.write(chunk); remaining -= len(chunk)

        def do_GET(self):
            if not self.local_request(): return
            path = unquote(urlsplit(self.path).path)
            if path.startswith('/api/') and path != '/api/health' and not self.authenticated():
                self.json_response(403, {'error': 'Open the session URL printed when the workbench starts.'}); return
            try:
                if path == '/api/workspace':
                    self.json_response(200, self.workspace_view(workspace.snapshot()))
                elif path == '/api/rule-library' and thesis_workflow is not None:
                    self.json_response(200, self.library_view())
                elif path == '/api/thesis-workflow' and thesis_workflow is not None:
                    self.json_response(200, self.workflow_view())
                elif path == '/api/task-coverage' and thesis_workflow is not None:
                    from teacher_task_coverage import build_task_coverage
                    self.json_response(200, build_task_coverage(thesis_workflow.root))
                elif path == '/api/new-papers' and new_papers is not None:
                    self.json_response(200, {'papers': [self.paper_view(paper) for paper in new_papers.recent()], 'capabilities': self.paper_capabilities()})
                elif path.startswith('/api/new-papers/') and new_papers is not None:
                    parts = path.removeprefix('/api/new-papers/').split('/')
                    if len(parts)==2 and parts[1]=='history':
                        self.json_response(200, {'reviews':new_papers.history(parts[0])})
                    elif len(parts)==3 and parts[1]=='history':
                        self.json_response(200, self.paper_view(new_papers.historical(parts[0],parts[2])))
                    elif len(parts)==1:
                        self.json_response(200, self.paper_view(new_papers.get(parts[0])))
                    else:
                        raise KeyError('Unknown paper resource')
                elif path.startswith('/api/documents/'):
                    asset_id = path.removeprefix('/api/documents/')
                    if '/' in asset_id or not asset_id:
                        raise KeyError('Unknown document')
                    file, asset = workspace.asset(asset_id)
                    if asset.get('kind') != 'document' or file.suffix.lower() != '.docx':
                        raise ValueError('此资产不是可预览DOCX。')
                    from docx_comment_reader import extract_docx
                    extracted = extract_docx(file, {'sha256':asset['sha256']})
                    if file_hash(file)!=asset['sha256']:
                        raise ValueError('读取期间原稿发生变化。')
                    comments=[{'id':r['comment']['id'],'text':r['comment']['text'],
                               'author':r['comment'].get('author',''),'anchor_text':r['anchor']['text']}
                              for r in extracted['comments']]
                    self.json_response(200, {'asset_id':asset_id,'student_id':asset.get('student_id') or asset_id,
                        'student_name':asset.get('student_name',asset['label']),'revision':asset.get('revision',0),
                        'role':asset.get('document_role','initial'),
                        'display_name':asset['label'],'file_name':file.name,'sha256':asset['sha256'],
                        'comments':comments,'pdf_assets':[]})
                elif path.startswith('/api/assets/'):
                    asset_id = path.removeprefix('/api/assets/')
                    if '/' in asset_id or not asset_id: raise KeyError('Unknown asset')
                    file, asset = workspace.asset(asset_id)
                    self.file_response(file, attachment=file.suffix.lower() in {'.doc', '.docx'},
                                       display_name=asset.get('display_file_name'))
                elif path.startswith('/api/jobs/'):
                    self.json_response(200, self.job_view(jobs.get(path.removeprefix('/api/jobs/'))))
                elif path == '/api/health':
                    challenge = self.headers.get('X-Health-Challenge', '')
                    startup = workspace.snapshot().get('startup', {})
                    self.json_response(200, {'status': 'ready', 'service': 'paper-evidence-workbench',
                        'identity_proof': hmac.new(workspace.session_token.encode(), (challenge + ':' + str(port)).encode(), 'sha256').hexdigest() if challenge else None,
                        'read_only_actions': True, 'new_paper_reviews': new_papers is not None and startup.get('checks_enabled', True),
                        'startup_status': startup.get('status', 'ready'),
                        **workspace_identity(ROOT), **runtime.state()})
                elif path.startswith('/api/'):
                    self.json_response(404, {'error': '接口不存在。'})
                else:
                    requested = (DIST/path.lstrip('/')).resolve()
                    if not requested.is_relative_to(DIST.resolve()):
                        self.json_response(403, {'error': '路径不允许。'}); return
                    if path.startswith('/assets/') and not requested.is_file():
                        self.json_response(404, {'error': '静态文件不存在。'}); return
                    if path == '/' or not requested.is_file(): requested = DIST/'index.html'
                    if not requested.exists():
                        self.json_response(503, {'error': '前端尚未构建，请在web_frontend运行npm run build。'}); return
                    self.file_response(requested)
            except KeyError:
                self.json_response(404, {'error': '资源不存在或未注册。'})
            except (BrokenPipeError, ConnectionResetError): pass
            except (ValueError, OSError) as error:
                self.json_response(409, {'error': str(error)})

        def do_POST(self):
            if not self.local_request(): return
            if not secrets.compare_digest(self.headers.get('X-Session-Token', '').encode('utf-8'), workspace.session_token.encode('utf-8')):
                self.close_connection = True
                self.json_response(403, {'error': '会话已失效，请刷新网页。'}); return
            if self.path == '/api/session':
                self.close_connection = True
                self.send_common_headers(204, 'text/plain', 0, {'Set-Cookie': f'paper_session_{port}={workspace.session_token}; HttpOnly; SameSite=Strict; Path=/api/'})
                return
            try:
                path = urlsplit(self.path).path
                size = int(self.headers.get('Content-Length', '0'))
                if path == '/api/new-papers' and new_papers is not None:
                    # Reject setup omissions before consuming/storing an upload.
                    try:
                        self.require_review_configuration()
                    except (ValueError, OSError):
                        self.close_connection = True
                        raise
                    from web_backend.new_paper_service import MAX_FILE_BYTES
                    if not 0 < size <= MAX_FILE_BYTES:
                        self.close_connection = True
                        raise ValueError('论文不能为空，且大小不能超过 20 MB。')
                    if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/octet-stream':
                        self.close_connection = True
                        raise ValueError('上传接口只接受 DOCX 原始文件内容。')
                    flags = [self.headers.get(name, 'false') for name in ('X-Use-Agent', 'X-Chapter-Policy', 'X-Three-Line-Tables')]
                    if any(value not in {'true', 'false'} for value in flags):
                        self.close_connection = True
                        raise ValueError('检查选项必须为 true 或 false。')
                    raw = self.rfile.read(size)
                    if len(raw) != size: raise ValueError('文件上传不完整。')
                    with mutation_lock:
                        runtime.require_accepting()
                        if thesis_workflow is not None and thesis_workflow.active(): raise RuntimeError('完整流程正在执行，请等待完成。')
                        if jobs.active(): raise RuntimeError('已有历史检查正在运行，请等待完成。')
                        paper = new_papers.upload(raw, self.headers.get('X-File-Name', ''),
                            self.headers.get('X-Student-Name', ''), flags[0] == 'true', flags[1] == 'true',
                            self.headers.get('X-DeepSeek-Key'), three_line_tables=flags[2] == 'true',
                            paper_type=self.headers.get('X-Paper-Type', ''),
                            max_requests=int(self.headers['X-Max-Requests']) if 'X-Max-Requests' in self.headers else None,
                            max_total_bound=int(self.headers['X-Max-Total-Bound']) if 'X-Max-Total-Bound' in self.headers else None)
                    self.json_response(202, self.paper_view(paper))
                    return
                if not 0 <= size <= 16384: raise ValueError('请求内容过大。')
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('只接受JSON请求。')
                data = json.loads(self.rfile.read(size) or b'{}')
                workflow_actions = {'/api/rule-library/refresh': 'refresh', '/api/rule-library/classify': 'classify',
                    '/api/rule-library/reclassify': 'reclassify',
                    '/api/thesis-workflow/discover': 'discover', '/api/thesis-workflow/collect': 'collect',
                    '/api/thesis-workflow/check-initials': 'check-initials'}
                if path in workflow_actions and thesis_workflow is not None:
                    if workflow_actions[path] == 'check-initials':
                        self.require_review_configuration()
                    if not isinstance(data, dict): raise ValueError('任务参数必须是JSON对象。')
                    with mutation_lock:
                        runtime.require_accepting()
                        if jobs.active() or new_papers is not None and new_papers.active():
                            raise RuntimeError('已有论文任务正在执行，请等待完成。')
                        job = thesis_workflow.start(workflow_actions[path], data, self.headers.get('X-DeepSeek-Key'))
                    self.json_response(202, {'job': job})
                elif path == '/api/jobs':
                    if workspace.snapshot().get('startup', {}).get('mode') in {'empty_workspace', 'packaged_workspace'}:
                        raise ValueError('当前未导入固定实验的历史证据，不能执行固定样本检查。')
                    if not isinstance(data, dict) or set(data) != {'action'} or data['action'] not in COMMANDS:
                        raise ValueError('请选择支持的只读检查。')
                    with mutation_lock:
                        runtime.require_accepting()
                        if thesis_workflow is not None and thesis_workflow.active(): raise RuntimeError('完整流程正在执行，请等待完成。')
                        if new_papers is not None and new_papers.active(): raise RuntimeError('已有新论文正在检查，请等待完成。')
                        job = jobs.start(data['action'])
                    self.json_response(202, job)
                elif path.startswith('/api/new-papers/') and path.endswith('/review') and new_papers is not None:
                    self.require_review_configuration()
                    if not isinstance(data, dict) or not {'use_agent','chapter_policy'}<=set(data) or set(data)-{'use_agent','chapter_policy','review_scope','three_line_tables','max_requests','max_total_bound','paper_type'}:
                        raise ValueError('重新检查需要 use_agent 和 chapter_policy 两个选项。')
                    paper_id = path.removeprefix('/api/new-papers/').removesuffix('/review')
                    with mutation_lock:
                        runtime.require_accepting()
                        if thesis_workflow is not None and thesis_workflow.active(): raise RuntimeError('完整流程正在执行，请等待完成。')
                        if jobs.active(): raise RuntimeError('已有历史检查正在运行，请等待完成。')
                        paper = new_papers.review(paper_id, data['use_agent'], data['chapter_policy'], self.headers.get('X-DeepSeek-Key'),data.get('review_scope','full'),three_line_tables=data.get('three_line_tables',False),max_requests=data.get('max_requests'),max_total_bound=data.get('max_total_bound'),paper_type=data.get('paper_type',''))
                    self.json_response(202, self.paper_view(paper))
                elif path.startswith('/api/new-papers/') and path.endswith('/cancel') and new_papers is not None:
                    if data != {}: raise ValueError('取消请求不能包含额外参数。')
                    paper_id = path.removeprefix('/api/new-papers/').removesuffix('/cancel')
                    self.json_response(202, self.paper_view(new_papers.cancel(paper_id)))
                elif path.startswith('/api/new-papers/') and path.endswith('/archive') and new_papers is not None:
                    if data != {}: raise ValueError('归档请求不能包含额外参数。')
                    paper_id = path.removeprefix('/api/new-papers/').removesuffix('/archive')
                    self.json_response(200, new_papers.archive(paper_id))
                elif path.startswith('/api/new-papers/') and path.endswith('/identity') and new_papers is not None:
                    if not isinstance(data, dict) or set(data) != {'student_name'} or not isinstance(data['student_name'], str):
                        raise ValueError('姓名修改请求格式不正确。')
                    paper_id = path.removeprefix('/api/new-papers/').removesuffix('/identity')
                    with mutation_lock:
                        paper = new_papers.update_identity(paper_id, data['student_name'])
                    self.json_response(200, self.paper_view(paper))
                elif path == '/api/refresh':
                    if data != {}: raise ValueError('刷新请求不能包含额外参数。')
                    self.json_response(200, self.workspace_view(workspace.refresh()))
                else: self.json_response(404, {'error': '接口不存在。'})
            except RuntimeError as error: self.json_response(409, {'error': str(error)})
            except (ValueError, KeyError, OSError) as error: self.json_response(400, {'error': str(error)})
    return Handler


def serve(port=8787, desktop_stdio=False):
    from web_backend.evidence_adapter import build_workspace
    from web_backend.new_paper_service import NewPapers
    from thesis_workflow import ThesisWorkflow
    if not DIST.joinpath('index.html').exists(): raise ValueError('请先构建web_frontend。')
    workspace = Workspace(build_workspace)
    if desktop_stdio:
        workspace.session_token = os.environ.pop('PAPER_DESKTOP_SESSION_TOKEN', '') or workspace.session_token
    print('正在核对本地证据……', flush=True)
    workspace.refresh()
    if workspace.snapshot().get('startup', {}).get('mode') == 'empty_workspace':
        print('首次启动：未导入历史证据，当前提供空工作区；教师规则配置完成前不受理论文检查。', flush=True)
    elif workspace.snapshot().get('startup', {}).get('mode') == 'packaged_workspace':
        print('独立版已加载通用检查配置；学校要求和历史教师原件未随包提供。', flush=True)
    jobs = Jobs()
    new_papers = NewPapers(ROOT/'data/new_papers')
    thesis_workflow = ThesisWorkflow(ROOT)
    from web_backend.desktop_runtime import DesktopRuntime
    runtime = DesktopRuntime(jobs, new_papers, thesis_workflow)
    server = ThreadingHTTPServer(('127.0.0.1', port), create_handler(workspace, jobs, port, new_papers, thesis_workflow, runtime))
    server.daemon_threads = True
    if not desktop_stdio:
        print(f'Workbench: http://127.0.0.1:{port}/#session_token={workspace.session_token}', flush=True)
    print('按Ctrl+C停止；本地检查不调用模型，启用新论文 agent 检查将按界面选项调用 DeepSeek。', flush=True)
    if desktop_stdio:
        runtime.start_stdio(sys.stdin, sys.stdout, server)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); jobs.executor.shutdown(wait=True); new_papers.close(); thesis_workflow.close()
