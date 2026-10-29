"""Recover an already-exported initial DOC through its preserved source snapshot."""
from datetime import datetime
import json
from pathlib import Path
from initial_text_fallback import recover, digest
from new_paper_agent import run_review
from paper_report import render_report
from thesis_workflow import save, ROOT

def run():
    data=ROOT/'data/thesis_workflow'
    folder=data/'initial_checks/212008213_胡力岩/job_20261008_191054_398335_35b5cd'
    original=folder/'胡力岩_初稿候选_r195.doc'
    parsed,info=recover(original,folder)
    jid='job_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_original_doc_text_recovery'
    output=folder/jid
    report=run_review(original,output,student_name='胡力岩',use_agent=False,collect_word=False,parsed_source=parsed)
    report.update(source_snapshot=info,source_role='svn_history_initial_candidate_original_doc_text_snapshot')
    report['limitations']=[x for x in report['limitations'] if '上传者' not in x]
    report['limitations'].extend(info['limitations'])
    save(output/'report.json',report)
    (output/'report.md').write_text(render_report(report),encoding='utf-8')
    row={'student_id':'212008213_胡力岩','student_name':'胡力岩','revision':195,'status':'partial',
         'summary':report['summary'],'coverage':report['coverage'],
         'source_path':info['source_reader_path'],'source_sha256':info['source_reader_sha256'],
         'report_path':str(output/'report.md'),'report_json_path':str(output/'report.json')}
    job={'id':jid,'action':'original-doc-text-recovery','label':'旧DOC初稿文字恢复','status':'partial',
         'created_at':datetime.now().astimezone().isoformat(),'finished_at':datetime.now().astimezone().isoformat(),
         'log_tail':'源DOC只读文字快照严格核验后完成文字检查；转换失败记录保留，图表与分页未完成。',
         'result_dir':str(data/'runs'/jid),'result_summary':{'students':[row],'checked':1,'failed_or_unavailable':0},
         'previous_job':'job_20261008_191054_398335_35b5cd','llm_requests':0,'known_total_tokens':0,
         'unknown_cost_attempts':0,'svn_requests':0,'word_launches':0,'reserved_token_bound':0}
    save(data/'jobs'/(jid+'.json'),job);save(data/'runs'/jid/'run.json',job)
    (data/'runs'/jid/'report.md').write_text('# 原DOC初稿文字恢复\n\n'+render_report(report),encoding='utf-8')
    return {'id':jid,'new_requests':0,'summary':report['summary']}

if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False))
