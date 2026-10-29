"""Prepare unsigned initial-paper candidates; never inspect held-out teacher comments."""
from pathlib import Path
import argparse
from atomic_json import read_json, write_json
from independent_evaluation import canonical_student_id, digest, ground_truth_template


def prepare(manifest, registry, output):
    payload = read_json(manifest)
    if payload.get('eligible_for_library') is not False or payload.get('teacher_comments_opened') is not False:
        raise ValueError('candidate sources are not explicitly quarantined')
    initial = [r for r in payload.get('files', []) if r.get('artifact_role') == 'initial']
    if len(initial) != 1:
        raise ValueError('exactly one verified initial source required')
    row = initial[0]
    if digest(row['exported_path']) != row['sha256']:
        raise ValueError('initial source hash mismatch')
    student = canonical_student_id(payload['student_id'])
    if student in {canonical_student_id(v) for v in registry['development_exposure']['student_ids']}:
        raise ValueError('candidate student already used during development')
    candidate = {'id': 'CANDIDATE_' + student, 'student_id': student,
                 'display_student_id': payload['student_id'],
                 'source_path': row['exported_path'], 'source_sha256': row['sha256'],
                 'split': 'unsigned_prospective_candidate',
                 'independence_attestation': None, 'ground_truth_status': 'pending',
                 'legacy_doc_scoring_ready': False,
                 'teacher_comments_opened': False,
                 'status': 'not_registered_independent_test_without_human_attestation'}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'prospective_candidate.json', candidate)
    write_json(output / 'human_truth_template.json', ground_truth_template(candidate, ['NP001', 'NP010', 'NP011', 'NP012', 'NP013']))
    write_json(output / 'exposure_attestation_template.json', {
        'reviewer': None, 'signed_at': None, 'not_used_in_rule_or_prompt_development': False,
        'notes': '须人工核验原稿/同学生其他版本未参与规则、提示词或开发；模板不是签审'})
    return candidate


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'registry', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.manifest, read_json(args.registry), args.output)
    print(result['status'])
