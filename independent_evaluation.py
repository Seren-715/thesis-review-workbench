"""Human-labelled, student-separated evaluation. Never use model scores as truth."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path

from atomic_json import read_json, write_json

VERSION = 'independent_evaluation_v1'
TEXT_EVALUATION_RULES = frozenset({'NP001', 'NP002', 'NP008', 'NP009', 'NP010',
                                  'NP011', 'NP012', 'NP013', 'NP014', 'NP016', 'NP017'})


def canonical_student_id(value):
    value = str(value).replace('\\', '/').rstrip('/').split('/')[-1]
    match = re.fullmatch(r'(\d{6,})(?:_.+)?', value)
    return match.group(1) if match else value


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create_registry(library):
    """Prior development exposure is immutable and cannot be relabelled as blind."""
    students = sorted({canonical_student_id(c['student_id']) for c in library['comments'] if c.get('student_id')})
    hashes = sorted({c['source_sha256'] for c in library['comments'] if c.get('source_sha256')})
    return {'schema_version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
            'development_exposure': {'student_ids': students, 'source_sha256': hashes,
                                     'library_source_fingerprint': library.get('source_fingerprint'),
                                     'policy': 'historical_exposure_cannot_be_undone'},
            'materials': [], 'status': 'no_independent_test_material_registered'}


def register_material(registry, material_id, student_id, source, split,
                      exposure_attestation=None):
    if split not in {'development', 'independent_test'}:
        raise ValueError('split must be development or independent_test')
    if not material_id or not student_id or any(m['id'] == material_id for m in registry['materials']):
        raise ValueError('material/student identity missing or material already registered')
    sha = digest(source)
    if any(m['source_sha256'] == sha for m in registry['materials']):
        raise ValueError('same source already registered; cannot change its split')
    seen = registry['development_exposure']
    normalized = canonical_student_id(student_id)
    exposed = normalized in {canonical_student_id(v) for v in seen['student_ids']} or sha in seen['source_sha256']
    exposed |= any(canonical_student_id(m['student_id']) == normalized and m['split'] == 'development'
                   for m in registry['materials'])
    if split == 'independent_test':
        if exposed:
            raise ValueError('student/source already exposed during rule development')
        att = exposure_attestation or {}
        if (att.get('not_used_in_rule_or_prompt_development') is not True or
                not att.get('reviewer') or not att.get('signed_at')):
            raise ValueError('independence requires a signed human exposure attestation')
    # Do not permit development of another version from an already sealed student.
    if split == 'development' and any(canonical_student_id(m['student_id']) == normalized and
                                     m['split'] == 'independent_test' for m in registry['materials']):
        raise ValueError('sealed test student cannot later enter development under this registry')
    row = {'id': material_id, 'student_id': normalized, 'display_student_id': student_id, 'source_path': str(Path(source).resolve()),
           'source_sha256': sha, 'split': split, 'registered_at': datetime.now(timezone.utc).isoformat(),
           'independence_attestation': exposure_attestation, 'ground_truth_status': 'pending'}
    registry['materials'].append(row)
    registry['status'] = 'materials_registered_ground_truth_pending'
    return row


def ground_truth_template(material, rule_ids):
    return {'schema_version': VERSION, 'material_id': material['id'],
            'source_sha256': material['source_sha256'], 'human_signoff': {'reviewer': None, 'signed_at': None},
            'rule_coverage': [{'rule_id': rid, 'applicable': None,
                               'exhaustive_for_rule': False, 'reason': None} for rid in rule_ids],
            'cases': [], 'case_template': {'id': 'G001', 'rule_id': rule_ids[0] if rule_ids else None,
                                          'paragraph_index': None, 'quote': None,
                                          'label': 'issue|not_issue|uncertain',
                                          'rationale': None, 'teacher_evidence_id': None},
            'instructions': ['独立人工结合完整论文逐条标注，不用模型输出填充真值',
                             '每条引文必须逐字来自对应段落；记录真实问题和适用范围',
                             '仅完整核验适用规则后勾选exhaustive_for_rule；不勾选则不计算召回率',
                             '初稿没有出现教师后稿问题不等于初稿漏检；必须直接标注初稿']}


def _signed(value):
    return bool(isinstance(value, dict) and value.get('reviewer') and value.get('signed_at'))


def evaluate(registry, material_id, truth, report, package):
    material = next((m for m in registry['materials'] if m['id'] == material_id), None)
    if material is None:
        raise ValueError('material not registered')
    sha = digest(material['source_path'])
    if sha != material['source_sha256'] or sha != truth.get('source_sha256') or sha != report.get('source_sha256'):
        raise ValueError('source hash mismatch')
    if truth.get('material_id') != material_id:
        raise ValueError('ground truth belongs to another material')
    # Re-extract original visible paragraphs independently of supplied report text.
    from new_paper_checks import read_docx, extract_paragraphs
    if Path(material['source_path']).read_bytes()[:8] == bytes.fromhex('d0cf11e0a1b11ae1'):
        raise ValueError('legacy DOC evaluation requires separately verified conversion/Word snapshot; current evaluator accepts Word OOXML only')
    document, styles, source_sha, _ = read_docx(material['source_path'])
    paragraphs = {p['index']: p['text'] for p in extract_paragraphs(document, styles)}
    cases = truth.get('cases', [])
    if not isinstance(cases, list) or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('duplicate or invalid ground truth cases')
    for case in cases:
        if (case.get('label') not in {'issue', 'not_issue', 'uncertain'} or
                not isinstance(case.get('quote'), str) or not case['quote'].strip() or
                case['quote'] not in paragraphs.get(case.get('paragraph_index'), '')):
            raise ValueError('ground truth quote/paragraph/label invalid')
    coverage = truth.get('rule_coverage', [])
    rules = {r['id']: r for r in package.get('rules', [])}
    declared = {r['rule_id'] for r in coverage if r.get('applicable') is True}
    excluded_rules = declared - TEXT_EVALUATION_RULES
    requested = declared & TEXT_EVALUATION_RULES
    # Packages hold family IDs; checker scope can use NP IDs. Match either honestly.
    aliases = {rid: r for r in rules.values() for rid in r.get('checker_ids', [])}
    aliases.update(rules)
    checks = {rid: aliases.get(rid) for rid in requested}
    signed_rules = all(r and r.get('human_signoff', {}).get('status') == 'approved' and
                       _signed(r.get('human_signoff')) and r['human_signoff'].get('approved_scope')
                       for r in checks.values())
    independent = material['split'] == 'independent_test' and _signed(material.get('independence_attestation'))
    exhaustive = bool(requested) and all(c.get('exhaustive_for_rule') is True
                                        for c in coverage if c.get('applicable') is True)
    truth_signed = _signed(truth.get('human_signoff'))
    ready = independent and exhaustive and truth_signed and signed_rules
    predictions, pending = [], []
    for finding in report.get('findings', []):
        if finding.get('rule_id') not in requested:
            continue
        # Image/OLE/visual outcomes require object-level human truth and matching,
        # which this text-only evaluator deliberately does not claim to support.
        if finding.get('vision_review') or finding.get('vision_receipt'):
            continue
        receipt = finding.get('semantic_review') or {}
        if receipt.get('decision') == 'suggested_revision' and receipt.get('validated_quote') is True:
            predictions.append(finding)
        elif finding.get('status') == 'confirmed_issue' or finding.get('human_review', {}).get('decision') == 'confirmed_issue':
            predictions.append(finding)
        elif receipt.get('decision') != 'not_issue':
            pending.append(finding)
    # One finding may cover multiple ground-truth instances. Matching is by exact
    # rule and source paragraph; unmatched suggestions remain false positives only
    # when human coverage explicitly declares exhaustive labels.
    positive = [c for c in cases if c['label'] == 'issue' and c['rule_id'] in requested]
    matched, false_positive_ids, ambiguous, matched_suggestion_ids = set(), [], [], []
    for finding in predictions:
        receipt = finding.get('semantic_review') or {}
        index = receipt.get('quote_paragraph_index', finding.get('paragraph_index'))
        quote = receipt.get('quote') or finding.get('text', '')
        if not quote or quote not in paragraphs.get(index, ''):
            raise ValueError('prediction quote is not present in verified source paragraph')
        targets = [c for c in cases if c['rule_id'] == finding['rule_id'] and
                   c['paragraph_index'] == index and
                   (c['quote'] in quote or quote in c['quote'])]
        good = [c for c in targets if c['label'] == 'issue']
        if good:
            matched.update(c['id'] for c in good)
            matched_suggestion_ids.append(finding.get('id'))
        elif any(c['label'] == 'uncertain' for c in targets):
            ambiguous.append(finding.get('id'))
        else:
            false_positive_ids.append(finding.get('id'))
    # Case-level true positives; one suggestion overlapping multiple labels counts
    # each labelled instance only once. Report matching unit explicitly.
    tp, fp, fn = len(matched), len(false_positive_ids), len(positive) - len(matched)
    precision_tp = len(matched_suggestion_ids)
    blocked = []
    if not independent: blocked.append('development_material_not_independent')
    if not truth_signed: blocked.append('human_ground_truth_unsigned')
    if not exhaustive: blocked.append('applicable_rule_coverage_not_exhaustive')
    if not signed_rules: blocked.append('rules_not_human_approved_and_scope_frozen')
    if excluded_rules: blocked.append('non_text_rules_not_supported_by_this_evaluator')
    if ambiguous: blocked.append('uncertain_ground_truth_matches_require_resolution')
    ready &= not ambiguous and not excluded_rules
    result = {'schema_version': VERSION, 'material_id': material_id, 'source_sha256': sha,
              'evaluation_status': 'independent_human_labelled' if ready else 'acceptance_blocked',
              'blocked_reasons': blocked, 'independent_accuracy_test': bool(ready),
              'matching_unit': 'precision_per_suggestion_recall_per_human_issue_instance',
              'evaluation_scope': 'source_bound_text_suggestions_only_excludes_vision_and_legacy_doc',
              'unsupported_declared_rule_ids': sorted(excluded_rules),
              'ground_truth_positive_count': len(positive), 'matched_truth_ids': sorted(matched),
              'unmatched_truth_ids': sorted(c['id'] for c in positive if c['id'] not in matched),
              'unmatched_suggestion_ids': false_positive_ids, 'uncertain_matches': ambiguous,
              'unconfirmed_finding_count': len(pending), 'applicable_rules': sorted(requested),
              'matched_suggestion_ids': matched_suggestion_ids,
              'precision': precision_tp / (precision_tp + fp) if ready and precision_tp + fp else None,
              'recall': tp / (tp + fn) if ready and tp + fn else None,
              'true_positives': tp if ready else None, 'false_positives': fp if ready else None,
              'false_negatives': fn if ready else None,
              'metric_limits': ['只针对人工声明完整标注的适用规则；不等于论文整体准确率',
                                '没有问题或没有建议时对应比率为null，不写100%',
                                '候选/未确认/拒答单列，不能伪装成正确预测']}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--library', type=Path, required=True)
    init.add_argument('--output', type=Path, required=True)
    register = commands.add_parser('register')
    register.add_argument('--registry', type=Path, required=True)
    register.add_argument('--id', required=True)
    register.add_argument('--student-id', required=True)
    register.add_argument('--source', type=Path, required=True)
    register.add_argument('--split', choices=['development', 'independent_test'], required=True)
    register.add_argument('--attestation', type=Path)
    register.add_argument('--template', type=Path, required=True)
    register.add_argument('--rules', nargs='+', required=True)
    check = commands.add_parser('evaluate')
    for name in ('registry', 'truth', 'report', 'package', 'output'):
        check.add_argument('--' + name, type=Path, required=True)
    check.add_argument('--material-id', required=True)
    args = parser.parse_args()
    if args.command == 'init':
        if args.output.exists():
            raise SystemExit('registry already exists; preserve exposure history instead of overwriting')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, create_registry(read_json(args.library)))
    elif args.command == 'register':
        from atomic_json import path_lock
        with path_lock(args.registry):
            registry = read_json(args.registry)
            item = register_material(registry, args.id, args.student_id, args.source, args.split,
                                     read_json(args.attestation) if args.attestation else None)
            write_json(args.registry, registry)
            args.template.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.template, ground_truth_template(item, args.rules))
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, evaluate(read_json(args.registry), args.material_id,
                                        read_json(args.truth), read_json(args.report), read_json(args.package)))
