'use strict';
const fs = require('node:fs');
const path = require('node:path');

function writeWorkspaceConfig(file, root, io = fs) {
  io.mkdirSync(path.dirname(file), {recursive:true});
  const data = JSON.stringify({workspaceRoot:root}, null, 2);
  const temp = `${file}.${process.pid}.tmp`;
  io.writeFileSync(temp, data, {mode:0o600});
  try {
    io.renameSync(temp, file);
  } catch (error) {
    // Some Windows encrypted or redirected user-data directories cannot rename.
    // This non-sensitive preference may use a direct write; review evidence never does.
    if (error.code !== 'EXDEV') throw error;
    io.writeFileSync(file, data, {mode:0o600});
    io.unlinkSync(temp);
  }
}
module.exports = {writeWorkspaceConfig};
