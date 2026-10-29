'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {ensureBundledEngine} = require('../src/bundledEngine.cjs');
const {pythonPath, validateWorkspace} = require('../src/serviceManager.cjs');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), '独立 app 测试 '));
  t.after(() => fs.rmSync(root, {recursive:true,force:true}));
  const source = path.join(root, 'resources', 'bundled-engine');
  for (const name of ['runtime/python.exe', 'run_agent_ui.py', 'web_frontend/dist/index.html']) {
    fs.mkdirSync(path.dirname(path.join(source, name)), {recursive:true});
    fs.writeFileSync(path.join(source, name), 'fixture');
  }
  const manifest = {schema_version:1,app_version:'0.2.0',runtime:'runtime/python.exe',release_id:'standalone-test-v1',files:{'run_agent_ui.py':crypto.createHash('sha256').update('fixture').digest('hex')}};
  fs.writeFileSync(path.join(source, 'bundle-manifest.json'), JSON.stringify(manifest));
  return {root,source,manifest,resources:path.join(root,'resources'),user:path.join(root,'用户数据')};
}
test('first launch copies bundled runtime to writable Chinese and space path; restart preserves records', async t => {
  const f=fixture(t); const target=await ensureBundledEngine(f.resources,f.user);
  assert.equal(target,fs.realpathSync(path.join(f.user,'engine')));
  assert.equal(pythonPath(target),path.join(target,'runtime','python.exe'));
  fs.mkdirSync(path.join(target,'data')); fs.writeFileSync(path.join(target,'data','review.json'),'saved');
  assert.equal(await ensureBundledEngine(f.resources,f.user),target);
  assert.equal(fs.readFileSync(path.join(target,'data','review.json'),'utf8'),'saved');
});
test('copy target does not exist even when runtime rejects existing destination roots', async t => {
  const f=fixture(t); let copies=0;
  const strictCopy = async (source,destination,options) => {
    copies++;
    assert.equal(fs.existsSync(destination),false,'Electron cp requires a nonexistent destination');
    assert.equal(fs.existsSync(path.dirname(destination)),true);
    assert.equal(path.basename(destination),'payload');
    await fs.promises.cp(source,destination,options);
  };
  const target=await ensureBundledEngine(f.resources,f.user,strictCopy);
  assert.equal(copies,1);
  assert.equal(validateWorkspace(target),target);
  assert.deepEqual(fs.readdirSync(f.user),['engine']);
});
test('new bundle release refuses to overwrite existing workspace', async t => {
  const f=fixture(t); const target=await ensureBundledEngine(f.resources,f.user);
  f.manifest.release_id='new-release'; fs.writeFileSync(path.join(f.source,'bundle-manifest.json'),JSON.stringify(f.manifest));
  await assert.rejects(ensureBundledEngine(f.resources,f.user),/需要先迁移/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(target,'bundle-manifest.json'))).release_id,'standalone-test-v1');
});
test('corrupt bundled file fails before publishing engine and cleans staging', async t => {
  const f=fixture(t);fs.writeFileSync(path.join(f.source,'run_agent_ui.py'),'corrupted');
  await assert.rejects(ensureBundledEngine(f.resources,f.user),/校验失败/);
  assert.deepEqual(fs.readdirSync(f.user),[]);
});
test('invalid manifest and incomplete existing engine fail clearly without merging', async t => {
  const f=fixture(t);fs.mkdirSync(path.join(f.user,'engine'),{recursive:true});
  fs.writeFileSync(path.join(f.user,'engine','private.txt'),'keep');
  await assert.rejects(ensureBundledEngine(f.resources,f.user),/已保留其中的数据/);
  assert.equal(fs.readFileSync(path.join(f.user,'engine','private.txt'),'utf8'),'keep');
  f.manifest.schema_version=2;fs.writeFileSync(path.join(f.source,'bundle-manifest.json'),JSON.stringify(f.manifest));
  await assert.rejects(ensureBundledEngine(f.resources,f.user),/不受支持/);
});
test('legacy virtual environment remains accepted', t => {
  const f=fixture(t);fs.mkdirSync(path.join(f.source,'.venv','Scripts'),{recursive:true});
  fs.renameSync(path.join(f.source,'runtime','python.exe'),path.join(f.source,'.venv','Scripts','python.exe'));
  assert.equal(validateWorkspace(f.source),fs.realpathSync(f.source));
  assert.equal(pythonPath(f.source),path.join(f.source,'.venv','Scripts','python.exe'));
});
