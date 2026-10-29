import {BookOpen} from 'lucide-react';
import type {ViewerTarget} from './DocumentViewer';
import type {ExternalFeedbackReview,ReferenceRequirement,ReferenceStandardCatalog,ReferenceStandardSource} from './types';
import {referenceParameterValue,referenceRequirementScope,referenceSourceKind,referenceSourceTitle} from './referenceStandardsModel';

type OpenReader=(target:ViewerTarget)=>void;
const readerButton='inline-flex items-center gap-2 text-xs text-teal-800 hover:underline';

export function ReferenceRequirementCard({requirement,sources=[],onOpen}:{requirement:ReferenceRequirement;sources?:ReferenceStandardSource[];onOpen?:OpenReader}) {
 const source=sources.find(item=>item.id===requirement.source_id);
 const sourceTitle=source?.title??requirement.source_title??referenceSourceTitle(requirement.source_id);
 const readerId=requirement.reader_asset_id??source?.reader_asset_id;
 return <article className="min-w-0 rounded-lg border border-stone-200 bg-white p-4">
  <div className="flex flex-wrap items-start justify-between gap-2"><h5 className="min-w-0 break-words text-sm font-medium">{sourceTitle}{requirement.location&&<span className="ml-2 text-xs font-normal text-stone-500">{requirement.location}</span>}</h5>{source&&<span className="rounded bg-stone-100 px-2 py-1 text-xs text-stone-600">{referenceSourceKind(source.source_kind)}</span>}</div>
  <blockquote className="mt-3 whitespace-pre-wrap break-words border-l-2 border-teal-700 bg-stone-50 p-3 text-sm leading-6 text-teal-950">{requirement.quote}</blockquote>
  {Object.keys(requirement.parameters??{}).length>0&&<dl className="mt-3 grid gap-x-4 gap-y-2 text-xs leading-6 sm:grid-cols-2">{Object.entries(requirement.parameters??{}).map(([key,value])=><div key={key} className="min-w-0 break-words"><dt className="inline text-stone-500">{key}：</dt><dd className="inline text-stone-800">{referenceParameterValue(value)}</dd></div>)}</dl>}
  <p className="mt-3 text-xs leading-6 text-stone-600">适用对象与解释：{referenceRequirementScope(requirement)}</p>
  {readerId&&onOpen&&<button type="button" onClick={()=>onOpen({id:readerId,label:`${sourceTitle} · 规范原文`,kind:'report',referenceMaterial:true,focusText:requirement.quote})} className={`${readerButton} mt-3`}><BookOpen size={14}/>在应用内阅读依据原文</button>}
 </article>;
}

export function ReferenceStandards({catalog,onOpen}:{catalog?:ReferenceStandardCatalog;onOpen:OpenReader}) {
 if(!catalog)return null;
 const needsReview=catalog.status==='source_review_required';
 const sources=needsReview?[]:catalog.sources??[],requirements=needsReview?[]:catalog.requirements??[];
 return <details open={needsReview||undefined} className="min-w-0 rounded-xl border border-stone-200 bg-white p-5 lg:p-6">
  <summary className="cursor-pointer text-lg font-semibold">已登记的参考规范与具体要求 <span className="ml-2 text-xs font-normal text-stone-500">{needsReview?'参考材料需重新核对':`${sources.length} 份材料 · ${requirements.length} 条明文要求`}</span></summary>
  {needsReview&&<p role="alert" className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">{catalog.message??'参考材料已发生变化或读取失败，相关参数暂不参与核查；请重新核对已登记的原文。'}</p>}
  <p className="mt-3 text-sm font-medium">{catalog.title??catalog.profile_title??'论文撰写参考规范'}</p>
  <p className="mt-2 text-xs leading-6 text-stone-600">{catalog.applicability??'应用前须明确选择本稿适用的材料、论文类型和规范版本。'}</p>
  <p className="mt-2 text-xs leading-6 text-stone-500">以下内容展示已登记的原文依据，未据此自动认定本稿适用或已完成核查。格式范本的示例内容不作为规范；已指定采用的批注要求另行登记，特殊对象、课程范围和例外条件分别保留。</p>
  {sources.length>0&&<div className="mt-4 grid items-start gap-3 md:grid-cols-2">{sources.map(source=><article key={source.id} className="min-w-0 rounded-lg border border-stone-200 bg-stone-50 p-3"><h4 className="break-words text-sm font-medium">{source.title}</h4><p className="mt-1 text-xs leading-6 text-stone-500">{referenceSourceKind(source.source_kind)}</p>{source.applicability&&<p className="mt-1 text-xs leading-6 text-stone-600">{source.applicability}</p>}{source.reader_asset_id&&<button type="button" onClick={()=>onOpen({id:source.reader_asset_id!,label:`${source.title} · 原文阅读`,kind:'report',referenceMaterial:true})} className={`${readerButton} mt-2`}><BookOpen size={14}/>阅读完整参考材料</button>}</article>)}</div>}
  {requirements.length>0&&<div className="mt-5 grid items-start gap-3 md:grid-cols-2">{requirements.map(requirement=><ReferenceRequirementCard key={requirement.id} requirement={requirement} sources={sources} onOpen={onOpen}/>)}</div>}
  {(catalog.missing_explicit_standards??[]).map(item=><p key={item.family} className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">{item.title}：{item.reason}</p>)}
  {catalog.status==='not_registered'&&<p className="mt-3 text-xs leading-6 text-stone-500">尚未登记可核验的参考材料，暂不使用未明确的参数判定错误。</p>}
 </details>;
}

export function ExternalFeedbackReviews({items,onOpen}:{items:ExternalFeedbackReview[];onOpen:OpenReader}) {
 return <div className="mt-4 space-y-4">
  <p className="rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">以下批注需要外部资料确认。</p>
  {items.map((item,index)=>{
   const evidence=item.evidence,name=evidence.student_name??'姓名待核验',readerId=evidence.source_asset_id??evidence.source?.asset_id;
   return <article key={evidence.id??evidence.evidence_id??index} className="min-w-0 rounded-lg border border-stone-200 bg-white p-4">
    <div className="flex flex-wrap items-start justify-between gap-2"><h4 className="text-sm font-medium">{name}{evidence.revision!==undefined&&` · 教师稿 r${evidence.revision}`}</h4><span className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-900">需要外部资料确认</span></div>
    <p className="mt-3 text-xs text-stone-500">教师原批注（全文）</p><blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 border-teal-700 bg-stone-50 p-3 text-sm leading-6 text-teal-950">{evidence.comment_text??evidence.comment?.text??'原始批注文字暂不可读取。'}</blockquote>
    {evidence.anchor_text&&<details className="mt-3 text-xs text-stone-600"><summary className="cursor-pointer">查看批注对应的论文原文</summary><p className="mt-2 whitespace-pre-wrap leading-6">{evidence.anchor_text}</p></details>}
    {readerId&&<button type="button" onClick={()=>onOpen({id:readerId,label:`${name} · 教师批改稿`,kind:'document',focusText:evidence.anchor_text})} className={`${readerButton} mt-3`}><BookOpen size={14}/>查看该批注所在教师稿</button>}
    {(item.independent_requirement_fragments??[]).length>0&&<details className="mt-4 rounded border border-stone-200 p-3 text-xs"><summary className="cursor-pointer font-medium text-teal-800">批注中可单独核对的明确要求</summary><p className="mt-2 leading-6 text-stone-500">以下仅保留教师明确原话，仍需确认所指对象和适用范围；不代表规则已经审定。</p>{item.independent_requirement_fragments!.map((fragment,i)=><blockquote key={i} className="mt-2 whitespace-pre-wrap border-l-2 border-teal-700 pl-3 leading-6">{fragment.quote}</blockquote>)}</details>}
   </article>;
  })}
 </div>;
}
