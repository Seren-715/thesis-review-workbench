import {useEffect,useState,type ReactNode} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {BookOpen,FileText,Layers,History,Settings2,FolderOpen,X,PanelLeft,ChevronRight} from 'lucide-react';
import {connectionLabel,navigationItems,type DesktopInfo} from './desktopBridge';

const icons={ 'new-papers':FileText,'rule-library':Layers,'svn-results':FileText,findings:BookOpen,comments:FileText,runs:History };
export function DesktopLayout({tab,onTabChange,checksEnabled,toolbar,children}:{tab:string;onTabChange:(tab:string)=>void;checksEnabled?:boolean;toolbar:ReactNode;children:ReactNode}) {
 const [desktop,setDesktop]=useState<DesktopInfo>(),[infoError,setInfoError]=useState('');
 const [settings,setSettings]=useState(false),[switching,setSwitching]=useState(false),[mobileNav,setMobileNav]=useState(false);
 const page=navigationItems.find(item=>item.id===tab)??navigationItems[0];
 useEffect(()=>{
  if(!window.paperDesktop)return;
  let alive=true;
  document.documentElement.dataset.desktop='true';
  window.paperDesktop.getInfo().then(info=>alive&&setDesktop(info)).catch(error=>alive&&setInfoError((error as Error).message));
  return()=>{alive=false;delete document.documentElement.dataset.desktop};
 },[]);
 async function chooseWorkspace() {
  if(!window.paperDesktop)return;
  setSwitching(true);setInfoError('');
  try { await window.paperDesktop.selectWorkspace(); }
  catch(error){setInfoError((error as Error).message)}
  finally {setSwitching(false)}
 }
 const navigate=(id:string)=>{onTabChange(id);setMobileNav(false)};
 return <div className="app-shell">
  <div className="app-titlebar"><span className="app-titlebar-brand"><BookOpen size={15}/>论文审阅工作台</span><span className="app-titlebar-mode">{window.paperDesktop?'桌面应用':'本机工作台'}</span></div>
  <div className="app-body">
   {mobileNav&&<button className="app-sidebar-overlay" aria-label="关闭功能导航" onClick={()=>setMobileNav(false)}/>}
   <aside className={`app-sidebar ${mobileNav?'is-open':''}`} aria-label="应用功能导航">
    <div className="app-workspace-brand"><div className="app-workspace-icon"><BookOpen size={20}/></div><div><p>论文审阅工作台</p><span>教师反馈驱动的论文核查</span></div></div>
    <nav aria-label="工作台视图" className="app-navigation">{['论文审阅','实验记录'].map(group=><div className="app-nav-group" key={group}><p className="app-nav-group-label">{group}</p>{navigationItems.filter(item=>item.group===group).map(item=>{const Icon=icons[item.id];return <button key={item.id} onClick={()=>navigate(item.id)} aria-current={item.id===tab?'page':undefined} title={item.description} className={`app-nav-item ${item.id===tab?'is-active':''}`}><Icon size={17}/><span>{item.label}</span>{item.id===tab&&<ChevronRight size={13} className="app-nav-chevron"/>}</button>})}</div>)}</nav>
    <div className="app-sidebar-bottom"><div className="app-connection"><span className={`app-status-dot ${checksEnabled===false?'is-pending':''}`}/><span>{connectionLabel(checksEnabled,desktop)}</span></div><button className="app-workspace-settings" aria-label="工作区与连接设置" onClick={()=>setSettings(true)}><Settings2 size={16}/>工作区与连接<span>设置</span></button></div>
   </aside>
   <section className="app-content">
    <header className="app-content-header"><div className="app-page-identity"><button className="app-mobile-navigation" aria-label="展开功能导航" onClick={()=>setMobileNav(true)}><PanelLeft size={18}/></button><div><h1>{page.label}</h1><p>{page.description}</p></div></div><div className="app-toolbar">{toolbar}</div></header>
    <main className="app-content-scroll" key={tab}><div className="app-content-inner">{children}</div></main>
   </section>
  </div>
  <Dialog.Root open={settings} onOpenChange={setSettings}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-40 bg-black/40"/><Dialog.Content className="app-settings-dialog"><div className="flex items-center justify-between gap-3"><Dialog.Title className="text-base font-semibold">工作区与连接设置</Dialog.Title><Dialog.Close aria-label="关闭工作区设置" className="rounded p-2 hover:bg-stone-100"><X size={18}/></Dialog.Close></div><Dialog.Description className="mt-2 text-sm leading-6 text-stone-500">工作区保存应用程序、教师证据与审阅结果。切换工作区不会移动或删除原有资料。</Dialog.Description><dl className="mt-5 space-y-4 text-sm"><div><dt className="text-xs text-stone-500">当前连接</dt><dd className="mt-1">{connectionLabel(checksEnabled,desktop)}</dd></div>{desktop&&<><div><dt className="text-xs text-stone-500">工程工作区</dt><dd className="mt-1 break-all rounded border border-stone-200 bg-stone-50 p-3 font-mono text-xs">{desktop.workspaceRoot}</dd></div><div><dt className="text-xs text-stone-500">本机服务地址</dt><dd className="mt-1 break-all">{desktop.baseUrl}</dd></div><div><dt className="text-xs text-stone-500">应用版本</dt><dd className="mt-1">{desktop.appVersion}</dd></div></>}</dl>{window.paperDesktop?<><button disabled={switching} onClick={chooseWorkspace} className="mt-5 inline-flex items-center gap-2 rounded-md bg-teal-800 px-4 py-2 text-sm text-white hover:bg-teal-900"><FolderOpen size={16}/>{switching?'正在切换工作区…':'选择工程工作区'}</button><p className="mt-3 text-xs leading-6 text-stone-500">请选择包含论文审阅程序和证据资料的工程目录；SVN 学生论文工作副本是数据来源，应在工程配置中单独设置。存在执行中的任务时，应用会阻止切换工作区。</p></>:<p className="mt-5 rounded border border-stone-200 bg-stone-50 p-3 text-sm leading-6 text-stone-600">当前以网页方式运行，连接现有本机服务。工作区选择功能在独立桌面应用中提供。</p>}{infoError&&<p role="alert" className="mt-4 text-sm leading-6 text-red-800">{infoError}</p>}</Dialog.Content></Dialog.Portal></Dialog.Root>
 </div>;
}
