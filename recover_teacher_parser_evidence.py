"""Recover already-preserved teacher evidence through strict representation audit.

No downloads, model calls or Word processes. Only the two reviewed comment-text
representation failures are eligible. Originals/conversion audits remain intact.
"""
from datetime import datetime
import json
from pathlib import Path

from comment_representation_audit import audit_parser_comment_representation
from docx_comment_reader import extract_docx
from legacy_doc_provenance_v2 import file_hash, validate_lineage
from teacher_rule_library import get_library

ROOT = Path(__file__).resolve().parent
EXPORTS = ROOT / 'data/thesis_workflow/exports'
JOB = 'job_20261008_184822_959306_264c55'
TARGETS = [('212006230_张小龙', 216), ('212008213_胡力岩', 356)]
EXPECTED_ERROR = 'Word and XML full comment text/author differ; manual review required'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _save_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def recover():
    prepared, completed = [], []
    # Validate every target first, before publishing any manifest changes.
    for student, revision in TARGETS:
        folder = EXPORTS / student / JOB
        manifest_path = folder / 'manifest.json'
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode('utf-8-sig'))
        matches = [f for f in manifest.get('failures', []) if f.get('revision') == revision]
        if len(matches) != 1:
            raise ValueError(f'{student} r{revision}: failure is not unique')
        failure = matches[0]
        if failure.get('status') == 'resolved_via_strict_parser_representation':
            records = [f for f in manifest['files'] if f.get('revision') == revision and f.get('repository_path') == failure.get('repository_path')]
            if len(records) != 1:
                raise ValueError('Resolved marker is not bound to one eligible evidence record')
            validate_lineage(folder, records[0])
            completed.append({'student_name': manifest['student_name'], 'revision': revision, 'status': 'already_recovered_verified'})
            continue
        if failure.get('status') != 'failed_no_retry' or failure.get('error') != EXPECTED_ERROR:
            raise ValueError('Only the reviewed parser representation failure may be recovered')
        original = dict(failure['original_export'])
        source = Path(original['exported_path']).resolve()
        if not source.is_relative_to(folder.resolve()) or file_hash(source) != original['sha256']:
            raise ValueError('Preserved original export hash/path differs')
        derived = source.with_name(source.stem + '_analysis.docx')
        audit_path = source.with_name(source.stem + '_conversion.json')
        audit = _read(audit_path)
        extracted = extract_docx(derived, {'sample_id': student, 'sha256': file_hash(derived)})
        representation = audit_parser_comment_representation(audit, extracted, derived)
        if representation['status'] != 'strict_comment_representation_verified_visual_pending':
            raise ValueError('Expected reviewed structural representation mapping was not used')
        proof_path = source.with_name(source.stem + '_parser_recovery_representation.json')
        if proof_path.exists():
            if _read(proof_path) != representation:
                raise ValueError('Existing representation sidecar differs; it was not overwritten')
        else:
            _save_new(proof_path, representation)
        item = dict(original, exported_path=str(derived), sha256=file_hash(derived), size_bytes=derived.stat().st_size,
                    artifact_role='analysis_copy_from_legacy_svn_doc',
                    original_export={'document_path': str(source), 'sha256': original['sha256']},
                    conversion={'document_path': str(audit_path), 'sha256': file_hash(audit_path), 'mode': audit['status']},
                    comment_representation={'document_path': str(proof_path), 'sha256': file_hash(proof_path), 'mode': representation['status']},
                    comment_count=len(extracted['comments']), parser_recovery='strict_representation_verified_visual_pending')
        validate_lineage(folder, item)
        if any(f.get('revision') == revision and f.get('repository_path') == item['repository_path'] for f in manifest['files']):
            raise ValueError('Eligible manifest record already exists without matching resolved marker')
        prepared.append((folder, manifest_path, raw_manifest, manifest, failure, item, representation))
    for folder, manifest_path, raw_manifest, manifest, failure, item, representation in prepared:
        if manifest_path.read_bytes() != raw_manifest:
            raise ValueError('Manifest changed concurrently; stop without overwrite')
        backup = folder / 'manifest_before_parser_recovery.json'
        if backup.exists():
            if backup.read_bytes() != raw_manifest:
                raise ValueError('Original manifest backup already differs')
        else:
            with backup.open('xb') as stream:
                stream.write(raw_manifest)
        manifest['files'].append(item)
        failure['original_status'] = failure['status']
        failure['status'] = 'resolved_via_strict_parser_representation'
        failure['resolution'] = {'resolved_at': datetime.now().isoformat(), 'analysis_copy_sha256': item['sha256'],
                                 'comment_representation': item['comment_representation'],
                                 'preserved_original_error': failure['error'], 'word_requests': 0, 'svn_requests': 0, 'llm_requests': 0}
        manifest['remaining_versions'] = max(0, manifest.get('remaining_versions', 1) - 1)
        unresolved = [f for f in manifest['failures'] if f.get('status') != 'resolved_via_strict_parser_representation']
        manifest['status'] = 'collected_partial' if unresolved else 'collected'
        manifest['parser_recovery'] = {'status': 'completed', 'original_manifest_backup': str(backup),
                                      'original_manifest_sha256': file_hash(backup), 'word_requests': 0, 'svn_requests': 0, 'llm_requests': 0}
        temporary = manifest_path.with_name('manifest_parser_recovery.tmp')
        _save_new(temporary, manifest)
        if manifest_path.read_bytes() != raw_manifest:
            raise ValueError('Manifest changed while recovery was prepared; stop')
        temporary.replace(manifest_path)
        completed.append({'student_name': manifest['student_name'], 'revision': item['revision'], 'status': 'recovered_verified',
                          'comments': item['comment_count'], 'mapped_comments': representation['mapped_comment_count'],
                          'manifest': str(manifest_path), 'backup_sha256': file_hash(backup)})
    library = get_library(ROOT, refresh=True)
    unique_versions = {}
    for path in EXPORTS.glob('*/job_*/manifest.json'):
        manifest = _read(path)
        for item in manifest.get('files', []):
            if item.get('status') != 'exported':
                continue
            if item.get('artifact_role') == 'analysis_copy_from_legacy_svn_doc':
                validate_lineage(path.parent, item)
            elif file_hash(item['exported_path']) != item['sha256']:
                raise ValueError('Collected evidence source hash mismatch during final count')
            key = (manifest.get('sample_id'), item['revision'], item['repository_path'])
            unique_versions[key] = item
    summary = {'schema_version': 1, 'status': 'parser_evidence_recovered', 'recoveries': completed,
               'eligible_unique_teacher_versions': len(unique_versions), 'eligible_students': len({k[0] for k in unique_versions}),
               'library_summary': library.get('summary'), 'requests': {'word': 0, 'svn': 0, 'llm': 0}}
    report_path = ROOT / 'data/thesis_workflow' / ('parser_recovery_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
    _save_new(report_path, summary)
    summary['report_path'] = str(report_path)
    return summary


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(recover(), ensure_ascii=False, indent=2))
