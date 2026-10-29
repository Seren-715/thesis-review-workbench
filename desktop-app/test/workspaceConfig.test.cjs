const {test} = require('node:test');
const assert = require('node:assert/strict');
const {writeWorkspaceConfig} = require('../src/workspaceConfig.cjs');

test('redirected Windows preferences recover from EXDEV without changing review evidence', () => {
  const writes = [], removed = [];
  const io = {mkdirSync(){}, writeFileSync(file,data){writes.push([file,data])},
    renameSync(){throw Object.assign(new Error('cross-device'), {code:'EXDEV'})},
    unlinkSync(file){removed.push(file)}};
  writeWorkspaceConfig('config/desktop-workspace.json', 'D:\\svn_agent', io);
  assert.equal(writes.length, 2);
  assert.equal(writes[1][0], 'config/desktop-workspace.json');
  assert.deepEqual(JSON.parse(writes[1][1]), {workspaceRoot:'D:\\svn_agent'});
  assert.equal(removed.length, 1);
});
test('permission failure is reported and does not overwrite the preference', () => {
  const writes = [];
  const io = {mkdirSync(){}, writeFileSync(file){writes.push(file)},
    renameSync(){throw Object.assign(new Error('denied'), {code:'EACCES'})}};
  assert.throws(()=>writeWorkspaceConfig('config/desktop-workspace.json', 'D:\\svn_agent', io), /denied/);
  assert.equal(writes.length, 1);
});
