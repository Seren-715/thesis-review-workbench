import {Fragment, type ReactNode} from 'react';

function inline(text:string):ReactNode[] {
 const parts=text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\s)]+\))/g);
 return parts.map((part,index)=>{
  if(part.startsWith('`')&&part.endsWith('`'))return <code key={index} className="rounded bg-stone-100 px-1 text-[0.9em]">{part.slice(1,-1)}</code>;
  if(part.startsWith('**')&&part.endsWith('**'))return <strong key={index}>{part.slice(2,-2)}</strong>;
  const link=part.match(/^\[([^\]]+)\]\(([^\s)]+)\)$/);
  if(link&&/^https?:\/\//i.test(link[2]))return <a key={index} href={link[2]} target="_blank" rel="noopener noreferrer" className="text-teal-800 underline">{link[1]}</a>;
  return <Fragment key={index}>{part}</Fragment>;
 });
}

/** A deliberately small Markdown renderer. All source is React text; HTML is never interpreted. */
export function MarkdownReport({text}:{text:string}) {
 if(/^\s*[\[{]/.test(text))return <pre className="m-4 whitespace-pre-wrap break-words rounded border border-stone-200 bg-white p-5 text-xs leading-6">{text}</pre>;
 const lines=text.replace(/\r/g,'').split('\n');
 const blocks:ReactNode[]=[];
 for(let i=0;i<lines.length;){
  const line=lines[i];
  if(!line.trim()){i++;continue}
  if(line.startsWith('```')){
   const code:string[]=[];i++;
   while(i<lines.length&&!lines[i].startsWith('```'))code.push(lines[i++]);
   i++;blocks.push(<pre key={i} className="overflow-x-auto whitespace-pre-wrap rounded bg-stone-50 p-4 text-xs"><code>{code.join('\n')}</code></pre>);continue;
  }
  const heading=line.match(/^(#{1,6})\s+(.+)$/);
  if(heading){
   const level=heading[1].length;
   blocks.push(<div key={i++} role="heading" aria-level={level} className={level===1?'border-b pb-3 text-2xl font-semibold':level===2?'pt-4 text-xl font-semibold':'pt-2 text-base font-semibold'}>{inline(heading[2])}</div>);continue;
  }
  if(line.includes('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1]||'')){
   const cells=(row:string)=>row.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim());
   const headers=cells(line), rows:string[][]=[];i+=2;
   while(i<lines.length&&lines[i].includes('|')&&lines[i].trim())rows.push(cells(lines[i++]));
   blocks.push(<div key={i} className="overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr>{headers.map((h,k)=><th key={k} className="border bg-stone-50 p-3 text-left font-medium">{inline(h)}</th>)}</tr></thead><tbody>{rows.map((row,k)=><tr key={k}>{row.map((cell,j)=><td key={j} className="border p-3 align-top">{inline(cell)}</td>)}</tr>)}</tbody></table></div>);continue;
  }
  if(/^\s*(-|\*|\d+\.)\s+/.test(line)){
   const ordered=/^\s*\d+\./.test(line), rows:string[]=[];
   while(i<lines.length&&/^\s*(-|\*|\d+\.)\s+/.test(lines[i]))rows.push(lines[i++].replace(/^\s*(-|\*|\d+\.)\s+/,''));
   const items=rows.map((row,k)=><li key={k} className="pl-1">{inline(row)}</li>);
   blocks.push(ordered?<ol key={i} className="list-decimal space-y-2 pl-6">{items}</ol>:<ul key={i} className="list-disc space-y-2 pl-6">{items}</ul>);continue;
  }
  if(/^\s*---+\s*$/.test(line)){blocks.push(<hr key={i++} className="border-stone-200"/>);continue}
  const paragraph:string[]=[line];i++;
  while(i<lines.length&&lines[i].trim()&&!/^(#|```|[-*]\s|\d+\.\s|\s*---)/.test(lines[i]))paragraph.push(lines[i++]);
  blocks.push(<p key={i} className="whitespace-pre-wrap break-words">{inline(paragraph.join('\n'))}</p>);
 }
 return <article aria-label="排版后的检查报告" className="m-4 space-y-4 rounded border border-stone-200 bg-white p-5 text-sm leading-7 md:p-8">{blocks}</article>;
}
