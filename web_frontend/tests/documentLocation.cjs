const fs=require('fs');
const assert=require('assert/strict');
const pathModule=require('path');
const ts=require('typescript');
const path=pathModule.resolve(__dirname,'../src/documentLocation.ts');
const source=fs.readFileSync(path,'utf8');
const moduleShim={exports:{}};
new Function('exports',ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(moduleShim.exports);
const {resolveTextLocation,bindSourceParagraph}=moduleShim.exports;
const receipts=[];
function test(name,run){run();receipts.push({name,status:'passed'});}
const prefix='相同的论文章节前缀文字'.repeat(10);
test('old_prefix_false_jump_reproduced_and_full_query_correct',()=>{
 const texts=[prefix+'第一份说明',prefix+'第二份说明'];
 const query=texts[1];
 assert.equal(texts.findIndex(text=>text.replace(/\s+/g,'').includes(query.replace(/\s+/g,'').slice(0,80))),0);
 assert.deepEqual(resolveTextLocation(texts,query),{indices:[1],bound:false,ambiguous:false});
});
test('ordinary_duplicate_has_all_candidates_no_selected_index',()=>assert.deepEqual(resolveTextLocation(['重复段落','重复段落'],'重复段落'),{indices:[0,1],bound:false,ambiguous:true}));
test('quote_binding_prioritizes_complete_source_paragraph',()=>{
 const texts=['其他范围同样提到这个方法','完整来源段落也提到这个方法'];
 assert.deepEqual(resolveTextLocation(texts,'这个方法',{sourceAssetId:'source',paragraphIndex:908,text:texts[1]}),{indices:[1],bound:true,ambiguous:false});
});
test('duplicate_paragraph_context_disambiguates_second',()=>{
 const texts=['第一章','重复正文','甲后文','第二章','重复正文','乙后文'];
 assert.deepEqual(resolveTextLocation(texts,'重复正文',{sourceAssetId:'source',paragraphIndex:908,text:'重复正文',previousText:'第二章',nextText:'乙后文'}),{indices:[4],bound:true,ambiguous:false});
});
test('source_index_is_never_used_as_dom_index',()=>{
 const texts=['第一章','重复正文','甲后文','第二章','重复正文','乙后文'];
 assert.deepEqual(resolveTextLocation(texts,'重复正文',{sourceAssetId:'source',paragraphIndex:1,text:'重复正文',previousText:'第二章',nextText:'乙后文'}).indices,[4]);
});
test('changed_or_missing_context_does_not_silently_select',()=>assert.equal(resolveTextLocation(['甲','重复正文','乙','丙','重复正文','丁'],'重复正文',{sourceAssetId:'source',paragraphIndex:1,text:'重复正文',previousText:'未知前文',nextText:'丁'}).ambiguous,true));
test('same_duplicate_context_stays_ambiguous',()=>assert.equal(resolveTextLocation(['甲','重复正文','乙','甲','重复正文','乙'],'重复正文',{sourceAssetId:'source',paragraphIndex:4,text:'重复正文',previousText:'甲',nextText:'乙'}).ambiguous,true));
test('whitespace_normalization_preserves_full_match',()=>assert.deepEqual(resolveTextLocation(['正文 内 容\n完整'],'正文内容完整').indices,[0]));
test('missing_full_text_never_falls_back_to_prefix',()=>assert.deepEqual(resolveTextLocation([prefix+'不同尾部'],prefix+'目标尾部').indices,[]));
test('empty_query_is_not_a_match',()=>assert.deepEqual(resolveTextLocation(['甲','乙'],' \n ').indices,[]));
test('paragraph_binding_uses_exact_record_id_and_neighbors',()=>{
 const paragraphs=[{index:7,text:'前文'},{index:908,text:'目标',location:{label:'第三章'}},{index:909,text:'后文'}];
 assert.deepEqual(bindSourceParagraph('asset-a',paragraphs,908),{sourceAssetId:'asset-a',paragraphIndex:908,text:'目标',previousText:'前文',nextText:'后文',label:'第三章'});
 assert.equal(bindSourceParagraph('asset-a',paragraphs,1),undefined);
 assert.equal(bindSourceParagraph('asset-a',[...paragraphs,{index:908,text:'冲突源段落'}],908),undefined);
});
test('viewer_checks_source_and_no_prefix_first_match',()=>{
 const viewer=fs.readFileSync(pathModule.resolve(__dirname,'../src/DocumentViewer.tsx'),'utf8');
 assert.ok(viewer.includes("crypto.subtle.digest('SHA-256'"));
 assert.ok(viewer.includes('target.focusParagraph?.sourceAssetId===activeId'));
 assert.ok(viewer.includes('target.sourceSha256 && current.sha256!==target.sourceSha256'));
 assert.ok(!viewer.includes(".slice(0,80)"));
 assert.ok(viewer.includes('系统未自动选择首项'));
 assert.ok(viewer.includes('const binding=useTargetBinding &&'));
 assert.ok(viewer.includes('requestLocation(c.anchor_text)'));
 assert.ok(viewer.includes('requestLocation(target.focusText!,true)'));
 assert.ok(viewer.includes('已按用户选择定位候选'));
});
const output={source:path,tests:receipts,total:receipts.length,requests:{llm:0,svn:0,word:0},limitations:['纯函数合成反例与静态安全接线核对；浏览器渲染验收应独立执行。']};
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(output,null,2));
console.log(JSON.stringify(output,null,2));
