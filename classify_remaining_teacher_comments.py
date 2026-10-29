"""Resume all pending teacher text in independently audited bounded batches.

Each batch uses at most two model requests and 24,000 reserved tokens by default.
An external/validation failure, unknown cost, or changed corpus stops the run.
No automatic retry and no promotion of review candidates to executable code.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from teacher_rule_library import classify_library, get_library, publish_classification


def save(path, payload):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def run_remaining(root, output_dir, client, *, max_requests=24, max_total_tokens=50000, requests_per_batch=2, reserved_per_batch=24000, model='deepseek-flash'):
    if not 1<=max_requests<=24 or not 1000<=max_total_tokens<=50000 or not 1<=requests_per_batch<=2 or not 1000<=reserved_per_batch<=24000:
        raise ValueError('预算必须在单轮2请求/24000保守token、总24请求/50000token以内')
    root,folder=Path(root),Path(output_dir)
    folder.mkdir(parents=True,exist_ok=False)
    initial=get_library(root,refresh=True)
    fingerprint=initial['source_fingerprint']
    state={'schema_version':1,'status':'prepared','started_at':datetime.now(timezone.utc).isoformat(),'root':str(root),
           'source_fingerprint':fingerprint,'max_requests':max_requests,'max_total_tokens':max_total_tokens,'requests_per_batch':requests_per_batch,
           'reserved_per_batch':reserved_per_batch,'request_attempts':0,'known_total_tokens':0,'unknown_cost_attempts':0,'automatic_retries':0,
           'batches':[],'initial_summary':initial['summary'],'final_summary':initial['summary']}
    save(folder/'run.json',state)
    current=initial
    while current['summary']['unclassified_comments']:
        remaining_requests=max_requests-state['request_attempts']
        remaining_tokens=max_total_tokens-state['known_total_tokens']
        if remaining_requests<=0 or remaining_tokens<1000:
            state['status']='budget_stopped_remaining_pending'
            break
        current=get_library(root,refresh=True)
        if current['source_fingerprint']!=fingerprint:
            state['status']='source_changed_stopped_no_retry'
            break
        batch_dir=folder/f'batch_{len(state["batches"])+1:03d}'
        batch={'result_dir':str(batch_dir),'status':'started'}
        state['batches'].append(batch)
        state['status']='running'
        save(folder/'run.json',state)
        try:
            result=classify_library(current,batch_dir,client,max_requests=min(requests_per_batch,remaining_requests),
                                    max_reserved_tokens=min(reserved_per_batch,remaining_tokens),model=model)
            cost=result['classification_run']
            batch.update(status=cost['status'],request_attempts=cost['request_attempts'],known_total_tokens=cost['known_total_tokens'],
                         unknown_cost_attempts=cost['unknown_cost_attempts'],remaining_comments=len(cost['remaining_comment_ids']))
            state['request_attempts']+=cost['request_attempts']
            state['known_total_tokens']+=cost['known_total_tokens']
            state['unknown_cost_attempts']+=cost['unknown_cost_attempts']
            current=publish_classification(root,result)
            state['final_summary']=current['summary']
            save(folder/'run.json',state)
            print(f'第{len(state["batches"])}批：请求{cost["request_attempts"]}次，已知token {cost["known_total_tokens"]}，尚未处理{current["summary"]["unclassified_comments"]}条；{cost["status"]}',flush=True)
            if cost['status']=='failed_no_retry' or cost['unknown_cost_attempts']:
                state['status']='failed_no_retry'
                break
            if not cost['request_attempts']:
                state['status']='budget_stopped_remaining_pending'
                break
        except Exception as exc:
            # The per-request ledger contains pending-cost evidence if publication
            # or an external action fails. Never include SDK body/key in this log.
            ledger_path=batch_dir/'classification_run.json'
            if ledger_path.is_file() and 'request_attempts' not in batch:
                ledger=json.loads(ledger_path.read_text(encoding='utf-8'))
                for key in ('request_attempts','known_total_tokens','unknown_cost_attempts'):
                    state[key]+=ledger.get(key,0)
                    batch[key]=ledger.get(key,0)
            batch.update(status='failed_no_retry',error_type=type(exc).__name__)
            state.update(status='failed_no_retry',error_type=type(exc).__name__)
            break
    else:
        state['status']='all_teacher_text_processed_review_candidates_only'
    state['finished_at']=datetime.now(timezone.utc).isoformat()
    state['final_summary']=current['summary']
    state['remaining_comments']=current['summary']['unclassified_comments']
    state['failed_sources']=current['coverage'].get('failed_sources',[])
    save(folder/'run.json',state)
    save(folder/'library.json',current)
    return state


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(Path(__file__).resolve().parent))
    parser.add_argument('--output-dir')
    parser.add_argument('--max-requests',type=int,default=24)
    parser.add_argument('--max-total-tokens',type=int,default=50000)
    parser.add_argument('--requests-per-batch',type=int,default=2)
    parser.add_argument('--reserved-per-batch',type=int,default=24000)
    parser.add_argument('--model',default='deepseek-flash')
    args=parser.parse_args()
    key=os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise SystemExit('进程环境中没有可用DEEPSEEK_API_KEY；没有发起模型请求。')
    from openai import OpenAI
    client=OpenAI(api_key=key,base_url='https://api.deepseek.com',timeout=45,max_retries=0)
    root=Path(args.root)
    output=Path(args.output_dir) if args.output_dir else root/'data/teacher_rule_library'/('full_classification_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    finished=run_remaining(root,output,client,max_requests=args.max_requests,max_total_tokens=args.max_total_tokens,
                           requests_per_batch=args.requests_per_batch,reserved_per_batch=args.reserved_per_batch,model=args.model)
    print(json.dumps({k:finished[k] for k in ('status','request_attempts','known_total_tokens','unknown_cost_attempts','remaining_comments','final_summary')},ensure_ascii=False))
    print('审计目录：'+str(output))
