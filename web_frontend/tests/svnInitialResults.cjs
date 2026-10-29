const fs=require('fs'),path=require('path'),assert=require('assert/strict'),ts=require('typescript');
const result={exports:{}};
new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/svnResultModel.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(result,result.exports);
const {initialResultEntries,initialResultCounts,initialMode,initialEntryName}=result.exports;
const receipts=[];function test(name,fn){fn();receipts.push({name,status:'passed'})}
const latest={id:'job-new',action:'check-initials',status:'completed',started_at:'2026-10-11T20:54:16+08:00',options:{use_agent:false,student_ids:['212006167_吕孝森']},result_summary:{students:[{student_id:'212006167_吕孝森',student_name:'吕孝森',revision:182,summary:{candidate_count:36,agent_suggested_count:0,result_groups:{changes:0,compliant:0,unreviewed:41,pending:14}}}]}};
test('actual_local_mode_is_explicit_not_agent_completed',()=>assert.equal(initialMode(latest),'仅本地检查 · 未启用 Agent'));
test('agent_mode_is_from_saved_job_options',()=>assert.equal(initialMode({...latest,options:{use_agent:true}}),'已启用 Agent · 本稿完成情况见报告'));
test('enabled_zero_request_round_does_not_claim_model_review',()=>assert.equal(initialMode({...latest,options:{use_agent:true},llm_requests:0}),'已启用 Agent · 本次未发出模型请求'));
test('actual_complete_scope_is_distinct_from_enabled_mode',()=>assert.equal(initialMode({...latest,options:{use_agent:true},llm_requests:0},{coverage:[{semantic_review_complete:true}]}),'Agent 已完成部分内容审阅'));
test('unknown_mode_does_not_infer_from_zero_requests',()=>assert.equal(initialMode({...latest,options:undefined,llm_requests:0}),'审阅模式未记录'));
test('actual_four_groups_remain_disjoint_not_summed_with_candidates',()=>assert.deepEqual(initialResultCounts(latest.result_summary.students[0]),{changes:0,compliant:0,unreviewed:41,pending:14}));
test('legacy_count_only_receipt_does_not_fabricate_new_groups',()=>assert.deepEqual(initialResultCounts({summary:{candidate_count:36,confirmed_count:0,pending_count:19,agent_suggested_count:2}}),{changes:null,compliant:null,unreviewed:null,pending:null}));
test('invalid_count_is_unknown_not_negative_or_string',()=>assert.deepEqual(initialResultCounts({summary:{result_groups:{changes:-1,compliant:'0',unreviewed:2.5,pending:7}}}),{changes:null,compliant:null,unreviewed:null,pending:7}));
test('only_initial_check_jobs_are_shown_latest_first',()=>{
 const jobs=[{...latest,id:'old',started_at:'2026-10-09T10:00:00+08:00'},{...latest,id:'export',action:'collect'},latest];
 assert.deepEqual(initialResultEntries(jobs).map(item=>item.job.id),['job-new','old']);assert.equal(jobs[0].id,'old');
});
test('running_job_without_result_rows_remains_visible',()=>{const entry=initialResultEntries([{...latest,result_summary:undefined,status:'running'}])[0];assert.equal(entry.student,undefined);assert.equal(initialEntryName(entry),'吕孝森')});
test('new_result_entry_is_separate_from_experimental_tabs',()=>{
 const navigation=fs.readFileSync(path.resolve(__dirname,'../src/desktopBridge.ts'),'utf8');assert.match(navigation,/id:'svn-results'.+group:'论文审阅'/);
 const app=fs.readFileSync(path.resolve(__dirname,'../src/App.tsx'),'utf8');assert.match(app,/tab==='svn-results'&&<SvnInitialResults onOpen=\{setViewer\}/);
 const view=fs.readFileSync(path.resolve(__dirname,'../src/SvnInitialResults.tsx'),'utf8');assert.match(view,/fetch\('\/api\/thesis-workflow'\)/);assert.doesNotMatch(view,/method:\s*['"]POST/);assert.match(view,/查看完整核查结果/);assert.match(view,/查看核查过程/);assert.match(view,/查看原稿/);
 assert.match(view,/job\.ledger_asset_id&&<button/);assert.match(view,/id:job\.ledger_asset_id!/);assert.doesNotMatch(view,/id:student\?\.ledger_asset_id/);
});
console.log(JSON.stringify({tests:receipts.length,passed:receipts.length,receipts},null,2));
