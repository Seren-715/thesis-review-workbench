"""Update NP022 alone; preserve other reviews and never launch Word or a model."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
import shutil

from atomic_json import read_json, write_json
from new_paper_checks import read_docx, extract_paragraphs, load_teacher_rules
from paper_locations import annotate_paragraphs
from paper_report import result_group, render_report
from table_format_checks import check_native_tables

POLICY_VERSION = 'explicit_three_line_tables_v3'


def attach_verified_table_pages(report, layout):
    """Bind the same source's table to a saved, hash-verified Word PDF page."""
    if not layout or (layout.get('status') != 'native_table_layout_collected_visual_pending'
            or layout.get('source_unchanged') is not True
            or layout.get('source', {}).get('sha256') != report.get('source_sha256')
            or layout.get('layout_changed_during_export') is True):
        return 0
    pdf = layout.get('pdf', {})
    path = Path(pdf.get('path', ''))
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != pdf.get('sha256'):
        return 0
    tables = layout.get('tables_before', [])
    # Word's table order cannot be assumed to match a flattened nested grid.
    if len(tables) != report.get('table_count') or any(t.get('nesting_level') != 1 for t in tables):
        return 0
    rows = {t.get('index'): t for t in tables}
    normalize = lambda s: re.sub(r'\s+', '', str(s).replace('\x07', ''))
    bound = 0
    targets = [f for f in report.get('findings', []) if f.get('rule_id') == 'NP022']
    targets += [o for c in report.get('coverage', []) if c.get('rule_id') == 'NP022'
                for o in c.get('checked_objects', []) + c.get('excluded_objects', [])]
    for item in targets:
        index = item.get('table_index')
        table = rows.get(index + 1) if type(index) is int else None
        if not table or not normalize(item.get('text')) or normalize(item['text']) not in normalize(table.get('text')):
            continue
        first, last = table.get('first_physical_page'), table.get('last_physical_page')
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= layout.get('word_page_count', 10000):
            continue
        item['physical_pages'] = list(range(first, last + 1))
        item['word_layout_source_sha256'] = report['source_sha256']
        bound += 1
    return bound


def refreshed_report(source, prior, layout=None, three_line_tables=False):
    """Recompute only the policy-dependent table check; do not reinterpret text."""
    if type(three_line_tables) is not bool:
        raise ValueError('三线表要求必须明确为布尔值。')
    document, styles, digest, _ = read_docx(source)
    if prior.get('source_sha256') != digest:
        raise ValueError('原件与已存报告不同，不能复用审阅。')
    paragraphs = annotate_paragraphs(extract_paragraphs(document, styles))
    rule = next(r for r in load_teacher_rules() if r['id'] == 'NP022')
    findings, coverage = check_native_tables(document, styles, paragraphs, rule, three_line_tables)
    report = deepcopy(prior)
    report['findings'] = [f for f in report['findings'] if f['rule_id'] != 'NP022'] + findings
    report['coverage'] = [c for c in report['coverage'] if c['rule_id'] != 'NP022'] + [coverage]
    report['rules'] = [r for r in report['rules'] if r['id'] != 'NP022'] + [rule]
    pages_bound = attach_verified_table_pages(report, layout)
    summary = report['summary']
    summary.update(candidate_count=sum(f['status'] == 'candidate' for f in report['findings']),
                   confirmed_count=sum(f['status'] == 'confirmed_issue' for f in report['findings']),
                   pending_count=sum(f['status'] == 'needs_review' for f in report['findings']),
                   structural_candidate_count=sum(f['status'] in {'candidate', 'clue'} and not f.get('semantic_required') for f in report['findings']),
                   result_groups={g:sum(result_group(f) == g for f in report['findings'])
                                  for g in ('changes','compliant','unreviewed','pending')})
    previous = prior.get('run', {})
    now = datetime.now().astimezone().isoformat()
    state = deepcopy(previous)
    state.update(refresh_kind='explicit_table_policy_only', table_policy_version=POLICY_VERSION,
                 three_line_tables=three_line_tables, mode='local_policy_update', model=None,
                 started_at=now, finished_at=now, requests=[], tool_calls=[], request_attempts=0,
                 dispatched_requests=0, known_total_tokens=0, unknown_cost_attempts=0,
                 reserved_token_bound=0, word_launches=0, review_scope='table_policy',
                 semantic_evidence_reused=sum(bool(f.get('semantic_review', {}).get('validated_quote')) for f in report['findings']),
                 vision_evidence_reused=sum(bool(f.get('vision_review', {}).get('validated_image')) for f in report['findings']),
                 layout_evidence_reused=bool(pages_bound), automatic_retries=0)
    if 'progress' in state:
        state['progress'].update(requests_used=0, known_tokens=0)
    report['run'] = state
    report['requests'] = {'llm':0, 'svn':0, 'word':0}
    return report


def refresh_saved_paper(folder):
    """Create a new receipt, keeping the original upload and older runs intact."""
    folder = Path(folder).resolve()
    paper = read_json(folder / 'paper.json')
    if paper.get('status') in {'queued', 'running'}:
        raise ValueError('论文正在检查，不能刷新。')
    previous_id = paper.get('review_id', '')
    if not re.fullmatch(r'review_[A-Za-z0-9_]+', previous_id):
        return None
    previous = folder / previous_id
    if not (previous / 'report.json').is_file():
        return None
    prior = read_json(previous / 'report.json')
    if prior.get('run', {}).get('table_policy_version') == POLICY_VERSION:
        return None
    source = folder / 'source.docx'
    if sha256(source.read_bytes()).hexdigest() != paper['sha256']:
        raise ValueError('上传原件哈希变化，停止刷新。')
    layout = read_json(previous / 'layout_result.json') if (previous / 'layout_result.json').is_file() else None
    report = refreshed_report(source, prior, layout, paper.get('three_line_tables', False))
    rid = 'review_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_tablepolicy'
    output = folder / rid
    output.mkdir()
    state = report['run']
    state['previous_review_id'] = previous_id
    state['previous_report_sha256'] = sha256((previous / 'report.json').read_bytes()).hexdigest()
    state['previous_run_sha256'] = sha256((previous / 'run.json').read_bytes()).hexdigest() if (previous / 'run.json').is_file() else None
    if layout and (layout.get('source', {}).get('sha256') == paper['sha256']
                   and layout.get('source_unchanged') is True):
        pdf = layout.get('pdf', {})
        # Same-byte uploads may reuse a layout record from another paper while
        # already keeping their own verified PDF copy in the prior receipt.
        old_pdf = Path(prior.get('pdf_path') or pdf.get('path', '')).resolve()
        if (old_pdf.is_relative_to(folder) and old_pdf.is_file()
                and sha256(old_pdf.read_bytes()).hexdigest() == pdf.get('sha256')):
            target = output / 'layout' / 'preview.pdf'
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(old_pdf, target)
            layout = deepcopy(layout)
            layout['pdf']['path'] = str(target)
            report['pdf_path'], report['pdf_sha256'] = str(target), pdf['sha256']
            state['layout_evidence_reused'] = True
        else:
            report.pop('pdf_path', None)
            report.pop('pdf_sha256', None)
            state['layout_evidence_reused'] = False
    # The viewer registers evidence only under the new receipt. Copy the same
    # verified bytes and rebind paths; keep semantic/vision judgments untouched.
    for finding in report['findings']:
        evidence = finding.get('figure_evidence', {})
        if evidence.get('source_sha256') != paper['sha256']:
            continue
        for key, hash_key, asset_key in (('preview_path','preview_sha256','preview_asset_id'),
                                         ('display_preview_path','display_preview_sha256','display_preview_asset_id')):
            old_image = Path(evidence.get(key, '')).resolve()
            digest = evidence.get(hash_key)
            if (not old_image.is_relative_to(folder) or old_image.suffix.lower() != '.png'
                    or not old_image.is_file() or sha256(old_image.read_bytes()).hexdigest() != digest):
                continue
            target = output / 'figure_evidence' / (digest + '.png')
            target.parent.mkdir(exist_ok=True)
            if not target.is_file():
                shutil.copyfile(old_image, target)
            evidence[key] = str(target)
            evidence.pop(asset_key, None)
    if layout:
        write_json(output / 'layout_result.json', layout)
    write_json(output / 'run.json', state)
    write_json(output / 'report.json', report)
    (output / 'report.md').write_text(render_report(report), encoding='utf-8')
    paper.update(review_id=rid, previous_review_id=previous_id, three_line_tables=state['three_line_tables'],
                 table_policy_version=POLICY_VERSION, table_policy_updated_at=state['finished_at'])
    write_json(folder / 'paper.json', paper)
    return {'paper_id':paper['id'], 'student_name':paper['student_name'], 'previous_review_id':previous_id,
            'review_id':rid, 'old_groups':prior['summary'].get('result_groups'),
            'new_groups':report['summary']['result_groups'], 'coverage':next(c for c in report['coverage'] if c['rule_id']=='NP022')['status'],
            'source_sha256':paper['sha256'], 'new_llm_requests':0, 'new_svn_requests':0, 'new_word_requests':0}
