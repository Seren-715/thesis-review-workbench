import {useEffect,useRef,useState} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {renderAsync} from 'docx-preview';
import {BookOpen,FileText,Search,X,Download} from 'lucide-react';
import type {Asset,PaperDocument} from './types';
import {MarkdownReport} from './MarkdownReport';
import {normalizeLocationText,resolveTextLocation,type ParagraphBinding} from './documentLocation';

const assetUrl=(id:string)=>`/api/assets/${encodeURIComponent(id)}`;
const pdfPage=(value:number|undefined)=>Number.isSafeInteger(value)&&value!>0?value:undefined;
export interface ViewerTarget {id:string;label:string;kind:Asset['kind'];referenceMaterial?:boolean;focusText?:string;focusParagraph?:ParagraphBinding;sourceSha256?:string;focusTableIndex?:number;focusTableCount?:number;page?:number;imageFocusFraction?:number;preferWordLayout?:boolean}

export function DocumentViewer({target,documents,onClose,humanize}:{target:ViewerTarget|null;documents:PaperDocument[];onClose:()=>void;humanize:(text:string)=>string}){
 const [activeId,setActiveId]=useState(''),[loading,setLoading]=useState(false),[error,setError]=useState(''),[text,setText]=useState(''),[pdf,setPdf]=useState<Asset|null>(null),[positionMessage,setPositionMessage]=useState('');
 const bodyRef=useRef<HTMLDivElement>(null);
 const [locationChoices,setLocationChoices]=useState<{element:Element;text:string;before:string;after:string}[]>([]);
 const clearLocation=()=>{setLocationChoices([]);bodyRef.current?.querySelectorAll('.reader-text-focus').forEach(el=>el.classList.remove('reader-text-focus'))};
 const [pendingFocus,setPendingFocus]=useState('');
 const [pageDraft,setPageDraft]=useState('1');
 const pendingTargetBinding=useRef(true);
 const [remoteDocument,setRemoteDocument]=useState<PaperDocument|null>(null);
 const current=documents.find(d=>d.asset_id===activeId)??(remoteDocument?.asset_id===activeId?remoteDocument:undefined);
 const sourcePdf=current?(current.source_format==='pdf'||/\.pdf$/i.test(current.file_name)):target?.kind==='pdf';
 const visiblePdf=pdf??(sourcePdf&&activeId?{id:activeId,label:target?.label??'PDF 原稿',kind:'pdf' as const,page:pdfPage(target?.page)}:null);
 useEffect(()=>{setPageDraft(String(pdfPage(visiblePdf?.page)??1))},[visiblePdf?.id,visiblePdf?.page]);
 const versionChoices=new Map<string,PaperDocument>();
 for(const document of documents){const key=`${document.student_id}:${document.revision}:${document.sha256}`;if(!versionChoices.has(key)||document.asset_id===activeId)versionChoices.set(key,document)}
 if(remoteDocument?.asset_id===activeId)versionChoices.set(`remote:${activeId}`,remoteDocument);
 const documentChoices=Array.from(versionChoices.values());
 useEffect(()=>{setActiveId(target?.id??'');const first=documents.find(d=>d.asset_id===target?.id)?.pdf_assets?.[0];setPdf((target?.preferWordLayout||!target?.focusText)&&first?{...first,page:target?.page??first.page}:null);pendingTargetBinding.current=true;setPendingFocus(target?.focusText || '');setPositionMessage('');clearLocation()},[target?.id,target?.page,target?.focusText,target?.focusParagraph,target?.sourceSha256,target?.focusTableIndex,target?.focusTableCount,target?.preferWordLayout]);
 useEffect(()=>{if(current && target && current.asset_id===target.id && target.preferWordLayout && current.pdf_assets?.[0]){setPdf({...current.pdf_assets[0],page:target.page??current.pdf_assets[0].page});setPendingFocus('')}},[current?.asset_id,target?.id,target?.page,target?.focusTableIndex,target?.preferWordLayout]);
 useEffect(()=>{
  if(!target||target.kind!=='document'||!activeId||documents.some(d=>d.asset_id===activeId))return;
  const controller=new AbortController();setLoading(true);setError('');
  fetch(`/api/documents/${encodeURIComponent(activeId)}`,{signal:controller.signal}).then(async response=>{
   if(!response.ok)throw new Error(`原稿元数据读取失败（${response.status}）`);
   const value=await response.json() as PaperDocument;
   if(value.asset_id!==activeId)throw new Error('原稿身份不匹配');
   if(!controller.signal.aborted)setRemoteDocument(value);
  }).catch(e=>{if(!controller.signal.aborted){setError((e as Error).message);setLoading(false)}});
  return()=>controller.abort();
 },[activeId,target?.kind]);
 useEffect(()=>{
  if(!target||!activeId)return;
  if(sourcePdf){setLoading(false);setError('');setText('');return;}
  if(target.kind==='document'&&!current)return;
  if(current&&pdf){setLoading(false);setError('');return;}
  const controller=new AbortController();let alive=true;
  setLoading(true);setError('');setText('');setPositionMessage('');clearLocation();
  const holder=bodyRef.current;holder?.replaceChildren();
  (async()=>{
   const response=await fetch(assetUrl(activeId),{signal:controller.signal});
   if(!response.ok)throw new Error(`原稿读取失败（${response.status}），请重新读取已保存结果后重试。`);
   if(current){
    const blob=await response.blob();if(!alive||!holder)return;
    if(activeId===target.id && target.sourceSha256 && current.sha256!==target.sourceSha256)throw new Error('检查记录与当前原稿的来源哈希不一致，已停止原文定位。');
    if(current.sha256){
     const digest=await crypto.subtle.digest('SHA-256',await blob.arrayBuffer());
     const actual=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
     if(actual!==current.sha256.toLowerCase())throw new Error('原稿内容与文档记录的来源哈希不一致，已停止预览与原文定位。');
    }
    if(!alive)return;
    const isolated=document.createElement('div');
    try{
     await renderAsync(blob,isolated,isolated,{className:'paper-docx',inWrapper:true,renderHeaders:true,renderFooters:true,renderFootnotes:true,renderEndnotes:true,renderComments:false,renderChanges:false,renderAltChunks:false,ignoreFonts:true,useBase64URL:true,ignoreLastRenderedPageBreak:false,breakPages:true});
    }catch{
     throw new Error('此 DOCX 结构可提取文字，但网页排版预览不兼容。请用 Word 另存为标准 DOCX 后重新上传；已提取的检查结果仍保留，预览失败不代表论文检查合格。');
    }
    // DOCX 是外部资料：不允许其超链接、嵌入网页或事件在阅读器中执行。
    isolated.querySelectorAll('script,iframe,object,embed').forEach(el=>el.remove());
    isolated.querySelectorAll('*').forEach(el=>{for(const attr of Array.from(el.attributes)){if(attr.name.startsWith('on'))el.removeAttribute(attr.name)}});
    isolated.querySelectorAll('a').forEach(el=>el.removeAttribute('href'));
    isolated.querySelectorAll('img').forEach(el=>{const src=el.getAttribute('src')??'';if(!src.startsWith('data:')&&!src.startsWith('blob:'))el.removeAttribute('src')});
    if(alive)holder.replaceChildren(isolated);
   }else if(target.kind==='report'){
    const raw=await response.text();if(alive){try{setText(humanize(JSON.stringify(JSON.parse(raw),null,2)))}catch{setText(humanize(raw))}}
   }
  })().catch(e=>{if(alive&&!controller.signal.aborted)setError((e as Error).message)}).finally(()=>{if(alive)setLoading(false)});
  return()=>{alive=false;controller.abort();holder?.replaceChildren()};
 },[target?.id,activeId,pdf?.id,current?.asset_id,sourcePdf]);
 function selectLocation(element:Element,message:string){
  bodyRef.current?.querySelectorAll('.reader-text-focus').forEach(el=>el.classList.remove('reader-text-focus'));
  element.classList.add('reader-text-focus');element.scrollIntoView({block:'center'});setPositionMessage(message);
 }
 function locate(value:string,useTargetBinding=false){
  clearLocation();
  const wanted=normalizeLocationText(value);if(!wanted){setPositionMessage('当前记录不含可用于定位的文字。');return;}
  if(useTargetBinding && activeId===target?.id && value===target?.focusText && typeof target.focusTableIndex==='number') {
   const tables=Array.from(bodyRef.current?.querySelectorAll('article table')??[]).filter(el=>!el.closest('header,footer'));
   const table=tables[target.focusTableIndex];
   if(tables.length===target.focusTableCount && table && normalizeLocationText(table.textContent??'').includes(wanted)) {
    selectLocation(table,`已定位第 ${target.focusTableIndex+1} 张可编辑表格，来源版本、表格数量与完整检索文字已核对。此处为网页预览，分页以 Word 排版为准。`);
   } else setPositionMessage('网页预览中的表格数量或完整文字与检查记录不一致，无法可靠定位。请结合表题查找原稿中的对应表格。');
   return;
  }
  const all=Array.from(bodyRef.current?.querySelectorAll('article p,article td,article h1,article h2,article h3,article h4,article h5,article h6')??[]).filter(el=>!el.closest('header,footer'));
  // Prefer leaf paragraph nodes to avoid counting the same table-cell text twice.
  const elements=all.filter(el=>normalizeLocationText(el.textContent??'') && !all.some(child=>child!==el && el.contains(child)));
  const texts=elements.map(el=>el.textContent??'');
  const binding=useTargetBinding && activeId===target?.id && value===target.focusText && target.focusParagraph?.sourceAssetId===activeId?target.focusParagraph:undefined;
  const result=resolveTextLocation(texts,value,binding);
  if(result.indices.length===1){
   selectLocation(elements[result.indices[0]],result.bound?`已核对同源完整段落${binding?.label?`（${binding.label}）`:''}并定位；段落索引未直接用作网页位置。Word 页码请在排版 PDF 中查看。`:'已定位唯一的完整文字匹配。此定位依据文字内容，Word 页码请在排版 PDF 中查看。');
  }else if(result.ambiguous){
   setLocationChoices(result.indices.map(index=>({element:elements[index],text:texts[index],before:texts[index-1]??'',after:texts[index+1]??''})));
   setPositionMessage(`检索到 ${result.indices.length} 处完整文字匹配，现有来源位置与上下文不足以唯一定位。请选择下方候选位置；系统未自动选择首项。`);
  }else setPositionMessage('网页中未能定位完整检索文字，可结合章节或表题查看原稿；检索未命中不证明原稿不存在相应内容。');
 }
 function requestLocation(value:string,useTargetBinding=false){
  if(sourcePdf){setPositionMessage('PDF 按物理页定位。请在页面中核对引文；若需查找文字，可使用 PDF 阅读器的查找功能。');return;}
  pendingTargetBinding.current=useTargetBinding;
  if(pdf){setPendingFocus(value);setPdf(null)}
  else locate(value,useTargetBinding);
 }
 useEffect(()=>{
  if(!loading&&!pdf&&pendingFocus&&bodyRef.current?.children.length){locate(pendingFocus,pendingTargetBinding.current);setPendingFocus('')}
 },[loading,pdf?.id,pendingFocus]);
 function jumpPdfPage(){
  const page=Number(pageDraft);
  if(!Number.isSafeInteger(page)||page<1||(current?.page_count&&page>current.page_count)){setPositionMessage('请输入有效的 PDF 物理页码。');return;}
  if(visiblePdf){setPdf({...visiblePdf,page});setPositionMessage(`已请求打开 PDF 第 ${page} 个物理页；请结合页面引文核对具体位置。`)}
 }
 const heading=current?.display_name??target?.label??'原稿阅读';
 return <Dialog.Root open={!!target} onOpenChange={open=>{if(!open)onClose()}}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-40 bg-black/60"/><Dialog.Content className="fixed inset-2 z-50 flex flex-col overflow-hidden rounded-lg bg-white shadow-xl md:inset-5"><header className="flex shrink-0 items-center justify-between gap-3 border-b p-4"><div className="min-w-0"><Dialog.Title className="flex items-center gap-2 text-base font-semibold"><BookOpen size={18}/><span className="truncate">{humanize(heading)}</span></Dialog.Title><Dialog.Description className="mt-1 text-xs text-stone-500">{sourcePdf?'直接查看 PDF 原稿；物理页码按文件页面顺序从第 1 页计，与论文印出的页码可能不同。':current?'可在网页中查找论文文字；已有 Word 排版 PDF 的版本也可直接查看保存的页面。':target?.referenceMaterial?'本视图展示已提取的参考材料原文及来源位置；材料中的示例和适用范围应分别核对。':target?.kind==='report'?'本视图将保存的报告排版显示，并将学生代号替换为姓名；下载的文件仍保留原始内容。':'在网页中查看已保存的证据附件。'}</Dialog.Description></div><Dialog.Close aria-label="关闭原稿阅读器" className="shrink-0 rounded p-2 hover:bg-stone-100"><X size={20}/></Dialog.Close></header>
 {current&&<div className="flex shrink-0 flex-wrap items-center gap-3 border-b bg-stone-50 px-4 py-3"><label className="flex min-w-0 max-w-full flex-1 items-center gap-2 text-sm"><FileText size={15}/><span className="shrink-0">切换论文版本</span><select aria-label="切换阅读文档" value={activeId} onChange={e=>{clearLocation();setPositionMessage('');setPendingFocus('');setActiveId(e.target.value);setPdf(documents.find(d=>d.asset_id===e.target.value)?.pdf_assets?.[0]??null)}} className="min-w-0 max-w-full flex-1 rounded border border-stone-200 bg-white p-2 text-sm">{documentChoices.map(d=><option key={d.asset_id} value={d.asset_id}>{d.display_name}</option>)}</select></label>{!sourcePdf&&activeId===target?.id&&target.focusText&&<button disabled={loading} onClick={()=>requestLocation(target.focusText!,true)} className="inline-flex items-center gap-1 text-sm text-teal-800"><Search size={14}/>定位对应原文</button>}{!sourcePdf&&(current.pdf_assets??[]).length>0&&<><button onClick={()=>setPdf(null)} aria-pressed={!pdf} className="rounded border border-stone-200 bg-white px-3 py-2 text-sm text-teal-800">正文预览（文字定位）</button><button onClick={()=>setPdf({...current.pdf_assets![0],page:activeId===target?.id?target?.page:undefined})} aria-pressed={!!pdf} className="rounded border border-stone-200 bg-white px-3 py-2 text-sm text-teal-800">Word 排版（PDF）</button></>}</div>}
 {current&&!sourcePdf&&<div className="shrink-0 border-b px-4 py-2 text-xs leading-5 text-stone-500">{pdf?'以下为此版本已保存的 Word 排版 PDF。页面顺序从第 1 页计，可能与论文印出的页码不同；打开此预览不会重新运行 Word。':`正文预览便于查找文字；表格边框、公式和分页可能与 Word 不同。${current.pdf_assets?.length?'核对页面外观请切换“Word 排版（PDF）”。':'此版本尚无已保存的 Word 排版 PDF，请用 Word 核对页面外观。'}高亮底色仅用于标出正在查看的位置，不是论文原有底色。`}{positionMessage&&<p role="status" className="text-teal-800">{positionMessage}</p>}</div>}
 {sourcePdf&&<div className="shrink-0 border-b bg-stone-50 px-4 py-3"><form onSubmit={event=>{event.preventDefault();jumpPdfPage()}} className="flex flex-wrap items-center gap-3 text-sm"><label className="flex items-center gap-2">PDF 物理页<input aria-label="PDF 物理页码" type="number" min={1} max={current?.page_count} value={pageDraft} onChange={event=>setPageDraft(event.target.value)} className="w-24 rounded border border-stone-300 bg-white px-3 py-2"/>{current?.page_count?` / ${current.page_count}`:null}</label><button type="submit" className="rounded border border-stone-200 bg-white px-3 py-2 text-teal-800">查看此页</button></form>{target?.focusText&&<details className="mt-2 text-xs text-stone-600"><summary className="cursor-pointer">核对本次定位引文</summary><p className="mt-2 whitespace-pre-wrap leading-6">{target.focusText}</p></details>}{positionMessage&&<p role="status" className="mt-2 text-xs text-teal-800">{positionMessage}</p>}<p className="mt-2 text-xs text-stone-500">在应用内直接阅读原稿；此处按页定位，不表示已精确高亮对应句子。文字查找可使用 PDF 阅读器的查找功能。</p></div>}
 {current&&!pdf&&locationChoices.length>0&&<section aria-label="原文定位候选" className="max-h-56 shrink-0 overflow-auto border-b bg-white px-4 py-3"><h2 className="text-sm font-medium">选择原文位置（{locationChoices.length} 处）</h2><p className="mt-1 text-xs text-stone-500">候选按网页正文顺序排列，不对应检查记录的段落序号。选择前请核对相邻文字。</p><div className="mt-2 space-y-2">{locationChoices.map((choice,index)=><button key={index} onClick={()=>selectLocation(choice.element,`已按用户选择定位候选 ${index+1}；该位置未经系统唯一绑定。`)} className="block w-full rounded border border-stone-200 p-3 text-left text-xs leading-6 hover:bg-teal-50"><span className="font-medium text-teal-800">候选 {index+1}</span><span className="mt-1 block text-stone-500">前文：{choice.before.slice(-120)||'无可提取相邻文字'}</span><span className="block break-words text-stone-900">{choice.text.length>280?`${choice.text.slice(0,140)}…${choice.text.slice(-140)}`:choice.text}</span><span className="block text-stone-500">后文：{choice.after.slice(0,120)||'无可提取相邻文字'}</span></button>)}</div></section>}
 <div className="flex min-h-0 flex-1 flex-col lg:flex-row"><section aria-label="论文正文与排版预览" className="relative min-h-0 flex-1 overflow-auto bg-stone-100"><div className={current&&!visiblePdf?'':'hidden'} ref={bodyRef}/>{loading&&!visiblePdf&&<p role="status" className="p-8 text-center text-sm text-stone-500">正在读取原稿并生成网页预览…</p>}{error&&!visiblePdf&&<p role="alert" className="p-8 text-sm text-red-800">{error}</p>}{visiblePdf&&<iframe key={`${visiblePdf.id}:${visiblePdf.page??1}`} title={`${heading} PDF 原稿与页面证据`} src={assetUrl(visiblePdf.id)+(pdfPage(visiblePdf.page)?`#page=${visiblePdf.page}`:'')} className="h-full min-h-96 w-full border-0"/>}{!current&&target?.kind==='report'&&!loading&&<MarkdownReport text={text}/>}{!current&&activeId&&target?.kind==='image'&&<img src={assetUrl(activeId)} alt={heading} onLoad={event=>{const fraction=target?.imageFocusFraction;const container=event.currentTarget.parentElement;if(container && typeof fraction==='number' && fraction>=0 && fraction<=1)container.scrollTop=Math.max(0,event.currentTarget.clientHeight*fraction-container.clientHeight*0.65)}} className="mx-auto max-w-full p-4"/>}{!current&&target?.kind==='document'&&!loading&&<p className="p-8 text-sm text-stone-500">尚未取得此版本的网页预览记录。请重新读取已保存结果后重试。</p>}</section>
 {current&&sourcePdf&&<aside aria-label="PDF 批注解析范围" className="max-h-64 w-full shrink-0 overflow-auto border-t bg-white p-4 lg:max-h-none lg:w-80 lg:border-l lg:border-t-0 xl:w-96"><h2 className="text-sm font-semibold">PDF 原生批注</h2><p className="mt-3 text-xs leading-6 text-stone-500">PDF 原生批注尚未解析；本轮 Agent 建议在结果页查看。此处未显示批注数量，不能据此判断原稿是否包含批注。</p></aside>}
 {current&&!sourcePdf&&<aside aria-label="此版本已提取的文档批注" className="max-h-64 w-full shrink-0 overflow-auto border-t bg-white p-4 lg:max-h-none lg:w-80 lg:border-l lg:border-t-0 xl:w-96"><h2 className="text-sm font-semibold">此版本的文档批注 <span className="font-normal text-stone-500">{current.comments.length} 条</span></h2><p className="mt-2 text-xs leading-5 text-stone-500">{current.role==='teacher'?'显示此教师提交稿已提取的批注及其对应原文。空文字批注可能含图片，请结合原稿查看。':current.role==='uploaded'?'当前文档为上传论文。Agent 本轮的修改建议在批改结果页；右侧仅显示文件本身已有的批注。上传文件不自动认定为 SVN 初稿。':'当前文档为已保存的学生初稿。其他版本中的教师批注请在工作台相应栏目查看。'}</p>{current.comments.length===0&&<p className="mt-5 text-sm text-stone-500">当前文档未提取到批注记录。</p>}{current.comments.map(c=><article key={c.id} className="mt-4 rounded border border-stone-200 p-3"><p className="text-xs text-stone-500">批注 {c.id} · {c.author||'作者未记录'}</p><p className="mt-2 whitespace-pre-wrap text-sm font-medium leading-6 text-teal-900">{c.text||'空文字批注，请结合原稿图像查看。'}</p><details className="mt-3 text-xs text-stone-500"><summary className="cursor-pointer">查看批注所指的原文</summary><p className="mt-2 whitespace-pre-wrap rounded bg-stone-50 p-3 leading-6">{c.anchor_text||'批注对应位置未取得可提取文字，可能包含图片或公式。'}</p>{c.anchor_text&&<button disabled={loading} onClick={()=>requestLocation(c.anchor_text)} className="mt-2 inline-flex items-center gap-1 text-teal-800"><Search size={12}/>定位批注对应原文</button>}</details></article>)}</aside>}
 </div><footer className="flex shrink-0 items-center justify-between gap-3 border-t px-4 py-3 text-xs text-stone-500"><span>{current?(current.role==='uploaded'?`${current.student_name} · 上传论文 · ${current.file_name}`:`${current.student_name} · ${current.role==='initial'?'学生初稿':'教师批改稿'} · r${current.revision}`):'已保存的本地附件'}</span><a href={assetUrl(activeId)} download className="inline-flex items-center gap-1 text-teal-800"><Download size={13}/>{current?'下载此版本论文文件':'下载附件原文件'}</a></footer>
 </Dialog.Content></Dialog.Portal></Dialog.Root>
}
