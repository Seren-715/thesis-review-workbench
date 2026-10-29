const fs=require('fs'),path=require('path'),assert=require('assert/strict'),ts=require('typescript');
const moduleResult={exports:{}};
new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/taskCompletionModel.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(moduleResult,moduleResult.exports);
const {coverageCount,pendingStudentIds,chooseCoverageBatch,taskCoverageValid}=moduleResult.exports;
const receipts=[];function test(name,fn){fn();receipts.push({name,status:'passed'})}
const students=[{student_id:'a',student_name:'安娜羽',status:'not_checked',report_exists:false,agent_completed:false},
 {student_id:'b',student_name:'本地学生',status:'local_only',report_exists:true,agent_completed:false},
 {student_id:'c',student_name:'已完学生',status:'completed',report_exists:true,agent_completed:true},
 {student_id:'d',student_name:'缺稿学生',status:'no_initial',report_exists:false,agent_completed:false},
 {student_id:'e',student_name:'来源异常',status:'source_invalid',report_exists:true,agent_completed:false}];
test('unknown_and_invalid_counts_never_render_as_zero',()=>{for(const value of [undefined,null,-1,1.2,'16',NaN])assert.equal(coverageCount(value),'未核实');assert.equal(coverageCount(0),'0');assert.equal(coverageCount(332),'332')});
test('no_report_choice_only_includes_confirmed_unchecked_sources',()=>assert.deepEqual(pendingStudentIds(students,'no-report'),['a']));
test('local_report_is_not_complete_agent_review',()=>assert.deepEqual(pendingStudentIds(students,'unreviewed'),['a','b']));
test('selection_respects_available_students_ten_limit_and_no_duplicates',()=>{const ids=Array.from({length:13},(_,i)=>String(i));assert.deepEqual(chooseCoverageBatch([...ids,'0'],ids.slice(0,12)),{selected:ids.slice(0,10),remaining:2,unavailable:1})});
test('empty_workspace_is_known_empty_and_does_not_fabricate_completion',()=>{assert.deepEqual(pendingStudentIds([],'no-report'),[]);assert.equal(taskCoverageValid({schema_version:1,status:'not_available'}),true)});
test('malformed_sources_are_rejected_instead_of_defaulting_to_zero',()=>{assert.equal(taskCoverageValid({schema_version:1,status:'verified',initials:{summary:{students:17},students:null}}),false);assert.equal(taskCoverageValid({schema_version:1,status:'verified',rules:{summary:null}}),false);assert.equal(taskCoverageValid({schema_version:2,status:'verified'}),false)});
test('actual_data_shape_preserves_distinct_processing_stages',()=>{const value={schema_version:1,status:'needs_review',rules:{status:'verified',summary:{total_comments:332,candidate_families:44,linked_comments:193,semantic_review_required_comments:118}},initials:{status:'verified',summary:{students:17,report_students:16,fully_completed_students:0},students}};assert.equal(taskCoverageValid(value),true);assert.equal(value.initials.summary.fully_completed_students,0)});
test('completion_surface_only_reads_and_requires_manual_task_submission',()=>{const view=fs.readFileSync(path.resolve(__dirname,'../src/TaskCompletion.tsx'),'utf8');assert.match(view,/fetch\('\/api\/task-coverage',\{signal:controller.signal\}\)/);assert.doesNotMatch(view,/method:\s*['"]POST/);assert.match(view,/仅选择学生并定位至核查设置，不提交任务/);assert.match(view,/已有复核登记不表示/);assert.match(view,/linked_comments/);assert.match(view,/checker_linked_comments/);const parent=fs.readFileSync(path.resolve(__dirname,'../src/TeacherRules.tsx'),'utf8');assert.match(parent,/chooseCoverageBatch\(ids,students\.map/);assert.match(parent,/id="teacher-initial-selection"/)});
console.log(JSON.stringify({tests:receipts.length,passed:receipts.length,receipts},null,2));
