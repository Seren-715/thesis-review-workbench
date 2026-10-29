"""Read pinned supplementary visual receipts; no Word, SVN or model calls."""
import json
from pathlib import Path
from cross_student_rule_pilot import digest
from inspect_initial_structure_pilot import collect
from check_remaining_initial_layout_pilot import prepare, validate
from verified_table_pilot import load_verified_pagination as load_prior, RECEIPT_RELATIVE as PRIOR

RECEIPT_RELATIVE = Path("remaining_initial_layout_20261007_194115_597974/visual_review/verification.json")
RECEIPT_SHA256 = "075e5f8f2dac4a1455b78aaa1aade6ea6114fa03489db4ff557c4d2ed487b477"


def verify_artifacts(rows):
    paths = [Path(row['path']).resolve() for row in rows]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate supplementary artifacts')
    for row, path in zip(rows, paths):
        if digest(path) != row['sha256']:
            raise ValueError('Supplementary evidence changed: ' + str(path))


def load_verified_pagination(review_dir, verification_path):
    root, proof = Path(review_dir).resolve(), Path(verification_path).resolve()
    if proof != (root / RECEIPT_RELATIVE).resolve() or digest(proof) != RECEIPT_SHA256:
        raise ValueError('Only pinned supplementary receipt is eligible')
    receipt = json.loads(proof.read_text(encoding='utf-8'))
    if receipt['status'] != 'verified_remaining_initial_layout_development_pilot' or receipt['export_item'] != 7:
        raise ValueError('Supplementary acceptance/mode missing')
    verify_artifacts(receipt['artifacts'])
    roles = {}
    for row in receipt['artifacts']:
        roles.setdefault(row['role'], []).append(row)
    def read_one(role):
        if len(roles.get(role, [])) != 1:
            raise ValueError('Expected one artifact: ' + role)
        return json.loads(Path(roles[role][0]['path']).read_text(encoding='utf-8'))
    p1, p2, p3 = (read_one(role) for role in ('p001_visual_review', 'p002_visual_review', 'p003_supplement'))
    verify_artifacts(p1['artifacts'])
    manual = [row for row in p1['artifacts'] if row['role'] == 'prior_manual_heading_candidates']
    if len(manual) != 1:
        raise ValueError('Expected one source-bound manual chapter candidate record')
    manual_record = json.loads(Path(manual[0]['path']).read_text(encoding='utf-8'))
    verify_artifacts(manual_record['source_part_exports'])
    if manual_record['source'] != p1['source'] or {r['part']: r['sha256'] for r in manual_record['source_part_exports']} != manual_record['part_sha256']:
        raise ValueError('Manual chapter XML exports differ from source record')
    verify_artifacts(p2['rendered_page_artifacts'])
    verify_artifacts(p2['contact_sheets'])
    verify_artifacts([p3['page_artifact']])
    run = read_one('word_batch_run')
    plans = prepare(root)
    if run['status'] != 'collected_visual_pending' or run['export_item'] != 7 or run['plans'] != plans or len(run['results']) != 2:
        raise ValueError('Supplementary Word batch/source inventory differs')
    for plan, result, visual in zip(plans, run['results'], (p1, p2)):
        if result['source'] != plan['source'] or visual['source'] != plan['source']:
            raise ValueError('Visual and Word source identities differ')
        audit = json.loads(Path(result['audit_path']).read_text(encoding='utf-8-sig'))
        tables = validate(audit, plan, Path(result['pdf_path']))
        if tables != result['tables'] or digest(Path(result['audit_path'])) != result['audit_sha256']:
            raise ValueError('Word table/audit evidence differs')
    prior = load_prior(root, root / PRIOR)
    if Path(p3['parent_receipt']).resolve() != (root / PRIOR).resolve() or p3['parent_receipt_sha256'] != prior['verification_sha256']:
        raise ValueError('P003 supplement has a different parent')
    initial = collect(root)
    rows = []
    for fresh, saved, visual in zip(initial, receipt['initial_results'], (p1, p2, p3)):
        source = fresh['source']
        if any(source[k] != saved[k] or source[k] != visual['source'][k] for k in ('sample_id', 'revision', 'sha256')):
            raise ValueError('Supplementary source identity mismatch')
        rows.append({k: saved[k] for k in ('sample_id', 'revision', 'sha256', 'table_status')}
                    | {'confirmed_table_issue_count': saved.get('confirmed_missing_label_count', 0),
                       'visual_chapter_count': saved.get('visual_chapter_count'),
                       'chapter_rule_scope_status': saved.get('chapter_rule_scope_status', 'not_evaluated_by_layout_tool')})
    if len(initial) != 3 or len(receipt['initial_results']) != 3 or [r['sample_id'] for r in rows] != ['P001', 'P002', 'P003']:
        raise ValueError('Exactly three ordered initial sources required')
    if p1['chapter_visual_review']['visible_chapter_title_count'] != 5 or p1['XR002_scope_review']['applicability_decision'] is not None:
        raise ValueError('P001 chapter observation/scope changed')
    if p2['confirmed_missing_label_count'] != 2 or [v['table_index'] for v in p2['violations']] != [3, 11]:
        raise ValueError('P002 violation receipt differs')
    if rows[0]['confirmed_table_issue_count'] != 0 or rows[1]['confirmed_table_issue_count'] != len(p2['violations']) or rows[2]['confirmed_table_issue_count'] != 0:
        raise ValueError('Summary counts differ from visual receipts')
    return {'items': rows, 'teacher_reference': prior['teacher_reference'],
            'verification_path': str(proof), 'verification_sha256': RECEIPT_SHA256,
            'receipt': receipt, 'visual_reviews': [p1, p2, p3],
            'limits': 'Markup receipts only. P001 five visible chapters do not establish saved outline or XR002 applicability; scope_review. P002 two verified continuation issues. P003 both native tables single-page; image pages not fully reviewed. Teacher r249 separate. Development replay, not independent accuracy.'}
