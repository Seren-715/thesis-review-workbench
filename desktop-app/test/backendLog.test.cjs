'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {EventEmitter}=require('node:events');
const {attachBackendLog,redactLog}=require('../src/backendLog.cjs');
test('diagnostics redact environment credentials and common headers',()=>{
  const output=redactLog('key=custom123 Authorization: Bearer abc123 api_key="other456" sk-example789',{API_KEY:'custom123'});
  for(const value of ['custom123','abc123','other456','sk-example789'])assert.ok(!output.includes(value));
});
test('stream chunks are buffered before credential filtering and preserve Unicode',t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'backend-log-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const child={stdout:new EventEmitter(),stderr:new EventEmitter()};const file=attachBackendLog(child,root);
  const line=Buffer.from('中文错误 sk-secretvalue\n');
  child.stderr.emit('data',line.subarray(0,2));child.stderr.emit('data',line.subarray(2,14));child.stderr.emit('data',line.subarray(14));
  const output=fs.readFileSync(file,'utf8');assert.match(output,/中文错误/);assert.ok(!output.includes('secretvalue'));
});
