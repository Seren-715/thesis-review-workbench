"""Execute saved workflow batches with one shared conservative round budget."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import time
import urllib.request
from atomic_json import read_json,write_json

BASE='http://127.0.0.1:8787'


def get(endpoint):
    return json.load(urllib.request.urlopen(BASE+'/api/'+endpoint,timeout=60))


def submit(endpoint,options):
    token=get('workspace')['session_token']
    request=urllib.request.Request(BASE+'/api/'+endpoint,
        data=json.dumps(options).encode(),headers={'Content-Type':'application/json',
        'Origin':BASE,'X-Session-Token':token},method='POST')
    return json.load(urllib.request.urlopen(request,timeout=60))['job']


def wait_job(job,output):
    started=time.monotonic(); previous=None
    while time.monotonic()-started<3600:
        state=get('thesis-workflow')
        current=next(j for j in state['jobs'] if j['id']==job['id'])
        write_json(output/(job['id']+'.json'),current)
        marker=(current['status'],current.get('log_tail','').splitlines()[-1:] or [''])
        if marker!=previous:
            print(json.dumps({'job_id':job['id'],'status':current['status'],'latest':marker[1]},ensure_ascii=False),flush=True)
            previous=marker
        if current['status'] not in {'queued','running'}:
            return current
        time.sleep(3)
    raise TimeoutError('任务仍在执行，保持任务及已保存进度；未重复提交。')


def record_budget(job,output):
    path=output/'round_budget.json'; budget=read_json(path)
    if job['id'] in budget['jobs']:return
    reserve=job.get('reserved_token_bound',0)
    if type(reserve) is not int or reserve<0:raise ValueError('预算记录无效')
    budget['reserved_used']+=reserve
    budget['known_tokens']+=job.get('known_total_tokens',0)
    budget['unknown_cost_attempts']+=job.get('unknown_cost_attempts',0)
    budget['jobs'].append(job['id']); budget['remaining']=max(0,budget['ceiling']-budget['reserved_used'])
    budget['updated_at']=datetime.now().astimezone().isoformat()
    write_json(path,budget)
    if budget['reserved_used']>budget['ceiling']:raise ValueError('共同预留上限异常；停止后续模型请求。')


def run(output,local_all=False,student=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if local_all:
        discovery=get('thesis-workflow')['discovery']
        ids=[s['id'] for s in discovery['students'] if s.get('initial')]
        for start in range(0,len(ids),10):
            options=dict(student_ids=ids[start:start+10],use_agent=False,chapter_policy=False,max_reserved_tokens=0)
            job=wait_job(submit('thesis-workflow/check-initials',options),output)
            record_budget(job,output)
            if job.get('llm_requests',0):raise ValueError('本地阶段出现模型请求，停止。')
            if job['status']=='failed':raise ValueError('本地批次执行失败，见运行记录。')
    if student:
        budget=read_json(output/'round_budget.json')
        if budget['unknown_cost_attempts'] or not budget['remaining']:
            raise ValueError('共同预算不可继续使用，未提交模型请求。')
        options=dict(student_ids=[student],use_agent=True,chapter_policy=False,
                     review_scope='content',max_reserved_tokens=budget['remaining'])
        job=wait_job(submit('thesis-workflow/check-initials',options),output)
        record_budget(job,output)
    final=get('task-coverage');write_json(output/'task_coverage_after.json',final)
    print(json.dumps({'rules':final['rules']['summary'],'initials':final['initials']['summary'],
                      'budget':read_json(output/'round_budget.json')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--local-all',action='store_true')
    parser.add_argument('--student')
    args=parser.parse_args();run(args.output,args.local_all,args.student)
