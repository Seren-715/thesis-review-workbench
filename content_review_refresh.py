"""Rebuild and review complete content scopes without rerunning other checks."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import os
import re

from atomic_json import read_json, write_json
from new_paper_checks import execute_checks
from paper_locations import enrich_report_locations
from paper_report import result_group, render_report
from reference_review_refresh import copy_verified_assets

CONTENT_CHECKERS = {'abstract_semantics', 'conclusion_semantics',
                    'implementation_semantics', 'complete_section_semantics'}
SCOPE_NOTE = '本轮仅更新完整章节内容审阅范围，其他已存判断保持原有来源记录。'


def content_rule_ids(report):
    """Discover registered and populated complete-scope checks, without an ID list."""
    return ({r['id'] for r in report.get('rules', []) if r.get('checker') in CONTENT_CHECKERS}
            | {f['rule_id'] for f in report.get('findings', [])
               if f.get('semantic_required') and f.get('content_scope')})


def refreshed_content(source, prior):
    # The normal local dispatcher supplies the current registered content
    # methods. It performs no model, SVN or Word calls. Only its complete-scope
    # content objects are selected; every other prior result stays untouched.
    refreshed = execute_checks(source, student_name=prior.get('student_name', ''),
        chapter_policy=prior.get('run', {}).get('chapter_policy', False),
        three_line_tables=prior.get('run', {}).get('three_line_tables', False))
    if refreshed['source_sha256'] != prior.get('source_sha256'):
        raise ValueError('内容审阅原件与旧报告哈希不一致。')
    current_ids = content_rule_ids(refreshed)
    replaced_ids = current_ids | content_rule_ids(prior)
    rules = [r for r in refreshed['rules'] if r['id'] in current_ids]
    findings = [f for f in refreshed['findings'] if f['rule_id'] in current_ids]
    coverage = [c for c in refreshed['coverage'] if c['rule_id'] in current_ids]
    report = deepcopy(prior)
    report['paragraphs'] = refreshed['paragraphs']
    report['findings'] = [f for f in report['findings'] if f['rule_id'] not in replaced_ids] + findings
    report['coverage'] = [c for c in report['coverage'] if c['rule_id'] not in replaced_ids] + coverage
    report['rules'] = [r for r in report['rules'] if r['id'] not in replaced_ids] + rules
    enrich_report_locations(report)
    from new_paper_agent import reuse_semantic_evidence
    reuse_semantic_evidence(report, prior)
    return report


def refresh_saved_content(folder, use_agent=False, client=None, max_requests=12, max_total_bound=90000):
    folder = Path(folder).resolve()
    paper = read_json(folder / 'paper.json')
    if paper.get('status') in {'running', 'queued'}:
        raise ValueError('正在审阅的论文不能更新内容范围。')
    previous_id = paper.get('review_id', '')
    if not re.fullmatch(r'review_[A-Za-z0-9_]+', previous_id):
        raise ValueError('缺少原审阅记录。')
    previous = folder / previous_id
    prior = read_json(previous / 'report.json')
    source = folder / 'source.docx'
    if sha256(source.read_bytes()).hexdigest() != paper['sha256']:
        raise ValueError('上传原件哈希变化，停止更新。')
    report = refreshed_content(source, prior)
    content_ids = content_rule_ids(report)
    rid = 'review_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_context'
    output = folder / rid
    output.mkdir()
    copied_pdf = copy_verified_assets(folder, prior, report, output)
    if copied_pdf and (previous / 'layout_result.json').is_file():
        layout = read_json(previous / 'layout_result.json')
        if layout.get('source', {}).get('sha256') == paper['sha256']:
            layout['pdf']['path'] = report['pdf_path']
            write_json(output / 'layout_result.json', layout)
    from new_paper_agent import BoundedClient, MODEL, update_review_progress, _finalize_phase_errors
    from content_context_agent import ContextReviewQueue, CONTENT_POLICY
    state = {'source_sha256':paper['sha256'], 'status':'running',
        'started_at':datetime.now().astimezone().isoformat(),
        'refresh_kind':'complete_content_scope_only', 'content_policy_version':CONTENT_POLICY,
        'review_scope':'content', 'mode':'bounded_content_review' if use_agent else 'local_content_scope',
        'table_policy_version':prior.get('run', {}).get('table_policy_version'),
        'reference_policy_version':prior.get('run', {}).get('reference_policy_version'),
        'three_line_tables':paper.get('three_line_tables', False),
        'model':MODEL if use_agent else None, 'max_requests':max_requests,
        'max_reserved_token_bound':max_total_bound, 'requests':[], 'tool_calls':[],
        'request_attempts':0, 'dispatched_requests':0, 'known_total_tokens':0,
        'unknown_cost_attempts':0, 'reserved_token_bound':0, 'automatic_retries':0,
        'word_launches':0, 'layout_evidence_reused':copied_pdf,
        'previous_review_id':previous_id,
        'previous_report_sha256':sha256((previous / 'report.json').read_bytes()).hexdigest(),
        'semantic_evidence_reused':sum(bool(f.get('semantic_review', {}).get('validated_quote'))
                                     for f in report['findings'] if f['rule_id'] not in content_ids),
        'vision_evidence_reused':sum(bool(f.get('vision_review', {}).get('validated_image')) for f in report['findings'])}
    queue = ContextReviewQueue(report, state, output, allowed_rule_ids=content_ids)
    write_json(output / 'report.json', report)
    write_json(output / 'run.json', state)
    if use_agent:
        if client is None:
            from openai import OpenAI
            key = os.environ.get('DEEPSEEK_API_KEY')
            if not key:
                raise ValueError('未配置DeepSeek Key，未发送模型请求。')
            client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=45, max_retries=0)
        from model_privacy import identity_names
        budget = BoundedClient(client, output, state, identity_names(report))
        while queue.has_pending():
            if not queue.step(budget):
                state['budget_stop_reason'] = '内容审阅达到本轮有限预算，已读证据已保存，未完成项可继续审阅。'
                break
    _finalize_phase_errors(report, state)
    if sha256(source.read_bytes()).hexdigest() != paper['sha256']:
        raise ValueError('内容审阅期间原件发生变化，停止发布。')
    # Publish only if no independent review replaced our starting snapshot.
    latest = read_json(folder / 'paper.json')
    if latest.get('review_id') != previous_id or latest.get('status') in {'running','queued'}:
        raise ValueError('另一轮审阅已开始或更新；本轮证据保留，但不覆盖当前记录。')
    state.update(status='partial', finished_at=datetime.now().astimezone().isoformat())
    update_review_progress(report, state)
    state['completion']['reason'] = SCOPE_NOTE + state['completion']['reason']
    report['run'], report['requests'] = deepcopy(state), {'llm':state['dispatched_requests'], 'word':0, 'svn':0}
    report['summary']['result_groups'] = {g:sum(result_group(f)==g for f in report['findings'])
                                         for g in ('changes','compliant','unreviewed','pending')}
    report['summary']['candidate_count'] = sum(f['status']=='candidate' for f in report['findings'])
    report['summary']['agent_suggested_count'] = sum(f.get('semantic_review',{}).get('decision')=='suggested_revision'
        or f.get('vision_review',{}).get('decision')=='suggested_revision' for f in report['findings'])
    report['summary']['agent_excluded_count'] = sum(f.get('semantic_review',{}).get('decision')=='not_issue'
        or f.get('vision_review',{}).get('decision')=='not_issue' for f in report['findings'])
    write_json(output / 'report.json', report)
    write_json(output / 'run.json', state)
    (output / 'report.md').write_text(render_report(report), encoding='utf-8')
    latest.update(review_id=rid, previous_review_id=previous_id, content_policy_version=CONTENT_POLICY)
    write_json(folder / 'paper.json', latest)
    return {'paper_id':paper['id'], 'review_id':rid, 'new_requests':state['dispatched_requests'],
            'new_tokens':state['known_total_tokens'], 'completion':state['completion'],
            'content':[{'id':f['id'], 'decision':f.get('semantic_review',{}).get('decision'),
                        'scope_paragraphs':f['content_scope']['total_paragraphs'],
                        'read_chunks':len(f.get('content_read_progress',{}).get('read_chunk_ids',[])),
                        'total_chunks':f['content_scope']['total_chunks']}
                       for f in report['findings'] if f.get('content_scope')]}
