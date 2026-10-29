import {useCallback,useEffect,useRef,useState} from 'react';
import {ArrowDown,RefreshCw} from 'lucide-react';
import {coverageCount,initialCompletionLabels,pendingStudentIds,taskCoverageValid,type TaskCoverage} from './taskCompletionModel';

const button='inline-flex items-center justify-center gap-2 rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-teal-800 hover:border-teal-500 disabled:cursor-not-allowed disabled:opacity-50';
export function TaskCompletion({refreshKey,disabled,onSelect,onResults}:{refreshKey?:string;disabled:boolean;onSelect:(ids:string[],label:string)=>void;onResults?:()=>void}){
 const [value,setValue]=useState<TaskCoverage|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const loadGeneration=useRef(0);
 const load=useCallback(async()=>{const generation=++loadGeneration.current;const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),15000);setLoading(true);setError('');try{
  const response=await fetch('/api/task-coverage',{signal:controller.signal});const body=await response.json();
  if(!response.ok)throw new Error(body.error??`任务完成情况读取失败 (${response.status})`);
  if(!taskCoverageValid(body))throw new Error('任务完成情况记录结构不完整，无法核实统计。');
  if(generation===loadGeneration.current)setValue(body);
 }catch(e){if(generation===loadGeneration.current){setValue(null);setError((e as Error).name==='AbortError'?'任务完成情况读取超时。请稍后更新；当前统计尚未核实。':(e as Error).message);}}finally{clearTimeout(timeout);if(generation===loadGeneration.current)setLoading(false);}},[]);
 useEffect(()=>{void load();},[load,refreshKey]);
 const rules=value?.rules,initials=value?.initials,students=initials?.students??[];
 const noReport=pendingStudentIds(students,'no-report'),unreviewed=pendingStudentIds(students,'unreviewed');
 return <section aria-label="教师任务完成情况" className="rounded-xl border border-stone-200 bg-white p-5 lg:p-6">
  <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-medium text-teal-800">以已保存的批注、检查项和学生核查记录为依据</p><h3 className="mt-2 text-xl font-semibold">教师任务完成情况</h3><p className="mt-2 max-w-4xl text-sm leading-6 text-stone-600">批注已分类、检查方法已关联、初稿已有报告是不同的处理阶段。有报告不表示已完成全文审阅；未核实或未完成的项目单独列示。</p></div><button onClick={()=>void load()} disabled={loading} className={button}><RefreshCw size={15}/>更新完成情况</button></div>
  {loading&&<p role="status" className="mt-4 text-sm text-stone-600">正在核对任务完成情况……</p>}
  {error&&<p role="alert" className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error} 未显示零值代替未知统计。</p>}
  {!loading&&value&&<>
   <div className="mt-5 grid gap-5 xl:grid-cols-2">
    <article className="min-w-0 rounded-lg border border-stone-200 p-4"><h4 className="font-semibold">1. 批注归纳与检查项建立</h4><dl className="mt-3 grid gap-3 sm:grid-cols-2">{[
     ['已提取批注','total_comments'],['反馈类别候选','candidate_families'],['已关联检查方法的类别','linked_families'],['尚无检查方法的类别','unlinked_families'],
     ['已关联反馈类别的批注','linked_comments'],['未关联反馈类别的批注','unlinked_comments'],['已关联检查方法的批注','checker_linked_comments'],['尚待分类处理的文字批注','classification_pending_comments'],['模型分类尚待语义复核的批注','semantic_review_required_comments'],['已登记检查项','checker_items'],['无教师依据的补充结构检查','supplemental_checker_items'],
     ['已有复核登记的检查项','reviewed_checker_items'],['尚无复核登记的检查项','unreviewed_checker_items'],
    ].map(([label,key])=><div key={key} className="rounded bg-stone-50 p-3"><dt className="text-xs leading-5 text-stone-500">{label}</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{coverageCount(rules?.summary[key])}</dd></div>)}</dl>
    <p className="mt-3 text-xs leading-6 text-stone-600">空文字批注 {coverageCount(rules?.summary.empty_comments)} 条；含图片批注 {coverageCount(rules?.summary.image_comments)} 条。两类可重叠，需要结合原始批注处理。类别关联、检查方法关联与可靠性验收分别统计；已有复核登记不表示该检查项对所有论文均已通过准确性验收。</p>
    {rules?.families?.some(family=>family.status==='candidate_only')&&<details className="mt-3 text-xs"><summary className="cursor-pointer text-teal-800">查看尚未关联检查方法的类别</summary><ul className="mt-2 list-disc space-y-1 pl-5 text-stone-600">{rules.families.filter(family=>family.status==='candidate_only').map(family=><li key={family.id}>{family.title} · {family.id}</li>)}</ul></details>}
    {rules?.new_requirement_proposals?.length? <details className="mt-4 text-xs"><summary className="cursor-pointer text-teal-800">查看分类表尚未覆盖的新要求（{rules.new_requirement_proposals.length} 条，未启用）</summary><p className="mt-2 leading-6 text-stone-500">以下规格从教师明确原话提取，尚待语义复核、适用范围确认及独立正反例验证。未计为可靠规则，也不计为学生论文错误。</p><div className="mt-2 space-y-3">{rules.new_requirement_proposals.map((proposal,index)=><article key={`${proposal.family}-${index}`} className="rounded border border-stone-200 p-3"><h5 className="font-medium">{proposal.title} · {proposal.student_name} · r{proposal.revision}</h5><blockquote className="mt-2 border-l-2 border-teal-300 pl-3 leading-6">{proposal.quote}</blockquote><p className="mt-2 leading-6 text-stone-600">范围草案：{proposal.scope}。判据草案：{proposal.criterion_draft}。</p></article>)}</div></details>:null}
    </article>
    <article className="min-w-0 rounded-lg border border-stone-200 p-4"><h4 className="font-semibold">2. 全体学生历史初稿核查</h4><dl className="mt-3 grid gap-3 sm:grid-cols-2">{[
     ['已发现学生（统计范围）','students'],['已找到初稿候选','initial_candidates'],['已有核查报告的学生','report_students'],['已完成可执行自动审阅的学生','automatic_review_complete_students'],['全部检查与核验均完成的学生','fully_completed_students'],['尚无核查报告的学生','not_checked_students'],['仅有本地检查的学生','local_only_students'],['检查失败的学生','failed_students'],['原稿来源校验未通过','source_invalid_students'],
    ].map(([label,key])=><div key={key} className="rounded bg-stone-50 p-3"><dt className="text-xs leading-5 text-stone-500">{label}</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{coverageCount(initials?.summary[key])}</dd></div>)}</dl>
    <p className="mt-3 text-xs leading-6 text-stone-600">统计范围依据当前已保存的仓库发现记录{initials?.snapshot_revision!=null?`（SVN 快照 r${initials.snapshot_revision}）`:''}；不代表尚未发现的仓库学生也已处理。Agent 审阅、来源核验及适用检查项存在未完成记录时，不计为完整完成。</p>
    <div className="mt-3 flex flex-wrap gap-2"><button disabled={disabled||!noReport.length} className={button} onClick={()=>onSelect(noReport,'尚无报告学生')}><ArrowDown size={14}/>选择尚无报告学生（{noReport.length}）</button><button disabled={disabled||!unreviewed.length} className={button} onClick={()=>onSelect(unreviewed,'尚未完成审阅学生')}><ArrowDown size={14}/>选择尚未完成审阅学生（{unreviewed.length}）</button>{onResults&&<button className={button} onClick={onResults}>查看 SVN 初稿核查结果</button>}</div>
    <p className="mt-3 text-xs leading-6 text-stone-500">每次最多选择 10 名学生。以上按钮仅选择学生并定位至核查设置，不提交任务；请核对 Agent 选项和预算后执行核查。</p>
    </article>
   </div>
   {value.issues?.length? <details className="mt-4 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900" open><summary className="cursor-pointer font-medium">统计资料尚有 {value.issues.length} 项需核对</summary><ul className="mt-2 list-disc space-y-1 pl-5">{value.issues.map((issue,index)=><li key={index} className="break-words leading-6">{issue.scope?`${issue.scope}：`:''}{issue.error}</li>)}</ul></details>:null}
   {students.length?<details className="mt-5" open><summary className="cursor-pointer text-sm font-medium">学生初稿处理明细（{students.length} 名）</summary><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[640px] text-left text-xs"><thead className="bg-stone-50 text-stone-600"><tr>{['学生 / 版本','核查报告','Agent 审阅','处理状态与主要阻碍'].map(title=><th key={title} scope="col" className="border-b border-stone-200 p-3 font-medium">{title}</th>)}</tr></thead><tbody>{students.map(student=><tr key={student.student_id}><th scope="row" className="border-b border-stone-100 p-3 font-medium">{student.student_name}<span className="mt-1 block font-normal text-stone-500">{student.revision!=null?`初稿候选 r${student.revision}`:'初稿版本未确认'}</span></th><td className="border-b border-stone-100 p-3">{student.report_exists===true?'已有报告':student.report_exists===false?'尚无报告':'未核实'}</td><td className="border-b border-stone-100 p-3">{student.agent_completed===true?'已完成':student.agent_enabled===false?'未启用':student.agent_enabled===true?'已启用 · 尚未完整完成':'未核实'}</td><td className="max-w-xl border-b border-stone-100 p-3 leading-6"><p className="font-medium">{initialCompletionLabels[student.status]??'处理状态待核实'}</p>{student.blockers?.map((blocker,index)=><p key={index} className="text-stone-500">{blocker}</p>)}</td></tr>)}</tbody></table></div></details>:<p className="mt-4 rounded bg-stone-50 p-3 text-sm leading-6 text-stone-600">尚无可核实的学生初稿清单。请先核对仓库文件与历史版本；当前不推断所有学生均已完成。</p>}
   {value.updated_at&&<p className="mt-3 text-xs text-stone-400">记录核对时间：{new Date(value.updated_at).toLocaleString('zh-CN')}</p>}
  </>}
 </section>;
}
