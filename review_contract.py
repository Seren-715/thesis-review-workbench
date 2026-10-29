"""Actual model/prompt/parser identities bound to reusable review receipts."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_PARAMETERS = {'max_tokens':1200,'thinking':{'type':'disabled'},'response_format':'json_object','sdk_retries':0}
PARSE_FILES = ('new_paper_checks.py','paper_locations.py','content_review_context.py',
               'expanded_paper_checks.py','object_reference_checks.py','table_format_checks.py',
               'teacher_content_checks.py','teacher_extended_checks.py','teacher_page_requirements.py',
               'teacher_remaining_layout_checks.py','teacher_remaining_format_checks.py','teacher_object_presence_checks.py',
               'teacher_additional_content_checks.py', 'reference_structure_rules.py',
               'reference_typography_checks.py', 'teacher_semantic_content_checks.py',
               'remaining_teacher_feedback_checks.py', 'general_review_rules.py')
ENGINE_FILES = ('new_paper_agent.py','content_context_agent.py','paper_vision_review.py',
                'model_privacy.py','adaptive_evidence_agent.py','shared_scope_content_queue.py',
                'teacher_example_context.py')


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def review_identity(phase='all'):
    # Runtime strings, rather than manually maintained version labels.
    import new_paper_agent as agent
    import content_context_agent as content
    import paper_vision_review as vision
    prompts = {'text':[agent.EVIDENCE_REVIEW_SYSTEM,agent.REFERENCE_REVIEW_SYSTEM],
               'content':[content.SYSTEM], 'figures':[vision.SYSTEM], 'tools':[agent.SYSTEM]}
    if (ROOT/'adaptive_evidence_agent.py').is_file():
        import adaptive_evidence_agent as adaptive
        prompts['adaptive']=[adaptive.SYSTEM]
    selected = prompts if phase=='all' else {phase:prompts.get(phase,[])}
    return {'version':'review_contract_v1','model':agent.MODEL,
            'model_parameters':MODEL_PARAMETERS,
            'parse_version':digest({p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in PARSE_FILES}),
            'prompt_sha256':digest(selected),'privacy_version':'local_pii_mask_v1',
            'engine_version':digest({p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
                                    for p in ENGINE_FILES if (ROOT/p).is_file()})}


def compatible_prior(report, prior):
    """Production reports require a full contract; legacy results stay in history."""
    current = report.get('review_contract')
    old = prior.get('review_contract') or prior.get('run',{}).get('review_contract')
    run = prior.get('run',{})
    expected = current or review_identity()
    for key in ('model','model_parameters','parse_version','prompt_sha256','engine_version'):
        if key in run and run[key] != expected[key]:return False
    if 'prompt_version' in run:return False
    if current:
        return old == current
    # Low-level callers may have no run. Still reject declared incompatible
    # metadata rather than silently validating old-model results as new ones.
    return old is None or old==expected


def compatible_saved_request(report,request):
    current=report.get('review_contract')
    expected=current or review_identity()
    if 'model' in request and request['model']!=expected['model']:return False
    if not current:return True
    parameters=expected['model_parameters']
    return (request.get('model')==expected['model'] and
            request.get('max_tokens')==parameters['max_tokens'] and
            request.get('extra_body',{}).get('thinking')==parameters['thinking'])
