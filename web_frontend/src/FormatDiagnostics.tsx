import type {ViewerTarget} from './DocumentViewer';
import type {FormatDiagnosticsData,NewPaperReport} from './types';
import {ReferenceRequirementCard} from './ReferenceStandards';

function DiagnosticGroup({rows,diagnostic,available,onOpen}:{rows:FormatDiagnosticsData['coverage'];diagnostic:FormatDiagnosticsData;available:boolean;onOpen?:(target:ViewerTarget)=>void}) {
 if(!rows.length)return null;
 return <details className="min-w-0 rounded-lg border border-stone-200 bg-white p-5">
  <summary className="cursor-pointer text-base font-semibold">{available?'已有参考条文的格式反馈':'尚需明确判定标准的格式反馈'}（{rows.length} 类）</summary>
  <p className="mt-3 text-xs leading-6 text-stone-500">{available?'已登记下列参考材料中的具体参数。应用前仍需确认本稿采用的规范版本、适用对象及例外条件；找到条文不代表已完成本稿检查，也不直接增加修改建议数量。':'下列反馈尚缺明确的目标参数。已保留教师原话及可读取的样式属性；属性差异不直接视为错误，也不按每个对象增加人工核验记录。'}</p>
  {available&&diagnostic.reference_standards?.applicability&&<p className="mt-2 text-xs leading-6 text-stone-600">{diagnostic.reference_standards.applicability}</p>}
  <div className="mt-4 grid items-start gap-3 md:grid-cols-2">{rows.map(row=>{
   const capability=diagnostic.capabilities.find(cap=>cap.family===row.family);
   return <article key={row.family} className="min-w-0 rounded border border-stone-200 p-3">
    <p className="text-sm font-medium">{row.title??'格式反馈'}</p><p className="mt-2 text-xs leading-6 text-stone-600">{row.detail}</p>
    {available&&(capability?.reference_requirements??[]).length>0&&<div className="mt-3 space-y-3">{capability!.reference_requirements!.map(requirement=><ReferenceRequirementCard key={requirement.id} requirement={requirement} sources={diagnostic.reference_standards?.sources??[]} onOpen={onOpen}/>)}</div>}
    <details className="mt-3 text-xs text-stone-600"><summary className="cursor-pointer text-teal-800">查看教师反馈与已采集属性</summary>
     {(capability?.teacher_evidence??[]).map((e,i)=><p key={i} className="mt-2 whitespace-pre-wrap border-l-2 border-teal-700 pl-3 leading-6">{e.quote}</p>)}
     {(diagnostic.sources[row.family]?.source_property_records??[]).map(record=><details key={record.paragraph_index} className="mt-3 rounded bg-stone-50 p-3">
      <summary className="cursor-pointer leading-6">{record.location?.label??'原文范围待确认'}</summary>
      <p className="mt-2 whitespace-pre-wrap leading-6">{record.text}</p>
      <details className="mt-2"><summary className="cursor-pointer">样式属性及继承来源（技术记录）</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({paragraph:record.paragraph_properties,runs:record.runs},null,2)}</pre></details>
     </details>)}
    </details>
   </article>;
  })}</div>
 </details>;
}

export default function FormatDiagnostics({report,onOpen}:{report:NewPaperReport;onOpen?:(target:ViewerTarget)=>void}) {
 const diagnostic=report.format_diagnostics;
 if(!diagnostic)return null;
 const needsReview=diagnostic.reference_standards?.status==='source_review_required';
 const available=needsReview?[]:diagnostic.coverage.filter(row=>row.status==='reference_standard_available');
 const unspecified=diagnostic.coverage.filter(row=>row.status==='standard_not_specified');
 if(!available.length&&!unspecified.length&&!needsReview)return null;
 return <div className="space-y-4">
  {needsReview&&<p role="alert" className="rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">{diagnostic.reference_standards?.message??'参考材料需重新核对，先前登记的参数暂不作为本稿判定依据。'}</p>}
  <DiagnosticGroup rows={available} diagnostic={diagnostic} available onOpen={onOpen}/>
  <DiagnosticGroup rows={unspecified} diagnostic={diagnostic} available={false} onOpen={onOpen}/>
 </div>;
}
