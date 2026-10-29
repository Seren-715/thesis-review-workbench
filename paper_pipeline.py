"""Unified entry: accepted replay or configurable small-batch acquisition.

New candidate rules require review; this runner never silently promotes them.
"""
import argparse
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
from cross_student_rule_pilot import digest

ROOT=Path(__file__).resolve().parent
RUNS=ROOT/'data'/'pipeline_runs'
REVIEW=ROOT/'data'/'svn_exports'/'cross_student_rules_20261007_121139_462691'/'reviewed_pilot_20261007_121835_788699'
ACCEPTED='three_rule_agent_v3_20261007_203354_631957'
CODE_NAMES=['paper_pipeline.py','pipeline_sources.py','pipeline_candidates.py','extract_comments_pilot.py',
            'docx_comment_reader.py','triage_initial_first_person_pilot.py','inspect_initial_structure_pilot.py',
            'comment_evidence_policy.py','pilot_rule_analysis.py','cross_student_rule_pilot.py',
            'verified_context_review_pilot.py','verified_initial_review_pilot.py','verified_table_pilot.py',
            'check_remaining_initial_layout_pilot.py','legacy_doc_provenance.py','review_cross_student_pilot.py']


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    path=Path(path); temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def code_hashes(): return {n:digest(ROOT/n) for n in CODE_NAMES}


def inventory(folder):
    return [{'path':str(p.resolve()),'sha256':digest(p)} for p in sorted(folder.rglob('*')) if p.is_file()]


def verify_artifacts(rows):
    for row in rows:
        if digest(Path(row['path']))!=row['sha256']:
            raise ValueError('Completed stage artifact changed: '+row['path'])


def new_run(mode, config=None):
    folder=RUNS/(mode+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True,exist_ok=False)
    state={'schema_version':1,'mode':mode,'status':'prepared','steps':[],
           'code_hashes':code_hashes(),'automatic_retries':0,'independent_accuracy_test':False,
           'project_api_request_cap':0 if mode=='replay' else 1,
           'new_api_requests':0,'new_known_tokens':0,'unknown_cost_attempts':0,'word_launches':0,
           'rules_automatically_activated':False}
    if config is not None:
        save(folder/'config.json',config); state['config_sha256']=digest(folder/'config.json')
    save(folder/'run.json',state)
    return folder,state


def replay():
    # Verify the actual frozen run against fresh local tools, without API.
    import asyncio
    import three_rule_pilot_agent_v3 as agent
    accepted=REVIEW/ACCEPTED
    proof=read(accepted/'integration_verification.json')
    if proof['status']!='verified_real_v3_model_integration': raise ValueError('Accepted v3 receipt missing')
    verify_artifacts(proof['artifacts'])
    old=read(accepted/'run.json')
    for name,sha in old['code_hashes'].items():
        if digest(ROOT/name)!=sha: raise ValueError('Accepted agent code changed')
    tools={n:(agent.VerifiedTableTool(REVIEW,REVIEW/agent.RECEIPT_RELATIVE) if n=='load_verified_table_pagination'
              else agent.BatchTool(n,REVIEW)) for n in agent.NAMES}
    async def load():
        for tool in tools.values(): await tool.execute()
    asyncio.run(load())
    final=read(accepted/'final_report.json')
    expected={'items':agent.expected_rows(tools),'teacher_reference':tools['load_verified_table_pagination'].teacher_reference}
    if json.dumps(final,sort_keys=True)!=json.dumps(expected,sort_keys=True):
        raise ValueError('Accepted final differs from current evidence')
    for name,tool in tools.items():
        if read(accepted/(name+'_evidence.json'))!=tool.full_result:
            raise ValueError('Accepted full tool evidence differs')
    folder,state=new_run('replay')
    save(folder/'verified_report.json',final)
    links={'accepted_agent_run':str(accepted),'reviewed_rules':str(REVIEW/'reviewed_rules.json'),
           'context_review':str(REVIEW/'first_person_context_review_20261007_202622_417418'),
           'visual_review':str(REVIEW/'remaining_initial_layout_20261007_194115_597974'/'visual_review'),
           'source_lineage':'Full verified tools and acceptance receipts remain at linked original paths; no regenerated SVN origin.'}
    save(folder/'evidence_index.json',links)
    lines=['# 三学生小批次统一交付报告','','本入口复用并核验既有导出、批注、规则、语境和分页证据；本轮未重新导出SVN、启动Word或请求模型。','',
           '| 学生 | 版本 | 已复核第一人称待改 | 用户确认 | Codex复核 | 缺续表 |','|---|---:|---:|---:|---:|---:|']
    for row in final['items']:
        lines.append(f'| {row["sample_id"]} | {row["revision"]} | {row["reviewed_change_candidate_count"]} | {row["user_confirmed_count"]} | {row["codex_reviewed_count"]} | {row["confirmed_table_issue_count"]} |')
    lines+=['','30次第一人称命中涉及23段落，按本轮教师要求作待改判定；不代表通用禁令或独立准确率。',
            'P002该项依据跨学生试验政策及既有用户范围确认，非其教师直接第一人称批注。',
            '章节：P002/P003已有人工作出的分章问题判定，详见原核查表；智能体当前仍报告大纲证据。P001可见五章，但分章规则适用范围未裁决。',
            'P003学生r246两张原生表单页；教师r249缺续表另列，不能当成初稿错误或后续修复。图片/脚注、其他批注和新样本覆盖仍有限。','',
            f'[已验收智能体报告](<{(accepted/"report.md").as_posix()}>)',
            f'[逐项语境复核](<{(REVIEW/"first_person_context_review_20261007_202622_417418"/"核查表_语境复核副本.md").as_posix()}>)',
            f'[规则与教师批注依据](<{(REVIEW/"reviewed_rules.json").as_posix()}>)',
            f'[章节人工记录](<{(ROOT/"deliverables"/"三学生初稿人工核查表.md").as_posix()}>)','',
            '本轮新增项目API用量0；链接的历史第三版实际用量3020 token，未计入本轮新增成本。',
            '这是固定三学生的阶段交付，不是整仓库任务完成。新样本流程通过run命令配置采集，候选规则需复核后才能启用。']
    (folder/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    state.update(status='completed_verified_replay',new_api_requests=0,new_known_tokens=0,
                 svn_requests=0,word_launches=0,reused_historical_agent_tokens=old['known_total_tokens'])
    state['artifacts']=inventory(folder)
    state['artifacts']=[r for r in state['artifacts'] if Path(r['path']).name!='run.json']
    save(folder/'run.json',state)
    return folder,state


def run_pipeline(folder,state,client_factory=None):
    from pipeline_sources import validate_config,export_samples,extract_samples,check_samples
    from pipeline_candidates import classify,payload
    if code_hashes()!=state['code_hashes']: raise ValueError('Pipeline code changed; resume refused')
    if digest(folder/'config.json')!=state['config_sha256']: raise ValueError('Pipeline config changed')
    config=validate_config(read(folder/'config.json'))
    previous={r['name']:r for r in state['steps']}
    for row in previous.values():
        if row['status']!='completed':
            raise ValueError('Interrupted/failed stage requires explicit recovery; no automatic retry')
        verify_artifacts(row['artifacts'])
    values={}
    operations={
      'export':lambda destination:export_samples(config,destination),
      'extract':lambda destination:extract_samples(values['export'],destination),
      'candidates':lambda destination:candidate_stage(values['extract'],destination,client_factory or make_client),
      'check':lambda destination:check_samples(values['extract'],values['export'],destination),
    }
    for name,operation in operations.items():
        if name in previous:
            values[name]=read(folder/name/'result.json'); continue
        destination=folder/name; destination.mkdir(exist_ok=False)
        step={'name':name,'status':'running','artifacts':[]};state['steps'].append(step)
        state['status']='running';save(folder/'run.json',state)
        print('执行阶段：',name,flush=True)
        try:
            result=operation(destination)
            if name=='candidates' and result['status']=='failed_no_retry':
                raise ValueError('Candidate request stopped; see candidates/run.json; no retry')
            save(destination/'result.json',result)
            if code_hashes()!=state['code_hashes']: raise ValueError('Pipeline code changed during stage')
            step.update(status='completed',artifacts=inventory(destination))
            values[name]=result;save(folder/'run.json',state)
        except BaseException as error:
            step.update(status='failed_no_retry',error_type=type(error).__name__)
            if isinstance(error,ValueError): step['validation_error']=str(error)
            candidate_ledger=folder/'candidates'/'run.json'
            if candidate_ledger.exists():
                cost=read(candidate_ledger)
                state.update(new_api_requests=cost['request_attempts'],new_known_tokens=cost['known_total_tokens'],
                             unknown_cost_attempts=cost['unknown_cost_attempts'])
            state['status']='failed_no_retry';save(folder/'run.json',state)
            if getattr(error,'status',None):
                state['blocked_reason']=error.status
                state['stage_ledger']=error.state_path
                if error.status=='needs_conversion': state['status']='needs_conversion'
                ledger=read(error.state_path)
                if 'svn_attempts' in ledger: state['svn_request_attempts']=len(ledger['svn_attempts'])
                save(folder/'run.json',state)
            if isinstance(error,KeyboardInterrupt): raise
            return folder,state
    candidates=values['candidates'];state.update(status='awaiting_rule_and_context_review',
         new_api_requests=candidates['request_attempts'],new_known_tokens=candidates['known_total_tokens'],
         unknown_cost_attempts=candidates['unknown_cost_attempts'],activated_rules=0)
    state['svn_request_attempts']=values['export'].get('svn_request_attempts',0)
    for step in state['steps']: verify_artifacts(step['artifacts'])
    lines=['# 新小批次流程报告','','导出、批注提取、候选归纳及学生版本线索检查已执行；候选规则未启用，仍需语义、适用范围和视觉复核。','',
           f'候选组数：{len(candidates.get("groups",[]))}；实际已知token：{state["new_known_tokens"]}；未知用量请求：{state["unknown_cost_attempts"]}。',
           '单学生反馈不能证明跨学生常犯；同学生多版本不增加学生数。SVN提交者与批注作者分别保留。',
           '初稿角色暂为configured_student_version；提交说明及历史仍需核实。正文我字、大纲及原生表数量只是线索，不代表错误或真实分页。','',
           f'[提取来源](<{(folder/"extract"/"result.json").as_posix()}>)',
           f'[候选规则及用量](<{(folder/"candidates"/"run.json").as_posix()}>)',
           f'[学生版本检查线索](<{(folder/"check"/"result.json").as_posix()}>)','',
           '下一步逐条复核候选证据和初稿身份；表格分页需Word及视觉核验。当前不宣称完整任务或独立准确率。']
    (folder/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    state['report_sha256']=digest(folder/'report.md');save(folder/'run.json',state)
    return folder,state


def candidate_stage(extracted,destination,factory):
    from pipeline_candidates import classify,payload
    client=factory() if payload(extracted)['items'] else None
    try: return classify(extracted,destination,client)
    finally:
        if client is not None and callable(getattr(client,'close',None)): client.close()


def make_client():
    from openai import OpenAI
    key=os.environ.get('DEEPSEEK_API_KEY') or getpass.getpass('DeepSeek API Key（仅本次使用）：')
    if not key.strip(): raise ValueError('No API key; no request')
    return OpenAI(api_key=key,base_url='https://api.deepseek.com',timeout=60,max_retries=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('replay',help='Verify accepted three-student evidence and generate a package, no API/SVN/Word')
    for name in ('plan','run'):
        command=sub.add_parser(name);command.add_argument('--config',required=True,type=Path)
    for name in ('resume','status'):
        command=sub.add_parser(name);command.add_argument('--run-dir',required=True,type=Path)
    args=parser.parse_args()
    if args.command=='plan':
        from pipeline_sources import validate_config
        config=validate_config(read(args.config))
        print('配置通过；样本：',len(config['samples']))
        print('阶段：export → extract → candidates（最多1次API，输出1600）→ check → 等待人工复核')
        print('本命令不访问SVN、Word或API；未启用任何候选规则。');return 0
    if args.command=='replay': folder,state=replay()
    elif args.command=='run':
        from pipeline_sources import validate_config
        config=validate_config(read(args.config));folder,state=new_run('new_batch',config)
        folder,state=run_pipeline(folder,state)
    else:
        folder=args.run_dir.resolve();state=read(folder/'run.json')
        if args.command=='resume':
            if state['mode']!='new_batch': raise ValueError('Only new-batch runs support stage resume')
            folder,state=run_pipeline(folder,state)
    print('状态：',state['status']);print('结果目录：',folder)
    if (folder/'report.md').exists(): print('可读报告：',folder/'report.md')
    print('运行记录：',folder/'run.json')
    if state.get('stage_ledger'): print('停止依据：',state.get('blocked_reason'),state['stage_ledger'])
    return 1 if state['status'] in {'failed_no_retry','needs_conversion'} else 0


if __name__=='__main__': raise SystemExit(main())
