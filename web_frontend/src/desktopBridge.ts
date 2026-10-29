export interface DesktopInfo {
 nativeDesktop: true;
 workspaceRoot: string;
 baseUrl: string;
 backendOwner: 'owned'|'shared'|'none';
 appVersion: string;
}
export interface PaperDesktopBridge {
 getInfo(): Promise<DesktopInfo>;
 selectWorkspace(): Promise<{cancelled:boolean;changed?:boolean;workspaceRoot?:string}>;
}
declare global {
 interface Window { paperDesktop?: PaperDesktopBridge; }
}

export const navigationItems = [
 {id:'new-papers',label:'批改新论文',description:'上传论文并查看审阅结果',group:'论文审阅'},
 {id:'rule-library',label:'SVN 批注与检查规则',description:'导出教师论文、整理规则并核查初稿',group:'论文审阅'},
 {id:'svn-results',label:'SVN 初稿核查结果',description:'查看学生历史初稿的报告、原稿与执行过程',group:'论文审阅'},
 {id:'findings',label:'实验初稿结果',description:'查看固定实验样本的原文与依据',group:'实验记录'},
 {id:'comments',label:'实验教师批注',description:'查看固定实验样本的教师反馈',group:'实验记录'},
 {id:'runs',label:'实验运行记录',description:'查看固定实验样本的执行与用量',group:'实验记录'},
] as const;

export function connectionLabel(checksEnabled:boolean|undefined,desktop?:DesktopInfo) {
 if(checksEnabled===false)return '检查来源待配置';
 if(desktop?.backendOwner==='none')return '服务尚未连接';
 return '本机服务已连接';
}
