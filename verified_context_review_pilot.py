"""Pinned context-review reader, not a semantic classifier; no API/Word/SVN."""
import json
from pathlib import Path
from collections import Counter
from cross_student_rule_pilot import digest
from triage_initial_first_person_pilot import collect_triage

RELATIVE = Path('first_person_context_review_20261007_202622_417418')
REVIEW_SHA = 'f1a94cd066d56a8b3defa7eeeeedb92e25f20b939f6d1b803b572f550bffa403'
VERIFICATION_SHA = '521f83e853c6e27ba9e43a12e574a11ae45ee16c833487136cab7045537307d9'
USER_KEYS = {('P001',116,100), ('P002',680,13), ('P003',124,67)}


def load_verified_context_review(review_dir):
    folder = Path(review_dir).resolve() / RELATIVE
    for name, sha in [('review.json', REVIEW_SHA), ('verification.json', VERIFICATION_SHA)]:
        if digest(folder/name) != sha:
            raise ValueError('Pinned context review changed: '+name)
    data=json.loads((folder/'review.json').read_text(encoding='utf-8'))
    proof=json.loads((folder/'verification.json').read_text(encoding='utf-8'))
    if data['status']!='source_bound_codex_context_review' or data['automated_error_judgment_enabled'] is not False:
        raise ValueError('Unexpected context acceptance')
    if proof['status']!='verified_source_bound_context_review' or proof['independent_semantic_review_agrees'] is not True:
        raise ValueError('Missing independent context verification')
    for artifact in proof['artifacts']+[data['original_worksheet']]:
        if digest(Path(artifact['path']))!=artifact['sha256']:
            raise ValueError('Context artifact changed: '+artifact['path'])
    for teacher in data['teacher_evidence']:
        source=teacher['source']
        if digest(Path(source['document_path']))!=source['sha256']:
            raise ValueError('Context teacher source changed')
    fresh=collect_triage(Path(review_dir))
    if len(fresh)!=3 or len(data['documents'])!=3:
        raise ValueError('Three context sources required')
    summaries=[]
    for current,saved in zip(fresh,data['documents']):
        source=current['source']; triage=current['triage']
        if source!=saved['source']:
            raise ValueError('Context source identity differs')
        candidates=[r for r in triage['items'] if r['status']=='retained_for_context_review']
        reviewed=saved['reviewed_items']
        if len(candidates)!=len(reviewed) or saved['excluded_items']!=[r for r in triage['items'] if r['status']!='retained_for_context_review']:
            raise ValueError('Context occurrence/exclusion coverage differs')
        for candidate,record in zip(candidates,reviewed):
            # The recorded reason is the reviewed justification; the scanner's
            # reason only described the earlier pending state. Identity/text
            # and all other original fields must still match exactly.
            if any(record[k]!=v for k,v in candidate.items() if k!='reason'):
                raise ValueError('Context paragraph, offset or text differs')
            key=(source['sample_id'],record['paragraph_index'],record['offset'])
            expected='existing_user_confirmation' if key in USER_KEYS else 'codex_context_review'
            if record['review_origin']!=expected or record['automated_error_judgment_enabled'] is not False:
                raise ValueError('Review attribution differs')
            if record['verdict']!='change_candidate_under_pilot_teacher_policy':
                raise ValueError('Unexpected policy decision')
        counts=Counter(r['review_origin'] for r in reviewed)
        summaries.append({k:source[k] for k in ('sample_id','revision','sha256')} |
             {'raw_wo_count':triage['raw_character_hits'], 'excluded_count':triage['excluded_count'],
              'retained_context_count':len(candidates), 'reviewed_change_candidate_count':len(reviewed),
              'user_confirmed_count':counts['existing_user_confirmation'],
              'codex_reviewed_count':counts['codex_context_review'],
              'unreviewed_context_count':len(candidates)-len(reviewed)})
    if sum(s['user_confirmed_count'] for s in summaries)!=3 or sum(s['codex_reviewed_count'] for s in summaries)!=27:
        raise ValueError('Context aggregate attribution differs')
    return {'items':summaries, 'review':data, 'verification':proof,
            'review_sha256':REVIEW_SHA, 'verification_sha256':VERIFICATION_SHA,
            'limits':'Source-bound prior context review, not new semantic judgment by this deployed agent. 3 user confirmations and27 Codex reviews; change candidates under pilot teacher policy, not universal errors or independent ground truth. P002 uses transferred pilot policy.'}
