'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {StringDecoder} = require('node:string_decoder');

function redactLog(line, env = process.env) {
  let value = line;
  for (const [name, secret] of Object.entries(env)) {
    if (/key|token|password|secret/i.test(name) && typeof secret === 'string' && secret.length >= 4) value = value.split(secret).join('[REDACTED]');
  }
  return value.replace(/\bsk-[A-Za-z0-9_-]+/g, '[REDACTED]')
    .replace(/(bearer\s+)\S+/gi, '$1[REDACTED]')
    .replace(/((?:api[_-]?key|x-deepseek-key|authorization|password|secret|token)["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi, '$1[REDACTED]');
}
function attachBackendLog(child, root) {
  const directory = path.join(root, 'data', 'ui_jobs');
  fs.mkdirSync(directory, {recursive:true});
  const file = path.join(directory, 'desktop_backend.log');
  // Rotation bounds routine diagnostics; it never touches review artifacts.
  if (fs.existsSync(file) && fs.statSync(file).size > 5 * 1024 * 1024) fs.renameSync(file, `${file}.${Date.now()}.old`);
  fs.appendFileSync(file, `\n[${new Date().toISOString()}] backend start\n`, {mode:0o600});
  const append = (stream, line) => {
    try { fs.appendFileSync(file, `[${stream}] ${redactLog(line)}\n`); }
    catch { /* A later disk failure must not crash the service manager. */ }
  };
  for (const name of ['stdout', 'stderr']) {
    const decoder = new StringDecoder('utf8');
    let pending = '', dropping = false;
    child[name].on('data', chunk => {
      pending += decoder.write(chunk);
      let newline;
      while ((newline = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
        if (!dropping && line.length <= 65536) append(name, line);
        else append(name, '[oversized diagnostic line omitted]');
        dropping = false;
      }
      if (pending.length > 65536) { pending = ''; dropping = true; }
    });
    child[name].on('end', () => { pending += decoder.end(); if (pending && !dropping) append(name, pending); });
  }
  return file;
}
module.exports = {attachBackendLog, redactLog};
