'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {validateWorkspace} = require('./serviceManager.cjs');

// Never merge an installation into an existing workspace: its records and
// configuration belong to the user, including after an application upgrade.
function readManifest(root) {
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(path.join(root, 'bundle-manifest.json'), 'utf8')); }
  catch { throw new Error('内置引擎清单无法读取。'); }
  if (manifest.schema_version !== 1 || manifest.app_version !== '0.2.0' ||
      manifest.runtime !== 'runtime/python.exe' || typeof manifest.release_id !== 'string' ||
      !/^[a-zA-Z0-9._-]+$/.test(manifest.release_id)) throw new Error('内置引擎清单版本或格式不受支持。');
  return manifest;
}
async function verifyFiles(root, manifest) {
  if (manifest.files === undefined) return;
  if (!manifest.files || typeof manifest.files !== 'object' || Array.isArray(manifest.files)) throw new Error('引擎文件校验清单格式错误。');
  for (const [relative, expected] of Object.entries(manifest.files)) {
    const file = path.resolve(root, relative);
    const local = path.relative(root, file);
    if (!local || local.startsWith('..') || path.isAbsolute(local) || typeof expected !== 'string' || !/^[a-f0-9]{64}$/i.test(expected)) throw new Error('引擎文件校验清单含无效路径或哈希。');
    const actual = crypto.createHash('sha256').update(await fs.promises.readFile(file)).digest('hex');
    if (actual !== expected.toLowerCase()) throw new Error(`内置引擎文件校验失败：${relative}`);
  }
}
async function ensureBundledEngine(resourcesPath, userData, copyDirectory = fs.promises.cp.bind(fs.promises)) {
  const destination = path.join(userData, 'engine');
  const source = path.join(resourcesPath, 'bundled-engine');
  let bundled;
  try { validateWorkspace(source); bundled = readManifest(source); }
  catch (error) { throw new Error(`应用内置审阅引擎缺失或不完整，请重新安装完整应用。${error.message}`); }
  if (fs.existsSync(destination)) {
    try {
      const installed = readManifest(destination);
      if (installed.release_id !== bundled.release_id || installed.app_version !== bundled.app_version) throw new Error('应用引擎版本已更新，需要先迁移旧工作区；旧资料已保留，请勿删除工作区。');
      return validateWorkspace(destination);
    }
    catch (error) { throw new Error(`本机审阅工作区不完整，已保留其中的数据，未自动覆盖。工作区：${destination}。${error.message}`); }
  }
  await fs.promises.mkdir(userData, {recursive:true});
  const stagingParent = await fs.promises.mkdtemp(path.join(userData, 'engine-install-'));
  const staging = path.join(stagingParent, 'payload');
  try {
    await copyDirectory(source, staging, {recursive:true, force:false, errorOnExist:true});
    validateWorkspace(staging);
    await verifyFiles(staging, bundled);
    await fs.promises.rename(staging, destination);
    return validateWorkspace(destination);
  } catch (error) {
    throw new Error(`无法初始化本机审阅引擎，请检查磁盘空间和工作区写入权限后重试。工作区：${destination}。${error.message}`);
  } finally {
    // Only our newly allocated temporary directory can be removed here.
    if (fs.existsSync(stagingParent)) await fs.promises.rm(stagingParent, {recursive:true, force:true});
  }
}

module.exports = {ensureBundledEngine, readManifest, verifyFiles};
