import FormatDiagnostics from './FormatDiagnostics';
import {ReferenceRequirementCard} from './ReferenceStandards';
import {useEffect, useRef, useState} from 'react';
import {AlertCircle, BookOpen, CheckCircle2, FileText, Loader2, RefreshCw, Save, Upload} from 'lucide-react';
import type {ViewerTarget} from './DocumentViewer';
import {bindSourceParagraph} from './documentLocation';
const sourceParagraph=(paper:NewPaper,index:number|null|undefined)=>bindSourceParagraph(paper.source_asset_id,paper.report?.paragraphs??[],index);
import type {NewPaper, NewPaperEvidence, NewPaperExcludedClue, NewPaperFinding, NewPaperList, NewPaperRun, ReferenceTarget} from './types';

const stateLabels: Record<string, string> = {queued:'等待开始检查', running:'正在检查论文', completed:'本轮检查完成', partial:'部分检查完成', failed:'本轮检查失败', interrupted:'上次检查中断 · 可继续检查'};
const findingLabels: Record<string, string> = {candidate:'自动查找的线索', confirmed_issue:'已确认问题', needs_review:'需人工核验', clue:'自动查找的线索'};
const activeStates = ['queued', 'running', 'cancelling'];
function paperStateLabel(paper:NewPaper) {
 if(paper.source_format==='pdf' && paper.status==='completed')return 'PDF 文字处理已结束 · 全文与版面核查尚未完成';
 if(paper.status==='cancelled')return '已安全取消 · 保留已完成结果';
 if(paper.status==='cancelling')return '正在安全取消 · 等待当前步骤完成';
 if(paper.status==='historical')return '以前的检查结果 · 仅供查看';
 const completion=(paper.run || paper.report?.run)?.completion;
 if(paper.status==='completed' && completion){
  if(paper.report?.summary.result_groups?.unreviewed && (paper.run || paper.report?.run)?.review_scope==='references')return '图表引用检查已更新 · 其他项目尚未审完';
  if(paper.report?.summary.result_groups?.unreviewed && (paper.run || paper.report?.run)?.review_scope==='content')return '章节内容已更新 · 仍有其他待审项目';
  if(completion.status==='completed')return paper.report?.summary.result_groups?.pending ? '本次自动审阅结束 · 部分项目需人工核验' : '本次自动审阅完成';
  if(completion.status==='completed_with_manual')return '本次自动审阅结束 · 部分项目需人工核验';
  if(completion.status==='budget_remaining')return '本次用量达到上限 · 仍有项目未审完';
  if(completion.status==='stopped')return '审阅已停止 · 查看原因';
 }
 if(paper.status === 'completed' && (paper.result_status === 'partial' || paper.report?.run?.status === 'partial')) return '已保存部分结果 · 尚未全部检查完成';
 return stateLabels[paper.status] || paper.status;
}
const fieldClass = 'w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm focus:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-100';
const buttonClass = 'inline-flex items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm text-teal-800 hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-50';
const coverageLabels: Record<string, string> = {checked:'本项检查已执行', partial:'仅检查了部分内容', needs_review:'需确认范围或补充依据', not_applicable:'本稿不适用', not_applicable_policy:'未启用此格式要求', visual_review_required:'需查看实际图片或页面'};

function ReviewProgress({run,active}:{run?:NewPaperRun;active:boolean}) {
 const progress=run?.progress,completion=run?.completion;
 if(!progress && !completion && !active)return null;
 const stages:Record<string,string>={tools:'运行论文检查工具',text:'结合上下文审阅文字',content:'分批阅读章节并综合判断',figures:'审阅图片',completed:'本次自动处理已结束'};
 return <div className="mt-4 rounded-md border border-teal-200 bg-teal-50 p-4 text-sm leading-6 text-teal-950" role="status" aria-live="polite">
  <p className="font-medium">{active ? stages[progress?.stage || ''] || '正在准备审阅证据' : completion?.status==='budget_remaining' ? '本轮预算已到上限' : completion?.status==='stopped' ? '本轮审阅已停止' : '本轮自动审阅已结束'}</p>
  {progress && <><progress className="mt-2 h-2 w-full accent-teal-700" aria-label="本次送模型审阅的项目进度" max={Math.max(1,progress.total)} value={progress.reviewed+progress.blocked}/><p className="mt-1 text-xs tabular-nums">模型已读取 {progress.reviewed} / {progress.total} 项 · 无法自动判定 {progress.blocked} 项 · 尚未处理 {progress.remaining} 项</p><p className="mt-2 text-xs">此进度统计本次需模型审阅的项目，不表示已审阅的论文页数，也不代表整篇论文合格。需人工核验的项目在下方单列。</p></>}
  {completion && !active && <p className="mt-2 text-xs">{completion.reason}</p>}
  {active && <p className="mt-2 text-xs">本次任务会自动逐批处理，无需反复点击。达到请求或 token 上限后会保存进度；无法可靠判断的项目会保留原因，交由人工核验。</p>}
 </div>;
}

function inferStudentName(fileName:string) {
 const stem = fileName.replace(/\.[^.]+$/, '');
 const ignored = new Set(['毕业论文', '论文', '本科生', '本科毕业设计', '开题报告', '任务书', '文献综述']);
 const parts = stem.split(/[_\-\s]+/).map(item=>item.trim()).filter(Boolean);
 return parts.find(item=>/^[\u4e00-\u9fff]{2,5}$/.test(item) && !ignored.has(item)) || '';
}

async function api<T>(url:string, options?:RequestInit):Promise<T> {
 const response = await fetch(url, options);
 const body = await response.json();
 if (!response.ok) throw new Error(body.error || `请求失败（${response.status}）`);
 return body as T;
}

function Evidence({item,onOpen}:{item:NewPaperEvidence;onOpen:(target:ViewerTarget)=>void}) {
 const quote = item.comment_text || item.comment?.text || item.quote || item.text || '批注文字未随此项返回';
 const student = item.student_name || item.source?.student_name || '教师样本';
 const revision = item.revision ?? item.source?.revision;
 const source = item.source_asset_id || item.source?.asset_id;
 return <article className="mt-3 rounded-md border border-stone-200 bg-stone-50 p-3">
  <p className="text-xs text-stone-500">{student}{revision !== undefined && ` · 教师提交稿 r${revision}`}{(item.comment_id || item.comment?.id) && ` · 批注 ${item.comment_id || item.comment?.id}`}</p>
  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-teal-900">{quote}</p>
  {item.anchor_text && <details className="mt-2 text-xs text-stone-500"><summary className="cursor-pointer">批注对应原文</summary><p className="mt-2 whitespace-pre-wrap leading-6">{item.anchor_text}</p></details>}
  {source && <button className="mt-2 inline-flex items-center gap-1 text-xs text-teal-800" onClick={()=>onOpen({id:source,label:`${student} · 教师批改稿`,kind:'document',focusText:item.anchor_text})}><BookOpen size={13}/>在应用内查看教师稿</button>}
 </article>;
}

function HighlightText({text,start,end}:{text:string;start:number|null;end:number|null}) {
 const chars = Array.from(text || '');
 const valid = typeof start === 'number' && typeof end === 'number' && start >= 0 && end > start && end <= chars.length;
 return <p className="whitespace-pre-wrap break-words text-sm leading-7">{valid?<>{chars.slice(0,start!).join('')}<mark className="rounded-sm bg-teal-100 text-teal-950">{chars.slice(start!,end!).join('')}</mark>{chars.slice(end!).join('')}</>:text}</p>;
}

function Excerpt({finding}:{finding:NewPaperFinding}) {
 const quote=finding.semantic_review?.quote;
 let position=quote?finding.text.indexOf(quote):-1;
 if(quote && finding.rule_id==='NP001' && typeof finding.start==='number' && typeof finding.end==='number') {
  while(position>=0) {
   const quoteStart=Array.from(finding.text.slice(0,position)).length;
   if(quoteStart<=finding.start && finding.end<=quoteStart+Array.from(quote).length)break;
   position=finding.text.indexOf(quote,position+1);
  }
 }
 return <HighlightText text={finding.text || ''} start={position>=0?Array.from(finding.text.slice(0,position)).length:finding.start} end={position>=0?Array.from(finding.text.slice(0,position+quote!.length)).length:finding.end}/>;
}

function ContentScopeReview({finding,paper,onOpen}:{finding:NewPaperFinding;paper:NewPaper;onOpen:(target:ViewerTarget)=>void}) {
 const scope=finding.content_scope,review=finding.semantic_review;
 if(!scope)return null;
 const readIds=new Set(finding.content_read_progress?.read_chunk_ids || []);
 const chunks=scope.chunks || [];
 const readChunks=chunks.filter(chunk=>readIds.has(chunk.id));
 const readParagraphs=new Set(readChunks.map(chunk=>chunk.paragraph_index));
 const readCharacters=readChunks.reduce((total,chunk)=>total+Array.from(chunk.text).length,0);
 const decisionLabels:Record<string,string>={satisfied:'已说明',revision:'建议补充或调整',uncertain:'证据仍不足',not_applicable:'本稿不适用'};
 const judgmentLabels:Record<string,string>={suggested_revision:'建议修改 · 请核对',not_issue:'已审阅内容符合本项要求',needs_review:'依据不足 · 需人工核验'};
 return <div className="mt-4 rounded-md border border-teal-200 bg-teal-50 p-4 text-sm leading-6">
  <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium text-teal-950">章节原文审阅进度与结果</p><span className="text-xs text-teal-800">{review?.validated_scope?'已综合判断下列原文范围':finding.review_blocked?'无法自动完成判断':'原文审阅或综合判定尚未完成'}</span></div>
  <p className="mt-2 text-xs tabular-nums text-teal-900">模型已读取 {readChunks.length} / {chunks.length} 个原文片段 · 涉及 {readParagraphs.size} / {scope.total_paragraphs} 个段落 · 已读取 {readCharacters} / {scope.total_characters} 字符</p>
  <progress className="mt-2 h-2 w-full accent-teal-700" max={Math.max(1,chunks.length)} value={readChunks.length} aria-label="章节原文实际读取进度"/>
  <p className="mt-2 text-xs text-stone-600">{scope.complete?'已提取此次检查范围内的全部可见原文；原文提取完成不表示模型审阅已完成。':'此次检查范围的原文尚不完整。'}模型完成各批次原文审阅后进行综合判断，单一段落中的信息缺失不能作为整章缺失的依据。</p>
  {review?.validated_scope && <div className="mt-3 border-t border-teal-200 pt-3"><p className="font-medium text-teal-950">Agent 综合判断：{judgmentLabels[review.decision] || review.decision}</p><p className="mt-2">{review.reason}</p>{review.decision!=='not_issue' && review.suggestion && <p className="mt-2">建议：{review.suggestion}</p>}</div>}
  {review?.dimension_checks?.length ? <div className="mt-4 space-y-3">{review.dimension_checks.map((dimension,index)=><div key={`${dimension.dimension}-${index}`} className="rounded border border-stone-200 bg-white p-3"><div className="flex flex-wrap items-start justify-between gap-2"><p className="font-medium text-stone-900">{dimension.dimension}</p><span className="rounded bg-stone-50 px-2 py-1 text-xs text-stone-600">{decisionLabels[dimension.decision] || dimension.decision}</span></div><p className="mt-2 text-xs leading-6 text-stone-600">{dimension.reason}</p>{dimension.evidence.map((evidence,quoteIndex)=><div key={`${evidence.paragraph_index}-${quoteIndex}`} className="mt-3 border-l-2 border-teal-700 pl-3"><p className="text-xs text-stone-500">{evidence.location?.label || '来源段落范围待标明'}</p><p className="mt-1 whitespace-pre-wrap text-sm leading-7">{evidence.quote}</p>{paper.source_asset_id && <button className="mt-2 inline-flex items-center gap-1 text-xs text-teal-800" onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · ${dimension.dimension}原文证据`,kind:'document',focusText:evidence.quote,focusParagraph:sourceParagraph(paper,evidence.paragraph_index)})}><BookOpen size={13}/>在应用内定位此条证据</button>}</div>)}</div>)}</div> : null}
  <details className="mt-4 text-xs text-stone-600"><summary className="cursor-pointer text-teal-800">查看完整来源范围与读取状态（{scope.total_paragraphs} 段）</summary>{scope.headings?.length ? <p className="mt-2 leading-6">范围标题：{scope.headings.map(heading=>heading.text).join(' / ')}</p> : null}<div className="mt-3 space-y-3">{chunks.map(chunk=><div key={chunk.id} className="rounded border border-stone-200 bg-white p-3"><div className="flex flex-wrap justify-between gap-2"><p className="text-stone-500">{chunk.location?.label || '来源范围待标明'}{chunk.role==='table_cell'?' · 表格文字':''}</p><span className={readIds.has(chunk.id)?'text-teal-800':'text-stone-500'}>{readIds.has(chunk.id)?'已读取':'尚未读取'}</span></div><p className="mt-2 whitespace-pre-wrap leading-7">{chunk.text}</p>{paper.source_asset_id && <button className="mt-2 text-teal-800" onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 章节完整原文`,kind:'document',focusText:chunk.text,focusParagraph:sourceParagraph(paper,chunk.paragraph_index)})}>在应用内定位原文</button>}</div>)}</div></details>
 </div>;
}

function FigureReview({finding,onOpen}:{finding:NewPaperFinding;onOpen:(target:ViewerTarget)=>void}) {
 const evidence=finding.figure_evidence,vision=finding.vision_review;
 if(finding.rule_id!=='NP007')return null;
 const labels:Record<string,string>={chart:'数据图',diagram:'结构图 / 用例图',table_screenshot:'表格截图',ui_screenshot:'界面截图',photo:'照片',other:'其他图片'};
 return <div className="mt-3 rounded border border-stone-200 bg-stone-50 p-3 text-sm leading-6">
  {evidence?.preview_asset_id && <button className="mb-3 flex w-full items-center gap-4 rounded border border-stone-200 bg-white p-3 text-left" onClick={()=>onOpen({id:evidence.preview_asset_id!,label:finding.text+' · 审阅图片证据',kind:'image',imageFocusFraction:evidence.caption_y_fraction})}><img loading="lazy" src={`/api/assets/${evidence.preview_asset_id}`} alt={finding.text} className="max-h-44 w-28 object-contain"/><span className="text-xs text-teal-800">在应用内放大查看审阅证据<br/>{evidence.evidence_basis==='word_page'?`Word 实际排版 · 物理页 ${evidence.physical_page}`:evidence.evidence_basis==='pdf_page'?`PDF 原稿 · 物理页 ${evidence.physical_page}`:'已核验原图'}{evidence.width_cm && <><br/>文档放置宽度约 {evidence.width_cm.toFixed(1)} cm</>}</span></button>}
  {vision?.validated_image ? <><p className="font-medium text-teal-900">Agent 查看图片后的判断 · {labels[vision.image_kind] || vision.image_kind}{vision.reused?' · 沿用此前对同一图片的有效判断':''}</p><p className="mt-2">{vision.reason}</p><ul className="mt-2 list-disc pl-5">{vision.observations.map((item,index)=><li key={index}>{item}</li>)}</ul>{vision.visible_text.length>0 && <p className="mt-2 text-xs text-stone-500">模型识别的图内文字：{vision.visible_text.join('、')}</p>}{vision.decision!=='not_issue' && vision.suggestion && <p className="mt-2">建议：{vision.suggestion}</p>}</> : <><p className="font-medium">模型尚未完成该图片的审阅</p><p className="mt-2 text-xs text-stone-500">{evidence?.reviewable?'图片证据已准备，任务将在用量上限内自动审阅。达到上限后可继续处理剩余图片；未审阅图片不计为已确认错误。':evidence?.note || '尚未可靠建立图题与图片的对应关系，需人工核验，不依据图题单独判定错误。'}</p></>}
 </div>;
}

function ReferenceDiagnosis({finding,paper,onOpen}:{finding:NewPaperFinding;paper:NewPaper;onOpen:(target:ViewerTarget)=>void}) {
 const diagnosis=finding.reference_diagnostic;
 if(!diagnosis)return null;
 const labels:Record<string,string>={nearby_number_mismatch:'附近同类图表的题注编号不同',possible_kind_mismatch:'有同编号的其他类型图表，需要比较内容',duplicate_target:'题注编号重复，无法唯一确定引用的是哪一个',unlabelled_nearby_object:'附近有图表或公式，尚未找到对应题注',target_not_found:'尚未找到被引用的图表或公式'};
 const selected=finding.semantic_review?.validated_quote?finding.semantic_review.target_id:undefined;
 const primary=diagnosis.candidates.find(target=>target.id===selected) || diagnosis.candidates[0];
 const otherCandidates=diagnosis.candidates.filter(target=>target.id!==primary?.id);
 const renderTarget=(target:ReferenceTarget)=><div key={target.id} className={`rounded border bg-white p-3 ${target.id===selected?'border-teal-500':'border-stone-200'}`}>
   <p className="font-medium text-stone-800">{target.id===selected && <span className="mr-2 rounded bg-teal-50 px-2 py-1 text-xs text-teal-800">Agent 选定目标</span>}{target.text}</p>
   <p className="mt-1 text-xs text-stone-500">{target.location?.label || '原文题注'}{target.physical_page?` · ${paper.source_format==='pdf'?'PDF':'Word'} 物理页 ${target.physical_page}`:''}</p>
   <p className="mt-1 text-xs text-stone-600">{target.id===selected?'Agent 已结合引用处描述与此题注比较内容；判断依据见下方。':target.relation==='same_number_other_kind'?'同号的另一类对象；数字相同不能证明是正确目标':target.relation==='duplicate_same_label'?'重复编号候选；需要区分真实目标':target.same_heading_scope?'与引用处位于同一小节；仍需比较内容':'同类附近题注；仍需比较内容'}</p>
   <div className="mt-2 flex flex-wrap gap-3 text-xs text-teal-800">{paper.source_asset_id && <button className="inline-flex items-center gap-1" onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · ${target.text}`,kind:'document',focusText:target.text,focusParagraph:sourceParagraph(paper,target.paragraph_index),page:target.physical_page,preferWordLayout:!!target.physical_page})}><BookOpen size={13}/>在应用内查看候选目标</button>}{target.preview_asset_id && <button onClick={()=>onOpen({id:target.preview_asset_id!,label:target.text+' · Word 页面证据',kind:'image',imageFocusFraction:target.caption_y_fraction})}>放大页面证据</button>}</div>
  </div>;
 return <div className="mt-3 rounded border border-stone-200 bg-stone-50 p-3 text-sm leading-6">
  <p className="font-medium">原引用：<span className="text-teal-900">{diagnosis.reference_text}</span></p>
  <p className="mt-1 text-xs text-stone-600">引用查找结果：{labels[diagnosis.classification] || diagnosis.classification}。已找到同类型、同编号的题注 {diagnosis.exact_target_count} 个。</p>
  {primary ? <div className="mt-3 space-y-2">{renderTarget(primary)}{otherCandidates.length>0 && <details className="mt-2 text-xs"><summary className="cursor-pointer text-teal-800">比较其他候选（{otherCandidates.length} 项）</summary><div className="mt-2 space-y-2">{otherCandidates.map(renderTarget)}</div></details>}</div> : <p className="mt-2 text-xs text-stone-600">尚未取得可靠的替代题注。{diagnosis.nearby_image_paragraph_indices.length?'引用附近存在图片对象，应检查是否漏写题注。':'尚不能确认对象是否缺失，需要核对对象和题注。'}</p>}
  {diagnosis.unparsed_caption_count>0 && <p className="mt-2 text-xs text-stone-600">另有 {diagnosis.unparsed_caption_count} 处文本框题注未加入可靠目标清单；不能据此断定对象不存在。</p>}
 </div>;
}

function ExcludedClue({clue,paper,onOpen}:{clue:NewPaperExcludedClue;paper:NewPaper;onOpen:(target:ViewerTarget)=>void}) {
 const thanks = /致谢|谢辞/.test(clue.location?.section || '') || /致谢|谢辞/.test(clue.reason);
 const label = thanks ? '致谢用语 · 不按正文问题处理' : clue.disposition === 'outside_body_scope' ? '正文范围外' : clue.disposition === 'lexical_exclusion' ? '此处的“我”不属于第一人称用法' : '所属范围需确认';
 return <article className="mt-3 rounded-md border border-stone-200 bg-stone-50 p-4">
  <div className="flex flex-wrap items-start justify-between gap-2"><p className="text-xs font-medium text-stone-600">{clue.location?.label || '原文范围待定位'} · 此段第 {clue.start + 1}</p><span className="rounded border border-stone-200 bg-white px-2 py-1 text-xs text-stone-500">{label}</span></div>
  <div className="mt-3 border-l-2 border-stone-300 bg-white p-3"><HighlightText text={clue.text} start={clue.start} end={clue.end}/></div>
  <p className="mt-3 text-xs leading-5 text-stone-500">{clue.reason}</p>
  {paper.source_asset_id && <button onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · “我”字原文线索`,kind:'document',focusText:clue.text,focusParagraph:sourceParagraph(paper,clue.paragraph_index)})} className="mt-3 inline-flex items-center gap-1 text-xs text-teal-800"><BookOpen size={13}/>在原稿中定位该段落</button>}
 </article>;
}

export function NewPaperReview({sessionToken,onOpen:openViewer,onDocumentsChange}:{sessionToken:string;onOpen:(target:ViewerTarget)=>void;onDocumentsChange:()=>void}) {
 const [data,setData] = useState<NewPaperList|null>(null);
 const [selected,setSelected] = useState('');
 const [file,setFile] = useState<File|null>(null);
 const [name,setName] = useState('');
 const [nameHint,setNameHint] = useState('');
 const [nameEdited,setNameEdited] = useState(false);
 const [policyNotice,setPolicyNotice] = useState('');
 const [maxRequests,setMaxRequests] = useState(16);
 const [maxTokens,setMaxTokens] = useState(60000);
 const [history,setHistory] = useState<{review_id:string;current:boolean;known_total_tokens:number;requests:number;status:string;review_scope:string}[]>([]);
 const [historicalPaper,setHistoricalPaper] = useState<NewPaper|null>(null);
 const [historyLoading,setHistoryLoading] = useState(false);
 const [useAgent,setUseAgent] = useState(false);
 const [chapterPolicy,setChapterPolicy] = useState(false);
 const [threeLineTables,setThreeLineTables] = useState(false);
 const [paperType,setPaperType] = useState('');
 const [key,setKey] = useState('');
 const [error,setError] = useState('');
 const [loading,setLoading] = useState(true);
 const [uploadOpen,setUploadOpen] = useState(true);
 const [busy,setBusy] = useState(false);
 const [identityDraft,setIdentityDraft] = useState('');
 const [identitySaving,setIdentitySaving] = useState(false);
 const [filter,setFilter] = useState('all');
 const [resultGroup,setResultGroup] = useState<'changes'|'compliant'|'unreviewed'|'pending'>('changes');
 const inputRef = useRef<HTMLInputElement>(null);
 const currentPaper = data?.papers.find(item=>item.id === selected) ?? data?.papers[0];
 const paper = historicalPaper?.id === currentPaper?.id ? historicalPaper : currentPaper;
 const onOpen=(target:ViewerTarget)=>{
  if(paper && (paper.source_format==='pdf'||/\.pdf$/i.test(paper.file_name)) && target.id===paper.source_asset_id){
   const paragraph=paper.report?.paragraphs.find(item=>item.index===target.focusParagraph?.paragraphIndex);
   const page=target.page??paragraph?.physical_page??paragraph?.page??paragraph?.location?.physical_page??paragraph?.location?.page;
   openViewer({...target,kind:'pdf',page});
  }else openViewer(target);
 };
 const activeSignature = data?.papers.filter(item=>activeStates.includes(item.status)).map(item=>item.id).join('|') || '';
 const checksEnabled=data?.capabilities.checks_enabled!==false;
 const configurationMessage=data?.capabilities.configuration_message||'论文检查来源尚未配置，请先完成工作区规则与证据配置。';
 const maxBytes = (data?.capabilities.max_file_mb ?? 20) * 1024 * 1024;

 useEffect(()=>{
  if(!currentPaper)return;
  let alive=true;
  setHistoricalPaper(null);
  api<{reviews:typeof history}>(`/api/new-papers/${encodeURIComponent(currentPaper.id)}/history`).then(value=>{if(alive)setHistory(value.reviews)}).catch(()=>{if(alive)setHistory([])});
  return ()=>{alive=false};
 },[currentPaper?.id,currentPaper?.review_id,currentPaper?.status]);

 useEffect(()=>{if(paper) setIdentityDraft(paper.student_name)},[paper?.id,paper?.student_name]);
 useEffect(()=>{if(paper){setThreeLineTables(paper.three_line_tables===true);setChapterPolicy(paper.chapter_policy===true);setPaperType(paper.paper_type??'')}},[paper?.id]);

 useEffect(()=>{
  let alive = true;
  api<NewPaperList>('/api/new-papers').then(value=>{if(alive){setData(value);setSelected(value.papers[0]?.id || '');setUploadOpen(value.papers.length===0)}}).catch(e=>alive && setError((e as Error).message)).finally(()=>alive && setLoading(false));
  return ()=>{alive = false};
 },[]);

 useEffect(()=>{
  if(!activeSignature) return;
  let alive = true;
  const ids = activeSignature.split('|');
  const timer = setInterval(()=>{
   for(const id of ids) api<NewPaper>(`/api/new-papers/${encodeURIComponent(id)}`).then(updated=>{
    if(!alive) return;
    setData(previous=>previous ? {...previous,papers:previous.papers.map(item=>item.id === id ? updated : item)} : previous);
    if(!activeStates.includes(updated.status)) onDocumentsChange();
   }).catch(e=>alive && setError((e as Error).message));
  },1500);
  return ()=>{alive=false;clearInterval(timer)};
 },[activeSignature]);

 function chooseFile(next:File|null) {
  setError('');
  if(!next){setFile(null);return}
  if(!/\.(docx|pdf)$/i.test(next.name)){setError('此上传入口支持 DOCX 和 PDF。旧 DOC 文件请先另存为 DOCX 或 PDF，再上传。');setFile(null);return}
  if(next.size <= 0 || next.size > maxBytes){setError(`请选择非空、大小不超过 ${data?.capabilities.max_file_mb ?? 20} MB 的 DOCX 或 PDF 文件。`);setFile(null);return}
  setFile(next);
  setPolicyNotice(chapterPolicy||threeLineTables?'已选择新文件：分章与三线表要求已重置，请依据本稿适用的教师要求重新确认。':'已选择新文件：分章与三线表要求默认关闭。');
  setThreeLineTables(false);setChapterPolicy(false);setPaperType('');
  const suggestion = inferStudentName(next.name);
  if(!nameEdited)setName(suggestion);
  setNameHint(suggestion);
 }

 async function refresh() {
  setBusy(true);setError('');
  try{setData(await api<NewPaperList>('/api/new-papers'));onDocumentsChange()}catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }

 async function submit() {
  if(!checksEnabled){setError(configurationMessage);return;}
  if(!file) return;
  if(!Number.isInteger(maxRequests)||maxRequests<1||maxRequests>(data?.capabilities.default_max_requests??16)||!Number.isInteger(maxTokens)||maxTokens<1000||maxTokens>(data?.capabilities.default_max_reserved_tokens??60000)){setError('请填写有效用量上限：模型请求次数需为 1 至页面上限的整数，token 需为 1000 至页面上限的整数。');return}
  if(!name.trim()){setError('请填写学生姓名，以便区分论文。');return}
  if(useAgent && !data?.capabilities.api_key_available && !key.trim()){setError('请填写 DeepSeek API Key，或切换为仅本地检查。');return}
  setBusy(true);setError('');
  try{
   const headers:Record<string,string> = {'Content-Type':'application/octet-stream','X-Session-Token':sessionToken,'X-File-Name':encodeURIComponent(file.name),'X-Student-Name':encodeURIComponent(name.trim()),'X-Use-Agent':String(useAgent),'X-Chapter-Policy':String(chapterPolicy),'X-Three-Line-Tables':String(threeLineTables),'X-Paper-Type':paperType,'X-Max-Requests':String(maxRequests),'X-Max-Total-Bound':String(maxTokens)};
   if(useAgent && key.trim()) headers['X-DeepSeek-Key'] = key.trim();
   const created = await api<NewPaper>('/api/new-papers',{method:'POST',headers,body:file});
   setKey('');setFile(null);if(inputRef.current)inputRef.current.value='';
   setNameEdited(false);setName('');setNameHint('');setPolicyNotice('');
   setData(previous=>previous ? {...previous,papers:[created,...previous.papers.filter(item=>item.id !== created.id)]} : previous);
   setSelected(created.id);setFilter('all');onDocumentsChange();
  }catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }

 async function reviewAgain(scope:'full'|'figures'|'content'='full') {
  if(!checksEnabled){setError(configurationMessage);return;}
  if(!paper)return;
  if(paper.historical)return;
  if(!Number.isInteger(maxRequests)||maxRequests<1||maxRequests>(data?.capabilities.default_max_requests??16)||!Number.isInteger(maxTokens)||maxTokens<1000||maxTokens>(data?.capabilities.default_max_reserved_tokens??60000)){setError('请先填写有效的本次模型请求次数和 token 上限。');return}
  const agent=scope==='figures'||scope==='content'||useAgent;
  if(agent && !data?.capabilities.api_key_available && !key.trim()){setError('图片、章节内容或 Agent 全范围审阅需要 DeepSeek API Key；可使用服务器配置。');return}
  setBusy(true);setError('');
  try{
   const headers:Record<string,string> = {'Content-Type':'application/json','X-Session-Token':sessionToken};
   if(agent && key.trim()) headers['X-DeepSeek-Key']=key.trim();
   const updated=await api<NewPaper>(`/api/new-papers/${encodeURIComponent(paper.id)}/review`,{method:'POST',headers,body:JSON.stringify({use_agent:agent,chapter_policy:chapterPolicy,three_line_tables:threeLineTables,paper_type:paperType,review_scope:scope,max_requests:maxRequests,max_total_bound:maxTokens})});
   setHistoricalPaper(null);
   setKey('');setData(previous=>previous ? {...previous,papers:previous.papers.map(item=>item.id === updated.id ? updated : item)} : previous);
  }catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }

 async function saveIdentity() {
  if(!paper || !identityDraft.trim()) {setError('请填写用于区分论文的学生姓名。');return}
  setIdentitySaving(true);setError('');
  try {
   const updated = await api<NewPaper>(`/api/new-papers/${encodeURIComponent(paper.id)}/identity`,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':sessionToken},body:JSON.stringify({student_name:identityDraft.trim()})});
   setData(previous=>previous ? {...previous,papers:previous.papers.map(item=>item.id === updated.id ? updated : item)} : previous);
   onDocumentsChange();
  } catch(e) {setError((e as Error).message)} finally {setIdentitySaving(false)}
 }

 async function selectReview(reviewId:string) {
  if(!currentPaper)return;
  if(!reviewId||reviewId===currentPaper.review_id){setHistoricalPaper(null);return}
  setHistoryLoading(true);setError('');
  try{setHistoricalPaper(await api<NewPaper>(`/api/new-papers/${encodeURIComponent(currentPaper.id)}/history/${encodeURIComponent(reviewId)}`))}catch(e){setError((e as Error).message)}finally{setHistoryLoading(false)}
 }

 async function managePaper(action:'cancel'|'archive') {
  if(!currentPaper)return;
  setBusy(true);setError('');
  try{
   const updated=await api<NewPaper>(`/api/new-papers/${encodeURIComponent(currentPaper.id)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':sessionToken},body:'{}'});
   if(action==='cancel')setData(previous=>previous?{...previous,papers:previous.papers.map(item=>item.id===updated.id?updated:item)}:previous);
   else{setData(previous=>previous?{...previous,papers:previous.papers.filter(item=>item.id!==currentPaper.id)}:previous);setHistoricalPaper(null);setSelected('');onDocumentsChange()}
  }catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }

 const report = paper?.report;
 const pdfSource=paper?.source_format==='pdf'||/\.pdf$/i.test(paper?.file_name??'');
 const run = paper?.run || report?.run;
 function groupFor(item:NewPaperFinding):'changes'|'compliant'|'unreviewed'|'pending'{
  if(item.review_blocked)return 'pending';
  if(item.content_scope && !item.semantic_review?.validated_scope && item.status!=='confirmed_issue')return 'unreviewed';
  const decision=item.vision_review?.validated_image?item.vision_review.decision:item.semantic_review?.validated_quote?item.semantic_review.decision:item.local_review?.validated_structure?item.local_review.decision:undefined;
  if(item.status==='confirmed_issue'||decision==='suggested_revision')return 'changes';
  if(decision==='not_issue')return 'compliant';
  if(item.rule_id==='NP007' && item.figure_evidence?.reviewable && !item.vision_review?.validated_image)return 'unreviewed';
  if(item.status==='candidate' && (item.semantic_required || ['NP001','NP002'].includes(item.rule_id)) && !item.semantic_review?.validated_quote)return 'unreviewed';
  return 'pending';
 }
 const groupCounts={changes:0,compliant:0,unreviewed:0,pending:0};
 for(const item of report?.findings??[])groupCounts[groupFor(item)]++;
 const findings = report?.findings?.filter(item=>(filter === 'all' || item.rule_id === filter)&&groupFor(item)===resultGroup) || [];
 useEffect(()=>{setResultGroup(groupCounts.changes?'changes':groupCounts.unreviewed?'unreviewed':groupCounts.pending?'pending':'compliant')},[paper?.id,groupCounts.changes,groupCounts.unreviewed,groupCounts.pending]);
 const excludedClues = report?.excluded_clues || [];
 const rawFirstPersonCount = report?.raw_first_person_count ?? (excludedClues.length + (report?.findings?.filter(item=>item.rule_id === 'NP001').length || 0));
 const allBusy = busy || !!data?.papers.some(item=>activeStates.includes(item.status));
 return <div className="space-y-5">
  <details className="app-upload-panel rounded-lg border border-stone-200 bg-white p-5" open={uploadOpen} onToggle={e=>setUploadOpen(e.currentTarget.open)}>
   <summary className="app-upload-summary"><span className="flex items-center gap-2 text-sm font-semibold"><Upload size={17}/>新论文上传与规则审阅</span><span className="text-xs font-normal text-stone-500">{uploadOpen?'收起上传配置':'上传新论文'}</span></summary>
   <div className="mt-4 flex items-start justify-between gap-3"><div><h2 className="sr-only">新论文上传与规则审阅</h2><p className="mt-2 text-sm leading-6 text-stone-500">按已建立的教师反馈规则与补充检查项核查论文，展示原文、依据和修改建议。每次检查结果单独保存，具体检查范围见下方；不代表已覆盖所有论文问题。</p></div><Upload size={22} className="mt-1 shrink-0 text-teal-800"/></div>
   {!checksEnabled&&<p role="status" className="mt-4 rounded border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-950">{configurationMessage} 上传检查与继续审阅暂不可用；已保存记录仍可查看。</p>}
   {loading && <p role="status" className="mt-4 text-sm text-stone-500">正在读取检查配置…</p>}
   <div className="mt-5 grid gap-5 lg:grid-cols-2">
    <div className="space-y-4">
     <label className="block text-sm font-medium">学生姓名<input className={`${fieldClass} mt-2`} value={name} onChange={e=>{setName(e.target.value);setNameEdited(true)}} maxLength={80} placeholder="填写便于区分论文的姓名，如黄发平" autoComplete="off"/>{nameHint && <span className="mt-1 block text-xs font-normal text-stone-500">从文件名识别的姓名：“{nameHint}”。{nameEdited?'已保留手动填写的姓名，请核对其与文件名识别结果的差异。':'已自动填写，请核对后上传。'}</span>}</label>
     <label className="block text-sm font-medium">论文文件（DOCX / PDF）<input ref={inputRef} type="file" accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={e=>chooseFile(e.target.files?.[0] || null)} className="mt-2 block w-full rounded-md border border-dashed border-stone-300 bg-stone-50 p-4 text-sm file:mr-3 file:rounded file:border-0 file:bg-teal-800 file:px-3 file:py-2 file:text-white"/></label>
     <p className="text-xs leading-5 text-stone-500">支持不超过 {data?.capabilities.max_file_mb ?? 20} MB 的 DOCX 或 PDF 文件。PDF 按物理页提取原文；扫描页的文字提取情况会在检查覆盖中说明。显示姓名来自文件名或手动填写，不是身份核验结果。上传副本用于检查，原文件不会被改写。</p>
     {policyNotice && <p role="status" className="rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900">{policyNotice}</p>}
    </div>
    <div className="space-y-4">
     <fieldset><legend className="text-sm font-medium">批改模式</legend><div className="mt-2 flex flex-wrap gap-4 text-sm"><label className="flex items-center gap-2"><input type="radio" name="new-paper-mode" checked={useAgent} onChange={()=>setUseAgent(true)}/>Agent 批改</label><label className="flex items-center gap-2"><input type="radio" name="new-paper-mode" checked={!useAgent} onChange={()=>setUseAgent(false)}/>仅本地检查</label></div><p className="mt-2 text-xs leading-5 text-stone-500">{useAgent?'本地工具提取检查线索，Agent 结合上下文审阅文字与可读取的图片；任务在本次用量上限内自动分批处理，并记录实际请求次数与 token 用量。':'仅执行本地检查，不调用模型。涉及上下文理解或图像判断的项目保留为尚未审阅或需人工核验状态，不计为已确认错误。'}</p></fieldset>
     {useAgent && <fieldset className="rounded border border-stone-200 bg-stone-50 p-3"><legend className="px-1 text-sm font-medium">本次模型用量上限</legend><div className="grid grid-cols-2 gap-3"><label className="text-xs text-stone-600">最多调用模型次数<input type="number" min={1} max={data?.capabilities.default_max_requests??16} value={maxRequests} onChange={e=>setMaxRequests(Number(e.target.value))} className={`${fieldClass} mt-1`}/></label><label className="text-xs text-stone-600">输入与输出 token 预留上限<input type="number" min={1000} step={1000} max={data?.capabilities.default_max_reserved_tokens??60000} value={maxTokens} onChange={e=>setMaxTokens(Number(e.target.value))} className={`${fieldClass} mt-1`}/></label></div><p className="mt-2 text-xs leading-5 text-stone-500">token 是模型计算用量的单位，并非字数。程序在每次请求前预留输入与输出用量，预计超过上限时保存进度并停止发起新请求；此设置用于本机任务限额，不能替代供应商的账户费用限额。</p></fieldset>}
     <p className="rounded border border-teal-200 bg-teal-50 p-3 text-xs leading-6 text-teal-900">{useAgent?'外部 API 数据处理说明：Agent 将检查所需的论文原文、上下文和待审图片发送给 DeepSeek。正文对已识别的学生或教师姓名、邮箱、学号、手机号脱敏，但无法保证识别所有姓名；图片内信息请自行检查。不将完整论文文件作为模型请求附件发送。本机网页原稿预览不向外部服务发送文档。':'仅本地检查和网页原稿预览不向模型发送论文内容；切换 Agent 模式后会发送检查所需的原文与待审图片。'}</p>
     {useAgent && <label className="block text-sm font-medium">DeepSeek API Key{data?.capabilities.api_key_available && <span className="ml-2 text-xs font-normal text-teal-800">服务器已配置，可留空</span>}<input type="password" value={key} onChange={e=>setKey(e.target.value)} className={`${fieldClass} mt-2`} placeholder={data?.capabilities.api_key_available?'留空使用服务器配置':'输入 API Key，仅用于本次任务'} autoComplete="off" spellCheck={false}/><span className="mt-2 block text-xs font-normal leading-5 text-stone-500">手动填写的 Key 不写入本地存储或运行记录；任务受理后清空输入框。</span></label>}
     <label className="block text-sm">论文类型（用于参考文献数量核查）<select value={paperType} onChange={e=>setPaperType(e.target.value)} className={`${fieldClass} mt-2`}><option value="">未确认，不按数量阈值判错</option><option value="engineering_design">工程设计类</option><option value="civil_engineering">土建类工程设计</option><option value="experimental_research">试验研究类</option><option value="theoretical_research">理论探讨类（理科）</option><option value="software">计算机软件类</option><option value="humanities">经济、管理及文科类</option></select><span className="mt-1 block text-xs leading-5 text-stone-500">按任务书或教师确认选择；不从题目推断类型。外文文献语言仍须可靠核对。</span></label>
     <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={chapterPolicy} onChange={e=>setChapterPolicy(e.target.checked)} className="mt-1"/><span>此论文要求“需求分析”和“系统设计”独立成章<span className="mt-1 block text-xs leading-5 text-stone-500">仅在教师或模板明确要求时启用；其他类型论文默认不适用该项规则。</span></span></label>
     <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={threeLineTables} onChange={e=>setThreeLineTables(e.target.checked)} className="mt-1"/><span>此论文明确要求表格采用三线表<span className="mt-1 block text-xs leading-5 text-stone-500">根据本稿适用的教师或模板要求启用。未启用时，边框信息仅作记录，不依据三线表格式判定错误；其他论文的教师批注不自动作为本稿格式要求。</span></span></label>
    </div>
   </div>
   <div className="mt-5 flex flex-wrap items-center gap-3"><button onClick={submit} disabled={!checksEnabled || !file || !data || allBusy} className="inline-flex items-center gap-2 rounded-md bg-teal-800 px-4 py-2.5 text-sm text-white hover:bg-teal-900 disabled:cursor-not-allowed disabled:opacity-50">{busy?<Loader2 size={16} className="animate-spin"/>:<Upload size={16}/>}上传并开始{useAgent?' Agent 批改':'本地检查'}</button><span className="text-xs text-stone-500">{file ? `${file.name} · ${(file.size/1024/1024).toFixed(2)} MB` : '请选择一份新论文'}</span></div>
  </details>
  {error && <div role="alert" className="flex items-start gap-2 rounded border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-800"><AlertCircle size={17} className="mt-1 shrink-0"/>{error}</div>}
  <div className="app-review-grid">
   <aside className="app-paper-list rounded-lg border border-stone-200 bg-white"><div className="flex items-center justify-between border-b p-4"><h3 className="text-sm font-medium">新论文批改记录</h3><button onClick={refresh} disabled={busy} aria-label="刷新新论文列表" className="rounded p-1 text-teal-800 hover:bg-stone-50"><RefreshCw size={15}/></button></div>{data?.papers.map(item=>{const inferred=inferStudentName(item.file_name); return <button key={item.id} onClick={()=>{setSelected(item.id);setFilter('all')}} className={`w-full border-b border-stone-100 p-4 text-left hover:bg-stone-50 ${item.id === paper?.id ? 'border-l-4 border-l-teal-700 bg-teal-50/50 pl-3':''}`}><p className="text-sm font-medium">{item.student_name}</p><p className="mt-1 break-all text-xs leading-5 text-stone-500">{item.file_name}</p>{inferred && inferred !== item.student_name && <p className="mt-1 text-xs text-amber-800">文件名提示：{inferred}（请核对）</p>}<p className="mt-2 text-xs text-teal-800">{paperStateLabel(item)}</p><p className="mt-1 text-xs tabular-nums text-stone-400">{new Date(item.created_at).toLocaleString('zh-CN')}</p></button>})}{data?.papers.length === 0 && <p className="p-5 text-sm leading-6 text-stone-500">尚无新论文记录。上传后，审阅结果将在本页保存并显示。</p>}</aside>
   <div className="app-paper-detail space-y-4">
    {currentPaper && <section aria-label="审阅轮次与任务管理" className="rounded-lg border border-stone-200 bg-white p-4"><div className="flex flex-wrap items-end gap-3"><label className="min-w-56 flex-1 text-xs text-stone-500">选择审阅记录<select aria-label="选择审阅记录" value={paper?.review_id||''} onChange={e=>selectReview(e.target.value)} disabled={historyLoading} className={`${fieldClass} mt-1`}><option value={currentPaper.review_id}>最新审阅记录{currentPaper.review_id?` · ${currentPaper.review_id.slice(7,22)}`:''}</option>{history.filter(item=>!item.current).map((item,index)=><option key={item.review_id} value={item.review_id}>历史审阅 {history.length-index-1} · {item.review_id.slice(7,22)} · {item.known_total_tokens.toLocaleString('zh-CN')} token · {item.requests} 次请求</option>)}</select></label>{activeStates.includes(currentPaper.status)?<button className={buttonClass} disabled={busy||currentPaper.status==='cancelling'} onClick={()=>managePaper('cancel')}>{currentPaper.status==='cancelling'?'等待当前步骤完成':'取消本次检查并保留结果'}</button>:<details className="text-xs text-stone-500"><summary className="cursor-pointer">归档论文记录</summary><p className="my-2 max-w-64 leading-5">归档后，该记录不在当前列表中显示；论文原件及历次审阅结果仍保留在本机。</p><button className={buttonClass} disabled={busy} onClick={()=>managePaper('archive')}>归档记录并保留文件</button></details>}</div>{paper?.historical&&<p role="status" className="mt-3 text-xs leading-5 text-teal-800">当前为历史审阅记录的只读视图，不影响最新审阅结果。选择最新审阅记录后可继续执行检查。{!run?.review_contract&&' 该结果生成于此前版本，尚未通过当前程序重新核验。'}</p>}{activeStates.includes(currentPaper.status)&&<p className="mt-3 text-xs leading-5 text-stone-500">取消不会立即中断正在进行的步骤：当前模型请求或本地步骤完成后保存进度，并停止后续处理。已发生的模型用量仍会记录；关闭网页不会取消任务。</p>}</section>}
    {paper && <section className="rounded-lg border border-stone-200 bg-white p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs text-stone-500">新论文 / {paper.student_name}</p><h3 className="mt-2 break-all text-lg font-semibold">{paper.file_name}</h3><p role="status" className="mt-2 flex items-center gap-2 text-sm text-teal-800">{activeStates.includes(paper.status)?<Loader2 size={15} className="animate-spin"/>:['failed','interrupted'].includes(paper.status)?<AlertCircle size={15}/>:<CheckCircle2 size={15}/>} {paperStateLabel(paper)}</p><div className="mt-3 flex flex-wrap items-end gap-2"><label className="text-xs text-stone-500">显示姓名<input className={`${fieldClass} mt-1 w-56`} value={identityDraft} onChange={e=>setIdentityDraft(e.target.value)} maxLength={80}/></label><button className={buttonClass} disabled={identitySaving || allBusy || !!paper?.historical || !identityDraft.trim()} onClick={saveIdentity}>{identitySaving?<Loader2 size={14} className="animate-spin"/>:<Save size={14}/>}保存姓名</button><span className="pb-2 text-xs text-stone-400">仅更新显示姓名，不修改论文原件或核验学生身份</span></div></div><div className="flex flex-wrap gap-2">{paper.source_asset_id && <button className={buttonClass} onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 新上传论文`,kind:'document'})}><BookOpen size={14}/>在应用内查看原稿</button>}<button className={buttonClass} disabled={!checksEnabled || allBusy || !!paper?.historical} onClick={()=>reviewAgain()}><RefreshCw size={14}/>按当前配置执行检查与审阅</button><button className={buttonClass} disabled={pdfSource || !checksEnabled || allBusy || !!paper?.historical} title={pdfSource?'PDF 图片审阅尚未支持':undefined} onClick={()=>reviewAgain('figures')}><BookOpen size={14}/>继续图片审阅</button><button className={buttonClass} disabled={pdfSource || !checksEnabled || allBusy || !!paper?.historical} title={pdfSource?'PDF 完整章节语境审阅尚未支持':undefined} onClick={()=>reviewAgain('content')}><BookOpen size={14}/>继续章节内容审阅</button></div></div>{paper.error && <p role="alert" className="mt-4 whitespace-pre-wrap text-sm leading-6 text-red-800">{paper.error}</p>}
     <p className="mt-3 text-xs leading-6 text-stone-500">“按当前配置执行检查与审阅”使用当前选定的审阅模式、格式要求与用量上限，重新执行本地检查，并复用仍适用的已审阅结果。“继续图片审阅”和“继续章节内容审阅”分别调用模型处理对应内容，沿用当前记录已保存的分章与三线表要求。调整格式要求后，应执行“按当前配置执行检查与审阅”。</p>{report && <p className="mt-3 text-xs text-stone-500">本次已保存的三线表设置：{paper.three_line_tables ? '已明确启用' : '未启用，表格边框不按此格式判错'}。如需调整要求，请修改相应配置后执行“按当前配置执行检查与审阅”。</p>}
     {pdfSource&&<div role="status" className="mt-4 rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-950"><p className="font-medium">PDF 当前检查范围</p><p>按物理页提取可识别文字，并对当前支持的文字规则进行局部语境审阅。尚未覆盖扫描页 OCR、Word 原生表格与公式属性、完整章节语义和版面规范，不能认定整篇论文已完成审阅。</p>{report?.pdf_pages&&<p>原稿共 {report.pdf_pages.page_count} 个物理页；其中 {report.pdf_pages.unrecognized_pages.length} 页未提取到可识别文字，需要查看原稿或提供带文字层的 PDF。</p>}</div>}
     <ReviewProgress run={run} active={activeStates.includes(paper.status)}/>{run?.review_scope==='content' && <p className="mt-3 text-xs leading-5 text-stone-500">本次仅更新摘要、结论与实现章节的内容审阅；其他文字、图表引用与图片沿用原结果。要处理其他未审项目，请使用全范围检查或图片审阅按钮。</p>}
     {report && <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[{label:'建议修改',value:groupCounts.changes},{label:'已审阅内容符合要求',value:groupCounts.compliant},{label:'Agent 尚未审阅',value:groupCounts.unreviewed},{label:'需人工核验',value:groupCounts.pending}].map(stat=><div key={stat.label} className="rounded border border-stone-200 bg-stone-50 p-3"><p className="text-xs text-stone-500">{stat.label}</p><p className="mt-1 text-2xl font-semibold tabular-nums">{stat.value ?? 0}</p></div>)}</div>}
     {report?.summary.agent_suggested_count !== undefined && <p className="mt-3 text-xs leading-5 text-teal-800">每条检查记录只计入一个分区，同一段原文涉及不同检查项时可有多条记录。“已审阅内容符合要求”不代表整篇论文合格；尚未审阅和需人工核验不算已发现错误。</p>}
     {run?.phase_errors?.length ? <p role="status" className="mt-3 text-xs text-amber-800">部分模型回答未通过校验，已保存对应项目与原因。无法继续自动判定的项目列入“需人工核验”；因用量上限未处理的项目列入“Agent 尚未审阅”。</p> : null}
     {run?.refresh_kind==='explicit_table_policy_only' && <p className="mt-3 text-xs leading-5 text-stone-500">已更新本稿三线表检查的适用范围，文字与图片审阅沿用原结果；本次仅执行本地处理，未新增模型或 Word 调用。</p>}
     {run?.refresh_kind === 'local_new_rules_and_readable_locations' && <p className="mt-3 text-xs leading-5 text-stone-500">本次在本地更新检查项；沿用此前已核验的上下文判断 {run.semantic_evidence_reused ?? 0} 项{run.layout_evidence_reused ? '及已保存分页' : ''}，新增模型请求 {run.dispatched_requests ?? run.request_attempts ?? run.requests ?? 0} 次。</p>}
     {run?.error && <p role="alert" className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900">{run.error}</p>}
     {run && <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-stone-500"><span>本次模型调用：{run.dispatched_requests ?? run.request_attempts ?? run.requests ?? 0}</span><span>本次已记录 token：{run.known_total_tokens ?? '未记录'}</span><span>未返回用量的模型调用：{run.unknown_cost_attempts ?? 0}</span>{run.word_launches !== undefined && <span>启动 Word 读取排版：{run.word_launches} 次</span>}<span>已完成图片判断：{run.vision_reviewed ?? 0} 张 · 尚未审完 {run.vision_unreviewed ?? 0} 张</span><span>模型：{data?.capabilities.model || 'deepseek-flash'}</span></div>}
     {(paper.report_asset_id || paper.ledger_asset_id) && <div className="mt-4 flex gap-4 text-xs text-teal-800">{paper.report_asset_id && <button onClick={()=>onOpen({id:paper.report_asset_id!,label:`${paper.student_name} · 批改报告`,kind:'report'})}>在应用内阅读报告</button>}{paper.ledger_asset_id && <button onClick={()=>onOpen({id:paper.ledger_asset_id!,label:`${paper.student_name} · 本轮运行记录`,kind:'report'})}>查看运行记录</button>}</div>}
    </section>}
    {report && <>
     <FormatDiagnostics report={report} onOpen={onOpen}/>
     <details className="rounded-lg border border-stone-200 bg-white p-5"><summary className="cursor-pointer text-base font-semibold">本次检查项目与覆盖范围（{report.rules.length} 项，点击展开）</summary><p className="mt-2 text-xs leading-5 text-stone-500">此次采用检查项 {report.rules.length} 项。以下分别说明已执行、部分检查或不适用的项目；完成检查不表示论文完全符合要求。教师反馈保留原批注，额外的自动检查单列来源，不代表教师已确认的通用规范。</p><div className="mt-4 grid gap-3 md:grid-cols-2">{report.coverage.map(item=><article key={item.rule_id} className="rounded border border-stone-200 p-3"><div className="flex flex-wrap items-start justify-between gap-2"><p className="text-sm font-medium">{item.title} <span className="text-xs font-normal text-stone-400">{item.rule_id}</span></p><span className="rounded border border-stone-200 bg-stone-50 px-2 py-1 text-xs text-stone-600">{coverageLabels[item.status] || item.status}</span></div><p className="mt-2 text-xs leading-5 text-stone-500">{item.detail || item.status}</p>{report.rules.find(rule=>rule.id===item.rule_id)?.policy === 'supplemental_structural_check' && <p className="mt-2 text-xs text-stone-500">额外的自动检查 · 并非教师已确认的格式要求</p>}{report.rules.find(rule=>rule.id===item.rule_id)?.recurrence_status === 'cross_student_candidate' && <p className="mt-2 text-xs text-teal-800">不同学生批注中出现过的要求 · 仍需确认是否适用于本稿</p>}{item.excluded_objects?.length ? <details className="mt-3 text-xs text-stone-500"><summary className="cursor-pointer text-teal-800">查看未按本项要求检查的表格（{item.excluded_objects.length} 项）</summary><div className="mt-2 space-y-3">{item.excluded_objects.map(object=><div key={`${object.table_index}-${object.paragraph_index}`} className="rounded bg-stone-50 p-3"><p>{object.location?.label || 'Word 表格'} · Word 表格第 {object.table_index+1} 张</p><p className="mt-2 leading-5">{object.reason}</p><details className="mt-2"><summary className="cursor-pointer">表格原文</summary><p className="mt-2 whitespace-pre-wrap leading-6">{object.text}</p></details>{paper.source_asset_id && <button className="mt-2 text-teal-800" onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 表格检查范围`,kind:'document',focusText:object.text,focusTableIndex:object.table_index,focusTableCount:report.table_count??undefined,preferWordLayout:true,page:object.physical_pages?.[0]})}>在原稿中查看这张表格</button>}</div>)}</div></details> : null}{item.rule_id==='NP022' && item.policy_not_applicable && item.checked_objects?.length ? <details className="mt-3 text-xs text-stone-500"><summary className="cursor-pointer text-teal-800">查看保留的表格记录（{item.checked_objects.length} 张，不计为问题）</summary><div className="mt-2 space-y-3">{item.checked_objects.map(object=><div key={object.table_index} className="rounded bg-stone-50 p-3"><p className="font-medium text-stone-700">{object.scope_evidence?.captions?.join(' / ') || `Word 表格第 ${object.table_index+1} 张`}</p><p className="mt-1 leading-5">{object.location?.label}{object.physical_pages?.length ? ` · Word 物理页 ${object.physical_pages.join('–')}` : ''}</p><p className="mt-2 leading-5">本稿未启用三线表要求，边框信息仅作记录，不作为判定表格错误或符合要求的依据。</p>{paper.source_asset_id && <button className="mt-2 inline-flex items-center gap-1 text-teal-800" onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 表格原稿`,kind:'document',focusText:object.text,focusTableIndex:object.table_index,focusTableCount:report.table_count??undefined,preferWordLayout:true,page:object.physical_pages?.[0]})}><BookOpen size={13}/>查看 Word 排版中的表格</button>}</div>)}</div></details> : null}{item.rule_id === 'NP002' && <details className="mt-3 text-xs text-stone-500"><summary className="cursor-pointer">查看本稿章节层级</summary>{report.headings?.length ? <ol className="mt-2 space-y-2">{report.headings.map(heading=><li key={heading.index} className="rounded bg-stone-50 p-2"><span className="mr-2 text-stone-400">{heading.heading_level ? `${heading.heading_level} 级标题` : '标题线索'}</span>{heading.text}{paper.source_asset_id && <button className="ml-3 text-teal-800" onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 章节标题`,kind:'document',focusText:heading.text,focusParagraph:sourceParagraph(paper,heading.index)})}>查看原文</button>}</li>)}</ol> : <p className="mt-2 leading-5">本轮未取得可确认的标题层级。</p>}</details>}</article>)}</div></details>
     <section className="rounded-lg border border-stone-200 bg-white p-5"><div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-base font-semibold">原文位置与批改建议</h3><label className="flex items-center gap-2 text-xs text-stone-500">检查项<select value={filter} onChange={e=>setFilter(e.target.value)} className="rounded border border-stone-200 bg-white p-2"><option value="all">全部检查项</option>{report.rules.map(rule=><option key={rule.id} value={rule.id}>{rule.title}</option>)}</select></label></div><p className="mt-2 text-xs leading-5 text-stone-500">按四个分区查看结果；每条记录都尽量保留原文位置与判断依据。右侧可按检查项筛选。</p>
      <div role="tablist" aria-label="批改结果分区" className="mt-5 flex flex-wrap gap-2">{([{id:'changes',label:'建议修改'},{id:'compliant',label:'已审阅内容符合要求'},{id:'unreviewed',label:'Agent 尚未审阅'},{id:'pending',label:'需人工核验'}] as const).map(group=><button key={group.id} role="tab" aria-selected={resultGroup===group.id} onClick={()=>setResultGroup(group.id)} className={`rounded-lg border px-4 py-2 text-sm ${resultGroup===group.id?'border-teal-800 bg-teal-800 text-white':'border-stone-200 bg-white text-stone-600'}`}>{group.label} <span className="ml-2 tabular-nums">{groupCounts[group.id]}</span></button>)}</div>
      <p className="mt-3 text-xs leading-6 text-stone-500">{resultGroup==='changes'?'本分区列示模型与本地工具提出的修改建议，以及人工已确认的问题。自动建议仍需核对原文和本稿要求。':resultGroup==='compliant'?'本分区列示模型或检查工具判定符合本项要求的已审阅内容，保留原文和依据；不代表整篇论文全部合格。':resultGroup==='unreviewed'?'本分区列示 Agent 尚未完成审阅的文字或图片，不计为已确认错误。后续审阅将复用本稿中与当前要求一致且已通过核验的结果。':'本分区包含需确认适用要求、核验图像与版面或补充判断依据的项目；上述项目尚未确认为错误。'}数量按检查记录统计，同一段原文可能涉及不同检查项。</p>
      {findings.length === 0 && <p className="mt-5 rounded bg-stone-50 p-4 text-sm text-stone-500">当前分区没有符合筛选条件的记录；可切换分区或选择“全部检查项”。</p>}
      {findings.map(finding=><article key={finding.id} className="mt-4 rounded-md border border-stone-200 p-4"><div className="flex flex-wrap items-start justify-between gap-2"><h4 className="text-sm font-semibold">{finding.content_scope && finding.semantic_review?.validated_scope ? finding.title.replace(/(?:待语义审阅|待语境审阅)$/, "") : finding.title}</h4><span className="rounded border border-stone-200 bg-stone-50 px-2 py-1 text-xs text-stone-600">{groupFor(finding)==='compliant' ? '已审阅内容符合要求' : groupFor(finding)==='changes' ? finding.status==='confirmed_issue'?'人工已确认':finding.local_review?.validated_structure?'本地工具建议修改':'Agent 建议修改' : finding.review_blocked ? '无法自动判定' : finding.content_scope && !finding.semantic_review?.validated_scope || finding.semantic_required && !finding.semantic_review?.validated_quote ? '尚未结合上下文审阅' : finding.rule_id === 'NP007' && finding.status === 'needs_review' ? '需要查看实际图片' : findingLabels[finding.status] || finding.status}</span></div><p className="mt-2 text-xs text-stone-500">{finding.rule_id}{finding.location?.label ? ` · ${finding.location.label}` : typeof finding.paragraph_index === 'number' ? ' · 原文范围待定位' : ''}{finding.physical_pages?.length ? ` · 物理页 ${finding.physical_pages.join('–')}` : ''}{typeof finding.table_index === 'number' ? ` · Word 表格第 ${finding.table_index+1} 张` : ''}</p>{finding.scope_evidence?.captions?.length ? <p className="mt-2 text-sm text-teal-900">{finding.scope_evidence.captions.join(' / ')}</p> : null}{finding.policy === 'supplemental_structural_check' && <p className="mt-2 text-xs text-stone-500">额外的自动检查 · 并非教师已确认的格式要求</p>}{finding.semantic_required && !finding.reference_diagnostic && !finding.content_scope && <div className="mt-3 rounded border border-stone-200 bg-stone-50 p-3 text-xs leading-6 text-stone-600"><p>{finding.semantic_review?.validated_quote ? finding.semantic_review.decision==='not_issue' ? '模型已结合上下文检查此片段，判断符合本项要求。' : '模型已结合上下文给出判断；请核对下方原文引文与建议。' : finding.review_blocked ? '模型未能给出可核验的判断，具体原因如下；重复执行审阅不会将该项自动转为已确认问题。' : '已找到相关原文，但模型尚未结合上下文完成判断；仅完成原文提取不能作为判定错误的依据。'}</p>{finding.section_complete === false && <p>当前只覆盖章节片段{finding.omitted_paragraph_count ? `，还有 ${finding.omitted_paragraph_count} 个段落未送审` : ''}，不能据此断言整章缺少内容。</p>}{finding.context?.length ? <details className="mt-2"><summary className="cursor-pointer text-teal-800">查看同章节上下文（{finding.context.length} 段）</summary>{finding.context.map((context,index)=><div key={`${context.paragraph_index}-${index}`} className="mt-2 rounded border border-stone-200 bg-white p-3"><p className="text-stone-500">{context.location?.label || '同章节原文片段'}</p><p className="mt-2 whitespace-pre-wrap leading-7">{context.text}</p>{paper.source_asset_id && <button className="mt-2 text-teal-800" onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 章节上下文`,kind:'document',focusText:context.text,focusParagraph:sourceParagraph(paper,context.paragraph_index)})}>在应用内定位原文</button>}</div>)}</details> : null}</div>}{finding.table_text && <details className="mt-3 text-xs text-stone-500"><summary className="cursor-pointer">表格文字摘录</summary><p className="mt-2 whitespace-pre-wrap rounded bg-stone-50 p-3 leading-6">{finding.table_text}</p></details>}{finding.text && <div className="mt-3 border-l-2 border-teal-700 bg-stone-50 p-3"><Excerpt finding={finding}/></div>}{!finding.content_scope && <p className="mt-3 text-sm leading-6"><span className="font-medium">依据：</span>{finding.vision_review?.validated_image?finding.vision_review.reason:finding.reason}{finding.rule_id === 'NP007' && !finding.vision_review?.validated_image && <span className="mt-2 block text-xs text-stone-500">已定位待核验图片，尚未依据实际图像判定其清晰度；该记录不计为已确认错误。</span>}</p>}{groupFor(finding)!=='compliant' && !finding.vision_review && !finding.content_scope && finding.suggestion && <p className="mt-2 text-sm leading-6"><span className="font-medium">建议：</span>{finding.suggestion}</p>}
       {finding.review_blocked && <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-900"><p className="font-medium">无法自动判定</p><p className="mt-1">{finding.review_blocked.reason}</p></div>}
       {finding.local_review?.validated_structure && <p className="mt-3 text-xs leading-5 text-teal-800">本项由本地工具读取 Word 表格边框后得出，已核对需要采用三线表的表格范围；这是自动检查建议，不是人工已确认的问题。</p>}
       <ContentScopeReview finding={finding} paper={paper} onOpen={onOpen}/><ReferenceDiagnosis finding={finding} paper={paper} onOpen={onOpen}/><FigureReview finding={finding} onOpen={onOpen}/>
       {finding.semantic_review?.validated_quote && !finding.content_scope && <div className="mt-3 rounded border border-teal-200 bg-teal-50 p-3 text-sm leading-6"><p className="font-medium text-teal-900">Agent 结合上下文的判断：{({suggested_revision:'建议修改 · 待确认',not_issue:'此片段符合本项要求',needs_review:'仍需人工核验'} as Record<string,string>)[finding.semantic_review.decision] || finding.semantic_review.decision}</p>{finding.semantic_review.quote && <p className="mt-2 whitespace-pre-wrap text-xs text-teal-800">对应原文：{finding.semantic_review.quote}</p>}<p className="mt-2">{finding.semantic_review.reason}</p>{finding.semantic_review.decision!=='not_issue' && finding.semantic_review.suggestion && <p className="mt-2">建议：{finding.semantic_review.suggestion}</p>}</div>}
       {paper.source_asset_id && finding.physical_pages?.length ? <button onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 实际页面证据`,kind:'document',page:finding.physical_pages![0],preferWordLayout:true})} className="mt-3 inline-flex items-center gap-1 text-xs text-teal-800"><BookOpen size={13}/>查看原稿实际页面</button> : null}
       {paper.source_asset_id && finding.text && !finding.physical_pages?.length && <button onClick={()=>onOpen({id:paper.source_asset_id,label:`${paper.student_name} · 新上传论文`,kind:'document',focusText:finding.text,page:finding.location?.physical_page??finding.location?.page,focusParagraph:sourceParagraph(paper,finding.paragraph_index),focusTableIndex:finding.table_index,focusTableCount:report.table_count??undefined,preferWordLayout:typeof finding.table_index==='number'})} className="mt-3 inline-flex items-center gap-1 text-xs text-teal-800"><BookOpen size={13}/>在原稿中定位该段落</button>}
       {(finding.reference_requirements??[]).length>0 && <details className="mt-4 text-xs text-stone-600"><summary className="cursor-pointer">参考规范依据（{finding.reference_requirements!.length} 条）</summary><div className="mt-3 space-y-3">{finding.reference_requirements!.map(requirement=><ReferenceRequirementCard key={requirement.id} requirement={requirement} onOpen={onOpen}/>)}</div></details>}
       {(finding.teacher_evidence??[]).length > 0 && <details className="mt-4 text-xs text-stone-500"><summary className="cursor-pointer">教师批注依据（{(finding.teacher_evidence??[]).length} 条）</summary>{(finding.teacher_evidence??[]).map((evidence,index)=><Evidence key={evidence.id || index} item={evidence} onOpen={onOpen}/>)}</details>}
      </article>)}
      {(filter === 'all' || filter === 'NP001') && excludedClues.length > 0 && paper && <details className="mt-6 border-t border-stone-200 pt-5"><summary className="cursor-pointer text-sm font-medium">查看未按正文第一人称问题处理的“我”字（共找到 {rawFirstPersonCount} 处，排除 {excludedClues.length} 处）</summary><p className="mt-2 text-xs leading-5 text-stone-500">致谢、正文范围外及“我国”等不表示作者第一人称的用法，不按正文第一人称问题处理。下列记录保留排除原因，只供核对原文，不计为问题。</p>{excludedClues.map((clue,index)=><ExcludedClue key={`${clue.paragraph_index}-${clue.start}-${index}`} clue={clue} paper={paper} onOpen={onOpen}/>)}</details>}
     </section>
     <details className="rounded-lg border border-stone-200 bg-white p-5"><summary className="cursor-pointer text-sm font-medium">检查适用范围与覆盖限制</summary><ul className="mt-3 list-disc space-y-2 pl-5 text-xs leading-6 text-stone-500">{report.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul><div className="mt-4 space-y-3">{report.rules.map(rule=><article key={rule.id} className="rounded border border-stone-200 p-3"><p className="text-sm font-medium">{rule.title} · {rule.id}</p>{rule.policy === 'supplemental_structural_check' && <p className="mt-2 text-xs text-stone-500">额外的自动检查 · 并非教师已确认的格式要求</p>}{rule.recurrence_status === 'cross_student_candidate' && <p className="mt-2 text-xs text-teal-800">不同学生批注中出现过的要求 · 仍需确认是否适用于本稿</p>}{rule.recurrence_status === 'single_student_feedback' && <p className="mt-2 text-xs text-stone-500">目前仅有一名学生的批注依据 · 尚未证明是常见问题</p>}<p className="mt-1 text-xs leading-5 text-stone-500">适用范围：{rule.scope}</p>{(rule.reference_requirements??[]).map(requirement=><ReferenceRequirementCard key={requirement.id} requirement={requirement} onOpen={onOpen}/>)}{(rule.teacher_evidence??[]).map((item,index)=><Evidence key={item.id || index} item={item} onOpen={onOpen}/>)}</article>)}</div></details>
    </>}
    {!paper && !loading && <section className="rounded-lg border border-dashed border-stone-300 bg-white p-10 text-center"><FileText size={30} className="mx-auto text-stone-400"/><h3 className="mt-4 text-base font-medium">新论文审阅结果</h3><p className="mt-2 text-sm leading-6 text-stone-500">上传后可查看修改建议、已审阅内容符合要求、未审项目和需人工核验项目，直接在应用内打开对应原文。</p></section>}
   </div>
  </div>
 </div>;
}
