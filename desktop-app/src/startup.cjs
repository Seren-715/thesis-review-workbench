'use strict';
const status = document.getElementById('status');
async function refresh() { const info = await window.paperDesktop.getInfo(); document.getElementById('workspace').textContent = info.workspaceRoot || '尚未建立连接'; if (info.startupError) status.textContent = info.startupError; }
async function perform(method) {
  const buttons = [...document.querySelectorAll('button')]; buttons.forEach(button => button.disabled = true);
  status.textContent = '正在核验工程并连接审阅服务…';
  try { const result = await window.paperDesktop[method](); if (result?.cancelled) { status.textContent = '已取消选择，当前工程保持不变。'; await refresh(); } }
  catch (error) { status.textContent = error.message.replace(/^Error invoking remote method '[^']+': Error: /, ''); }
  finally { buttons.forEach(button => button.disabled = false); }
}
document.getElementById('choose').addEventListener('click', () => perform('selectWorkspace'));
document.getElementById('retry').addEventListener('click', () => perform('retry'));
void refresh();
// Main-process startup can finish after this page's first IPC request.
// Refresh its result until navigation replaces this startup document.
setInterval(() => { void refresh().catch(error => { status.textContent = error.message; }); }, 1200);
