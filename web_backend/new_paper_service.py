"""Persistent, local-only uploads and explicitly requested bounded reviews."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
from threading import Event, Lock
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile

from atomic_json import read_json, write_json
from new_paper_checks import _xml, validate_document_body

MAX_FILE_BYTES = 20 * 1024 * 1024
WORD_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'


def validate_name(value, *, filename=False):
    value = unquote(value or '').strip()
    if len(value) > (240 if filename else 128) or any(ord(c) < 32 for c in value):
        raise ValueError('文件名或学生姓名过长或包含控制字符。')
    if filename:
        if not value or '/' in value or '\\' in value or ':' in value or value in {'.', '..'}:
            raise ValueError('请提供普通文件名，不能包含路径。')
        if Path(value).suffix.lower() not in {'.docx', '.pdf'}:
            raise ValueError('新论文支持 DOCX 和 PDF；旧 DOC 请另存为 DOCX。')
    return value


def validate_key(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('API Key 格式不合法；请重新填写。')
    return value


def validate_docx(data):
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError('论文不能为空，且大小不能超过 20 MB。')
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            rows = package.infolist()
            names = [row.filename for row in rows]
            if len(rows) > 10000 or len(names) != len(set(names)):
                raise ValueError('DOCX 容器文件数量异常或存在重复条目。')
            if sum(row.file_size for row in rows) > 100 * 1024 * 1024:
                raise ValueError('DOCX 解压内容超过安全限制。')
            for row in rows:
                if row.flag_bits & 1 or row.filename.startswith(('/', '\\')) or '..' in Path(row.filename.replace('\\', '/')).parts:
                    raise ValueError('DOCX 容器包含不支持的条目。')
                if row.file_size > 30 * 1024 * 1024:
                    raise ValueError('DOCX 内部单个文件过大。')
            if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
                raise ValueError('这不是支持的 Word DOCX 文件。')
            content = package.read('[Content_Types].xml')
            document = package.read('word/document.xml')
            if b'<!DOCTYPE' in content.upper() or b'<!DOCTYPE' in document.upper():
                raise ValueError('不支持带外部实体声明的文档。')
            types = ET.fromstring(content)
            if not any(item.get('PartName') == '/word/document.xml' and item.get('ContentType') == WORD_TYPE for item in types):
                raise ValueError('请上传普通 DOCX 文档，暂不支持宏文档或其他 ZIP 文件。')
            styles = _xml(package.read('word/styles.xml')) if 'word/styles.xml' in names else None
            validate_document_body(_xml(document), styles)
            if any(name.lower().endswith('vbaproject.bin') for name in names):
                raise ValueError('暂不支持包含宏的文档。')
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError, KeyError) as error:
        raise ValueError('DOCX 文件损坏或并非有效 Word 文档。') from error


def default_runner(*args, **kwargs):
    if Path(args[0]).suffix.lower() == '.pdf':
        from pdf_paper_review import run_review
        return run_review(*args, **kwargs)
    from new_paper_agent import run_review
    return run_review(*args, **kwargs)


class NewPapers:
    def __init__(self, root, runner=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self.papers = {}
        self.cancel_events = {}
        self.runner = runner or default_runner
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='new-paper-review')
        for folder in sorted(self.root.glob('paper_*')):
            try:
                saved = read_json(folder/'paper.json')
                if not self.valid_id(saved.get('id')) or saved['id'] != folder.name:
                    continue
                source = folder / ('source.pdf' if saved.get('source_format') == 'pdf' else 'source.docx')
                if not source.is_file() or sha256(source.read_bytes()).hexdigest() != saved.get('sha256'):
                    continue
                if saved.get('status') in {'queued', 'running', 'cancelling'}:
                    saved.update(status='interrupted', error='上次服务中断，请明确点击重新检查；不会自动重试。')
                    self.save(saved)
                self.papers[saved['id']] = saved
            except (OSError, ValueError, KeyError):
                continue

    @staticmethod
    def valid_id(value):
        return isinstance(value, str) and re.fullmatch(r'paper_[A-Za-z0-9_]+', value) is not None

    def folder(self, paper_id):
        if not self.valid_id(paper_id):
            raise KeyError('Unknown paper')
        folder = (self.root/paper_id).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError('论文路径越界。')
        return folder

    def save(self, paper):
        path = self.folder(paper['id'])/'paper.json'
        write_json(path, paper)

    def active(self):
        with self.lock:
            return any(p['status'] in {'queued', 'running', 'cancelling'} for p in self.papers.values())

    def capabilities(self):
        from new_paper_agent import DEFAULT_REQUESTS, DEFAULT_TOTAL_BOUND, MAX_REQUESTS, MAX_TOTAL_BOUND
        return {'model': 'deepseek-flash', 'api_key_available': bool(os.environ.get('DEEPSEEK_API_KEY')), 'max_file_mb': 20,
                'supported_formats': ['docx','pdf'], 'pdf_ocr_available': False,
                'automatic_completion': True, 'default_max_requests': DEFAULT_REQUESTS,
                'default_max_reserved_tokens': DEFAULT_TOTAL_BOUND, 'max_requests': MAX_REQUESTS,
                'max_reserved_tokens': MAX_TOTAL_BOUND, 'output_repairs_per_item': 1}

    def recent(self):
        with self.lock:
            ids = [pid for pid in reversed(self.papers) if not self.papers[pid].get('archived_at')]
        return [self.get(paper_id) for paper_id in ids]

    def require_budget(self, max_requests=None, max_total_bound=None):
        from new_paper_agent import DEFAULT_REQUESTS, DEFAULT_TOTAL_BOUND
        requests = DEFAULT_REQUESTS if max_requests is None else max_requests
        tokens = DEFAULT_TOTAL_BOUND if max_total_bound is None else max_total_bound
        if type(requests) is not int or not 1 <= requests <= DEFAULT_REQUESTS:
            raise ValueError(f'网页请求预算必须是 1 至 {DEFAULT_REQUESTS} 的整数。')
        if type(tokens) is not int or not 1000 <= tokens <= DEFAULT_TOTAL_BOUND:
            raise ValueError(f'网页 token 预留预算必须是 1000 至 {DEFAULT_TOTAL_BOUND} 的整数。')
        return requests, tokens

    def review_folder(self, paper_id, review_id):
        if not isinstance(review_id, str) or not re.fullmatch(r'review_[A-Za-z0-9_]+', review_id):
            raise ValueError('审阅轮次格式不正确。')
        folder = self.folder(paper_id)
        result = (folder / review_id).resolve()
        if not result.is_relative_to(folder) or not result.is_dir():
            raise KeyError('Unknown review')
        return result

    def history(self, paper_id):
        with self.lock:
            if paper_id not in self.papers:
                raise KeyError('Unknown paper')
            paper = dict(self.papers[paper_id])
        rows = []
        for result in sorted(self.folder(paper_id).glob('review_*'), reverse=True):
            try:
                result = self.review_folder(paper_id, result.name)
                ledger = read_json(result/'run.json') if (result/'run.json').is_file() else {}
                report = read_json(result/'report.json') if (result/'report.json').is_file() else {}
                source = report.get('source_sha256', ledger.get('source_sha256'))
                if source not in (None, paper['sha256']):
                    continue
                rows.append({'review_id':result.name, 'current':result.name==paper.get('review_id'),
                    'status':ledger.get('status',report.get('status','preparing')),
                    'review_scope':ledger.get('review_scope','full'),
                    'known_total_tokens':ledger.get('known_total_tokens',0),
                    'requests':ledger.get('dispatched_requests',ledger.get('request_attempts',0)),
                    'created_at':ledger.get('started_at') or ledger.get('created_at') or result.name[7:22]})
            except (OSError, ValueError, KeyError):
                continue
        return rows

    def historical(self, paper_id, review_id):
        """Read a saved round without mutating its files or the current pointer."""
        with self.lock:
            if paper_id not in self.papers:
                raise KeyError('Unknown paper')
            paper = dict(self.papers[paper_id])
        result = self.review_folder(paper_id, review_id)
        current_id = paper.get('review_id')
        for field, filename in [('report','report.json'), ('run','run.json')]:
            if (result/filename).is_file():
                value = read_json(result/filename)
                if value.get('source_sha256') not in (None, paper['sha256']):
                    raise ValueError('此审阅轮与上传原件不一致。')
                paper[field] = value
        paper.update(review_id=review_id, current_review_id=current_id, historical=review_id!=current_id,
                     status='historical', error=None)
        return paper

    def cancel(self, paper_id):
        with self.lock:
            if paper_id not in self.papers:
                raise KeyError('Unknown paper')
            paper = self.papers[paper_id]
            if paper.get('status') not in {'queued','running','cancelling'}:
                raise RuntimeError('此论文当前没有正在执行的审阅。')
            event = self.cancel_events.get((paper_id,paper.get('review_id')))
            if event is None:
                raise RuntimeError('此任务不能取消，请等待现有步骤结束。')
            event.set()
            paper.update(status='cancelling', cancel_requested_at=datetime.now().isoformat())
            self.save(paper)
        return self.get(paper_id)

    def archive(self, paper_id):
        """Hide only an idle record; retain original and all receipts."""
        with self.lock:
            if paper_id not in self.papers:
                raise KeyError('Unknown paper')
            paper = self.papers[paper_id]
            if paper.get('status') in {'queued','running','cancelling'}:
                raise RuntimeError('请先安全取消任务，结束后才能归档。')
            self.folder(paper_id)  # Verify the resolved target before saving.
            paper['archived_at'] = datetime.now().isoformat()
            self.save(paper)
        return {'id':paper_id,'archived':True,'original_retained':True}

    def _ensure_excluded_clues_report(self, report_path, report):
        """Backfill evidence text in reports created before the UI exposed it."""
        clues = report.get('excluded_clues') or []
        if not clues:
            return
        changed = False
        if report.get('raw_first_person_count') is None:
            report['raw_first_person_count'] = len(clues) + sum(item.get('rule_id') == 'NP001' for item in report.get('findings', []))
            changed = True
        markdown_path = report_path.with_name('report.md')
        if markdown_path.is_file():
            try:
                markdown = markdown_path.read_text(encoding='utf-8')
                marker = '## “我”字原文线索（当前自动范围排除或待确认）'
                if marker not in markdown and '## 正文规则未判错的“我”字记录' not in markdown:
                    lines = ['', marker, '',
                             f"共记录 {len(clues)} 条；原始可见“我”字线索 {report.get('raw_first_person_count', len(clues))} 条。这里列出原文与处理原因，不代表这些位置已经确认合格。", '']
                    for clue in clues:
                        paragraph = clue.get('paragraph_index')
                        location = f"第 {paragraph + 1} 段" if isinstance(paragraph, int) else '段落位置未知'
                        lines += [f"### {location} · 字符位置 {clue.get('start', 0) + 1}", '', clue.get('text', ''), '', f"当前处理：{clue.get('reason', '')}", '']
                    markdown_path.write_text(markdown.rstrip() + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')
            except OSError:
                pass
        if changed:
            try:
                write_json(report_path, report)
            except OSError:
                pass

    def get(self, paper_id):
        with self.lock:
            if paper_id not in self.papers:
                raise KeyError('Unknown paper')
            paper = dict(self.papers[paper_id])
        review_id = paper.get('review_id')
        if review_id and re.fullmatch(r'review_[A-Za-z0-9_]+', review_id):
            result = self.folder(paper_id)/review_id
            ledger_path = result/'run.json'
            if ledger_path.is_file():
                ledger = read_json(ledger_path)
                if ledger.get('source_sha256') not in (None, paper['sha256']):
                    raise ValueError('运行进度与本次上传原件不一致。')
                paper['run'] = ledger
            report_path = result/'report.json'
            if report_path.is_file():
                paper['report'] = read_json(report_path)
                if paper.get('status') not in {'queued', 'running', 'cancelling'}:
                    self._ensure_excluded_clues_report(report_path, paper['report'])
                from paper_locations import enrich_report_locations
                if paper.get('source_format') != 'pdf':
                    enrich_report_locations(paper['report'])
                if paper.get('status') not in {'queued', 'running', 'cancelling'} and all(key in paper['report'] for key in ('paragraphs', 'rules', 'file_name', 'source_sha256')):
                    from paper_report import render_report
                    markdown_path = report_path.with_name('report.md')
                    rendered = render_report(paper['report'])
                    if not markdown_path.is_file() or markdown_path.read_text(encoding='utf-8') != rendered:
                        markdown_path.write_text(rendered, encoding='utf-8')
        return paper

    def require_options(self, use_agent, chapter_policy, api_key):
        if not isinstance(use_agent, bool) or not isinstance(chapter_policy, bool):
            raise ValueError('检查选项必须为布尔值。')
        key = validate_key(api_key) or validate_key(os.environ.get('DEEPSEEK_API_KEY'))
        if use_agent and not key:
            raise ValueError('启用模型检查需要 DeepSeek API Key；可填写密钥或选择本地检查。')
        return key

    def upload(self, data, file_name, student_name='', use_agent=False, chapter_policy=False, api_key=None, three_line_tables=False, max_requests=None, max_total_bound=None, paper_type=''):
        self.require_paper_type(paper_type)
        self.require_budget(max_requests, max_total_bound)
        if type(three_line_tables) is not bool:
            raise ValueError('三线表要求必须明确为布尔值。')
        file_name = validate_name(file_name, filename=True)
        student_name = validate_name(student_name) or '姓名未填写'
        key = self.require_options(use_agent, chapter_policy, api_key)
        source_format = 'pdf' if Path(file_name).suffix.lower() == '.pdf' else 'docx'
        if source_format == 'pdf':
            from pdf_paper_review import extract_pdf
            extract_pdf(data)
        else:
            validate_docx(data)
        with self.lock:
            if any(p['status'] in {'queued', 'running', 'cancelling'} for p in self.papers.values()):
                raise RuntimeError('已有论文正在检查，请等待完成。')
            paper_id = 'paper_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(4)
            folder = self.folder(paper_id)
            folder.mkdir()
            (folder / ('source.pdf' if source_format == 'pdf' else 'source.docx')).write_bytes(data)
            paper = {'id': paper_id, 'student_name': student_name, 'file_name': file_name,
                     'sha256': sha256(data).hexdigest(), 'created_at': datetime.now().isoformat(),
                     'status': 'uploaded', 'error': None, 'use_agent': use_agent, 'chapter_policy': chapter_policy,
                     'three_line_tables': three_line_tables, 'paper_type': paper_type, 'source_format': source_format}
            self.papers[paper_id] = paper
            self.save(paper)
        return self.review(paper_id, use_agent, chapter_policy, key, three_line_tables=three_line_tables, max_requests=max_requests, max_total_bound=max_total_bound, paper_type=paper_type)

    @staticmethod
    def require_paper_type(paper_type):
        if not isinstance(paper_type, str) or paper_type not in {'', 'engineering_design', 'experimental_research', 'theoretical_research', 'software', 'humanities', 'civil_engineering'}:
            raise ValueError('请选择支持的论文类型，未确认时请保留“未确认”。')

    def review(self, paper_id, use_agent=False, chapter_policy=False, api_key=None, review_scope='full', three_line_tables=False, max_requests=None, max_total_bound=None, paper_type=''):
        self.require_paper_type(paper_type)
        max_requests, max_total_bound = self.require_budget(max_requests, max_total_bound)
        if type(three_line_tables) is not bool:
            raise ValueError('三线表要求必须明确为布尔值。')
        if review_scope not in {'full','figures','content'}:raise ValueError('不支持该审阅范围。')
        if review_scope in {'figures','content'} and not use_agent:raise ValueError('实际图片或章节内容审阅需要启用模型。')
        key = self.require_options(use_agent, chapter_policy, api_key)
        with self.lock:
            if paper_id not in self.papers:
                raise KeyError('Unknown paper')
            if any(p['status'] in {'queued', 'running', 'cancelling'} for p in self.papers.values()):
                raise RuntimeError('已有论文正在检查，请等待完成。')
            paper = self.papers[paper_id]
            if review_scope in {'figures','content'}:
                chapter_policy=paper.get('chapter_policy',False)
                three_line_tables=paper.get('three_line_tables',False)
                paper_type=paper.get('paper_type','')
            previous_review_id = paper.get('review_id')
            review_id = 'review_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(3)
            paper.update(status='queued', review_id=review_id, previous_review_id=previous_review_id, error=None, use_agent=use_agent, chapter_policy=chapter_policy,review_scope=review_scope,three_line_tables=three_line_tables, max_requests=max_requests, max_total_bound=max_total_bound, paper_type=paper_type)
            self.cancel_events[(paper_id,review_id)] = Event()
            self.save(paper)
        self.executor.submit(self.run, paper_id, review_id, key)
        return self.get(paper_id)

    def update_identity(self, paper_id, student_name):
        """Correct the display identity without changing the uploaded bytes."""
        student_name = validate_name(student_name) or '姓名未填写'
        with self.lock:
            if paper_id not in self.papers:
                raise KeyError('Unknown paper')
            paper = self.papers[paper_id]
            if paper.get('status') in {'queued', 'running', 'cancelling'}:
                raise RuntimeError('论文正在检查，完成后再修改显示姓名。')
            previous = paper.get('student_name', '')
            paper['student_name'] = student_name
            paper['identity_updated_at'] = datetime.now().isoformat()
            paper['identity_previous_name'] = previous
            self.save(paper)
        review_id = paper.get('review_id')
        if review_id:
            report_path = self.folder(paper_id) / review_id / 'report.json'
            if report_path.is_file():
                try:
                    report = read_json(report_path)
                    report['student_name'] = student_name
                    write_json(report_path, report)
                except (OSError, ValueError):
                    pass
            markdown = self.folder(paper_id) / review_id / 'report.md'
            if markdown.is_file():
                try:
                    text = markdown.read_text(encoding='utf-8')
                    text = re.sub(r'(?m)^学生：[^；]+；', f'学生：{student_name}；', text, count=1)
                    markdown.write_text(text, encoding='utf-8')
                except OSError:
                    pass
        return self.get(paper_id)

    def verified_matching_reports(self,snapshot):
        """Only offer identical uploaded bytes; each checker validates scope."""
        with self.lock:
            candidates=[dict(p) for p in reversed(list(self.papers.values()))
                        if p['id']!=snapshot['id'] and p.get('sha256')==snapshot['sha256']
                        and p.get('source_format','docx') == snapshot.get('source_format','docx')
                        and p.get('status')=='completed'][:5]
        results=[]
        for saved in candidates:
            rid=saved.get('review_id','')
            if not re.fullmatch(r'review_[A-Za-z0-9_]+',rid):continue
            folder=self.folder(saved['id']);review=folder/rid
            try:
                source = folder / ('source.pdf' if saved.get('source_format') == 'pdf' else 'source.docx')
                if sha256(source.read_bytes()).hexdigest()!=snapshot['sha256']:continue
                report=read_json(review/'report.json')
                if report.get('source_sha256')!=snapshot['sha256']:continue
                layout=read_json(review/'layout_result.json') if (review/'layout_result.json').is_file() else None
                if layout:
                    pdf=Path(layout.get('pdf',{}).get('path','')).resolve()
                    if (layout.get('source',{}).get('sha256')!=snapshot['sha256'] or layout.get('source_unchanged') is not True
                        or not pdf.is_relative_to(self.root) or not pdf.is_file()
                        or sha256(pdf.read_bytes()).hexdigest()!=layout.get('pdf',{}).get('sha256')):layout=None
                results.append({'paper_id':saved['id'],'review_id':rid,'report':report,'layout':layout})
            except (OSError,ValueError,KeyError):continue
        return results

    def run(self, paper_id, review_id, key):
        paper, output, snapshot = None, None, {}
        try:
            with self.lock:
                paper = self.papers[paper_id]
                paper.pop('persistence_error', None)
                snapshot = dict(paper)
                cancel_event = self.cancel_events[(paper_id,review_id)]
                cancelled_before_start = cancel_event.is_set()
                if not cancelled_before_start:
                    paper.update(status='running', started_at=datetime.now().isoformat())
                    self.save(paper)
            folder = self.folder(paper_id)
            source = folder / ('source.pdf' if snapshot.get('source_format') == 'pdf' else 'source.docx')
            output = folder/review_id
            output.mkdir(exist_ok=True)
            if cancelled_before_start:
                write_json(output/'run.json', {'status':'cancelled', 'source_sha256':snapshot['sha256'],
                    'known_total_tokens':0,'request_attempts':0,'cancelled_before_start':True})
                with self.lock:
                    paper.update(status='cancelled', finished_at=datetime.now().isoformat(),result_status='cancelled')
                    self.save(paper)
                return
            if sha256(source.read_bytes()).hexdigest() != snapshot['sha256']:
                raise ValueError('上传原件哈希变化，已停止检查。')
            prior_options = {}
            previous_id = snapshot.get('previous_review_id')
            if previous_id and re.fullmatch(r'review_[A-Za-z0-9_]+', previous_id):
                previous = folder/previous_id
                if (previous/'report.json').is_file():
                    prior_options['prior_report'] = read_json(previous/'report.json')
                if (previous/'layout_result.json').is_file():
                    prior_options['layout_result'] = read_json(previous/'layout_result.json')
            matching=self.verified_matching_reports(snapshot)
            if matching:
                prior_options['additional_prior_reports']=[p['report'] for p in matching]
                if not prior_options.get('layout_result'):
                    found=next((p['layout'] for p in matching if p['layout']),None)
                    if found:prior_options['layout_result']=found
                if not prior_options.get('prior_report'):prior_options['prior_report']=matching[0]['report']
            if matching:
                with self.lock:
                    paper['same_source_evidence_candidates']=[{'paper_id':p['paper_id'],'review_id':p['review_id']} for p in matching]
                    self.save(paper)
            report = self.runner(source, output, student_name=snapshot['student_name'],
                                 chapter_policy=snapshot['chapter_policy'], api_key=key, use_agent=snapshot['use_agent'],
                                 file_name=snapshot['file_name'], review_scope=snapshot.get('review_scope','full'),
                                 three_line_tables=snapshot.get('three_line_tables',False),
                                 paper_type=snapshot.get('paper_type',''),
                                 max_requests=snapshot['max_requests'], max_total_bound=snapshot['max_total_bound'],
                                 cancel_check=cancel_event.is_set, **prior_options)
            if not isinstance(report, dict):
                raise ValueError('检查程序没有返回有效报告。')
            if sha256(source.read_bytes()).hexdigest() != snapshot['sha256']:
                raise ValueError('检查期间原件发生变化，结果不能使用。')
            if not (output/'report.json').is_file():
                write_json(output/'report.json', report)
            with self.lock:
                paper.update(status='cancelled' if cancel_event.is_set() or report.get('run',{}).get('status')=='cancelled' else 'completed', finished_at=datetime.now().isoformat(),
                             result_status=report.get('run', {}).get('status', report.get('status')))
                self.save(paper)
        except Exception as error:
            import traceback
            message = str(error)
            if key:
                message = message.replace(key, '[密钥已隐藏]')
            failure = {'status':'failed','exception_type':type(error).__name__, 'error':message[:1000],
                       'source_sha256':snapshot.get('sha256'), 'automatic_retries':0,
                       'frames':[{'file':frame.filename,'line':frame.lineno,'function':frame.name}
                                 for frame in traceback.extract_tb(error.__traceback__)]}
            with self.lock:
                if paper is not None:
                    # Release the queue in memory even if the storage failure also
                    # prevents writing the terminal ledger.
                    paper.update(status='failed', result_status='failed',
                                 finished_at=datetime.now().isoformat(), error=message[:1000])
                    try:
                        self.save(paper)
                    except Exception as save_error:
                        detail = str(save_error)
                        if key:
                            detail = detail.replace(key, '[密钥已隐藏]')
                        paper['persistence_error'] = detail[:1000]
            if output is not None:
                try:
                    write_json(output/'failure.json', failure)
                except OSError:
                    pass
        finally:
            key = None
            with self.lock:
                self.cancel_events.pop((paper_id,review_id),None)

    def close(self):
        self.executor.shutdown(wait=True)
