"""Verified reference clauses; template appearance never becomes a rule."""
import hashlib
from pathlib import Path
from atomic_json import read_json

ROOT=Path(__file__).resolve().parent


def load_reference_standards(root=ROOT):
    root=Path(root).resolve();path=root/'data/reference_standards/catalog.json'
    if not path.is_file():return {'status':'not_registered','requirements':[],'sources':[]}
    data=read_json(path)
    for source in data.get('sources',[]):
        original=Path(source['original_path']);copy=Path(source['source_copy_path']);reader=Path(source['reader_path'])
        if not copy.resolve().is_relative_to(root/'data/reference_standards') or not reader.resolve().is_relative_to(root/'data/reference_standards'):
            raise ValueError('规范材料路径不在已注册目录内')
        if any(hashlib.sha256(p.read_bytes()).hexdigest()!=sha for p,sha in
               [(original,source['source_sha256']),(copy,source['source_sha256']),(reader,source['reader_sha256'])]):
            raise ValueError('规范原件或已注册读取副本已变化')
    sources={s['id']:s for s in data.get('sources',[])}
    for clause in data.get('requirements',[]):
        source=sources.get(clause['source_id'])
        if not source or clause['quote'] not in Path(source['reader_path']).read_text(encoding='utf8'):
            raise ValueError('规范条文没有逐字来源依据')
    return data


def reference_standards_view(root=ROOT):
    """Fail closed for changed materials without aborting unrelated paper checks."""
    try:
        return load_reference_standards(root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'status':'source_review_required', 'title':'参考规范来源需重新核对',
                'requirements':[], 'sources':[],
                'message':'参考规范原件、读取副本或条文对应关系已变化；本次不采用这些参数，其他检查继续执行。',
                'verification_error':str(error)}


def attach_reference_parameters(diagnostic, catalog):
    if catalog.get('status')!='verified_reference_materials':
        for capability in diagnostic.get('capabilities', []):
            if capability.pop('reference_requirements', None):
                capability['standard_status'] = 'reference_verification_required'
        for row in diagnostic.get('coverage', []):
            if row.get('status') == 'reference_standard_available':
                row.update(status='standard_not_specified',
                           detail='已登记的参考标准本次未通过来源核验；需核对材料后再采用参数。')
                row.pop('reference_requirement_ids', None)
        diagnostic['reference_standards'] = catalog
        return diagnostic
    for capability in diagnostic.get('capabilities',[]):
        family=capability['family']
        matched=[r for r in catalog['requirements'] if family in r.get('feedback_families',[])]
        if not matched:continue
        capability.update(reference_requirements=matched,standard_status='explicit_reference_parameters_available',
            limitation='已有逐字参考标准；仍需确认本稿适用、所指对象及完整样式/渲染证据，不自动将历史模糊反馈判错。')
        for row in diagnostic.get('coverage',[]):
            if row['family']==family:
                row.update(status='reference_standard_available',reference_requirement_ids=[r['id'] for r in matched],
                    detail=f'已找到{len(matched)}条明确参考要求，并保留对象属性；规范条文与批注所指对象需对应后再判断，尚未据此自动判错。')
    diagnostic['reference_standards']={'status':catalog['status'],'title':catalog['title'],
        'profile_title':catalog['title'], 'applicability':catalog['applicability'],
        'sources':catalog['sources'], 'requirements':catalog['requirements']}
    return diagnostic


def attach_report_reference_evidence(report, catalog):
    """Bind verified reference quotes to registered readers, never report paths."""
    sources = {s['id']: s for s in catalog.get('sources', [])}
    verified = catalog.get('status') == 'verified_reference_materials'
    for section in ('rules', 'findings'):
        for item in report.get(section, []):
            clauses = item.get('reference_requirements', []) or item.get('reference_evidence', [])
            bound = []
            for index, clause in enumerate(clauses):
                source = sources.get(clause.get('source_id')) if verified else None
                quote = clause.get('quote')
                if not source or not quote or quote not in Path(source['reader_path']).read_text(encoding='utf8'):
                    continue
                bound.append(dict(clause, id=clause.get('id', f"{item.get('rule_id', item.get('id'))}-ref-{index}"),
                    source_title=source['title'], reader_asset_id=source.get('reader_asset_id'),
                    scope=clause.get('scope', item.get('scope', '项目指定参考规范；仅检查已明确的对象与属性。'))))
            item['reference_requirements'] = bound
    return report


def annotate_local_material_candidates(route,catalog):
    if not route:return route
    available=[s for s in catalog.get('sources',[]) if s.get('source_kind')=='teacher_lecture']
    if available and any(d['kind']=='instructional_slides' for d in route['dependencies']):
        route['available_reference_candidates']=[dict(source_id=s['id'],title=s['title']) for s in available]
        route['reason']+=' 工作区已找到论文写作PPT，可作为参考；尚未证明就是该批注所指材料或具体页。'
        route['next_step']+=' 先核对已注册PPT是否为教师所指版本和页次；无法对应时再补充准确材料。'
    return route
