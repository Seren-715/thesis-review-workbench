import type {ExternalFeedbackReview,ReferenceRequirement} from './types';

const sourceKinds:Record<string,string>={official_norm:'校级撰写规范',checklist:'论文检查表',department_guidance:'专业结构指导',illustrative_template:'格式范本 · 仅作示例',template_annotations:'格式范本批注 · 项目指定依据',teacher_lecture:'教师写作课件'};
const sourceTitles:Record<string,string>={institution_norm:'福州大学至诚学院撰写、装订规范',checklist:'论文检查表和常见问题',department_guidance:'计算机工程系毕业论文结构示例',illustrative_template:'毕业论文格式范本',teacher_lecture:'科技论文写作实践（2024年材料）'};

export function referenceSourceKind(kind:string){return sourceKinds[kind]??'已登记参考材料';}
export function referenceSourceTitle(id:string){return sourceTitles[id]??'参考材料';}
export function referenceParameterValue(value:unknown):string {
 if(value===null||value===undefined)return '未明确';
 if(typeof value==='boolean')return value?'是':'否';
 if(Array.isArray(value))return value.map(referenceParameterValue).join('、');
 if(typeof value==='object')return Object.entries(value).map(([key,item])=>`${key}：${referenceParameterValue(item)}`).join('；');
 return String(value);
}
export function referenceRequirementScope(requirement:ReferenceRequirement){return requirement.interpretation||String(requirement.parameters?.['对象']??'适用对象以条文说明为准。');}
export function externalReviewItems(value?:ExternalFeedbackReview[]|{items?:ExternalFeedbackReview[]}) {return Array.isArray(value)?value:Array.isArray(value?.items)?value.items:[];}
export function externalReviewSearchText(item:ExternalFeedbackReview){return [item.evidence.student_name,item.evidence.comment_text,item.reason,item.next_step,...(item.missing_materials??[]),...(item.independent_requirement_fragments??[]).map(f=>f.quote),...(item.available_reference_candidates??[]).map(c=>c.title)].filter(Boolean).join(' ').toLowerCase();}
