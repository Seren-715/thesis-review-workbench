'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/main.cjs'), 'utf8');
const start = source.indexOf('function profileDirectory(');
const end = source.indexOf('\n// Set the profile', start);
const profileDirectory = new Function('path', 'process', source.slice(start, end) + ';return profileDirectory;')(path, process);
test('profile override is optional and accepts absolute Chinese and space paths', () => {
  assert.equal(profileDirectory(['app.exe']), null);
  const profile = path.resolve('隔离验收 用户目录');
  assert.equal(profileDirectory(['app.exe', '--profile-dir=' + profile]), profile);
});
test('profile override rejects missing, relative and duplicate values', () => {
  for (const args of [['--profile-dir'], ['--profile-dir='], ['--profile-dir=  '], ['--profile-dir=relative'], ['--profile-dir=' + path.resolve('one'), '--profile-dir=' + path.resolve('two')]]) {
    assert.throws(() => profileDirectory(args), /profile-dir/);
  }
  if (process.platform === 'win32') for (const value of ['C:relative', '\\rooted-without-drive']) assert.throws(() => profileDirectory(['--profile-dir=' + value]), /absolute directory/);
});
test('profile is configured before the single-instance lock', () => {
  assert.ok(source.indexOf("app.setPath('userData', profile)") < source.indexOf('app.requestSingleInstanceLock()'));
});
