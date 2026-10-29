/** Source indices are evidence identifiers, never indices into the preview DOM. */
export interface ParagraphBinding {
 sourceAssetId:string;
 paragraphIndex:number;
 text:string;
 previousText?:string;
 nextText?:string;
 label?:string;
}
export const normalizeLocationText=(text:string)=>text.replace(/\s+/gu,'');
export interface TextLocationResult {indices:number[];bound:boolean;ambiguous:boolean}

/** Match complete search text. Repeated matches require verified context or user selection. */
export function resolveTextLocation(texts:string[],query:string,binding?:ParagraphBinding):TextLocationResult {
 const wanted=normalizeLocationText(query);
 if(!wanted)return {indices:[],bound:false,ambiguous:false};
 const normalized=texts.map(normalizeLocationText);
 const matches=normalized.flatMap((text,index)=>text.includes(wanted)?[index]:[]);
 if(binding && normalizeLocationText(binding.text).includes(wanted)) {
  const full=normalizeLocationText(binding.text);
  const exact=matches.filter(index=>normalized[index]===full);
  if(exact.length===1)return {indices:exact,bound:true,ambiguous:false};
  const before=normalizeLocationText(binding.previousText??''),after=normalizeLocationText(binding.nextText??'');
  if(before||after) {
   const contextual=exact.filter(index=>(!before||normalized[index-1]===before)&&(!after||normalized[index+1]===after));
   if(contextual.length===1)return {indices:contextual,bound:true,ambiguous:false};
  }
 }
 return {indices:matches,bound:false,ambiguous:matches.length>1};
}

export function bindSourceParagraph(sourceAssetId:string,paragraphs:{index:number;text:string;location?:{label:string}}[],index:number|null|undefined):ParagraphBinding|undefined {
 if(typeof index!=='number')return undefined;
 const matching=paragraphs.flatMap((paragraph,at)=>paragraph.index===index?[at]:[]);
 if(matching.length!==1)return undefined;
 const at=matching[0];
 // Adjacent source paragraphs are only used when the same full text and context
 // appear in the preview. A missing/changed renderer paragraph leaves ambiguity.
 return {sourceAssetId,paragraphIndex:index,text:paragraphs[at].text,previousText:paragraphs[at-1]?.text,nextText:paragraphs[at+1]?.text,label:paragraphs[at].location?.label};
}
