"""Execute a fixed scoped catalog through nanobot read-only tools, no model."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.registry import ToolRegistry,is_tool_error_result
from cross_student_rule_pilot import digest,save
from reviewed_rule_catalog import load_catalog
from check_rules_pilot import read_check_document,check_formula_labels
from triage_initial_first_person_pilot import triage
from verified_context_review_pilot import load_verified_context_review

CONTEXT_ROOT=Path(r'D:\svn_agent\data\svn_exports\cross_student_rules_20261007_121139_462691\reviewed_pilot_20261007_121835_788699')
CODE_NAMES=('scoped_rule_checks.py','reviewed_rule_catalog.py','paper_pipeline_v2.py','check_rules_pilot.py',
            'docx_comment_reader.py','triage_initial_first_person_pilot.py','verified_context_review_pilot.py',
            'inspect_initial_structure_pilot.py','legacy_doc_provenance.py','review_cross_student_pilot.py')


def first_person_result(source,paragraphs):
    clues=triage(paragraphs)
    retained=[r for r in clues['items'] if r['status']=='retained_for_context_review']
    context=load_verified_context_review(CONTEXT_ROOT)
    saved=next(d for d in context['review']['documents'] if d['source']['sample_id']==source['sample_id'])
    if any(saved['source'][k]!=source[k] for k in ('sample_id','revision','sha256')):
        raise ValueError('Context review not valid for this source')
    reviews=saved['reviewed_items']
    if len(retained)!=len(reviews): raise ValueError('Context review coverage differs')
    for a,b in zip(retained,reviews):
        if any(a[k]!=b[k] for k in ('paragraph_index','offset','full_paragraph_text','context')):
            raise ValueError('Context review paragraph identity differs')
    return {'execution_status':'clues_checked_context_receipt_reused','clues':clues,
            'reviewed_items':reviews,'reviewed_change_candidate_count':len(reviews),
            'user_confirmed_count':sum(r['review_origin']=='existing_user_confirmation' for r in reviews),
            'codex_reviewed_count':sum(r['review_origin']=='codex_context_review' for r in reviews),
            'unreviewed_context_count':len(retained)-len(reviews),
            'context_review_sha256':context['review_sha256'],
            'limits':'Reused source-identical prior judgments, not new autonomous semantic adjudication; occurrences not independent errors.'}


@tool_parameters({'type':'object','properties':{},'additionalProperties':False})
class ScopedRuleTool(Tool):
    _plugin_discoverable=False
    def __init__(self,rule,source): self.rule,self.source,self.result=rule,source,None
    @property
    def name(self): return 'check_'+self.rule['rule_id'].lower()
    @property
    def description(self): return self.rule['title']+'；'+self.rule['scope']
    @property
    def read_only(self): return True
    async def execute(self):
        source=self.source;checker=self.rule['checker']
        if digest(Path(source['document_path']))!=source['sha256']: raise ValueError('Student source changed')
        if checker in {'first_person','formula_labels'}:
            document=read_check_document(Path(source['document_path']),source)
            if checker=='first_person': payload=first_person_result(source,document['body_paragraphs'])
            else:
                labels=check_formula_labels(document['body_paragraphs'])
                payload={'execution_status':'partial_clues' if labels['status']=='partial' else 'label_clues_only',
                         'formula_label_evidence':labels,'confirmed_error_count':None,
                         'limits':'Duplicate/sequence flags are clues, not errors; image/unbound labels require visual identity. No global standard inferred.'}
        else:
            payload={'execution_status':self.rule['catalog_review_status'],'confirmed_error_count':None,
                     'pending_requirements':[self.rule['scope'],self.rule['check_method']],
                     'limits':'Registry execution returned a review task, not a document check or pass verdict.'}
        self.result={'rule_id':self.rule['rule_id'],'source':source,
                     'catalog_review_status':self.rule['catalog_review_status'],
                     'automatic_error_judgment_enabled':False,'result':payload}
        if digest(Path(source['document_path']))!=source['sha256']: raise ValueError('Student source changed during check')
        return json.dumps(self.result,ensure_ascii=False)


async def execute(run_dir):
    root=Path(run_dir).resolve();catalog=load_catalog(root)
    code={n:digest(Path(__file__).with_name(n)) for n in CODE_NAMES}
    output=root/('scoped_rule_check_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'));output.mkdir(exist_ok=False)
    save(output/'reviewed_catalog.json',catalog)
    state={'status':'prepared','framework':'nanobot_Tool_ToolRegistry_local_readonly_execution',
           'model_requests':0,'new_tokens':0,'svn_requests':0,'word_launches':0,
           'code_hashes':code,'tool_attempts':[],'automatic_error_judgment_enabled':False,
           'local_verification_rechecks':0,
           'independent_accuracy_test':False,'note':'Explicit local tool sequence; no LLM-selected actions or new semantic judgments this run.'}
    save(output/'run.json',state)
    registry=ToolRegistry();owned=[]
    for rule in catalog['rules']:
        tool=ScopedRuleTool(rule,catalog['initial_source']);registry.register(tool);owned.append(tool)
    try:
        results=[]
        for tool in owned:
            attempt={'name':tool.name,'status':'started'};state['tool_attempts'].append(attempt);save(output/'run.json',state)
            value=await registry.execute(tool.name,{})
            if is_tool_error_result(value) or tool.result is None: raise ValueError('Scoped tool failed: '+tool.name)
            results.append(tool.result);save(output/(tool.rule['rule_id']+'_evidence.json'),tool.result)
            attempt['status']='completed';save(output/'run.json',state)
            print(tool.rule['rule_id']+'：'+tool.result['result']['execution_status'],flush=True)
        if load_catalog(root)!=catalog or {n:digest(Path(__file__).with_name(n)) for n in CODE_NAMES}!=code:
            raise ValueError('Scoped policy/source/code changed before finalization')
        # Refresh every executed evidence item before asserting result consistency.
        for tool,saved in zip(owned,results):
            await tool.execute()
            state['local_verification_rechecks']+=1
            if tool.result!=saved: raise ValueError('Scoped evidence changed before finalization')
        save(output/'checks.json',{'source':catalog['initial_source'],'rules':results,
                                 'full_coverage_verified':False,'automatic_error_judgment_enabled':False})
        first,formula=results[0]['result'],results[1]['result']['formula_label_evidence']
        lines=['# 六项范围明确的检查结果','','本轮按固定顺序执行nanobot只读工具；没有新增模型、SVN或Word请求。保留原候选及既有报告。各工具完成后另做六次本地证据一致性复读，次数记录在run.json。','',
               f'PR001：我字线索{first["clues"]["raw_character_hits"]}；范围/词义排除{first["clues"]["excluded_count"]}；已复核待改{first["reviewed_change_candidate_count"]}（用户确认{first["user_confirmed_count"]}、Codex复核{first["codex_reviewed_count"]}）。',
               f'PR002：{formula["status"]}；同段OMML标签{formula["formula_label_count"]}；图片编号线索{formula["image_label_count"]}；未绑定编号{formula["unbound_label_count"]}；提取编号文字{formula["text_label_count"]}，文字重复组{formula["text_duplicate_group_count"]}。',
               '公式图片仍待视觉核验；未检出重复不代表公式已全部检查或编号全部正确。','',
               '| 项目 | 状态 |','|---|---|']
        lines += [f'| {r["rule_id"]} {rule["title"]} | {r["result"]["execution_status"]} |' for r,rule in zip(results,catalog['rules'])]
        lines += ['','PR003/PR004等待初稿与教师选区身份对齐及局部编号条件；PR005/PR006等待真实版式视觉核验。未核验项不写零错误或通过。',
                  '未选续表和谢辞相关批注仍保存于完整提取证据，六项不代表全部批注覆盖；章节问题也不由本目录判定。谢辞是否重复尚未核定。',
                  '固定P001回查，不是跨学生常犯或独立准确率证明；本轮未改论文，不自动启用语义判错或改写。','',
                  f'[规则、教师批注和完整原选区](<{(output/"reviewed_catalog.json").as_posix()}>)',
                  f'[逐项结果和原文位置](<{(output/"checks.json").as_posix()}>)']
        (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
        state['status']='scoped_checks_completed_review_pending'
    except Exception as error:
        state.update(status='failed_no_retry',error_type=type(error).__name__)
        if isinstance(error,ValueError):state['validation_error']=str(error)
    save(output/'run.json',state)
    return output,state
