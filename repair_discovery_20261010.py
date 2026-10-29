"""Install audited discovery and quarantine the omitted student's SVN versions.

Exports only; no comments are read or used for rule/prompt development here.
"""
from datetime import datetime
import json
from pathlib import Path
import shutil

from atomic_json import read_json,write_json
from thesis_workflow import ThesisWorkflow,digest,now

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/audit_20261010'


def main():
    proposed=OUT/'discovery_complete_offline.json'
    receipt=read_json(OUT/'discovery_repair_receipt.json')
    if digest(proposed)!=receipt['discovery_sha256']:raise ValueError('Audited discovery changed')
    d=read_json(proposed)
    flow=ThesisWorkflow()
    if flow.active():raise ValueError('Workflow is active; discovery not changed')
    for key,hashkey in [('path','sha256'),('info_path','info_sha256')]:
        if digest(d['history'][key])!=d['history'][hashkey]:raise ValueError('Cached history changed')
    old=flow.data/'discovery.json'
    backup=OUT/'discovery_before_repair.json'
    if not backup.exists():shutil.copyfile(old,backup)
    write_json(old,d)
    student=next(s for s in d['students'] if s['id']=='212003242_安娜羽')
    folder=flow.data/'quarantine'/student['id']/'audit_20261010'
    folder.mkdir(parents=True,exist_ok=True)
    manifest_path=folder/'manifest.json'
    if manifest_path.exists():
        m=read_json(manifest_path)
    else:
        m={'student_id':student['id'],'student_name':student['name'],'files':[],
           'teacher_comments_opened':False,'eligible_for_library':False,
           'role':'omitted_student_holdout_candidate_not_human_signed',
           'independence_status':'requires_exposure_attestation_and_human_truth',
           'created_at':now(),'llm_requests':0,'word_launches':0,'svn_requests':0}
    jid='job_audit_20261010_quarantine_'+datetime.now().strftime('%H%M%S_%f')
    job={'id':jid,'created_at':now(),'action':'quarantine_export','label':'隔离导出漏采历史原件',
         'owner_pid':__import__('os').getpid(),'status':'running','svn_requests':0,'log_tail':''}
    versions=[('initial',student['initial'])]+[('teacher_quarantined',v) for doc in student['documents'] for v in doc['teacher_versions']]
    for i,(role,v) in enumerate(versions):
        previous=next((f for f in m['files'] if (f['revision'],f['repository_path'])==(v['revision'],v['repository_path'])),None)
        if previous:
            if digest(previous['exported_path'])!=previous['sha256']:raise ValueError('Quarantined source changed')
            continue
        destination=folder/f'{student["name"]}_{role}_r{v["revision"]}_{i+1:02d}{Path(v["file_name"]).suffix}'
        try:
            exported=flow._export(job,d,v,destination)
            exported['artifact_role']=role;m['files'].append(exported)
            m['svn_requests']+=1;write_json(manifest_path,m)
        except Exception as error:
            m['last_failure']={'role':role,'revision':v['revision'],'detail':str(error)}
            m['svn_requests']+=job['svn_requests']-m.get('requests_this_job',0)
            m['requests_this_job']=job['svn_requests'];write_json(manifest_path,m)
            job.update(status='failed',finished_at=now());flow._persist(job)
            raise
    job.update(status='completed',finished_at=now());flow._persist(job)
    m.update(status='quarantined_historical_sources_exported',manifest_sha_note='SHA stored per original')
    write_json(manifest_path,m)
    write_json(OUT/'discovery_install_receipt.json',{'students':d['total_students'],
        'teacher_versions':sum(s['teacher_version_count'] for s in d['students']),
        'installed_sha256':digest(old),'previous_discovery_path':str(backup),
        'previous_discovery_sha256':digest(backup),'quarantine_manifest':str(manifest_path),
        'exported_files':len(m['files']),'svn_requests_this_run':job['svn_requests'],
        'teacher_comments_opened':False,'llm_requests':0,'word_launches':0})
    print(json.dumps({'students':d['total_students'],'quarantined_sources':len(m['files']),
        'manifest':str(manifest_path),'svn_requests':job['svn_requests']},ensure_ascii=False))


if __name__=='__main__':main()
