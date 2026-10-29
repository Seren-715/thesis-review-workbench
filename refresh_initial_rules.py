"""Recheck all collected original initial sources without SVN, Word or model calls."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from new_paper_agent import run_review
from paper_report import render_report
from thesis_workflow import ROOT, save
from legacy_word_snapshot import build_parsed

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def refresh():
    data=ROOT/'data/thesis_workflow'
    jobs=sorted((read(p) for p in (data/'jobs').glob('job_*.json')),key=lambda j:j['id'])
    students={}
    for job in jobs:
        for row in job.get('result_summary',{}).get('students',[]) if isinstance(job.get('result_summary',{}).get('students'),list) else []:
            if row.get('report_json_path') and Path(row['report_json_path']).is_file():students[row['student_id']]=row
    jid='job_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_initial_rules_refreshed'
    folder=data/'runs'/jid;results=[]
    for student,row in students.items():
        before=read(row['report_json_path']);previous=Path(row['report_json_path']).parent
        info=before.get('source_snapshot');parsed=None
        source=Path(info['original_source_path']) if info else Path(row['source_path'])
        if digest(source)!=before['source_sha256']:raise ValueError('初稿源哈希已变化：'+student)
        if info:parsed,info=build_parsed(info['snapshot_path'],source)
        lp=previous/'layout_result.json';layout=read(lp) if lp.is_file() else None
        output=folder/student
        report=run_review(source,output,student_name=row['student_name'],use_agent=False,collect_word=False,
            layout_result=layout,prior_report=before,parsed_source=parsed)
        for key in ('source_role','svn_source','initial_selection','source_snapshot'):
            if key in before:report[key]=before[key]
        report['limitations']=[x for x in report['limitations'] if '上传者' not in x]
        save(output/'report.json',report);(output/'report.md').write_text(render_report(report),encoding='utf-8')
        results.append(dict(row,status='partial',summary=report['summary'],coverage=report['coverage'],
            report_path=str(output/'report.md'),report_json_path=str(output/'report.json')))
    job={'id':jid,'action':'initial-rules-refresh','label':'全部学生初稿 · 当前规则复查','status':'partial',
        'created_at':datetime.now().astimezone().isoformat(),'finished_at':datetime.now().astimezone().isoformat(),
        'result_dir':str(folder),'log_tail':'已对全部已采集初稿复查；源文件、Word分页与既有语义收据哈希核验后复用。未进行模型或视觉核验。',
        'result_summary':{'students':results,'checked':len(results),'failed_or_unavailable':0},
        'llm_requests':0,'known_total_tokens':0,'unknown_cost_attempts':0,'svn_requests':0,'word_launches':0,'reserved_token_bound':0}
    save(data/'jobs'/(jid+'.json'),job);save(folder/'run.json',job)
    (folder/'report.md').write_text('# 全部学生初稿规则复查\n\n'+ '\n'.join(f"- {r['student_name']} r{r['revision']}：程序结构线索{r['summary'].get('structural_candidate_count',0)}；内容待审阅{r['summary'].get('content_review_pending_count',0)}；[报告](<{r['report_path']}>)" for r in results)+'\n\n本轮模型、Word、SVN请求均为0。',encoding='utf-8')
    return {'job_id':jid,'students':len(results),'result_dir':str(folder),'new_requests':0}

if __name__=='__main__':print(json.dumps(refresh(),ensure_ascii=False))
