'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {EventEmitter} = require('node:events');
const {ServiceManager, validateWorkspace, rootFingerprint, healthMatches, isTrustedUrl, isAssetUrl, requestHealth} = require('../src/serviceManager.cjs');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'paper-desktop-'));
  for (const relative of ['run_agent_ui.py', '.venv/Scripts/python.exe', 'web_frontend/dist/index.html']) {
    const file = path.join(root, relative); fs.mkdirSync(path.dirname(file), {recursive:true}); fs.writeFileSync(file, 'fixture');
  }
  t.after(() => fs.rmSync(root, {recursive:true, force:true})); return root;
}
function health(root, active_tasks=0) { return {status:'ready', service:'paper-evidence-workbench', workspace_root:root, workspace_fingerprint:rootFingerprint(root), active_tasks}; }
test('workspace requires engine, own Python runtime and built frontend', t => {
  const root = fixture(t); assert.equal(validateWorkspace(root), fs.realpathSync.native(root));
  fs.unlinkSync(path.join(root, 'run_agent_ui.py')); assert.throws(() => validateWorkspace(root), /run_agent_ui/);
});
test('service identity rejects wrong root, marker, fingerprint and unknown task state', t => {
  const root = fixture(t); const valid = health(root); assert.equal(healthMatches(valid, root), true);
  for (const invalid of [{...valid, workspace_root:path.join(root,'other')}, {...valid, service:'unknown'}, {...valid,workspace_fingerprint:'0'.repeat(64)}, {...valid,active_tasks:null}, {...valid,active_tasks:-1}, {...valid,accepting_tasks:false}]) assert.equal(healthMatches(invalid, root), false);
});
test('trusted renderer origin excludes credentials, prefix attacks and other protocol', () => {
  const base = 'http://127.0.0.1:8787';
  assert.equal(isTrustedUrl(base+'/?q=a#item',base),true);
  for (const url of ['http://127.0.0.1:8788/','http://127.0.0.1.evil:8787/','http://user@127.0.0.1:8787/','https://127.0.0.1:8787/','file:///C:/secret','javascript:alert(1)']) assert.equal(isTrustedUrl(url,base),false);
});
test('asset preview and downloads require registered identifier route', () => {
  const base = 'http://127.0.0.1:8787'; const valid=base+'/api/assets/'+ 'a'.repeat(24);
  assert.equal(isAssetUrl(valid,base),true); assert.equal(isAssetUrl(valid+'?view=1',base),true);
  for (const suffix of ['/api/assets/../../config.json','/api/assets/test','/api/config','/assets/index.js','/api/assets/'+'a'.repeat(25)]) assert.equal(isAssetUrl(base+suffix,base),false);
});
test('health HTTP fails closed on malformed payload', async t => {
  const server=http.createServer((_req,res)=>res.end('{bad')); await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>server.close()); assert.equal(await requestHealth(`http://127.0.0.1:${server.address().port}`),null);
});
test('public health fields cannot authenticate a desktop backend', async t => {
  const root=fixture(t);
  const server=http.createServer((_req,res)=>res.end(JSON.stringify(health(root))));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>server.close());
  const base=`http://127.0.0.1:${server.address().port}`;
  assert.equal(await requestHealth(base, 1800, 'private-token'),null);
  const manager=new ServiceManager(); manager.root=root;manager.baseUrl=base;manager.sessionToken='private-token';
  await assert.rejects(manager.health());
  assert.equal(server.listening,true);
});
test('health proof requires the current challenge, port and secret', async t => {
  const crypto=require('node:crypto'); const root=fixture(t); let mode='correct';
  const server=http.createServer((req,res)=>{
    const challenge=req.headers['x-health-challenge'];
    const identity_proof=crypto.createHmac('sha256', mode==='wrong-secret'?'other':'private-token').update((mode==='replay'?'old':challenge)+':'+(mode==='wrong-port'?1:server.address().port)).digest('hex');
    res.end(JSON.stringify({...health(root),identity_proof}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>server.close());
  const base=`http://127.0.0.1:${server.address().port}`;
  assert.ok(await requestHealth(base,1800,'private-token'));
  for(mode of ['wrong-secret','replay','wrong-port']) assert.equal(await requestHealth(base,1800,'private-token'),null);
});
function ownedManager(root, event) {
  const manager = new ServiceManager({shutdownTimeout:100});
  const child = new EventEmitter(); child.exitCode = null; child.stdout = new EventEmitter();
  child.stdin = {destroyed:false, writable:true, write:line => {
    const value=JSON.parse(line); assert.deepEqual(value,{type:'shutdown',force:false});
    setImmediate(() => { if (event==='busy') child.stdout.emit('data',Buffer.from(JSON.stringify({type:'shutdown',status:'busy'})+'\n')); else child.emit('exit',0); });
  }};
  manager.root=root; manager.baseUrl='http://127.0.0.1:50001'; manager.owner='owned'; manager.child=child;
  manager.health=async()=>health(root); return manager;
}
test('new task between health check and shutdown keeps the owned engine alive', async t => {
  const manager=ownedManager(fixture(t),'busy'); const child=manager.child;
  assert.deepEqual(await manager.stop(),{stopped:false,busy:true,unconfirmed:false});
  assert.equal(manager.child,child);assert.equal(manager.owner,'owned');assert.equal(child.listenerCount('exit'),0);
});
test('owned shutdown waits for process exit and releases only its own connection', async t => {
  const manager=ownedManager(fixture(t),'exit');
  assert.deepEqual(await manager.stop(),{stopped:true});assert.equal(manager.child,null);assert.equal(manager.owner,'none');
});
test('unverified task state never sends shutdown or clears ownership', async t => {
  const manager=ownedManager(fixture(t),'exit'); manager.health=async()=>{throw new Error('unknown state');}; const child=manager.child;
  await assert.rejects(manager.stop(),/unknown state/);assert.equal(manager.child,child);assert.equal(manager.owner,'owned');
});
