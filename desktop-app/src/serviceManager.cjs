'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const net = require('node:net');
const { spawn } = require('node:child_process');
const {attachBackendLog} = require('./backendLog.cjs');

const SERVICE = 'paper-evidence-workbench';
function canonicalRoot(root) { return fs.realpathSync.native(path.resolve(root)); }
function rootFingerprint(root) { return crypto.createHash('sha256').update(path.resolve(root).toLowerCase()).digest('hex'); }
function pythonPath(root) {
  for (const relative of ['runtime/python.exe', '.venv/Scripts/python.exe']) {
    const file = path.join(root, relative);
    if (fs.existsSync(file) && fs.statSync(file).isFile()) return file;
  }
  throw new Error('工程缺少 Python 运行时（runtime/python.exe 或 .venv/Scripts/python.exe）。请重新安装完整应用，或选择已安装的工程。');
}
function validateWorkspace(root) {
  const resolved = canonicalRoot(root);
  for (const relative of ['run_agent_ui.py', path.relative(resolved, pythonPath(resolved)), 'web_frontend/dist/index.html']) {
    const file = path.join(resolved, relative);
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) throw new Error(`所选工程缺少 ${relative}。请选择已完成安装的论文审阅工程。`);
    const real = fs.realpathSync.native(file);
    if (path.relative(resolved, real).startsWith('..') || path.isAbsolute(path.relative(resolved, real))) throw new Error('工程入口必须位于所选工程目录内。');
  }
  return resolved;
}
function healthMatches(health, root) {
  return health?.status === 'ready' && health?.service === SERVICE && health.accepting_tasks !== false &&
    typeof health.workspace_root === 'string' &&
    path.resolve(health.workspace_root).toLowerCase() === path.resolve(root).toLowerCase() &&
    health.workspace_fingerprint === rootFingerprint(root) &&
    Number.isSafeInteger(health.active_tasks) && health.active_tasks >= 0;
}
function isTrustedUrl(value, baseUrl) {
  try { const target = new URL(value); return target.origin === new URL(baseUrl).origin && target.username === '' && target.password === '' && target.protocol === 'http:'; }
  catch { return false; }
}
function isAssetUrl(value, baseUrl) {
  if (!isTrustedUrl(value, baseUrl)) return false;
  return /^\/api\/assets\/[a-f0-9]{24}$/.test(new URL(value).pathname);
}
function requestHealth(baseUrl, timeout = 1800, sessionToken = null) {
  const challenge = crypto.randomBytes(32).toString('hex');
  return new Promise(resolve => {
    const request = http.get(`${baseUrl}/api/health`, {timeout, headers:{'X-Health-Challenge':challenge}}, response => {
      let body = ''; response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; if (body.length > 65536) request.destroy(); });
      response.on('end', () => { try { const value = response.statusCode === 200 ? JSON.parse(body) : null;
        if(sessionToken) {
          const expected = crypto.createHmac('sha256', sessionToken).update(challenge + ':' + new URL(baseUrl).port).digest('hex');
          if(typeof value?.identity_proof !== 'string' || value.identity_proof.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(value.identity_proof), Buffer.from(expected))) return resolve(null);
        }
        resolve(value); } catch { resolve(null); } });
      response.on('error', () => resolve(null));
    });
    request.on('timeout', () => request.destroy()); request.on('error', () => resolve(null));
  });
}
function availablePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(error => error ? reject(error) : resolve(port)); });
  });
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
class ServiceManager {
  constructor(options = {}) { this.options = options; this.child = null; this.root = null; this.baseUrl = null; this.owner = 'none'; this.starting = false; }
  info() { return {workspaceRoot:this.root, baseUrl:this.baseUrl, backendOwner:this.owner}; }
  async health() {
    if (!this.baseUrl || !this.root) throw new Error('尚未连接审阅服务。');
    const result = await requestHealth(this.baseUrl, 5000, this.sessionToken);
    if (!healthMatches(result, this.root)) throw new Error('无法核验审阅服务状态。为保护正在执行的任务，已停止退出或切换操作。');
    return result;
  }
  async start(root) {
    if (this.starting) throw new Error('审阅服务正在启动，请稍候。');
    if (this.root) throw new Error('请先安全停止当前连接，再切换工程。');
    this.starting = true;
    try {
      this.root = validateWorkspace(root);
      this.sessionToken = crypto.randomBytes(32).toString('base64url');
      // An occupied or unknown service is never replaced or terminated.
      const port = await availablePort(); this.baseUrl = `http://127.0.0.1:${port}`;
      const python = pythonPath(this.root);
      this.child = spawn(python, ['-X', 'utf8', path.join(this.root, 'run_agent_ui.py'), '--port', String(port), '--desktop-stdio'], {
        cwd:this.root, windowsHide:true, shell:false, stdio:['pipe','pipe','pipe'],
        env:{...process.env, PYTHONUTF8:'1', PYTHONUNBUFFERED:'1', PAPER_DESKTOP_SESSION_TOKEN:this.sessionToken}
      });
      this.owner = 'owned'; const child = this.child;
      let spawnError = null; let exited = false;
      child.on('error', error => { spawnError = error; });
      child.once('exit', (code, signal) => { exited = true; if (this.child === child) { this.child = null; this.root = null; this.baseUrl = null; this.owner = 'none'; } this.options.onExit?.({code, signal}); });
      attachBackendLog(child, this.root);
      child.stdin.on('error', () => {});
      const deadline = Date.now() + (this.options.startupTimeout || 90000);
      while (Date.now() < deadline) {
        if (spawnError || exited) throw new Error('审阅服务未能启动。请查看工程 data/ui_jobs/desktop_backend.log，或重新安装完整应用。');
        if (healthMatches(await requestHealth(this.baseUrl, 1800, this.sessionToken), this.root)) return this.info();
        await delay(400);
      }
      throw new Error('审阅服务启动超过等待时间。请检查工程依赖后再次连接。');
    } catch (error) {
      // A service that has not passed identity verification has accepted no desktop jobs.
      if (this.child) { this.child.kill(); this.child = null; }
      this.root = null; this.baseUrl = null; this.owner = 'none'; throw error;
    } finally { this.starting = false; }
  }
  async stop() {
    if (!this.root) return {stopped:true};
    const health = await this.health();
    if (health.active_tasks > 0) return {stopped:false, busy:true, activeTasks:health.active_tasks};
    if (this.owner === 'shared') { this.root = null; this.baseUrl = null; this.owner = 'none'; return {stopped:true, sharedServicePreserved:true}; }
    const child = this.child;
    if (!child || child.exitCode !== null) throw new Error('自有审阅服务连接已中断。请重新连接后核验任务状态。');
    if (child.stdin.destroyed || !child.stdin.writable) throw new Error('服务控制通道已中断，不能确认安全退出。');
    const result = await new Promise(resolve => {
      let buffer = ''; let timer;
      const finish = result => { clearTimeout(timer); child.off('exit', onExit); child.stdout.off('data', onData); resolve(result); };
      const onExit = () => finish('exited');
      const onData = chunk => {
        buffer += chunk.toString('utf8'); const lines = buffer.split('\n'); buffer = lines.pop();
        for (const line of lines) { try { const event = JSON.parse(line); if (event.type === 'shutdown' && event.status === 'busy') finish('busy'); } catch {} }
      };
      child.once('exit', onExit); child.stdout.on('data', onData);
      timer = setTimeout(() => finish('unconfirmed'), this.options.shutdownTimeout || 12000);
      child.stdin.write(JSON.stringify({type:'shutdown', force:false}) + '\n');
    });
    if (result !== 'exited') return {stopped:false, busy:result === 'busy', unconfirmed:result === 'unconfirmed'};
    this.child = null; this.root = null; this.baseUrl = null; this.owner = 'none'; return {stopped:true};
  }
}
module.exports = {ServiceManager, validateWorkspace, pythonPath, canonicalRoot, rootFingerprint, healthMatches, isTrustedUrl, isAssetUrl, requestHealth};
