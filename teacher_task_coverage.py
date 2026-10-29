"""Read-only completion ledger for teacher requirements, not accuracy claims."""
from collections import Counter
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from atomic_json import read_json


def result_is_complete(row):
    if row.get('status') != 'completed':
        return False
    groups = row.get('summary', {}).get('result_groups', {})
    if groups.get('unreviewed', 0) or groups.get('pending', 0):
        return False
    return bool(row.get('coverage')) and all(c.get('status') in {'checked', 'not_applicable', 'not_applicable_policy'}
                                            for c in row.get('coverage', []))


def build_task_coverage(root, checker_rules=None):
    root = Path(root).resolve()
    issues = []
    hashes = {}

    def read(path, scope):
        try:
            value = read_json(path)
            if not isinstance(value, dict):
                raise ValueError('JSON 根节点必须为对象')
            return value
        except (OSError, ValueError, TypeError) as exc:
            issues.append({'scope': scope, 'path': str(path), 'error': str(exc)[:300]})
            return None

    def verify(path, expected, scope):
        try:
            file = Path(path).resolve()
            if not file.is_relative_to(root):
                raise ValueError('证据文件不在当前工作区内')
            if not isinstance(expected, str) or len(expected) != 64:
                raise ValueError('证据缺少有效 SHA256')
            if file not in hashes:
                hashes[file] = sha256(file.read_bytes()).hexdigest()
            if hashes[file] != expected:
                raise ValueError('证据文件 SHA256 不符')
            return True
        except (OSError, ValueError, TypeError) as exc:
            issues.append({'scope': scope, 'path': str(path), 'error': str(exc)[:300]})
            return False

    library = read(root/'data/teacher_rule_library/library.json', 'rules')
    discovery = read(root/'data/thesis_workflow/discovery.json', 'discovery')
    library_sources = [verify(d.get('source_path'), d.get('sha256'), 'library_source')
                       for d in (library or {}).get('documents', [])]
    loaded = checker_rules
    if loaded is None and library is not None:
        try:
            from new_paper_checks import load_teacher_rules
            # GET must never rebuild or publish the library. Verify/load actual
            # checker definitions against this exact saved catalog instead.
            loaded = load_teacher_rules(library=deepcopy(library))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({'scope': 'checker_definitions', 'path': '', 'error': str(exc)[:300]})
    loaded = loaded or []
    families, checker_items = [], []
    for rule in loaded:
        evidence = rule.get('teacher_evidence', [])
        evidence_validity = [verify(e.get('source_path'), e.get('source_sha256'), 'checker_evidence') for e in evidence]
        verified = bool(evidence) and all(evidence_validity)
        checker_items.append({'id': rule['id'], 'title': rule.get('title', ''),
                              'family': rule.get('family'), 'enabled': rule.get('enabled', True),
                              'evidence_count': len(evidence), 'evidence_verified': verified,
                              'reviewed': verified and rule.get('same_category_feedback_reviewed') is True})
    for family in (library or {}).get('rules', []):
        linked = [r for r in checker_items if r['family'] == family['id'] and r['enabled'] and r['evidence_verified']]
        families.append({'id': family['id'], 'title': family.get('title', ''),
                         'status': 'checker_linked' if linked else 'candidate_only',
                         'checker_ids': [r['id'] for r in linked],
                         'distinct_supporting_student_count': family.get('distinct_supporting_student_count', 0),
                         'reviewed': family.get('same_category_feedback_reviewed') is True})
    comments = (library or {}).get('comments', [])
    rules = {'status': 'needs_review' if library_sources and not all(library_sources) else 'available' if library is not None else 'not_available',
             'summary': {'candidate_families': len(families),
                         'linked_families': sum(f['status'] == 'checker_linked' for f in families),
                         'unlinked_families': sum(f['status'] == 'candidate_only' for f in families),
                         'checker_items': len(checker_items),
                         'reviewed_checker_items': sum(r['reviewed'] for r in checker_items),
                         'unreviewed_checker_items': sum(not r['reviewed'] for r in checker_items),
                         'text_comments': sum(bool(c.get('text', '').strip()) for c in comments),
                         'semantic_review_required_comments': sum(c.get('status') == 'semantic_review_required' and not c.get('external_review') for c in comments),
                         'external_information_review_comments': sum(bool(c.get('external_review')) for c in comments),
                         'empty_or_image_comments': sum(not c.get('text', '').strip() for c in comments)},
             'families': families, 'checker_items': checker_items}
    rules['summary'].update(total_comments=len(comments),
        linked_comments=sum(bool(c.get('rule_ids')) for c in comments),
        unlinked_comments=sum(not c.get('rule_ids') for c in comments),
        empty_comments=sum(not c.get('text', '').strip() for c in comments),
        image_comments=sum(bool(c.get('comment_media')) for c in comments),
        classification_pending_comments=sum(c.get('status') == 'pending_semantic_classification' and not c.get('external_review') for c in comments))
    linked_family_ids = {f['id'] for f in families if f['status'] == 'checker_linked'}
    checker_evidence_ids = {(e.get('source_sha256'), str(e.get('comment_id')))
                            for r in loaded if r.get('enabled', True)
                            for e in r.get('teacher_evidence', [])}
    rules['summary']['checker_linked_comments'] = sum((c.get('source_sha256'), str(c.get('comment_id'))) in checker_evidence_ids for c in comments)
    rules['summary']['supplemental_checker_items'] = sum(r['enabled'] and not r['evidence_count'] for r in checker_items)
    from teacher_requirement_proposals import build_proposals
    proposals = build_proposals(library or {})['proposals'] if all(library_sources) else []
    rules['new_requirement_proposals'] = [{k: row.get(k) for k in ('title', 'family', 'quote', 'student_name', 'revision', 'scope', 'criterion_draft', 'status')}
                                          for row in proposals]
    rules['summary']['new_requirement_proposals'] = len(proposals)
    rules['summary']['proposed_new_families'] = len({p['family'] for p in proposals})
    if library is None:
        rules['summary'] = {key: None for key in rules['summary']}

    candidates = {}
    # Read all persisted jobs, not the last 30 returned by snapshot(). Same job
    # in jobs/ and runs/ counts once; the durable jobs copy is authoritative.
    jobs = {}
    data = root/'data/thesis_workflow'
    for path in sorted((data/'runs').glob('*/run.json')) + sorted((data/'jobs').glob('*.json')):
        job = read(path, 'jobs')
        if job:
            jobs[job.get('id', str(path))] = job
    for job in jobs.values():
        rows = job.get('result_summary', {}).get('students', [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get('student_id'):
                continue
            sid = row['student_id']
            candidates.setdefault(sid, []).append((job.get('created_at', ''), job, row))
    # Retain orphan reports too, with explicit identity/source validation below.
    for path in sorted((data/'initial_checks').glob('*/*/review/report.json')):
        sid = path.parents[2].name
        if any(r.get('report_json_path') == str(path) for _, _, r in candidates.get(sid, [])):
            continue
        report = read(path, 'orphan_report')
        if report is None:
            continue
        source = report.get('svn_source', {}) or {}
        revision = source.get('revision')
        candidates.setdefault(sid, []).append((report.get('run', {}).get('finished_at', ''),
            {'id': path.parents[1].name, 'options': {}},
            {'student_id': sid, 'revision': revision, 'report_json_path': str(path),
             'status': report.get('status'), 'source_path': source.get('document_path'),
             'source_sha256': report.get('source_sha256')}))
    students = []
    for student in (discovery or {}).get('students', []):
        sid, revision = student['id'], student.get('initial_revision')
        item = {'student_id': sid, 'student_name': student.get('name', sid), 'revision': revision,
                'initial_selection': student.get('initial_selection'), 'status': 'not_checked',
                'report_exists': False, 'source_verified': False, 'agent_enabled': False,
                'agent_completed': False, 'automated_completed': False, 'text_snapshot': False, 'blockers': []}
        relevant = [entry for entry in candidates.get(sid, []) if entry[2].get('revision') == revision
                    or (entry[2].get('revision') is None and entry[2].get('status') in {'failed', 'failed_no_retry'})]
        if revision is None:
            item.update(status='no_initial', blockers=['尚未发现历史初稿候选'])
        elif not relevant:
            item['blockers'].append('当前历史初稿候选尚无核查报告')
        else:
            _, job, row = max(relevant, key=lambda entry: entry[0])
            item.update(job_id=job.get('id'), agent_enabled=job.get('options', {}).get('use_agent', False))
            path = row.get('report_json_path')
            report = read(path, 'initial_report') if path else None
            if report is None:
                item.update(status='failed', blockers=[row.get('error') or '核查报告缺失或无法读取'])
            else:
                run = report.get('run', {})
                snapshot = report.get('source_snapshot') or {}
                text_snapshot = bool(snapshot) or 'snapshot' in str(report.get('source_role', ''))
                source_path = snapshot.get('original_source_path') or row.get('source_path')
                source_hash = snapshot.get('source_sha256') or row.get('source_sha256')
                source_ok = verify(source_path, source_hash, 'initial_source')
                if source_hash != report.get('source_sha256'):
                    source_ok = False
                    issues.append({'scope': 'initial_source', 'path': str(path), 'error': '报告来源 SHA256 与原件记录不符'})
                source_revision = (report.get('svn_source') or {}).get('revision')
                if source_revision is not None and source_revision != revision:
                    source_ok = False
                    issues.append({'scope': 'initial_source', 'path': str(path), 'error': '报告所核查版本与当前历史初稿候选不符'})
                if snapshot:
                    source_ok = verify(snapshot.get('snapshot_path'), snapshot.get('snapshot_sha256'), 'text_snapshot') and source_ok
                groups = report.get('summary', {}).get('result_groups')
                if groups is None:
                    from paper_report import result_group
                    groups = dict(Counter(result_group(f) for f in report.get('findings', [])))
                coverage = report.get('coverage', [])
                item.update(report_exists=True, report_path=str(path), report_status=report.get('status'),
                            source_verified=source_ok, text_snapshot=text_snapshot, result_groups=groups,
                            coverage_statuses=dict(Counter(c.get('status', 'unknown') for c in coverage)),
                            agent_enabled=bool(run.get('mode') == 'agent' or run.get('request_attempts', 0)),
                            agent_completed=run.get('completion', {}).get('status') == 'completed')
                complete = source_ok and bool(coverage) and result_is_complete(report)
                item['status'] = ('source_invalid' if not source_ok else 'completed' if complete else
                                  'local_only' if not item['agent_enabled'] else 'incomplete')
                if not item['agent_enabled']:
                    item['blockers'].append('本次仅执行本地核查，尚未完成 Agent 原文语境审阅')
                if groups.get('unreviewed'):
                    item['blockers'].append(f"仍有 {groups['unreviewed']} 项尚未审阅")
                if groups.get('pending'):
                    item['blockers'].append(f"仍有 {groups['pending']} 项需人工核验或范围确认")
                if text_snapshot:
                    item['blockers'].append('仅恢复原 DOC 文字快照，原生对象及分页未完整核查')
                if any(c.get('status') in {'partial', 'needs_review', 'visual_review_required'} for c in coverage):
                    item['blockers'].append('部分检查项覆盖尚未完成')
                missing = {r['id'] for r in checker_items if r['enabled']} - {c.get('rule_id') for c in coverage}
                progress = run.get('progress', {})
                item['automated_completed'] = bool(source_ok and not missing and run.get('mode') == 'agent'
                    and progress.get('remaining') == 0 and not progress.get('blocked', 0)
                    and run.get('completion', {}).get('status') in {'completed', 'completed_with_manual'}
                    and not run.get('error'))
                if missing:
                    item['status'] = 'incomplete' if source_ok else 'source_invalid'
                    item['blockers'].append('报告未覆盖当前已启用检查项：'+', '.join(sorted(missing)))
        students.append(item)
    summary = {'students': len(students), 'initial_candidates': sum(s['revision'] is not None for s in students),
               'report_students': sum(s['report_exists'] for s in students),
               'fully_completed_students': sum(s['status'] == 'completed' for s in students),
               'automatic_review_complete_students': sum(s['automated_completed'] for s in students),
               'not_checked_students': sum(s['status'] == 'not_checked' for s in students),
               'local_only_students': sum(s['report_exists'] and not s['agent_enabled'] for s in students),
               'incomplete_students': sum(s['report_exists'] and s['status'] != 'completed' for s in students),
               'failed_students': sum(s['status'] == 'failed' for s in students),
               'text_snapshot_students': sum(s['text_snapshot'] for s in students),
               'source_invalid_students': sum(s['status'] == 'source_invalid' for s in students)}
    if discovery is None:
        summary = {key: None for key in summary}
    return {'schema_version': 1, 'status': 'needs_review' if issues or summary['fully_completed_students'] != summary['initial_candidates'] else 'verified',
            'updated_at': datetime.now().astimezone().isoformat(), 'issues': issues, 'rules': rules,
            'initials': {'status': 'available' if discovery is not None else 'not_available',
                         'snapshot_revision': (discovery or {}).get('snapshot_revision'),
                         'summary': summary, 'students': students},
            'limitations': ['报告覆盖完成与独立准确率验收分别统计；已有报告不等于全部检查完成。',
                            '检查项已关联不等于候选规则已可靠签审。历史初稿候选不证明首次本地草稿。',
                            '已关联检查方法仅统计核验过教师证据的关联；无教师证据的补充结构检查单独统计。',
                            '已关联反馈类别、语义待复核、无文字及含图片是不同维度，数量不能直接相加。',
                            '仅本地核查与尚未完整核查计数有包含关系，不能相加作为学生总量。']}
