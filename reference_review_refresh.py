"""Refresh source-bound references; keep originals and other review receipts."""
import asyncio
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import os
import re
import shutil

from atomic_json import read_json, write_json
from new_paper_checks import read_docx, extract_paragraphs, load_teacher_rules
from object_reference_checks import VERSION, check_object_references, attach_reference_pages
from teacher_object_presence_checks import check_object_presence
from paper_locations import annotate_paragraphs
from paper_report import result_group, render_report

SCOPE_NOTE = '本轮仅更新引用诊断，其他检查复用已存结果。'
REFERENCE_RULE_IDS = {'NP010', 'NP050'}
REFRESH_VERSION = VERSION + '_teacher_single_labels_v1'


def refreshed_references(source, prior):
    document, styles, digest, _ = read_docx(source)
    if digest != prior.get('source_sha256'):
        raise ValueError('引用诊断原件与旧报告哈希不一致。')
    paragraphs = annotate_paragraphs(extract_paragraphs(document, styles))
    rules = load_teacher_rules()
    rule = next(r for r in rules if r['id'] == 'NP010')
    findings, coverage = check_object_references(document, paragraphs, rule, digest)
    additional, additional_coverage = check_object_presence(document, paragraphs, rules, digest)
    findings.extend(additional)
    report = deepcopy(prior)
    report['findings'] = [f for f in report['findings'] if f['rule_id'] not in REFERENCE_RULE_IDS] + findings
    report['coverage'] = [c for c in report['coverage'] if c['rule_id'] not in REFERENCE_RULE_IDS] + [coverage] + additional_coverage
    report['rules'] = [r for r in report['rules'] if r['id'] not in REFERENCE_RULE_IDS] + [r for r in rules if r['id'] in REFERENCE_RULE_IDS]
    from new_paper_agent import reuse_semantic_evidence
    reuse_semantic_evidence(report, prior)
    return report


def copy_verified_assets(folder, prior, report, output):
    """Make the new receipt self-contained; every copied byte is hash-verified."""
    folder, output = Path(folder).resolve(), Path(output).resolve()
    if not output.is_relative_to(folder):
        raise ValueError('输出目录不在本论文记录内。')
    old_pdf = Path(prior.get('pdf_path') or '').resolve()
    copied_pdf = False
    if (old_pdf.is_relative_to(folder) and old_pdf.is_file()
            and prior.get('source_sha256') == report['source_sha256']
            and sha256(old_pdf.read_bytes()).hexdigest() == prior.get('pdf_sha256')):
        target = output / 'layout' / 'preview.pdf'
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(old_pdf, target)
        report.update(pdf_path=str(target), pdf_sha256=prior['pdf_sha256'])
        copied_pdf = True
    else:
        report.pop('pdf_path', None)
        report.pop('pdf_sha256', None)
    for f in report['findings']:
        e = f.get('figure_evidence', {})
        if e.get('source_sha256') != report['source_sha256']:
            continue
        for key, hash_key, asset_key in [('preview_path','preview_sha256','preview_asset_id'), ('display_preview_path','display_preview_sha256','display_preview_asset_id')]:
            e.pop(asset_key, None)
            old = Path(e.get(key) or '').resolve()
            digest = e.get(hash_key)
            if not old.is_relative_to(folder) or not old.is_file() or old.suffix.lower() != '.png' or sha256(old.read_bytes()).hexdigest() != digest:
                e.pop(key, None)
                continue
            target = output / 'figure_evidence' / (digest + '.png')
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                shutil.copyfile(old, target)
            e[key] = str(target)
    return copied_pdf


def refresh_saved_references(folder, use_agent=False, client=None, max_requests=4, max_total_bound=60000):
    folder = Path(folder).resolve()
    paper = read_json(folder / 'paper.json')
    if paper.get('status') in {'running', 'queued'}:
        raise ValueError('正在批改的论文不能更新引用诊断。')
    previous_id = paper.get('review_id', '')
    if not re.fullmatch(r'review_[A-Za-z0-9_]+', previous_id):
        return None
    previous = folder / previous_id
    if not (previous / 'report.json').is_file():
        return None
    prior = read_json(previous / 'report.json')
    if prior.get('run', {}).get('reference_policy_version') == REFRESH_VERSION:
        return None
    source = folder / 'source.docx'
    if sha256(source.read_bytes()).hexdigest() != paper['sha256']:
        raise ValueError('上传原件哈希变化，停止更新。')
    report = refreshed_references(source, prior)
    rid = 'review_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_references'
    output = folder / rid
    output.mkdir()
    copied_pdf = copy_verified_assets(folder, prior, report, output)
    if (previous / 'layout_result.json').is_file():
        layout = read_json(previous / 'layout_result.json')
        if copied_pdf and layout.get('source', {}).get('sha256') == paper['sha256']:
            layout['pdf']['path'] = report['pdf_path']
            write_json(output / 'layout_result.json', layout)
    from new_paper_agent import SemanticReviewQueue, BoundedClient, FreshPaperTool, ToolRegistry, MODEL, update_review_progress
    now = datetime.now().astimezone().isoformat()
    state = {'source_sha256': report['source_sha256'], 'status':'running',
             'started_at':now, 'refresh_kind':'object_reference_diagnosis_only',
             'reference_policy_version': REFRESH_VERSION, 'review_scope':'references',
             'table_policy_version':prior.get('run', {}).get('table_policy_version'),
             'three_line_tables':paper.get('three_line_tables',False),
             'mode':'bounded_agent_reference_review' if use_agent else 'local_reference_diagnosis',
             'model':MODEL if use_agent else None, 'max_requests':max_requests,
             'max_reserved_token_bound':max_total_bound, 'requests':[], 'tool_calls':[],
             'request_attempts':0, 'dispatched_requests':0, 'known_total_tokens':0,
             'unknown_cost_attempts':0, 'reserved_token_bound':0, 'automatic_retries':0,
             'word_launches':0, 'layout_evidence_reused':copied_pdf,
             'previous_review_id':previous_id,
             'previous_report_sha256':sha256((previous/'report.json').read_bytes()).hexdigest(),
             'semantic_evidence_reused':sum(bool(f.get('semantic_review', {}).get('validated_quote')) for f in report['findings'] if f['rule_id'] not in REFERENCE_RULE_IDS),
             'vision_evidence_reused':sum(bool(f.get('vision_review', {}).get('validated_image')) for f in report['findings']),
             'reference_pages_bound':attach_reference_pages(report)}
    write_json(output / 'run.json', state)
    if use_agent:
        if client is None:
            from openai import OpenAI
            key = os.environ.get('DEEPSEEK_API_KEY')
            if not key:
                raise ValueError('服务器尚未配置DeepSeek Key，未发送模型请求。')
            client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=45, max_retries=0)
        registry = ToolRegistry()
        registry.register(FreshPaperTool('inspect_references_and_content', report))
        result = asyncio.run(registry.execute('inspect_references_and_content', {}))
        state['tool_calls'].append({'name':'inspect_references_and_content', 'arguments':{}, 'result':result, 'scope':'NP010 and NP050'})
        queue = SemanticReviewQueue(report, state, output, allowed_rule_ids=REFERENCE_RULE_IDS)
        from model_privacy import identity_names
        budget = BoundedClient(client, output, state, identity_names(report))
        while queue.has_pending():
            if not queue.step(budget):
                state['budget_stop_reason'] = '引用诊断达到本轮预算，未完成项保留待审。'
                break
    if sha256(source.read_bytes()).hexdigest() != paper['sha256']:
        raise ValueError('引用诊断期间原件发生变化，停止发布新记录。')
    state.update(status='partial', finished_at=datetime.now().astimezone().isoformat())
    update_review_progress(report, state)
    state['completion']['reason'] = SCOPE_NOTE + state['completion']['reason']
    if not use_agent and state['progress']['remaining']:
        state['completion'].update(status='stopped', reason='已完成本地引用诊断，新增候选尚未进行模型语境审阅；可继续审阅。')
    report['run'], report['requests'] = deepcopy(state), {'llm':state['dispatched_requests'], 'word':0, 'svn':0}
    report['summary']['result_groups'] = {g:sum(result_group(f)==g for f in report['findings']) for g in ('changes','compliant','unreviewed','pending')}
    report['summary']['candidate_count'] = sum(f['status']=='candidate' for f in report['findings'])
    report['summary']['agent_suggested_count'] = sum(f.get('semantic_review',{}).get('decision')=='suggested_revision' or f.get('vision_review',{}).get('decision')=='suggested_revision' for f in report['findings'])
    report['summary']['agent_excluded_count'] = sum(f.get('semantic_review',{}).get('decision')=='not_issue' or f.get('vision_review',{}).get('decision')=='not_issue' for f in report['findings'])
    write_json(output / 'report.json', report)
    write_json(output / 'run.json', state)
    (output/'report.md').write_text(render_report(report), encoding='utf-8')
    paper.update(review_id=rid, previous_review_id=previous_id, reference_policy_version=REFRESH_VERSION)
    write_json(folder / 'paper.json', paper)
    return {'paper_id':paper['id'], 'student_name':paper['student_name'], 'review_id':rid,
            'new_groups':report['summary']['result_groups'], 'new_llm_requests':state['dispatched_requests'],
            'new_tokens':state['known_total_tokens'], 'new_word_requests':0, 'new_svn_requests':0,
            'references':[{'id':f['id'], 'original':f['reference_diagnostic']['reference_label'],
                           'decision':f.get('semantic_review',{}).get('decision'),
                           'target':f.get('semantic_review',{}).get('target_label')} for f in report['findings'] if f['rule_id'] in REFERENCE_RULE_IDS]}
