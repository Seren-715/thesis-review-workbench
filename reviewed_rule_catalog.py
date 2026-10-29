"""Six narrowed P001 policies, source-bound and never universal auto-errors."""
from pathlib import Path
import json
from cross_student_rule_pilot import digest
from extract_comments_pilot import collect_batch
from pipeline_candidates import payload
from paper_pipeline import read, verify_artifacts, code_hashes

BATCH = Path(r'D:\svn_agent\data\pipeline_runs\new_batch_20261007_205126_048522')
CANDIDATE_SHA='96d4fafe8a20e588e782c04189a2a489c79fb0d5cbbd8ee7faa37370dffa2ac5'
SEMANTIC_SHA='afd914b7568a140ab922c187e45dceea50574036a7e39178a4bc08949493efa2'
ACCEPTANCE_SHA='cbcb12b3b2f40ddc585de0083f4a74c892daaf2ccfb187fe41c4244dd13f715b'


def load_catalog(run_dir):
    root=Path(run_dir).resolve()
    if root!=BATCH.resolve(): raise ValueError('Only the explicitly reviewed P001 batch is eligible; new batches need policy review')
    for relative,sha in [('candidates/candidate_rules.json',CANDIDATE_SHA),('semantic_review.json',SEMANTIC_SHA),('integration_verification.json',ACCEPTANCE_SHA)]:
        if digest(root/relative)!=sha: raise ValueError('Pinned policy review changed: '+relative)
    run=read(root/'run.json'); acceptance=read(root/'integration_verification.json')
    if run['status']!='awaiting_rule_and_context_review' or run['code_hashes']!=code_hashes():
        raise ValueError('Accepted pipeline state or old source code changed')
    if digest(root/'config.json')!=run['config_sha256']: raise ValueError('Accepted config changed')
    for step in run['steps']:
        if step['status']!='completed': raise ValueError('Incomplete accepted stage')
        verify_artifacts(step['artifacts'])
    verify_artifacts(acceptance['artifacts'])
    semantic=read(root/'semantic_review.json');verify_artifacts(semantic['artifacts'])
    extracted=read(root/'extract/result.json'); selected=payload(extracted)
    if selected!=read(root/'candidates/selected_evidence.json'): raise ValueError('Policy comment evidence differs')
    exported=read(root/'export/result.json');source=exported['samples'][0]['initial_source']
    if source['sample_id']!='P001' or source['revision']!=194 or source['sha256']!='1def8d253017061149444173cfd84e9355d0042f12e8877073934284e93954c1':
        raise ValueError('Scoped initial source differs')
    definitions=[
      ('PR001','正文第一人称语境','first_person', ['C001','C003'],
       '本轮P001可提取正文范围；排除词义/声明/参考文献/谢辞为本地试验约定，非教师允许证明。',
       '定位我字，分流非代词/范围排除，再读上下文；固定初稿可复用逐项已验收判定。',
       'scoped_clue_execution_with_verified_context_overlay'),
      ('PR002','公式编号文字线索','formula_labels',['C004','C006'],
       '同段OMML制表符公式标签或可提取图片旁标签；全篇编号规范、图片内容和其他布局未确认。',
       '保存标签位置，列出重复/顺序线索；未绑定及图片标签partial，不能推断整篇连续编号正确。',
       'scoped_partial_clue_execution'),
      ('PR003','图编号局部问题','figure_numbering',['C011'],
       '仅教师所指图3-1；正确编号、初稿对应图和命名规范未确认。',
       '独立记录图编号批注，待图身份对齐及编号标准，不执行公式编号规则。','manual_scope_alignment_required'),
      ('PR004','正文局部层次编号','local_list_numbering',['C005','C007','C009'],
       '教师所指段落的（1）/①及（2）局部层次，不推广到全文。',
       '对齐初稿段落与教师选区后核对具体层级，不全局替换编号。','manual_scope_alignment_required'),
      ('PR005','数据图展示与可读性','image_readability',['C010'],
       '所指数据图及明确同类数据图；无绝对图片尺寸、所有图必须截取的数据要求。',
       '按真实版面判断所指图片能否阅读及是否需要展示部分数据；尚无自动阈值。','visual_review_required'),
      ('PR006','表题位于表上方','table_caption_position',['C012'],
       '教师所指表3-1及已确认适用的同类表，XML顺序不证明版面上下关系。',
       '对齐初稿表格身份并核验打印布局中的表题位置；图片表须视觉检查。','visual_review_required'),
    ]
    rules=[]
    for rid,title,checker,refs,scope,method,status in definitions:
        evidence=[]
        for ref in refs:
            original=selected['mapping'][ref]
            evidence.append({'ref':ref,'evidence_id':original['evidence_id'],'source':original['source'],
                             'teacher_comment':original['comment'], 'original_anchor':original['anchor']})
        rules.append({'rule_id':rid,'title':title,'checker':checker,'scope':scope,'check_method':method,
                      'catalog_review_status':status,'supporting_students':['P001'],
                      'recurrence_status':'single_student_scoped_policy','sources':evidence,
                      'automatic_error_judgment_enabled':False,'automatic_rewrite_enabled':False})
    return {'status':'source_bound_scoped_catalog','source_batch':str(root),'initial_source':source,
            'rules':rules,'candidate_sha256':CANDIDATE_SHA,'semantic_review_sha256':SEMANTIC_SHA,
            'acceptance_sha256':ACCEPTANCE_SHA,'unselected_comments':semantic['unselected_text_comments'],
            'limitations':'Six scoped policies, not six proven recurring error types; no cross-student transfer, no automatic errors, no universal chapter/list/number standards.'}
