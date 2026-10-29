export interface InitialStudentResult {
 student_id?:string;student_name?:string;revision?:number;status?:string;error?:string;
 summary?:Record<string,unknown>;source_asset_id?:string;source_kind?:'document'|'report';
 report_asset_id?:string;ledger_asset_id?:string;
 coverage?:{rule_id:string;title:string;status:string;detail?:string;semantic_review_complete?:boolean}[];
}
export interface InitialCheckJob {
 id:string;action:string;status:string;created_at?:string;started_at?:string;finished_at?:string;
 options?:{use_agent?:boolean;student_ids?:string[]};log_tail?:string;error?:string;
 result_summary?:{students?:InitialStudentResult[]};ledger_asset_id?:string;report_asset_id?:string;
 llm_requests?:number;known_total_tokens?:number;unknown_cost_attempts?:number;svn_requests?:number;word_launches?:number;
}
export interface InitialResultEntry {id:string;job:InitialCheckJob;student?:InitialStudentResult;}
export const initialGroupLabels={changes:'建议修改',compliant:'已审阅内容符合要求',unreviewed:'Agent 尚未审阅',pending:'需人工核验'} as const;

export function initialResultEntries(jobs:InitialCheckJob[]):InitialResultEntry[] {
 return jobs.filter(job=>job.action==='check-initials').sort((a,b)=>(b.started_at??b.created_at??'').localeCompare(a.started_at??a.created_at??''))
 .flatMap(job=>job.result_summary?.students?.length?job.result_summary.students.map((student,index)=>({id:`${job.id}:${student.student_id??index}`,job,student})):[{id:job.id,job}]);
}
export function initialResultCounts(student?:InitialStudentResult):Record<keyof typeof initialGroupLabels,number|null> {
 const source=student?.summary?.result_groups;
 const groups=source&&typeof source==='object'?source as Record<string,unknown>:{};
 return Object.fromEntries(Object.keys(initialGroupLabels).map(key=>[key,typeof groups[key]==='number'&&Number.isInteger(groups[key])&&groups[key]>=0?groups[key]:null])) as Record<keyof typeof initialGroupLabels,number|null>;
}
export function initialMode(job:InitialCheckJob,student?:InitialStudentResult) {
 if(job.options?.use_agent===false)return '仅本地检查 · 未启用 Agent';
 if(job.options?.use_agent!==true)return '审阅模式未记录';
 if(student?.coverage?.some(item=>item.semantic_review_complete===true))return 'Agent 已完成部分内容审阅';
 if(job.llm_requests===0)return '已启用 Agent · 本次未发出模型请求';
 return '已启用 Agent · 本稿完成情况见报告';
}
export function initialEntryName(entry:InitialResultEntry) {
 return entry.student?.student_name??entry.job.options?.student_ids?.map(id=>id.replace(/^\d+_/, '')).join('、')??'学生信息待记录';
}
