"""Bounded, resumable SVN collection and independent paper-check jobs.

Repository bytes stay immutable. History-selected initial versions are reported
as historical candidates, never proof of a student's first local draft.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
from threading import Lock
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET
from atomic_json import read_json, write_json

ROOT = Path(__file__).resolve().parent
from app_settings import load_settings
_DEPLOYMENT_SETTINGS = load_settings(ROOT)
WORKCOPY = _DEPLOYMENT_SETTINGS['workcopy']
SVN = _DEPLOYMENT_SETTINGS['svn_executable']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)


def now():
    return datetime.now().astimezone().isoformat()


def review_budget_charge(run):
    """Count verified usage plus unreleased reservations, never invent zero cost."""
    if not isinstance(run, dict):
        raise ValueError('初稿审阅运行记录必须为对象。')
    def number(key):
        value = run.get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError('初稿审阅用量记录不完整：' + key)
        return value
    known = number('known_total_tokens')
    reserved = number('reserved_token_bound')
    unknown = number('unknown_cost_attempts')
    requests = run.get('requests')
    if requests is None:
        # Older/injected runners do not expose per-request release evidence.
        return max(known, reserved), unknown
    if not isinstance(requests, list):
        raise ValueError('初稿审阅请求清单不完整。')
    recorded = pending = verified = 0
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError('初稿审阅请求条目不完整。')
        upper = request.get('reserved_upper_bound')
        if type(upper) is not int or upper < 0:
            raise ValueError('初稿审阅缺少请求预算凭据。')
        recorded += upper
        usage = request.get('usage')
        if usage is None:
            pending += upper
        else:
            total = usage.get('total_tokens') if isinstance(usage, dict) else None
            if type(total) is not int or total < 0:
                raise ValueError('初稿审阅请求用量无效。')
            verified += total
    if verified != known:
        raise ValueError('初稿审阅累计用量与请求凭据不一致。')
    return known + pending + max(0, reserved - recorded), unknown


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False


def thesis_path_details(path, prefix=''):
    """Classify a repository path using the same rules for history and inventory."""
    parts = path.strip('/').split('/')
    if any(p in {'', '.', '..'} or '\\' in p for p in parts):
        return None, 'unsafe_repository_path'
    if prefix and not path.startswith('/' + prefix + '/'):
        return None, 'outside_working_copy_scope'
    relative = path[len(prefix) + 2:] if prefix else path.lstrip('/')
    relparts = relative.split('/')
    if len(relparts) < 2 or not re.fullmatch(r'\d{5,}_[^/\\:<>"|?*]+', relparts[0]):
        return None, 'not_student_directory'
    basename = relparts[-1]
    if basename.startswith('~$'):
        return None, 'temporary_word_owner_file'
    in_paper_directory = len(relparts) >= 3 and relparts[1] == '论文'
    in_student_root = len(relparts) == 2
    if not in_paper_directory and not in_student_root:
        return None, 'outside_supported_paper_location'
    if any(term in basename for term in ('查重', '检测报告', '对照报告', '任务书', '开题报告', '文献综述', '模板')):
        return None, 'non_thesis_document_name'
    if in_student_root and not re.search(r'论文|初稿|[一二三四五六七八九十\d]+稿|终稿|定稿', basename):
        return None, 'unverified_student_root_document_type'
    return {'student_id': relparts[0], 'repository_path': path,
            'relative_path': relative, 'file_name': basename,
            'location_kind': 'student_root' if in_student_root else 'paper_directory'}, None


def inventory_metadata_error(result, require_source_receipt=False):
    """Validate inventory's count, path and snapshot claims without fetching SVN."""
    inventory = result.get('current_inventory')
    if not isinstance(inventory, dict):
        return '当前文件清单元数据未形成对象。'
    if inventory.get('status') != 'verified_snapshot':
        return None
    snapshot = result.get('snapshot_revision')
    if (type(snapshot) is not int or snapshot < 1
            or inventory.get('snapshot_revision') != snapshot
            or inventory.get('source_url') != result.get('working_copy_url')
            or inventory.get('count_basis') != 'svn_list_files_at_snapshot'):
        return '当前文件清单与发现记录的仓库范围或快照不一致。'
    if require_source_receipt and (not isinstance(inventory.get('path'), str)
            or not inventory['path'] or not isinstance(inventory.get('sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', inventory['sha256'])):
        return '当前文件清单缺少路径或来源哈希。'
    root_url, wc_url = result.get('repository_root'), result.get('working_copy_url')
    if (not isinstance(root_url, str) or not isinstance(wc_url, str)
            or wc_url != root_url and not wc_url.startswith(root_url + '/')):
        return '当前文件清单仓库范围不完整。'
    prefix = unquote(wc_url[len(root_url):]).strip('/')
    seen = set()
    for student in result.get('students', []):
        documents, count = student.get('current_documents'), student.get('current_document_count')
        if (student.get('current_inventory_status') != 'verified_snapshot'
                or not isinstance(documents, list) or type(count) is not int
                or count != len(documents)):
            return '学生当前文件数量或核验状态与清单不一致。'
        for document in documents:
            path = document.get('repository_path') if isinstance(document, dict) else None
            if not isinstance(path, str) or PurePosixPath(path).suffix.lower() not in {'.doc', '.docx'}:
                return '当前文件清单包含无效文件路径。'
            details, reason = thesis_path_details(path, prefix)
            if (reason or details['student_id'] != student.get('id') or path in seen
                    or any(document.get(k) != details[k] for k in ('relative_path', 'file_name', 'location_kind'))):
                return '当前文件清单的学生归属、路径或名称不一致。'
            revision, size = document.get('last_changed_revision'), document.get('size')
            if ((revision is not None and (type(revision) is not int or not 1 <= revision <= snapshot))
                    or (size is not None and (type(size) is not int or size < 0))):
                return '当前文件清单包含无效提交版本或文件大小。'
            seen.add(path)
    if type(inventory.get('document_count')) is not int or inventory['document_count'] != len(seen):
        return '当前文件清单总数与学生清单不一致。'
    return None


def normalize_discovery_counts(result, require_source_receipt=False):
    """Legacy history counts cannot establish which files exist at the snapshot."""
    result.setdefault('current_inventory', {'status': 'not_collected',
        'snapshot_revision': result.get('snapshot_revision'),
        'count_basis': 'svn_list_files_at_snapshot'})
    error = inventory_metadata_error(result, require_source_receipt)
    if error:
        prior = result['current_inventory'] if isinstance(result['current_inventory'], dict) else {}
        result['current_inventory'] = dict(prior, status='invalid_metadata', validation_error=error)
    verified = result['current_inventory'].get('status') == 'verified_snapshot'
    for student in result.get('students', []):
        documents = student.get('documents', [])
        student.setdefault('history_document_count', student.get('document_count', len(documents)))
        student.setdefault('student_version_count', sum(len(d.get('student_versions', [])) for d in documents))
        if not verified:
            student['current_inventory_status'] = result['current_inventory'].get('status', 'not_collected')
            student['current_document_count'] = None
            student['current_documents'] = []
        else:
            student.setdefault('current_inventory_status', 'verified_snapshot')
    return result


def attach_current_inventory(result, list_bytes):
    """Attach actual files from svn list; preserve all eligible historical paths."""
    listing = ET.fromstring(list_bytes)
    lists = listing.findall('list')
    if listing.tag != 'lists' or len(lists) != 1:
        raise ValueError('SVN 当前文件清单缺少唯一仓库范围。')
    expected_url = result['working_copy_url']
    if unquote(lists[0].get('path', '')).rstrip('/') != unquote(expected_url).rstrip('/'):
        raise ValueError('SVN 当前文件清单与工作副本仓库范围不一致。')
    root_url = result['repository_root']
    prefix = unquote(expected_url[len(root_url):]).strip('/')
    students = {s['id']: s for s in result['students']}
    for student in students.values():
        student.update(current_documents=[], current_document_count=0,
                       current_inventory_status='verified_snapshot')
    issues, seen = [], set()
    for entry in lists[0].findall('entry'):
        if entry.get('kind') != 'file':
            continue
        name = entry.findtext('name') or ''
        if PurePosixPath(name).suffix.lower() not in {'.doc', '.docx'}:
            continue
        # svn list names are relative to the requested URL, never absolute.
        if name.startswith('/'):
            issues.append({'relative_path': name, 'reason': 'unsafe_repository_path', 'status': 'excluded'})
            continue
        path = '/' + (prefix + '/' if prefix else '') + name
        details, reason = thesis_path_details(path, prefix)
        if reason:
            issues.append({'repository_path': path, 'relative_path': name, 'reason': reason, 'status': 'excluded'})
            continue
        if path in seen:
            raise ValueError('SVN 当前文件清单包含重复路径。')
        seen.add(path)
        sid = details.pop('student_id')
        student = students.get(sid)
        if student is None:
            student = {'id': sid, 'name': sid.split('_', 1)[1], 'documents': [],
                'document_count': 0, 'history_document_count': 0, 'teacher_version_count': 0,
                'student_version_count': 0, 'initial': None, 'initial_revision': None,
                'initial_selection': 'no_pre_feedback_student_version',
                'current_documents': [], 'current_document_count': 0,
                'current_inventory_status': 'verified_snapshot'}
            students[sid] = student
            result['discovery_issues'].extend([
                {'student_id': sid, 'reason': 'no_pre_feedback_student_version', 'status': 'missing_initial', 'repository_paths': []},
                {'student_id': sid, 'reason': 'no_teacher_version', 'status': 'missing_teacher_version'}])
        commit = entry.find('commit')
        revision = int(commit.get('revision')) if commit is not None and commit.get('revision') else None
        if revision is not None and not 1 <= revision <= result['snapshot_revision']:
            raise ValueError('SVN 当前文件提交版本超出已查询快照。')
        size = entry.findtext('size')
        details.update(size=int(size) if size is not None else None,
                       last_changed_revision=revision)
        student['current_documents'].append(details)
    for student in students.values():
        student['current_documents'].sort(key=lambda d: d['repository_path'])
        student['current_document_count'] = len(student['current_documents'])
        present = {d['repository_path'] for d in student['current_documents']}
        for document in student['documents']:
            document['present_at_snapshot'] = document['repository_path'] in present
    result.update(students=sorted(students.values(), key=lambda s: s['id']), total_students=len(students))
    result['current_inventory'] = {'status': 'verified_snapshot',
        'snapshot_revision': result['snapshot_revision'], 'source_url': expected_url,
        'count_basis': 'svn_list_files_at_snapshot',
        'document_count': len(seen), 'excluded_files': issues,
        'limitations': ['数量表示快照中可用的论文文件，不表示独立论文篇数；同一论文的多稿文件分别计数。']}
    result['coverage']['current_document_paths'] = len(seen)
    result['coverage']['current_students_with_documents'] = sum(s['current_document_count'] > 0 for s in students.values())
    return result


def parse_history(info_bytes, log_bytes, teacher_author='mayy', current_list_bytes=None):
    info = ET.fromstring(info_bytes).find('entry')
    if info is None:
        raise ValueError('SVN 信息缺少工作副本记录。')
    root_url = (info.findtext('repository/root') or '').rstrip('/')
    wc_url = (info.findtext('url') or '').rstrip('/')
    if (not root_url or not wc_url.startswith(root_url + '/') and wc_url != root_url
            or any(urlsplit(u).username or urlsplit(u).password for u in (root_url, wc_url))):
        raise ValueError('SVN 地址不完整或包含账户信息。')
    prefix = unquote(wc_url[len(root_url):]).strip('/')
    snapshot = int(info.attrib['revision'])
    students, issues = {}, []
    for entry in ET.fromstring(log_bytes).findall('logentry'):
        revision = int(entry.attrib['revision'])
        if not 1 <= revision <= snapshot:
            raise ValueError('历史版本超出工作副本快照。')
        author, message = entry.findtext('author') or '', entry.findtext('msg') or ''
        for changed in entry.findall('paths/path'):
            path = changed.text or ''
            if PurePosixPath(path).suffix.lower() not in {'.doc', '.docx'}:
                continue
            def exclude(reason):
                issues.append({'revision': revision, 'author': author, 'repository_path': path,
                               'action': changed.get('action'), 'reason': reason, 'status': 'excluded'})
            if changed.get('kind') != 'file' or changed.get('action') not in {'A', 'M', 'R'}:
                exclude('not_exportable_file_change')
                continue
            details, reason = thesis_path_details(path, prefix)
            if reason:
                exclude(reason)
                continue
            basename, sid = details['file_name'], details['student_id']
            row = students.setdefault(sid, {'id': sid, 'name': sid.split('_', 1)[1], 'documents': {}})
            document = row['documents'].setdefault(path, {'repository_path': path, 'file_name': basename,
                                                        'location_kind': details['location_kind'],
                                                        'teacher_versions': [], 'student_versions': []})
            version = {'revision': revision, 'author': author, 'commit_time': entry.findtext('date'),
                       'commit_message': message, 'repository_path': path, 'file_name': basename,
                       'action': changed.get('action')}
            document['teacher_versions' if author == teacher_author else 'student_versions'].append(version)
    result = []
    for row in students.values():
        documents = list(row['documents'].values())
        for d in documents:
            for key in ('teacher_versions', 'student_versions'):
                d[key] = sorted(d[key], key=lambda v: v['revision'])
        # Earliest chronology wins across renamed/copied paper paths. A late
        # graduation filename must not replace an earlier .doc initial version.
        candidates = []
        first_teacher = min((v['revision'] for d in documents for v in d['teacher_versions']), default=snapshot+1)
        for d in documents:
            before = [v for v in d['student_versions'] if v['revision'] < first_teacher]
            for v in before:
                candidates.append((v['revision'], 0 if '初稿' in v['commit_message'] else 1,
                                   0 if '毕业' in v['file_name'] else 1, v))
        initial = min(candidates, key=lambda x: x[:3])[3] if candidates else None
        row.update(documents=documents, document_count=len(documents), history_document_count=len(documents),
                   teacher_version_count=sum(len(d['teacher_versions']) for d in documents),
                   student_version_count=sum(len(d['student_versions']) for d in documents),
                   initial=initial, initial_revision=initial['revision'] if initial else None,
                   initial_selection='explicit_initial_commit' if initial and '初稿' in initial['commit_message']
                       else 'earliest_available_pre_feedback_candidate' if initial else 'no_pre_feedback_student_version')
        result.append(row)
        if initial is None:
            issues.append({'student_id': row['id'], 'reason': 'no_pre_feedback_student_version',
                           'status': 'missing_initial', 'repository_paths': [d['repository_path'] for d in documents]})
        if not row['teacher_version_count']:
            issues.append({'student_id': row['id'], 'reason': 'no_teacher_version', 'status': 'missing_teacher_version'})
    result = {'schema_version': 3, 'discovery_version': 'snapshot_files_and_history_paths_v3',
            'status': 'history_discovered', 'updated_at': now(),
            'snapshot_revision': snapshot, 'repository_root': root_url, 'working_copy_url': wc_url,
            'teacher_author': teacher_author, 'total_students': len(result),
            'students': sorted(result, key=lambda r: r['id']),
            'discovery_issues': issues,
            'coverage': {'included_document_paths': sum(r['document_count'] for r in result),
                         'initial_candidates': sum(r['initial'] is not None for r in result),
                         'excluded_history_changes': sum(i['status'] == 'excluded' for i in issues),
                         'root_document_paths': sum(d['location_kind'] == 'student_root' for r in result for d in r['documents'])},
            'limitations': ['初稿选择仅依据SVN历史，不证明是首次本地草稿。',
                            '教师提交作者只证明版本来源，批注作者另行保留；未导出版本不算已采集。',
                            '历史路径数量与当前文件数量分别统计；没有文件清单时当前数量未知。']}
    normalize_discovery_counts(result)
    return attach_current_inventory(result, current_list_bytes) if current_list_bytes is not None else result


def reserved_evaluation_students(root):
    """Read metadata only; malformed isolation metadata blocks teacher collection."""
    folder = Path(root)/'data/independent_evaluation'
    reserved = {}
    def student_id(row, path):
        value = row.get('student_id') if isinstance(row, dict) else None
        if not isinstance(value, str) or not re.fullmatch(r'\d{5,}(?:_[^/\\:<>"|?*]+)?', value):
            raise ValueError(f'独立评测隔离记录无有效student_id，已阻止教师采集：{path.name}')
        return value.split('_', 1)[0]
    for path in folder.glob('**/prospective_candidate.json'):
        try:
            row = read_json(path)
        except (ValueError, OSError, TypeError) as error:
            raise ValueError('独立评测候选记录不可读取，已阻止教师采集。') from error
        sid = student_id(row, path)
        # Pending attestation is itself a reservation, not permission to expose
        # the student's held-out teacher feedback during development.
        reserved.setdefault(sid, []).append({'metadata_path': str(path),
            'split': row.get('split'), 'status': row.get('status'), 'reason': 'prospective_candidate_reserved'})
    registry = folder/'registry.json'
    if registry.exists():
        try:
            value = read_json(registry)
            materials = value.get('materials')
            if not isinstance(materials, list):
                raise ValueError('materials未形成列表')
            for row in materials:
                sid = student_id(row, registry)
                split = row.get('split')
                if split not in {'development', 'independent_test', 'holdout', 'pending_candidate'}:
                    raise ValueError('隔离分组未知')
                if split != 'development':
                    reserved.setdefault(sid, []).append({'metadata_path': str(registry),
                        'split': split, 'status': 'reserved', 'reason': 'independent_material_reserved'})
        except (ValueError, OSError, TypeError, AttributeError) as error:
            raise ValueError('独立评测注册记录无效，已阻止教师采集。') from error
    return reserved


class ThesisWorkflow:
    def __init__(self, root=ROOT, invoke=None, review_runner=None):
        self.root = Path(root).resolve()
        self.data = self.root / 'data/thesis_workflow'
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='thesis-workflow')
        self.invoke = invoke or self._invoke
        self.review_runner = review_runner
        self.jobs = {}
        for path in (self.data/'jobs').glob('job_*.json'):
            try:
                job = read_json(path)
                if job['status'] in {'queued', 'running'} and not process_alive(job.get('owner_pid')):
                    job.update(status='failed', error='服务中断，保留已完成来源；未自动重试。', finished_at=now())
                    save(path, job)
                self.jobs[job['id']] = job
            except (ValueError, OSError, KeyError):
                continue

    def _invoke(self, argv, cwd):
        result = subprocess.run(argv, cwd=str(cwd), capture_output=True, timeout=60, shell=False)
        if result.returncode:
            raise ValueError('SVN 请求失败，可能是网络、权限或历史路径问题；本次不自动重试。')
        return result.stdout

    def active(self):
        self._refresh_external_jobs()
        with self.lock:
            return any(j['status'] in {'queued', 'running'} for j in self.jobs.values())

    def _refresh_external_jobs(self):
        # CLI and UI share the same queue ledger. A live CLI job is not a
        # crashed server job; do not mark it failed during a UI restart.
        with self.lock:
            for path in (self.data/'jobs').glob('job_*.json'):
                if path.stem not in self.jobs:
                    value = read_json(path)
                    self.jobs[value['id']] = value
            for jid, current in list(self.jobs.items()):
                if current.get('owner_pid') == os.getpid():
                    continue
                path = self.data/'jobs'/(jid+'.json')
                if path.exists():
                    value = read_json(path)
                    if value['status'] in {'queued', 'running'} and not process_alive(value.get('owner_pid')):
                        value.update(status='failed', error='任务进程已退出，保留证据，不自动重试。', finished_at=now())
                        save(path, value)
                    self.jobs[jid] = value

    def _persist(self, job):
        save(self.data/'jobs'/(job['id']+'.json'), job)

    def log(self, job, message):
        with self.lock:
            job['log_tail'] = (job.get('log_tail', '') + '\n' + message).strip()[-6000:]
            self._persist(job)

    def snapshot(self):
        self._refresh_external_jobs()
        discovery_path = self.data/'discovery.json'
        discovery = normalize_discovery_counts(read_json(discovery_path) if discovery_path.exists()
            else {'status': 'not_discovered', 'students': [], 'total_students': 0},
            require_source_receipt=True)
        try:
            reserved = reserved_evaluation_students(self.root)
            for student in discovery['students']:
                reasons = reserved.get(student['id'].split('_', 1)[0])
                if reasons:
                    student['teacher_collection_status'] = 'reserved_for_independent_evaluation'
                    student['evaluation_reservation'] = reasons
                    student['teacher_collection_allowed'] = False
            discovery['reserved_student_count'] = sum(s.get('teacher_collection_allowed') is False for s in discovery['students'])
            if reserved:
                discovery.setdefault('limitations', []).append('独立评测及待签认候选学生的教师批注已隔离；采集选择包含这些学生时显式跳过，不纳入归纳。')
        except ValueError as error:
            discovery['teacher_collection_disabled'] = True
            discovery['evaluation_reservation_error'] = str(error)
            discovery.setdefault('limitations', []).append('隔离记录无效，教师采集已整体阻止，需修复元数据后继续。')
        with self.lock:
            jobs = deepcopy(sorted(self.jobs.values(), key=lambda j: j['created_at'], reverse=True)[:30])
        capabilities = [
            {'id': 'discover', 'label': '发现仓库论文', 'status': 'available', 'description': '只读SVN历史，列出学生、教师版本与初稿候选。'},
            {'id': 'collect', 'label': '采集教师批注', 'status': 'available', 'description': '原件按姓名与版本导出，保留全部批注，支持继续采集未处理版本。'},
            {'id': 'classify', 'label': '归纳全部批注', 'status': 'available', 'description': '本地证据分类加有预算的模型归纳，待处理批注明确列出。'},
            {'id': 'reclassify', 'label': '补充上下文复核批注分类', 'status': 'available', 'description': '对尚不能明确归类的批注补读已提取的同源上下文；保留不确定结论，不等于规则签审。'},
            {'id': 'check-initials', 'label': '核查学生初稿', 'status': 'available', 'description': '对任意发现学生的历史初稿候选执行当前规则库，逐稿保存结果。'},
            {'id': 'new-paper', 'label': '独立批改上传论文', 'status': 'available', 'description': '无需固定学生代号；工具核查并按预算提供带引文的语境建议。'},
        ]
        return {'capabilities': capabilities, 'discovery': discovery, 'jobs': jobs,
                'api_key_available': bool(os.environ.get('DEEPSEEK_API_KEY'))}

    def start(self, action, options=None, api_key=None):
        options = options or {}
        if action not in {'discover', 'collect', 'check-initials', 'refresh', 'classify', 'reclassify'}:
            raise ValueError('不支持的完整流程任务。')
        if action in {'discover', 'refresh'} and options:
            raise ValueError('此任务不接受额外参数。')
        if action in {'collect', 'check-initials'}:
            expected = {'student_ids', 'max_versions'} if action == 'collect' else {'student_ids', 'use_agent', 'chapter_policy'}
            allowed = expected | ({'max_reserved_tokens','review_scope'} if action == 'check-initials' else set())
            if not expected <= set(options) or not set(options) <= allowed:
                raise ValueError('批次选项不完整或含未知字段。')
            ids = options.get('student_ids')
            if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids) or not all(isinstance(i, str) for i in ids):
                raise ValueError('每批请选择1至10名不同学生。')
            discovery = self.snapshot()['discovery']
            valid = {s['id'] for s in discovery['students']}
            if not set(ids) <= valid:
                raise ValueError('学生不在当前已发现的仓库记录中。')
            if action == 'collect' and (type(options['max_versions']) is not int or not 1 <= options['max_versions'] <= 5):
                raise ValueError('每名学生每批最多导出1至5个尚未处理的教师版本。')
            if action == 'check-initials' and not all(type(options[k]) is bool for k in ('use_agent', 'chapter_policy')):
                raise ValueError('检查选项必须为布尔值。')
            if action == 'check-initials' and (type(options.get('max_reserved_tokens', 24000)) is not int
                    or not 0 <= options.get('max_reserved_tokens', 24000) <= 24000):
                raise ValueError('初稿审阅保守预算必须为0至24000 token。')
            if action == 'check-initials' and (not isinstance(options.get('review_scope','full'), str) or options.get('review_scope','full') not in {'full','content','figures'}):
                raise ValueError('初稿审阅范围必须为full、content或figures。')
        if action in {'classify', 'reclassify'}:
            if set(options) != {'max_requests', 'max_reserved_tokens'} or type(options['max_requests']) is not int or not 1 <= options['max_requests'] <= 4 or type(options['max_reserved_tokens']) is not int or not 4000 <= options['max_reserved_tokens'] <= 24000:
                raise ValueError('归纳预算必须为1至4次请求、4000至24000保守token上限。')
        key = api_key or os.environ.get('DEEPSEEK_API_KEY')
        if (action in {'classify', 'reclassify'} or
                options.get('use_agent') and options.get('max_reserved_tokens', 24000) > 0) and not key:
            raise ValueError('模型任务需要已配置DeepSeek Key；也可先运行本地采集与核查。')
        with self.lock:
            if any(j['status'] in {'queued', 'running'} for j in self.jobs.values()):
                raise RuntimeError('完整流程已有任务正在运行。')
            jid = 'job_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(3)
            job = {'id': jid, 'action': action, 'label': {'discover': '发现仓库论文', 'collect': '采集教师批注', 'check-initials': '核查学生初稿', 'refresh': '刷新教师规则库', 'classify': '归纳待处理批注', 'reclassify': '补充上下文复核批注分类'}[action],
                   'owner_pid': os.getpid(),
                   'created_at': now(), 'status': 'queued', 'options': deepcopy(options), 'log_tail': '',
                   'error': None, 'svn_requests': 0, 'llm_requests': 0, 'known_total_tokens': 0,
                   'unknown_cost_attempts': 0, 'word_launches': 0, 'automatic_retries': 0,
                   'conversion_attempts': 0, 'word_launches_unknown': 0,
                   'reserved_token_bound': 0, 'budget_charge_token_bound': 0,
                   'max_reserved_token_bound': options.get('max_reserved_tokens', 24000)}
            self.jobs[jid] = job
            self._persist(job)
        self.executor.submit(self._run, job, key)
        return deepcopy(job)

    def svn(self, job, arguments, cwd=WORKCOPY):
        with self.lock:
            job['svn_requests'] += 1
            self._persist(job)
        return self.invoke([str(SVN), *map(str, arguments), '--non-interactive'], cwd=Path(cwd))

    def discover(self, job):
        self.log(job, '读取工作副本快照、完整提交历史和当前文件清单……')
        info = self.svn(job, ['info', '--xml', '.'])
        entry = ET.fromstring(info).find('entry')
        if entry is None:
            raise ValueError('工作副本信息不完整。')
        url, rev = entry.findtext('url'), int(entry.attrib['revision'])
        history = self.svn(job, ['log', '--xml', '--verbose', '-r', f'{rev}:1', f'{url}@{rev}'])
        folder = self.data/'history'/job['id']
        folder.mkdir(parents=True)
        (folder/'info.xml').write_bytes(info)
        (folder/'history.xml').write_bytes(history)
        current_files = self.svn(job, ['list', '--xml', '--recursive', '-r', str(rev), f'{url}@{rev}'])
        (folder/'current_files.xml').write_bytes(current_files)
        result = parse_history(info, history, current_list_bytes=current_files)
        result['history'] = {'info_path': str(folder/'info.xml'), 'info_sha256': digest(folder/'info.xml'),
                             'path': str(folder/'history.xml'), 'sha256': digest(folder/'history.xml')}
        result['current_inventory'].update(path=str(folder/'current_files.xml'),
            sha256=digest(folder/'current_files.xml'))
        save(self.data/'discovery.json', result)
        self.log(job, f'发现{result["total_students"]}名学生；当前论文文件{result["current_inventory"]["document_count"]}个；历史与当前分开统计，未采集版本仍不算已完成。')
        return {'students': result['total_students'],
                'current_document_count': result['current_inventory']['document_count'],
                'teacher_versions': sum(s['teacher_version_count'] for s in result['students'])}

    def _discovery(self):
        result = self.snapshot()['discovery']
        history = result.get('history', {})
        for pathkey, hashkey in (('path', 'sha256'), ('info_path', 'info_sha256')):
            if not history.get(pathkey) or digest(history[pathkey]) != history[hashkey]:
                raise ValueError('已保存仓库历史变化，请重新发现来源。')
        info = ET.fromstring(Path(history['info_path']).read_bytes()).find('entry')
        if (info is None or int(info.attrib['revision']) != result.get('snapshot_revision')
                or (info.findtext('url') or '').rstrip('/') != result.get('working_copy_url')
                or (info.findtext('repository/root') or '').rstrip('/') != result.get('repository_root')):
            raise ValueError('发现记录与已核验工作副本快照不一致，请重新发现来源。')
        inventory = result.get('current_inventory', {})
        if inventory.get('status') == 'invalid_metadata':
            raise ValueError('当前文件清单元数据不一致，请重新发现来源。')
        if inventory.get('status') == 'verified_snapshot':
            if inventory_metadata_error(result, require_source_receipt=True):
                raise ValueError('当前文件清单快照不一致，请重新发现来源。')
            source = Path(inventory['path']).resolve()
            if not source.is_relative_to(self.data/'history') or digest(source) != inventory['sha256']:
                raise ValueError('当前文件清单来源哈希或保存位置不一致，请重新发现来源。')
            refreshed = attach_current_inventory(deepcopy(result), source.read_bytes())
            actual = {s['id']: s['current_documents'] for s in refreshed['students']}
            saved = {s['id']: s['current_documents'] for s in result['students']}
            if actual != saved:
                raise ValueError('当前文件清单内容与来源不一致，请重新发现来源。')
        return result

    def _export(self, job, discovery, version, destination):
        if not destination.resolve().is_relative_to(self.data):
            raise ValueError('导出位置不属于当前流程。')
        url = discovery['repository_root'] + quote(version['repository_path'], safe='/')
        source_url = url + '@' + str(version['revision'])
        # Recover only the observed basename-encoding failure from this
        # session's owned SVN output. Preserve the failed manifest and bytes.
        if '/exports/' in destination.as_posix():
            for mp in destination.parent.parent.glob('*/manifest.json'):
                if mp.parent == destination.parent:
                    continue
                previous = read_json(mp)
                failures = previous.get('failures', [])
                for failure in failures:
                    cached_info = failure.get('original_export', {})
                    if (cached_info.get('revision') == version['revision'] and cached_info.get('repository_path') == version['repository_path']
                            and cached_info.get('status') == 'exported' and cached_info.get('source_url') == source_url):
                        cached = Path(cached_info['exported_path']).resolve()
                        if not cached.is_relative_to(mp.parent.resolve()) or digest(cached) != cached_info.get('sha256'):
                            raise ValueError('此前失败转换保留的原件哈希不一致。')
                        destination.write_bytes(cached.read_bytes())
                        if digest(destination) != cached_info['sha256'] or digest(cached) != cached_info['sha256']:
                            raise ValueError('原件复用过程中发生变化。')
                        return dict(version, status='exported', source_url=source_url, exported_path=str(destination),
                                    sha256=digest(destination), size_bytes=destination.stat().st_size,
                                    export_transport='verified_previous_export_reused', previous_manifest=str(mp), previous_manifest_sha256=digest(mp))
                eligible = [f for f in failures if f.get('revision') == version['revision'] and f.get('repository_path') == version['repository_path'] and f.get('error') == 'SVN没有产出预期原件。']
                candidates = list(mp.parent.glob('*r'+str(version['revision'])+'_*'+destination.suffix))
                if len(eligible) == 1 and len(candidates) == 1 and len([f for f in failures if f.get('revision') == version['revision']]) == 1:
                    cached = candidates[0]
                    expected = f'{previous["student_name"]}_教师批改_r{version["revision"]}_{failures.index(eligible[0])+1:02d}'+destination.suffix
                    try:
                        mangled = expected.encode('gbk').decode('latin1')
                    except UnicodeError:
                        continue
                    if cached.name != mangled:
                        continue
                    cached_hash = digest(cached)
                    destination.write_bytes(cached.read_bytes())
                    if digest(destination) != cached_hash or digest(cached) != cached_hash:
                        raise ValueError('已导出原件复用过程中哈希变化。')
                    return dict(version, status='exported', source_url=source_url, exported_path=str(destination),
                        sha256=cached_hash, size_bytes=destination.stat().st_size,
                        export_transport='recovered_unicode_basename_from_successful_svn_export',
                        previous_manifest=str(mp), previous_manifest_sha256=digest(mp), original_cached_path=str(cached))
        # The installed SVN 1.14 client mangles Chinese output basenames under
        # this Windows locale. Use ASCII argv, then a native Unicode rename.
        staging = destination.with_name('svn_'+hashlib.sha256(destination.name.encode('utf-8')).hexdigest()[:16]+destination.suffix)
        if staging.exists() or destination.exists():
            raise ValueError('导出目标已存在，不覆盖历史证据。')
        self.svn(job, ['export', '--quiet', '-r', version['revision'], source_url, staging.name], destination.parent)
        if staging.is_file():
            staging.replace(destination)
        if not destination.is_file():
            raise ValueError('SVN没有产出预期原件。')
        return dict(version, status='exported', source_url=source_url, exported_path=str(destination),
                    sha256=digest(destination), size_bytes=destination.stat().st_size)

    def _analysis_copy(self, original, folder, item, job):
        from process_p002_doc_pilot import detect_format, convert_copy
        from comment_representation_audit import audit_parser_comment_representation
        from docx_comment_reader import extract_docx
        actual = detect_format(original)
        if actual == 'word_ooxml_zip':
            if original.suffix.lower() == '.docx':
                return original, item
            derived = folder/(original.stem+'_analysis.docx')
            derived.write_bytes(original.read_bytes())
            item = dict(item, exported_path=str(derived), sha256=digest(derived),
                        original_export={'document_path': str(original), 'sha256': digest(original)},
                        format_note='OOXML bytes with legacy suffix; exact byte copy')
            return derived, item
        derived = folder/(original.stem+'_analysis.docx')
        audit_path = folder/(original.stem+'_conversion.json')
        with self.lock:
            job['conversion_attempts'] = job.get('conversion_attempts', 0) + 1
            self._persist(job)
        try:
            converted = convert_copy(original, derived, audit_path)
        finally:
            if audit_path.exists():
                audit_state = read_json(audit_path, encoding='utf-8-sig')
                with self.lock:
                    if audit_state.get('word_version'):
                        job['word_launches'] += 1
                    elif audit_state.get('current_stage') != 'prepared':
                        job['word_launches_unknown'] = job.get('word_launches_unknown', 0) + 1
                    self._persist(job)
        source = {'sample_id': folder.parent.name, 'sha256': digest(derived)}
        extracted = extract_docx(derived, source)
        representation = audit_parser_comment_representation(converted['audit'], extracted, derived)
        representation_path = folder/(original.stem+'_representation.json')
        save(representation_path, representation)
        # This converter separately audits text/comment preservation; never let
        # a conversion failure become an eligible analysis source.
        audit = converted['audit']
        if audit.get('source_sha256_unchanged') is not True or audit.get('comment_fidelity_verified') is not True or audit.get('body_text_hash_equal') is not True:
            raise ValueError('旧DOC转换的文字或批注保留核验未通过。')
        item = dict(item, exported_path=str(derived), sha256=digest(derived),
                    artifact_role='analysis_copy_from_legacy_svn_doc',
                    original_export={'document_path': str(original), 'sha256': digest(original)},
                    conversion={'document_path': str(audit_path), 'sha256': digest(audit_path), 'mode': audit['status']},
                    comment_representation={'document_path': str(representation_path), 'sha256': digest(representation_path)})
        from legacy_doc_provenance_v2 import validate_lineage
        validate_lineage(folder, item)
        return derived, item

    def collect(self, job):
        reserved = reserved_evaluation_students(self.root)
        d = self._discovery()
        selected = [s for s in d['students'] if s['id'] in job['options']['student_ids']]
        results = []
        for s in selected:
            reserved = reserved_evaluation_students(self.root)
            if s['id'].split('_', 1)[0] in reserved:
                results.append({'student_id': s['id'], 'student_name': s['name'],
                    'status': 'skipped_reserved_for_independent_evaluation', 'exported': 0,
                    'comments': 0, 'failed': 0, 'remaining_versions': s['teacher_version_count'],
                    'reservation': reserved[s['id'].split('_', 1)[0]]})
                self.log(job, f'{s["name"]}：保留独立评测隔离，未导出或读取教师批注。')
                continue
            versions = [v for document in s['documents'] for v in document['teacher_versions']]
            versions.sort(key=lambda v: v['revision'], reverse=True)
            student_root = self.data/'exports'/s['id']
            seen = set()
            for mp in student_root.glob('*/manifest.json'):
                m = read_json(mp)
                for f in m.get('files', []):
                    if f.get('status') == 'exported' and digest(f['exported_path']) == f['sha256']:
                        seen.add((f['revision'], f['repository_path']))
            remaining = [v for v in versions if (v['revision'], v['repository_path']) not in seen]
            folder = student_root/job['id']
            folder.mkdir(parents=True)
            manifest = {'schema_version': 1, 'sample_id': s['id'], 'student_name': s['name'],
                        'teacher_author': d['teacher_author'], 'snapshot_revision': d['snapshot_revision'],
                        'files': [], 'failures': [], 'status': 'collecting'}
            save(folder/'manifest.json', manifest)
            for ordinal, v in enumerate(remaining[:job['options']['max_versions']], 1):
                if s['id'].split('_', 1)[0] in reserved_evaluation_students(self.root):
                    raise ValueError('当前学生在执行期间被登记为独立评测隔离；后续教师版本未导出。')
                self.log(job, f'{s["name"]}：导出教师r{v["revision"]}……')
                name = f'{s["name"]}_教师批改_r{v["revision"]}_{ordinal:02d}'+PurePosixPath(v['repository_path']).suffix
                original = folder/name
                exported = None
                try:
                    exported = self._export(job, d, v, original)
                    analysis, item = self._analysis_copy(original, folder, exported, job)
                    from docx_comment_reader import extract_docx
                    extracted = extract_docx(analysis, {'sample_id': s['id'], 'revision': v['revision'],
                            'svn_author': v['author'], 'repository_path': v['repository_path'],
                            'document_path': str(analysis), 'source_url': item['source_url'], 'sha256': item['sha256']})
                    item['comment_count'] = len(extracted['comments'])
                    manifest['files'].append(item)
                except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                    failure = {'revision': v['revision'], 'repository_path': v['repository_path'],
                               'status': 'failed_no_retry', 'error': str(error)[:600]}
                    if exported:
                        failure['original_export'] = exported
                    manifest['failures'].append(failure)
                save(folder/'manifest.json', manifest)
            manifest['status'] = 'collected_partial' if manifest['failures'] else 'collected'
            manifest['remaining_versions'] = max(0, len(remaining)-len(manifest['files']))
            save(folder/'manifest.json', manifest)
            results.append({'student_id': s['id'], 'student_name': s['name'], 'exported': len(manifest['files']),
                            'comments': sum(f['comment_count'] for f in manifest['files']),
                            'failed': len(manifest['failures']), 'remaining_versions': manifest['remaining_versions']})
        from teacher_rule_library import get_library
        processed = [r for r in results if r.get('status') != 'skipped_reserved_for_independent_evaluation']
        library = get_library(self.root, refresh=bool(processed)) if processed else None
        return {'students': results, 'processed_students': len(processed),
                'collected_students': len(processed),
                'skipped_reserved_students': len(results)-len(processed),
                'exported_versions': sum(r['exported'] for r in results),
                'failed_count': sum(r['failed'] for r in results),
                'comments': sum(r['comments'] for r in results), 'library_summary': library['summary'] if library else None}

    def _reusable_initial(self, student):
        """Reuse verified originals and complete snapshots, not filename guesses."""
        initial = student.get('initial') or {}
        saved = []
        paths = set((self.data/'initial_checks'/student['id']).glob('*/review/report.json'))
        for job in self.jobs.values():
            rows = job.get('result_summary', {}).get('students', [])
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get('student_id') == student['id'] and row.get('report_json_path'):
                    paths.add(Path(row['report_json_path']))
        for path in paths:
            try:
                path = path.resolve()
                if not path.is_relative_to(self.root.resolve()):
                    continue
                report = read_json(path)
                source_path = path.parent.parent/'source.json'
                source = read_json(source_path) if source_path.exists() else report.get('svn_source', {})
                if source.get('revision') != initial.get('revision') or source.get('repository_path') != initial.get('repository_path'):
                    continue
                snapshot = report.get('source_snapshot')
                analysis = Path(snapshot['original_source_path'] if snapshot else source['exported_path']).resolve()
                if not analysis.is_relative_to(self.root.resolve()) or digest(analysis) != report.get('source_sha256'):
                    continue
                if source.get('sha256') != report.get('source_sha256'):
                    continue
                parsed = None
                if snapshot:
                    snapshot_path = Path(snapshot['snapshot_path']).resolve()
                    if not snapshot_path.is_relative_to(self.root.resolve()) or digest(snapshot_path) != snapshot.get('snapshot_sha256'):
                        continue
                    from legacy_word_snapshot import build_parsed
                    parsed, _ = build_parsed(snapshot_path, analysis)
                elif analysis.suffix.lower() != '.docx':
                    continue
                elif source.get('conversion'):
                    from legacy_doc_provenance_v2 import validate_lineage
                    validate_lineage(path.parent.parent, source)
                elif source.get('original_export'):
                    original = source['original_export']
                    original_path = Path(original['document_path']).resolve()
                    if (not original_path.is_relative_to(self.root.resolve())
                            or digest(original_path) != original['sha256']
                            or original['sha256'] != source['sha256']):
                        continue
                layout = None
                if (path.parent/'layout_result.json').exists() and not snapshot:
                    candidate = read_json(path.parent/'layout_result.json')
                    if (candidate.get('source', {}).get('sha256') == report['source_sha256']
                            and candidate.get('source_unchanged') is True
                            and candidate.get('status') == 'native_table_layout_collected_visual_pending'):
                        layout = candidate
                saved.append((str(report.get('run', {}).get('finished_at', '')), analysis, source, parsed, snapshot, report, layout))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return max(saved, key=lambda row: row[0])[1:] if saved else None

    def check_initials(self, job, key):
        d = self._discovery()
        results = []
        job.setdefault('budget_charge_token_bound', 0)
        def record_progress():
            remaining = (0 if job['unknown_cost_attempts'] else max(0, min(
                job['max_reserved_token_bound'] - job['budget_charge_token_bound'],
                job['max_reserved_token_bound'] - job['reserved_token_bound'])))
            summary = {'students': deepcopy(results), 'checked': sum('summary' in r for r in results),
                       'failed_or_unavailable': sum('summary' not in r for r in results),
                       'remaining_token_bound': remaining,
                       'budget_exhausted': job['reserved_token_bound'] >= job['max_reserved_token_bound'],
                       'budget_overrun': job['reserved_token_bound'] > job['max_reserved_token_bound'],
                       'cost_unknown_stop': bool(job['unknown_cost_attempts'])}
            with self.lock:
                job['result_summary'] = summary
                self._persist(job)
            return summary

        def account(run):
            charged, unknown = review_budget_charge(run)
            with self.lock:
                for field in ('request_attempts', 'known_total_tokens', 'reserved_token_bound', 'word_launches'):
                    value = run.get(field, 0)
                    if type(value) is not int or value < 0:
                        raise ValueError('初稿审阅用量记录无效：' + field)
                job['llm_requests'] += run.get('request_attempts', 0)
                job['known_total_tokens'] += run.get('known_total_tokens', 0)
                job['unknown_cost_attempts'] += unknown
                job['reserved_token_bound'] += run.get('reserved_token_bound', 0)
                job['budget_charge_token_bound'] += charged
                job['word_launches'] += run.get('word_launches', 0)
                self._persist(job)

        for s in d['students']:
            if s['id'] not in job['options']['student_ids']:
                continue
            initial = s.get('initial')
            if not initial:
                results.append({'student_id': s['id'], 'student_name': s['name'], 'status': 'no_pre_feedback_student_version'})
                record_progress()
                continue
            folder = self.data/'initial_checks'/s['id']/job['id']
            folder.mkdir(parents=True)
            self.log(job, f'{s["name"]}：核查历史初稿候选r{initial["revision"]}……')
            accounted = runner_started = False
            try:
                reused = self._reusable_initial(s)
                prior_report = layout_result = None
                if reused:
                    analysis, source, parsed_source, snapshot_info, prior_report, layout_result = reused
                    self.log(job, f'{s["name"]}：复用哈希与版本核验通过的原件；已完成语义结论仅在规则与上下文一致时复用。')
                else:
                    original = folder/(s['name']+'_初稿候选_r'+str(initial['revision'])+PurePosixPath(initial['repository_path']).suffix)
                    source = self._export(job, d, initial, original)
                    parsed_source = snapshot_info = None
                    try:
                        analysis, source = self._analysis_copy(original, folder, source, job)
                    except ValueError:
                        from initial_text_fallback import recover
                        def record_word():
                            with self.lock:
                                job['word_launches'] += 1
                                self._persist(job)
                        parsed_source, snapshot_info = recover(original, folder, record_word)
                        analysis = original
                        source['text_snapshot'] = snapshot_info
                save(folder/'source.json', source)
                if self.review_runner is None:
                    from new_paper_agent import run_review
                    runner = run_review
                else:
                    runner = self.review_runner
                remaining = (0 if job['unknown_cost_attempts'] else
                             max(0, min(job['max_reserved_token_bound'] - job['budget_charge_token_bound'],
                                        job['max_reserved_token_bound'] - job['reserved_token_bound'])))
                if job['options']['use_agent'] and not remaining:
                    self.log(job, f'{s["name"]}：本轮模型预算不可继续使用；保留本地核查和兼容的既有审阅证据，不发出模型请求。')
                runner_started = True
                report = runner(analysis, folder/'review', student_name=s['name'],
                                chapter_policy=job['options']['chapter_policy'], api_key=key,
                                use_agent=job['options']['use_agent'], collect_word=True, file_name=analysis.name,
                                max_total_bound=remaining, strict_reservation_budget=True,
                                review_scope=job['options'].get('review_scope','full'),
                                parsed_source=parsed_source, prior_report=prior_report, layout_result=layout_result)
                # Account before saving derived reports: a disk failure must not
                # erase dispatched requests or permit another paid operation.
                account(report.get('run', {}))
                accounted = True
                report.update(source_role='svn_history_initial_candidate', svn_source=source,
                              initial_selection=s['initial_selection'])
                if snapshot_info:
                    report.update(source_role='svn_history_initial_candidate_original_doc_text_snapshot', source_snapshot=snapshot_info)
                report['limitations'] = [x for x in report['limitations'] if '上传者' not in x]
                report['limitations'].append('本版本为SVN历史初稿候选，不证明是首次本地草稿。')
                save(folder/'review/report.json', report)
                from paper_report import render_report
                (folder/'review/report.md').write_text(render_report(report), encoding='utf-8')
                results.append({'student_id': s['id'], 'student_name': s['name'], 'revision': initial['revision'],
                                'status': report.get('status'), 'summary': report['summary'],
                                'source_path': snapshot_info['source_reader_path'] if snapshot_info else str(analysis),
                                'source_sha256': snapshot_info['source_reader_sha256'] if snapshot_info else digest(analysis),
                                'report_path': str(folder/'review/report.md'), 'report_json_path': str(folder/'review/report.json'),
                                'coverage': report['coverage']})
            except (ValueError, OSError, subprocess.TimeoutExpired, KeyError, TypeError) as error:
                if runner_started and not accounted and job['options']['use_agent']:
                    try:
                        account(read_json(folder/'review/run.json'))
                    except (OSError, ValueError, TypeError):
                        # No auditable final usage: prevent subsequent students
                        # from issuing API requests, even if the model succeeded.
                        with self.lock:
                            job['unknown_cost_attempts'] += 1
                            job['budget_charge_token_bound'] = job['max_reserved_token_bound']
                            self._persist(job)
                detail = str(error).replace(key, '[密钥已隐藏]') if key else str(error)
                results.append({'student_id': s['id'], 'student_name': s['name'], 'status': 'failed_no_retry', 'error': detail[:600]})
            record_progress()
        return record_progress()

    def _run(self, job, key):
        folder = self.data/'runs'/job['id']
        folder.mkdir(parents=True)
        with self.lock:
            job.update(status='running', started_at=now(), result_dir=str(folder))
            self._persist(job)
        try:
            if job['action'] == 'discover':
                result = self.discover(job)
            elif job['action'] == 'collect':
                result = self.collect(job)
            elif job['action'] == 'check-initials':
                result = self.check_initials(job, key)
            else:
                from teacher_rule_library import get_library, classify_library, publish_classification
                library = get_library(self.root, refresh=job['action'] == 'refresh')
                if job['action'] in {'classify', 'reclassify'}:
                    from openai import OpenAI
                    client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=45, max_retries=0)
                    extra = {}
                    if job['action'] == 'reclassify':
                        from teacher_rule_library import classification_contract
                        contract = classification_contract(library)
                        reviewed = set()
                        for receipt in (self.data/'runs').glob('*/classification/classification_run.json'):
                            previous = read_json(receipt)
                            if previous.get('review_existing') is True and previous.get('classification_contract') == contract:
                                reviewed.update(previous.get('classified_comment_ids', []))
                        extra = {'review_existing': True, 'reviewed_comment_ids': sorted(reviewed)}
                    library = classify_library(library, folder/'classification', client, **job['options'], **extra)
                    library = publish_classification(self.root, library)
                    classification = library.get('classification_run', {})
                    job['llm_requests'] = classification.get('request_attempts', 0)
                    job['known_total_tokens'] = classification.get('known_total_tokens', 0)
                    job['unknown_cost_attempts'] = classification.get('unknown_cost_attempts', 0)
                    job['reserved_token_bound'] = classification.get('reserved_token_bound', 0)
                result = {'library_summary': library['summary'], 'comments': library['summary']['total_comments'],
                          'rules': library['summary']['rule_count'], 'remaining_comments': library['summary']['unclassified_comments'],
                          'request_attempts': job['llm_requests'], 'known_total_tokens': job['known_total_tokens']}
                if job['action'] in {'classify', 'reclassify'}:
                    result['classification_status'] = library.get('classification_run', {}).get('status')
                    result['still_uncertain_comments'] = len(library.get('classification_run', {}).get('still_uncertain_comment_ids', []))
                    result['processed_comments_this_round'] = len(library.get('classification_run', {}).get('classified_comment_ids', []))
                    result['remaining_comments'] = len(library.get('classification_run', {}).get('remaining_comment_ids', []))
            with self.lock:
                incomplete = bool(result.get('failed_count') or result.get('failed_or_unavailable')
                                  or result.get('still_uncertain_comments')
                                  or result.get('classification_status') not in {None, 'completed'})
                if job['action'] == 'check-initials':
                    from teacher_task_coverage import result_is_complete
                    incomplete = incomplete or any(not result_is_complete(row) for row in result.get('students', []))
                job.update(status='partial' if incomplete else 'completed', result_summary=result, finished_at=now())
                self._persist(job)
        except Exception as error:
            with self.lock:
                job.update(status='failed', error=str(error)[:800] if isinstance(error, (ValueError, OSError)) else '外部任务未完成，保留已完成证据；不自动重试。', finished_at=now())
                if key:
                    job['error'] = job['error'].replace(key, '[密钥已隐藏]')
                self._persist(job)
        finally:
            save(folder/'run.json', job)
            (folder/'report.md').write_text('# '+job['label']+'\n\n状态：'+job['status']+'\n\n```json\n'+json.dumps(job, ensure_ascii=False, indent=2)+'\n```\n', encoding='utf-8')
            key = None

    def close(self):
        self.executor.shutdown(wait=True)
