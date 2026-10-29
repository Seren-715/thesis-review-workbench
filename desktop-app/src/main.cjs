'use strict';
const {app, BrowserWindow, ipcMain, dialog, Tray, Menu, nativeImage} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {ServiceManager, validateWorkspace, isTrustedUrl, isAssetUrl} = require('./serviceManager.cjs');
const {writeWorkspaceConfig} = require('./workspaceConfig.cjs');
const {ensureBundledEngine} = require('./bundledEngine.cjs');

function profileDirectory(args) {
  const options = args.filter(value => value === '--profile-dir' || value.startsWith('--profile-dir='));
  if (!options.length) return null;
  if (options.length !== 1 || !options[0].startsWith('--profile-dir=')) throw new Error('--profile-dir must be specified once as --profile-dir=<absolute directory>');
  const value = options[0].slice('--profile-dir='.length);
  if (!value.trim() || !path.isAbsolute(value) || (process.platform === 'win32' && !/^(?:[a-zA-Z]:[\\/]|\\\\[^\\]+\\[^\\]+)/.test(value))) throw new Error('--profile-dir requires an absolute directory');
  return path.resolve(value);
}
// Set the profile before requesting the single-instance lock, so an isolated
// profile never reuses another profile's workspace preferences or process.
try {
  const profile = profileDirectory(process.argv);
  if (profile) { fs.mkdirSync(profile, {recursive:true}); app.setPath('userData', profile); }
} catch (error) {
  process.stderr.write(`Invalid application profile: ${error.message}\n`);
  app.exit(1);
  process.exit(1);
}

const START_PAGE = path.join(__dirname, 'startup.html');
const START_URL = pathToFileURL(START_PAGE).href;
const APP_ICON = path.join(__dirname, '..', 'assets', 'workbench.ico');
let window = null, tray = null, quitting = false, closing = false, transition = false, startupError = '';
const manager = new ServiceManager({onExit: () => {
  if (!quitting && !transition && window && !window.isDestroyed()) {
    startupError = '审阅服务连接已结束。请重新连接工程，并查看已保存的任务记录。';
    void window.loadFile(START_PAGE);
  }
}});
const configPath = () => path.join(app.getPath('userData'), 'desktop-workspace.json');
function readConfig() { try { const value = JSON.parse(fs.readFileSync(configPath(), 'utf8')); return typeof value.workspaceRoot === 'string' && value.workspaceRoot.trim() ? value.workspaceRoot : null; } catch { return null; } }
function writeConfig(root) {
  writeWorkspaceConfig(configPath(), root);
}
function senderAllowed(event) {
  if (!window || window.isDestroyed() || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) return false;
  const url = event.senderFrame.url;
  if (url === START_URL) return true;
  return manager.baseUrl && isTrustedUrl(url, manager.baseUrl) && ['/', '/index.html'].includes(new URL(url).pathname);
}
function registerIpc(channel, handler) { ipcMain.handle(channel, (event, ...args) => { if (!senderAllowed(event)) throw new Error('此页面不允许调用桌面功能。'); if (args.length) throw new Error('桌面接口不接受附加参数。'); return handler(); }); }
function getInfo() { return {nativeDesktop:true, appVersion:app.getVersion(), ...manager.info(), startupError}; }
async function connect(root) {
  if (transition) throw new Error('工程连接正在处理，请稍候。');
  transition = true; startupError = '';
  try {
    const automaticRoot = path.join(app.getPath('userData'), 'engine');
    const automatic = !root || path.resolve(root).toLowerCase() === path.resolve(automaticRoot).toLowerCase();
    const resolved = validateWorkspace(automatic ? await ensureBundledEngine(process.resourcesPath, app.getPath('userData')) : root);
    if (manager.root) {
      const health = await manager.health();
      if (health.active_tasks) throw new Error('仍有审阅任务正在执行。请待任务完成后切换工程，当前任务和记录将保留。');
      const stopped = await manager.stop();
      if (!stopped.stopped) throw new Error('当前审阅服务尚未确认安全退出，请待任务完成后再次切换。');
    }
    await manager.start(resolved); writeConfig(resolved);
    await window.loadURL(manager.baseUrl + '/#session_token=' + encodeURIComponent(manager.sessionToken));
    return {cancelled:false, changed:true, workspaceRoot:resolved};
  } catch (error) { startupError = error.message; if (!manager.baseUrl) await window.loadFile(START_PAGE); throw error; }
  finally { transition = false; }
}
function configureSession(session) {
  session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  session.setPermissionCheckHandler(() => false);
  session.webRequest.onBeforeRequest({urls:['http://*/*', 'https://*/*']}, (details, callback) => {
    callback({cancel:!manager.baseUrl || !isTrustedUrl(details.url, manager.baseUrl)});
  });
  session.on('will-download', (event, item) => {
    if (!manager.baseUrl || !isAssetUrl(item.getURL(), manager.baseUrl)) { event.preventDefault(); return; }
    const filename = path.basename(item.getFilename()).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_');
    // Electron owns the native save dialog; the renderer cannot choose a destination.
    item.setSaveDialogOptions({title:'另存已核验的论文或报告', defaultPath:path.join(app.getPath('downloads'), filename)});
  });
}
function secureContents(contents, preview = false) {
  contents.on('will-navigate', (event, url) => {
    if ((!preview && url === START_URL) || (manager.baseUrl && isTrustedUrl(url, manager.baseUrl))) return;
    event.preventDefault();
  });
  contents.on('will-redirect', (event, url) => { if (!manager.baseUrl || !isTrustedUrl(url, manager.baseUrl)) event.preventDefault(); });
  contents.on('will-attach-webview', event => event.preventDefault());
  contents.setWindowOpenHandler(({url}) => {
    if (!preview && manager.baseUrl && isAssetUrl(url, manager.baseUrl)) {
      const child = new BrowserWindow({title:'论文证据预览', width:1050, height:800, parent:window, autoHideMenuBar:true,
        webPreferences:{nodeIntegration:false, contextIsolation:true, sandbox:true, webSecurity:true, partition:'persist:paper-desktop'}});
      secureContents(child.webContents, true); void child.loadURL(url);
    }
    return {action:'deny'};
  });
}
function showWindow() { if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } }
function ensureTray() {
  if (tray) return;
  tray = new Tray(nativeImage.createFromPath(APP_ICON));
  tray.setToolTip('论文审阅工作台 · 后台任务继续执行');
  tray.setContextMenu(Menu.buildFromTemplate([{label:'打开论文审阅工作台', click:showWindow}, {label:'安全退出', click:() => void requestClose()}]));
  tray.on('double-click', showWindow);
}
async function requestClose() {
  if (closing || quitting) return; closing = true;
  try {
    if (transition) { await dialog.showMessageBox(window, {type:'info', title:'服务正在连接', message:'审阅服务正在启动或切换，请完成后再关闭窗口。', buttons:['保留窗口']}); return; }
    if (manager.root) {
      const health = await manager.health();
      if (health.active_tasks > 0) {
        const selected = await dialog.showMessageBox(window, {type:'question', title:'审阅任务正在执行',
          message:`当前有 ${health.active_tasks} 个任务正在执行。`, detail:'关闭窗口后，桌面应用和审阅服务继续在后台运行。可通过系统托盘重新打开。',
          buttons:['保留窗口', '关闭窗口，继续处理'], defaultId:0, cancelId:0});
        if (selected.response === 1) { ensureTray(); window.hide(); } return;
      }
      transition = true; const stopped = await manager.stop(); transition = false;
      if (!stopped.stopped) { ensureTray(); window.show(); await dialog.showMessageBox(window, {type:'info', title:'服务退出尚未确认', message:'已保留审阅服务。请待任务完成后再次安全退出。', buttons:['保留窗口']}); return; }
    }
    quitting = true; if (tray) tray.destroy(); app.quit();
  } catch (error) { transition = false; ensureTray(); showWindow(); await dialog.showMessageBox(window, {type:'warning', title:'无法确认任务状态', message:error.message, buttons:['保留窗口']}); }
  finally { closing = false; }
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', showWindow);
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); void requestClose(); } });
  app.on('window-all-closed', () => { /* The owned service remains protected by explicit safe exit. */ });
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    window = new BrowserWindow({title:'论文审阅工作台', icon:APP_ICON, width:1440, height:960, minWidth:1000, minHeight:700,
      backgroundColor:'#f4f5f6', titleBarStyle:'hidden', titleBarOverlay:{color:'#f4f5f6', symbolColor:'#263336', height:48},
      autoHideMenuBar:true, show:false, webPreferences:{preload:path.join(__dirname, 'preload.cjs'),
        nodeIntegration:false, contextIsolation:true, sandbox:true, webSecurity:true, partition:'persist:paper-desktop'}});
    configureSession(window.webContents.session); secureContents(window.webContents);
    window.on('close', event => { if (!quitting) { event.preventDefault(); void requestClose(); } });
    window.once('ready-to-show', showWindow);
    registerIpc('desktop:info', getInfo);
    registerIpc('desktop:minimize', () => { window.minimize(); });
    registerIpc('desktop:toggle-maximize', () => { window.isMaximized() ? window.unmaximize() : window.maximize(); return {maximized:window.isMaximized()}; });
    registerIpc('desktop:close', requestClose);
    registerIpc('desktop:retry', () => connect(readConfig()));
    registerIpc('desktop:select-workspace', async () => {
      if (transition) throw new Error('工程连接正在处理，请稍候。');
      if (manager.root && (await manager.health()).active_tasks) throw new Error('仍有审阅任务正在执行，请待任务完成后选择其他工程。');
      const selected = await dialog.showOpenDialog(window, {title:'选择已安装的论文审阅工程目录', defaultPath:manager.root || readConfig() || app.getPath('userData'), properties:['openDirectory']});
      if (selected.canceled || !selected.filePaths.length) return {cancelled:true};
      return connect(selected.filePaths[0]);
    });
    await window.loadFile(START_PAGE); showWindow();
    try { await connect(readConfig()); } catch { /* Startup page displays a precise configuration error. */ }
  });
}
