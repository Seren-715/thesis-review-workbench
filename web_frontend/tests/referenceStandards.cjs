const fs=require('fs'),path=require('path'),assert=require('assert/strict'),ts=require('typescript');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const root=path.resolve(__dirname,'../src'),cache=new Map();
function load(file){
 const full=path.join(root,file);
 if(cache.has(full))return cache.get(full).exports;
 const result={exports:{}};cache.set(full,result);
 const localRequire=id=>{
  if(!id.startsWith('.'))return require(id);
  const name=path.relative(root,path.resolve(path.dirname(full),id));
  return load(fs.existsSync(path.join(root,name+'.tsx'))?name+'.tsx':name+'.ts');
 };
 const code=ts.transpileModule(fs.readFileSync(full,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 new Function('module','exports','require',code)(result,result.exports,localRequire);return result.exports;
}
const model=load('referenceStandardsModel.ts'),views=load('ReferenceStandards.tsx'),{default:FormatDiagnostics}=load('FormatDiagnostics.tsx');
const receipts=[];function test(name,fn){fn();receipts.push({name,status:'passed'})}
const requirement={id:'code',source_id:'teacher_lecture',quote:'代码必须以文本形式黏贴，5号字体。不可使用截图替代',location:'课件第37页',scope:'code',interpretation:'代码采用五号（10.5磅），不规定所有语言统一缩进。',parameters:{对象:'代码',字号:'五号（10.5磅）'},reader_asset_id:'reader-37'};
const fullComment='图序和图在同一页。格式上看一下规范和上次论文讲座PPT。最后保留的完整批注句。';
const route={evidence:{id:'comment-1',student_name:'黄发平',revision:300,comment_text:fullComment,source_asset_id:'teacher-doc',anchor_text:'对应论文原文'},missing_materials:['教师指定PPT、规范或讲座资料'],reason:'外部资料的版本与页次尚未对应。',next_step:'核对教师指定版本和相关页次。',independent_requirement_fragments:[{quote:'图序和图在同一页'}],available_reference_candidates:[{source_id:'teacher_lecture',title:'科技论文写作实践（2024年材料）',reader_asset_id:'reader-lecture'}]};
test('external_route_supports_array_and_audit_items_without_changing_records',()=>{assert.equal(model.externalReviewItems([route])[0],route);assert.equal(model.externalReviewItems({items:[route]})[0],route);assert.deepEqual(model.externalReviewItems(undefined),[]);assert.equal(route.evidence.comment_text,fullComment)});
test('external_search_covers_missing_material_and_independent_clause',()=>{const text=model.externalReviewSearchText(route);assert.ok(text.includes('最后保留的完整批注句'));assert.ok(text.includes('教师指定ppt'));assert.ok(text.includes('图序和图在同一页'))});
test('external_card_preserves_full_comment_and_distinguishes_reference_candidate',()=>{
 const html=renderToStaticMarkup(React.createElement(views.ExternalFeedbackReviews,{items:[route],onOpen:()=>{}}));
 assert.ok(html.includes(fullComment));assert.ok(html.includes('教师原批注（全文）'));assert.ok(html.includes('需要外部资料确认'));
 assert.ok(!html.includes('尚需补充或确认的材料'));assert.ok(!html.includes('后续处理：'));assert.ok(!html.includes('工作区中可供核对的参考资料'));assert.ok(html.includes('不代表规则已经审定'));assert.ok(html.includes('查看该批注所在教师稿'));
});
test('external_quote_escapes_html_instead_of_rendering_document_instructions',()=>{const html=renderToStaticMarkup(React.createElement(views.ExternalFeedbackReviews,{items:[{...route,evidence:{...route.evidence,comment_text:'<script>execute()</script>原批注末尾'}}],onOpen:()=>{}}));assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('原批注末尾'));assert.ok(!html.includes('<script>'))});
test('requirements_render_specific_parameters_and_readable_scope',()=>{const html=renderToStaticMarkup(React.createElement(views.ReferenceRequirementCard,{requirement,onOpen:()=>{}}));assert.ok(html.includes(requirement.quote));assert.ok(html.includes('五号（10.5磅）'));assert.ok(html.includes(requirement.interpretation));assert.ok(!html.includes('适用对象与解释：code'));assert.ok(html.includes('在应用内阅读依据原文'))});
test('source_open_action_keeps_registered_asset_and_in_app_report_target',()=>{
 const opened=[];const tree=views.ReferenceRequirementCard({requirement,onOpen:value=>opened.push(value)});
 const visit=node=>{if(!node||typeof node!=='object')return;if(Array.isArray(node)){node.forEach(visit);return}if(node.type==='button')node.props.onClick();visit(node.props?.children)};visit(tree);
 assert.deepEqual(opened,[{id:'reader-37',label:'科技论文写作实践（2024年材料） · 规范原文',kind:'report',referenceMaterial:true,focusText:requirement.quote}]);
});
test('format_diagnostics_separate_available_standards_from_missing_color',()=>{
 const coverage=[...['font_layout','formula_font','code_layout'].map(family=>({family,title:family,status:'reference_standard_available',detail:'已找到具体条文'})),{family:'text_color',title:'正文颜色',status:'standard_not_specified',detail:'未找到正文固定颜色标准'}];
 const report={format_diagnostics:{coverage,capabilities:coverage.map(row=>({family:row.family,teacher_evidence:[{quote:'原教师反馈'}],reference_requirements:row.family==='text_color'?[]:[requirement]})),sources:{}}};
 const html=renderToStaticMarkup(React.createElement(FormatDiagnostics,{report,onOpen:()=>{}}));assert.ok(html.includes('已有参考条文的格式反馈（3 类）'));assert.ok(html.includes('尚需明确判定标准的格式反馈（1 类）'));assert.ok(html.includes('未找到正文固定颜色标准'));assert.ok(!html.includes('尚需明确判定标准的格式反馈（4 类）'));
});
test('template_is_displayed_as_example_not_mandatory_current_year',()=>{
 const html=renderToStaticMarkup(React.createElement(views.ReferenceStandards,{catalog:{status:'verified_reference_materials',title:'参考材料',applicability:'不据此认定当前学年统一规范',sources:[{id:'template',title:'格式范本',source_kind:'illustrative_template',reader_asset_id:'template-reader'}],requirements:[]},onOpen:()=>{}}));assert.ok(html.includes('格式范本 · 仅作示例'));assert.ok(html.includes('未据此自动认定本稿适用'));assert.ok(html.includes('阅读完整参考材料'));
});
test('teacher_rules_has_distinct_external_tab_and_uses_read_only_catalog_payload',()=>{const source=fs.readFileSync(path.join(root,'TeacherRules.tsx'),'utf8');assert.match(source,/id:'external',label:`需人工复核（外部资料）/);assert.match(source,/<ExternalFeedbackReviews items=\{visible as ExternalFeedbackReview\[\]\}/);assert.match(source,/<ReferenceStandards catalog=\{library\?\.reference_standards\}/);assert.match(source,/externalReviewSearchText\(item\)/)});
test('missing_parameters_are_unknown_not_zero_or_black',()=>{assert.equal(model.referenceParameterValue(undefined),'未明确');assert.equal(model.referenceParameterValue(null),'未明确');assert.equal(model.referenceParameterValue(0),'0');assert.equal(model.referenceParameterValue(['宋体','Times New Roman']),'宋体、Times New Roman')});
test('changed_reference_material_has_explicit_message_and_does_not_display_stale_parameters',()=>{
 const catalog={status:'source_review_required',message:'参考原文已变化，请重新核对该材料。',sources:[{id:'stale',title:'旧材料',source_kind:'teacher_lecture'}],requirements:[requirement]};
 const html=renderToStaticMarkup(React.createElement(views.ReferenceStandards,{catalog,onOpen:()=>{}}));
 assert.ok(html.includes(catalog.message));assert.ok(html.includes('参考材料需重新核对'));assert.ok(!html.includes(requirement.quote));assert.ok(!html.includes('0 份材料 · 0 条明文要求'));
 const diagnostic=renderToStaticMarkup(React.createElement(FormatDiagnostics,{report:{format_diagnostics:{coverage:[],capabilities:[],sources:{},reference_standards:catalog}}}));assert.ok(diagnostic.includes(catalog.message));
});
console.log(JSON.stringify({tests:receipts.length,passed:receipts.length,receipts},null,2));
