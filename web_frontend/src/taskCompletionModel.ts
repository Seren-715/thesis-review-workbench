export interface CoverageIssue {scope?:string;path?:string;error:string}
export interface CoverageStudent {
 student_id:string;student_name:string;revision?:number|null;status:string;
 report_exists?:boolean;source_verified?:boolean;agent_enabled?:boolean;agent_completed?:boolean;automated_completed?:boolean;
 blockers?:string[];result_groups?:Record<string,number>;report_path?:string;
}
export interface TaskCoverage {
 schema_version:number;status:string;updated_at?:string;issues?:CoverageIssue[];
 rules?:{status:string;summary:Record<string,number|null>;families?:{id:string;title:string;status:string;checker_ids?:string[];reviewed?:boolean}[];
 new_requirement_proposals?:{title:string;family:string;quote:string;student_name?:string;revision?:number;scope:string;criterion_draft:string;status:string}[]};
 initials?:{status:string;snapshot_revision?:number|null;summary:Record<string,number|null>;students:CoverageStudent[]};
}
export const initialCompletionLabels:Record<string,string>={
 not_checked:'尚无核查报告',local_only:'已完成本地检查 · 未完成 Agent 审阅',incomplete:'部分检查完成',
 completed:'已完成核查',failed:'核查失败',source_invalid:'原稿来源校验未通过',no_initial:'未找到初稿候选',
};
export function coverageCount(value:unknown):string {
 return typeof value==='number'&&Number.isInteger(value)&&value>=0?String(value):'未核实';
}
export function taskCoverageValid(value:unknown):value is TaskCoverage {
 if(!value||typeof value!=='object')return false;
 const item=value as Partial<TaskCoverage>;
 if(item.schema_version!==1||!['verified','needs_review','not_available'].includes(item.status??''))return false;
 if(item.issues!==undefined&&!Array.isArray(item.issues))return false;
 if(item.rules&&(!item.rules.summary||typeof item.rules.summary!=='object'||Array.isArray(item.rules.summary)))return false;
 if(item.rules?.families!==undefined&&!Array.isArray(item.rules.families))return false;
 if(item.rules?.new_requirement_proposals!==undefined&&!Array.isArray(item.rules.new_requirement_proposals))return false;
 if(item.initials&&(!item.initials.summary||typeof item.initials.summary!=='object'||!Array.isArray(item.initials.students)))return false;
 return !item.initials?.students.some(student=>!student||typeof student.student_id!=='string'||typeof student.student_name!=='string'||typeof student.status!=='string');
}
export function pendingStudentIds(students:CoverageStudent[],kind:'no-report'|'unreviewed'):string[]{
 return Array.from(new Set(students.filter(student=>
   kind==='no-report'?student.status==='not_checked'&&!student.report_exists:
   student.status!=='completed'&&student.status!=='no_initial'&&student.status!=='source_invalid'&&student.agent_completed!==true
 ).map(student=>student.student_id)));
}
export function chooseCoverageBatch(ids:string[],available:string[],limit=10):{selected:string[];remaining:number;unavailable:number}{
 const allowed=new Set(available),unique=Array.from(new Set(ids));
 const present=unique.filter(id=>allowed.has(id));
 return {selected:present.slice(0,limit),remaining:Math.max(0,present.length-limit),unavailable:unique.length-present.length};
}
