"""Revalidate a saved response under the explicit current quote-length contract.

No API request or automatic retry. Prior failure, source and response stay intact.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from new_paper_agent import save, validate_decisions
from paper_report import render_report

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def recover(paper_dir, number):
    paper_dir=Path(paper_dir).resolve()
    metadata=read(paper_dir/'paper.json')
    previous=paper_dir/metadata['review_id']
    report=read(previous/'report.json')
    if sha(paper_dir/'source.docx')!=report['source_sha256']:
        raise ValueError('原稿哈希变化，不能复算。')
    request_path=previous/f'request_{number:02d}.json'
    response_path=previous/f'response_{number:02d}.json'
    request,response=read(request_path),read(response_path)
    items=json.loads(request['messages'][1]['content'])['items']
    choices=response['choices']
    if len(choices)!=1 or choices[0]['finish_reason']!='stop':raise ValueError('保存响应没有正常结束。')
    by_id={f['id']:f for f in report['findings']}
    selected=[]
    for item in items:
        finding=by_id[item['id']]
        if finding['text']!=item['text'] or finding['rule_id']!=item['rule_id']:
            raise ValueError('保存请求与原文对象不匹配。')
        selected.append(finding)
    accepted=validate_decisions(choices[0]['message']['content'],selected)
    rid='review_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_saved_response_revalidated'
    output=paper_dir/rid;output.mkdir()
    for finding in selected:
        finding['semantic_review']=dict(accepted[finding['id']],revalidated_response_path=str(response_path),
                                       revalidated_response_sha256=sha(response_path))
    oldrun=report['run']
    report['run']=dict(oldrun,status='partial',request_attempts=0,known_total_tokens=0,
        unknown_cost_attempts=0,requests=[],tool_calls=[],reserved_token_bound=0,word_launches=0,
        semantic_evidence_reused=sum(bool(f.get('semantic_review',{}).get('validated_quote')) for f in report['findings']),
        semantic_reviewed=0,semantic_unreviewed=0,recovery_from_run=str(previous/'run.json'),
        recovery_run_sha256=sha(previous/'run.json'),revalidated_request_sha256=sha(request_path))
    report['run'].pop('error',None)
    for key,decision in [('agent_suggested_count','suggested_revision'),('agent_excluded_count','not_issue')]:
        report['summary'][key]=sum(f.get('semantic_review',{}).get('decision')==decision for f in report['findings'])
    report['summary']['content_review_pending_count']=sum(bool(f.get('semantic_required')) and f.get('semantic_review',{}).get('decision') not in {'suggested_revision','not_issue'} for f in report['findings'])
    report['requests'].update(llm=0,word=0)
    report['limitations']=[x for x in report['limitations'] if '模型引文与原文不一致' not in x]
    report['limitations'].append('保存响应按当前600字引文上限重新验证；原文、请求、响应与旧失败账本未修改，新增LLM请求为0。')
    if (previous/'layout_result.json').is_file():shutil.copyfile(previous/'layout_result.json',output/'layout_result.json')
    for filename,value in [('report.json',report),('run.json',report['run'])]:save(output/filename,value)
    (output/'report.md').write_text(render_report(report),encoding='utf-8')
    metadata.update(review_id=rid,status='completed',result_status='partial')
    save(paper_dir/'paper.json',metadata)
    return {'review_id':rid,'summary':report['summary'],'reused':report['run']['semantic_evidence_reused'],'new_requests':0}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paper-dir',required=True);parser.add_argument('--response-number',type=int,required=True)
    args=parser.parse_args()
    print(json.dumps(recover(args.paper_dir,args.response_number),ensure_ascii=False))
