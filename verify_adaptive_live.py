"""Explicit opt-in real API smoke, synthetic material; never an accuracy test."""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

from atomic_json import write_json


def run(output):
    from docx import Document
    from openai import OpenAI
    from new_paper_checks import read_docx,extract_paragraphs
    from paper_locations import annotate_paragraphs
    from new_paper_agent import configured_budget,SemanticReviewQueue,save
    from adaptive_evidence_agent import propose_next_action
    from review_contract import review_identity
    output.mkdir(parents=True,exist_ok=False)
    source=output/'synthetic_context.docx'
    doc=Document();doc.add_heading('系统角色提示词展示',1)
    doc.add_paragraph('我是Linux教师，会为实验提供提示。')
    doc.add_paragraph('上述“我”是系统向大模型发送的角色指令，不是论文作者自述。')
    doc.save(source)
    document,styles,sha,_=read_docx(source)
    paragraphs=annotate_paragraphs(extract_paragraphs(document,styles))
    paragraph=next(p for p in paragraphs if p['text'].startswith('我是Linux'))
    f={'id':'NP001-smoke','rule_id':'NP001','status':'candidate','title':'合成角色表述',
       'reason':'合成取证演示，不是学生论文问题','suggestion':'','teacher_evidence':[],
       'paragraph_index':paragraph['index'],'text':paragraph['text'],'start':0,'end':1,'context':[],
       'semantic_review':{'decision':'needs_review','validated_quote':True,
          'quote':paragraph['text'],'reason':'合成预置缺口：片段不足，需读取后续说明判断角色指令还是作者自述。',
          'suggestion':'结合下文判断。','origin':'synthetic_fixture_not_previous_model_judgment'}}
    report={'source_sha256':sha,'student_name':'合成验收','file_name':source.name,'findings':[f],
            'rules':[{'id':'NP001','scope':'仅合成演示：作者叙述我/我们需核查；角色指令例子不判错','teacher_evidence':[]}],
            'coverage':[],'limitations':['此材料是合成上下文缺口，不是学生真值或模型质量试验。'],
            'paragraphs':paragraphs,'review_contract':review_identity()}
    state={'source_sha256':sha,'mode':'synthetic_real_api_functional_smoke','model':review_identity()['model'],
           'requests':[],'request_attempts':0,'dispatched_requests':0,'known_total_tokens':0,
           'unknown_cost_attempts':0,'reserved_token_bound':0,'tool_calls':[],
           'max_requests':2,'max_reserved_token_bound':24000,'review_scope':'full',
           'review_contract':review_identity(),'independent_accuracy_test':False}
    key=os.environ.get('DEEPSEEK_API_KEY')
    if not key:raise ValueError('DEEPSEEK_API_KEY missing; no request sent')
    client=OpenAI(api_key=key,base_url='https://api.deepseek.com',timeout=45,max_retries=0)
    budget=configured_budget(client,output,state,report)
    action=propose_next_action(report,state,budget,pending_phases=('text',))
    save(output/'report.json',report);save(output/'run.json',state)
    if action['augmented_finding_ids']:
        queue=SemanticReviewQueue(report,state,output)
        if queue.has_pending():queue.step(budget)
    source_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==sha
    accepted=f.get('semantic_review',{})
    rejudged=(accepted.get('validated_quote') is True
              and accepted.get('origin')=='precise_evidence_v1'
              and bool(accepted.get('context_key'))
              and any(r.get('plan',{}).get('phase')=='text'
                      and r.get('status')=='response_received'
                      and r.get('finish_reason')=='stop' for r in state['requests']))
    status='live_context_read_and_rejudgment_verified' if action['augmented_finding_ids'] and rejudged and source_unchanged else 'functional_smoke_incomplete'
    state.update(status=status,finished_at=datetime.now().astimezone().isoformat())
    report['run']=deepcopy(state)
    save(output/'run.json',state);save(output/'report.json',report)
    receipt={'status':status,'source_unchanged':source_unchanged,'source_sha256':sha,
             'teacher_comments_used':False,'independent_accuracy_test':False,
             'llm_requests':state['dispatched_requests'],'known_tokens':state['known_total_tokens'],
             'unknown_cost_attempts':state['unknown_cost_attempts'],'decision':accepted.get('decision'),
             'validated_rejudgment':rejudged,
             'supplement_paragraphs':[p['index'] for p in f.get('adaptive_context',{}).get('paragraphs',[])],
             'scope_note':'预置合成needs_review；模型真实调用只读工具并追加读取后判断。不评价学生准确率。'}
    write_json(output/'acceptance.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.output)
