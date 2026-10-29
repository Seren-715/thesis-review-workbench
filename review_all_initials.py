"""Resume fair, bounded full-scope initial-paper reviews through standard jobs.

No paid operation occurs for ``plan``. ``run`` explicitly dispatches ordinary
ThesisWorkflow jobs, visible in the desktop UI. Each round handles one student,
reserves at most 24,000 tokens and reuses the workflow's verified source/receipts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time

from atomic_json import read_json, write_json

ROOT = Path(__file__).resolve().parent
TERMINAL = {'completed', 'partial', 'failed'}
STOP_SCHEMA = 'initial_review_stop_after_current_job_v1'


def now():
    return datetime.now().astimezone().isoformat()


def scheduler_state_path(root=ROOT, state_path=None):
    root = Path(root).resolve()
    path = Path(state_path or root/'data/thesis_workflow/all_initials/state.json').resolve()
    if not path.is_relative_to(root):
        raise ValueError('调度记录必须保存在项目目录内。')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def request_stop(root=ROOT, state_path=None):
    """Atomic independent marker; never wait on the scheduler's active lock."""
    path = scheduler_state_path(root, state_path)
    marker = path.with_suffix('.stop')
    write_json(marker, dict(schema=STOP_SCHEMA, state_path=str(path), requested_at=now(),
                            action='stop_after_current_job'))
    return dict(status='stop_requested', stop_marker_path=str(marker), state_path=str(path),
                detail='当前已派发任务完成并保存用量后停止；未取消在途请求。')


def clear_stop_marker(root=ROOT, state_path=None):
    """Remove only our known marker. No scheduler halt or job is modified."""
    path = scheduler_state_path(root, state_path)
    marker = path.with_suffix('.stop')
    if marker.exists():
        value = read_json(marker)
        if (not isinstance(value, dict) or value.get('schema') != STOP_SCHEMA or
            value.get('state_path') != str(path) or value.get('action') != 'stop_after_current_job'):
            raise ValueError('停止标记不是本调度器的已知标记，未解除。')
        marker.unlink()
    return dict(status='stop_marker_cleared', state_path=str(path),
                detail='仅解除人工请求的停止标记；费用未知、提交未知或其他异常停止仍须核对，不自动派发。')


def contract(rules):
    """Full checker definitions, not just rule counts, invalidate completion."""
    enabled = [r for r in rules if r.get('enabled', True)]
    encoded = json.dumps(enabled, ensure_ascii=False, sort_keys=True, default=str).encode()
    return {'sha256': hashlib.sha256(encoded).hexdigest(),
            'rule_ids': sorted(r['id'] for r in enabled)}


def report_progress(report, rule_ids):
    run = report.get('run', {})
    progress = run.get('progress', {})
    completion = run.get('completion', {})
    recorded = {r.get('rule_id') for r in report.get('coverage', [])}
    missing = sorted(set(rule_ids) - recorded)
    findings = report.get('findings', [])
    chunks = sum(len(set(f.get('content_read_progress', {}).get('read_chunk_ids', [])))
                 for f in findings)
    reviewed = progress.get('reviewed', 0)
    remaining = progress.get('remaining')
    from teacher_task_coverage import result_is_complete
    strict = result_is_complete(report)
    automatic = (run.get('mode') == 'agent' and not missing and not progress.get('blocked', 0)
                 and remaining == 0 and completion.get('status') in
                 {'completed', 'completed_with_manual'} and not run.get('error'))
    groups = report.get('summary', {}).get('result_groups', {})
    return {'automated_finished': automatic, 'strict_fully_completed': strict,
            'remaining': remaining, 'reviewed': reviewed, 'read_chunks': chunks,
            'blocked_objects': progress.get('blocked', 0), 'missing_rule_ids': missing,
            'manual_review_items': groups.get('pending', 0),
            'completion_status': completion.get('status'),
            'signature': [reviewed, chunks, progress.get('blocked', 0), remaining, missing]}


class InitialReviewScheduler:
    def __init__(self, workflow, *, root=ROOT, state_path=None, rules=None,
                 sleep=time.sleep, poll_seconds=1):
        self.workflow = workflow
        self.root = Path(root).resolve()
        self.path = scheduler_state_path(self.root, state_path)
        self.sleep, self.poll_seconds = sleep, poll_seconds
        if rules is None:
            from new_paper_checks import load_teacher_rules
            from teacher_rule_library import get_library
            rules = load_teacher_rules(library=get_library(self.root))
        self.contract = contract(rules)
        self.state = read_json(self.path) if self.path.exists() else {
            'schema_version': 1, 'created_at': now(), 'students': {},
            'rounds': [], 'inflight_job_id': None, 'status': 'planned'}

    def save(self):
        self.state['updated_at'] = now()
        students = list(self.state['students'].values())
        self.state['summary'] = {
            'students': len(students),
            'automated_finished': sum(s.get('automated_finished', False) for s in students),
            'strict_fully_completed': sum(s.get('strict_fully_completed', False) for s in students),
            'blocked_students': sum(s.get('blocked', False) for s in students),
            'rounds': len(self.state['rounds']),
            'known_total_tokens': sum(r.get('known_total_tokens', 0) for r in self.state['rounds'])}
        write_json(self.path, self.state)

    @contextmanager
    def exclusive(self):
        """A crashed scheduler requires explicit lock removal, never paid retry."""
        path = self.path.with_suffix('.lock')
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError('全量初稿调度锁存在；请核实原调度进程和正在执行的任务后处理。')
        try:
            os.write(fd, str(os.getpid()).encode())
            yield
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)

    def plan(self):
        discovery = self.workflow.snapshot()['discovery']
        students = discovery.get('students', [])
        valid_ids = {s['id'] for s in students}
        if not students:
            raise ValueError('尚无已核验的学生发现记录。')
        for previous_id in self.state['students']:
            if previous_id not in valid_ids:
                raise ValueError('已有调度学生不在当前发现记录中，须核对来源变化。')
        for student in students:
            sid = student['id']
            item = self.state['students'].setdefault(sid, {
                'student_id': sid, 'student_name': student.get('name', sid), 'round_count': 0})
            initial = student.get('initial')
            if not initial:
                item.update(blocked=True, block_reason='缺少教师反馈前的学生初稿候选。')
                continue
            source_identity = {'revision': initial.get('revision'),
                               'repository_path': initial.get('repository_path')}
            if item.get('source_identity') and item['source_identity'] != source_identity:
                raise ValueError(f'{sid} 初稿来源变化，停止自动续审。')
            item['source_identity'] = source_identity
            reused = self.workflow._reusable_initial(student)
            if reused is None:
                # This dispatcher does not quietly re-export a corrupt source.
                item.update(blocked=True, source_verification_failed=True,
                            block_reason='缺少哈希与版本核验通过的既有原件；先完成来源核对。')
                continue
            analysis, source, parsed, snapshot, report, layout = reused
            sha = report.get('source_sha256')
            if item.get('source_sha256') and item['source_sha256'] != sha:
                raise ValueError(f'{sid} 原件哈希变化，停止自动续审。')
            item['source_sha256'] = sha
            item['source_verification_failed'] = False
            old_contract = item.get('rule_contract')
            current = report_progress(report, self.contract['rule_ids'])
            # Full contract changes require one current check before adopting
            # old conclusions; existing workflow handles per-object reuse.
            if old_contract != self.contract:
                current['automated_finished'] = False
                item.update(no_progress_rounds=0, blocked=False, block_reason=None)
            item.update(current, rule_contract=deepcopy(self.contract), source_path=str(analysis))
        self.state['rule_contract'] = deepcopy(self.contract)
        self.save()
        return deepcopy(self.state)

    def _job(self, jid):
        self.workflow.snapshot()  # Refresh CLI/UI shared durable job state.
        return deepcopy(self.workflow.jobs[jid])

    def _wait(self, jid):
        while True:
            job = self._job(jid)
            if job.get('status') in TERMINAL:
                return job
            self.sleep(self.poll_seconds)

    def _consume(self, job):
        jid = job['id']
        if any(r['job_id'] == jid for r in self.state['rounds']):
            self.state['inflight_job_id'] = None
            self.save()
            return
        sid = self.state.get('inflight_student_id')
        item = self.state['students'][sid]
        entry = {k: job.get(k, 0) for k in (
            'known_total_tokens', 'unknown_cost_attempts', 'reserved_token_bound',
            'budget_charge_token_bound', 'llm_requests', 'svn_requests', 'word_launches')}
        entry.update(job_id=jid, student_id=sid, status=job.get('status'),
                     created_at=job.get('created_at'), finished_at=job.get('finished_at'),
                     max_reserved_token_bound=job.get('max_reserved_token_bound'),
                     result_dir=job.get('result_dir'))
        self.state['rounds'].append(entry)
        self.state['inflight_job_id'] = None
        item['round_count'] += 1
        # Persist charges first, including failure, before any next dispatch.
        self.save()

        for key in ('known_total_tokens', 'unknown_cost_attempts', 'reserved_token_bound',
                    'budget_charge_token_bound', 'llm_requests'):
            if type(entry[key]) is not int or entry[key] < 0:
                raise ValueError('用量记录无效，停止全部调度：'+key)
        if entry['unknown_cost_attempts']:
            raise ValueError('请求用量未确认，停止全部调度；未自动重试。')
        if max(entry['reserved_token_bound'], entry['budget_charge_token_bound'],
               entry['known_total_tokens']) > 24000:
            raise ValueError('本轮预算记录超过 24,000 token，停止全部调度。')
        rows = job.get('result_summary', {}).get('students', [])
        if job.get('status') == 'failed' or len(rows) != 1 or rows[0].get('error'):
            entry['error'] = job.get('error') or (rows[0].get('error') if rows else '缺少学生结果')
            raise ValueError('初稿任务失败，停止全部调度，不自动重试：'+str(entry['error']))
        path = Path(rows[0]['report_json_path']).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('报告路径越出项目目录。')
        report = read_json(path)
        if report.get('source_sha256') != item['source_sha256']:
            raise ValueError('报告来源哈希不一致，停止全部调度。')
        if report.get('run', {}).get('unknown_cost_attempts') or report.get('run', {}).get('error'):
            raise ValueError('审阅异常或费用未知，停止全部调度：'+str(report.get('run', {}).get('error', '用量未知')))
        current = report_progress(report, self.contract['rule_ids'])
        item['no_progress_rounds'] = (item.get('no_progress_rounds', 0) + 1
                                     if current['signature'] == item.get('signature') else 0)
        item.update(current, report_json_path=str(path), report_path=rows[0].get('report_path'),
                    last_job_id=jid)
        entry.update(report_json_path=str(path), report_path=rows[0].get('report_path'), progress=current)
        if item['no_progress_rounds'] >= 2 and not item['automated_finished']:
            item.update(blocked=True, block_reason='连续两轮没有新增可靠审阅证据；保留未读对象和人工核验事项，停止该学生付费续审。')
        if current['blocked_objects'] and current['remaining'] == 0:
            item.update(blocked=True, block_reason='审阅对象存在终止性证据或输出校验缺口；自动处理队列已停止，不认定整份自动审阅完成。')
        self.save()

    def _honor_stop(self):
        if not self.path.with_suffix('.stop').exists():
            return False
        self.state.update(status='stopped_for_review',
            stop_reason='已收到停止派发请求；当前已派发任务的结果及用量已保存，停止后续任务。')
        self.save()
        return True

    def run(self, *, max_rounds=17):
        if type(max_rounds) is not int or max_rounds < 1:
            raise ValueError('轮次上限必须为正整数。')
        with self.exclusive():
            try:
                if self.state.get('halt_requires_operator_review'):
                    raise RuntimeError('前次调度因费用、来源或任务异常停止；核对保存记录后再解除停止标记，未自动再次付费。')
                if self.state.get('dispatch_intent') and not self.state.get('inflight_job_id'):
                    raise RuntimeError('存在未确认的任务提交记录；先核对工作流任务清单，不自动再次提交。')
                # Resume dispatched operation before planning another paid one.
                if self.state.get('inflight_job_id'):
                    self._consume(self._wait(self.state['inflight_job_id']))
                if self._honor_stop():
                    return deepcopy(self.state)
                self.plan()
                if any(s.get('source_verification_failed') for s in self.state['students'].values()):
                    raise ValueError('存在未核验通过的初稿来源，停止全部付费调度。')
                self.state.update(status='running', stop_reason=None)
                self.save()
                for _ in range(max_rounds):
                    if self._honor_stop():
                        return deepcopy(self.state)
                    if self.workflow.active():
                        raise RuntimeError('已有工作流任务执行中，停止调度以避免冲突。')
                    waiting = [s for s in self.state['students'].values()
                               if not s.get('automated_finished') and not s.get('blocked')]
                    if not waiting:
                        break
                    item = min(waiting, key=lambda s: (s['round_count'], s['student_id']))
                    # Persist intent before starting; a failure after dispatch
                    # cannot silently start another paid job on next launch.
                    self.state['inflight_student_id'] = item['student_id']
                    self.state['dispatch_intent'] = now()
                    self.save()
                    if self.path.with_suffix('.stop').exists():
                        # This intent has not reached workflow.start; it is
                        # known to be undispatched, unlike a crashed submission.
                        self.state['dispatch_intent'] = None
                        self._honor_stop()
                        return deepcopy(self.state)
                    job = self.workflow.start('check-initials', {
                        'student_ids': [item['student_id']], 'use_agent': True,
                        'chapter_policy': False, 'max_reserved_tokens': 24000,
                        'review_scope': 'full'})
                    self.state['inflight_job_id'] = job['id']
                    self.state['dispatch_intent'] = None
                    self.save()
                    self._consume(self._wait(job['id']))
                    if self._honor_stop():
                        return deepcopy(self.state)
                students = list(self.state['students'].values())
                self.state['status'] = ('automatic_review_finished' if all(
                    s.get('automated_finished') for s in students) else 'saved_incomplete')
                self.save()
            except Exception as exc:
                self.state.update(status='stopped', stop_reason=str(exc), halt_requires_operator_review=True)
                self.save()
                raise
        return deepcopy(self.state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'run', 'stop', 'resume'])
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--max-rounds', type=int, default=17)
    args = parser.parse_args()
    # These two controls neither construct workflow/model clients nor acquire
    # the active scheduler lock. They modify only the separate stop marker.
    if args.action in {'stop', 'resume'}:
        action = request_stop if args.action == 'stop' else clear_stop_marker
        print(json.dumps(action(args.root, args.state), ensure_ascii=False, indent=2))
        return
    from thesis_workflow import ThesisWorkflow
    workflow = ThesisWorkflow(root=args.root)
    try:
        scheduler = InitialReviewScheduler(workflow, root=args.root, state_path=args.state)
        state = scheduler.plan() if args.action == 'plan' else scheduler.run(max_rounds=args.max_rounds)
        print(json.dumps({'status': state['status'], 'summary': state['summary'],
                          'state_path': str(scheduler.path)}, ensure_ascii=False, indent=2))
    finally:
        workflow.close()


if __name__ == '__main__':
    main()
