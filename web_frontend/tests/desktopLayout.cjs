const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const receipts=[];
function test(name,run){run();receipts.push({name,status:'passed'});}
function loadSource(name,overrides={}) {
 const filename=path.resolve(__dirname,'../src',name);
 const compiled=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const result={exports:{}};
 new Function('require','module','exports',compiled)(id=>Object.hasOwn(overrides,id)?overrides[id]:require(id),result,result.exports);
 return result.exports;
}
const bridge=loadSource('desktopBridge.ts');
test('navigation_preserves_all_six_real_workflows',()=>assert.deepEqual(bridge.navigationItems.map(item=>item.id),['new-papers','rule-library','svn-results','findings','comments','runs']));
test('configuration_pending_is_never_shown_as_checks_ready',()=>assert.equal(bridge.connectionLabel(false),'检查来源待配置'));
test('missing_owned_service_has_honest_connection_state',()=>assert.equal(bridge.connectionLabel(true,{backendOwner:'none'}),'服务尚未连接'));
test('browser_fallback_keeps_existing_connected_service',()=>assert.equal(bridge.connectionLabel(undefined),'本机服务已连接'));
test('review_grid_has_single_layout_owner_without_tailwind_responsive_conflicts',()=>{
 const source=fs.readFileSync(path.resolve(__dirname,'../src/NewPaperReview.tsx'),'utf8');
 assert.match(source,/className="app-review-grid"/);
 assert.match(source,/className="app-paper-detail space-y-4"/);
 assert.doesNotMatch(source,/app-review-grid[^"\n]*xl:grid-cols|app-paper-list[^"\n]*xl:col-span|space-y-4 xl:col-span-9/);
 const css=fs.readFileSync(path.resolve(__dirname,'../src/index.css'),'utf8');
 assert.match(css,/\.app-review-grid\s*\{display:grid;grid-template-columns:minmax\(0,1fr\)/);
 assert.match(css,/\.app-review-grid\s*\{grid-template-columns:256px minmax\(0,1fr\)/);
});
global.window={};
const {DesktopLayout}=loadSource('DesktopLayout.tsx',{'./desktopBridge':bridge});
test('browser_shell_renders_sidebar_scroll_area_and_selected_workflow',()=>{
 const html=renderToStaticMarkup(React.createElement(DesktopLayout,{tab:'findings',onTabChange:()=>{},toolbar:React.createElement('button',null,'读取结果')},React.createElement('p',null,'真实审阅内容')));
 assert.equal((html.match(/class="app-nav-item/g)||[]).length,6);
 assert.match(html,/aria-current="page"[^>]*><svg[\s\S]+实验初稿结果/);
 assert.match(html,/app-content-scroll/);
 assert.match(html,/真实审阅内容/);
 assert.match(html,/本机工作台/);
 assert.doesNotMatch(html,/Codex/);
});
test('native_shell_uses_existing_bridge_without_fake_window_controls',()=>{
 global.window={paperDesktop:{getInfo:async()=>({}),selectWorkspace:async()=>({cancelled:true})}};
 const html=renderToStaticMarkup(React.createElement(DesktopLayout,{tab:'new-papers',onTabChange:()=>{},checksEnabled:false,toolbar:null},null));
 assert.match(html,/桌面应用/);
 assert.match(html,/检查来源待配置/);
 assert.doesNotMatch(html,/最小化|最大化|关闭窗口/);
});
delete global.window;
console.log(JSON.stringify({tests:receipts.length,passed:receipts.length,receipts},null,2));
