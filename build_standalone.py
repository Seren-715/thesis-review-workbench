"""Build a relocatable Windows engine without private papers or credentials."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
RELEASE_ID = 'standalone-20261022-v1'
VERSION = '0.2.0'


def excluded(directory, names):
    return [name for name in names if name == '__pycache__' or name.endswith(('.pyc', '.pyo'))]


def build_runtime(destination):
    """Vendor the tested interpreter and dependencies; never copy a venv launcher."""
    base = Path(sys.base_prefix)
    site = Path(sys.prefix) / 'Lib/site-packages'
    if sys.platform != 'win32' or not (base/'python313.dll').is_file():
        raise RuntimeError('Build with the tested Windows x64 Python 3.13 environment.')
    destination.mkdir(parents=True)
    for file in base.iterdir():
        if file.suffix.lower() in {'.dll', '.exe'} and file.is_file():
            shutil.copy2(file, destination/file.name)
    shutil.copy2(base/'LICENSE.txt', destination/'LICENSE.python.txt')
    shutil.copytree(base/'DLLs', destination/'DLLs', ignore=excluded)
    def stdlib_excluded(directory, names):
        return excluded(directory, names) + [n for n in names if n in {'site-packages', 'test', 'tests', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip'}]
    shutil.copytree(base/'Lib', destination/'Lib', ignore=stdlib_excluded)
    def packages_excluded(directory, names):
        return excluded(directory, names) + [n for n in names if n.endswith('.pth') or n.startswith('__editable__') or n == 'direct_url.json']
    shutil.copytree(site, destination/'Lib/site-packages', ignore=packages_excluded)
    # Resolve pywin32 without executing host-specific .pth bootstraps.
    for dll in (site/'pywin32_system32').glob('*.dll'):
        shutil.copy2(dll, destination/dll.name)
    paths = ['.', 'Lib', 'DLLs', 'Lib/site-packages', 'Lib/site-packages/win32',
             'Lib/site-packages/win32/lib', 'Lib/site-packages/pythonwin', '..', '../nanobot']
    (destination/'python313._pth').write_text('\n'.join(paths)+'\n', encoding='utf-8')
    return [{'name': d.metadata['Name'], 'version': d.version,
             'license': d.metadata.get('License-Expression') or d.metadata.get('License', '')}
            for d in importlib.metadata.distributions()]


def build(destination):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f'Refusing to overwrite an existing engine: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name+'.building_'+datetime.now().strftime('%H%M%S'))
    staging.mkdir()
    packages = build_runtime(staging/'runtime')
    for file in ROOT.glob('*.py'):
        if file.name.startswith(('test_', 'verify_')) or file.name in {'build_standalone.py'}:
            continue
        shutil.copy2(file, staging/file.name)
    for file in ROOT.glob('*.ps1'):
        if file.name.startswith(('启动', '停止')):
            continue
        shutil.copy2(file, staging/file.name)
    shutil.copytree(ROOT/'web_backend', staging/'web_backend', ignore=lambda d,n:excluded(d,n)+[f for f in n if f.startswith('test_')])
    shutil.copytree(ROOT/'web_frontend/dist', staging/'web_frontend/dist')
    shutil.copytree(ROOT/'nanobot/nanobot', staging/'nanobot/nanobot', ignore=excluded)
    shutil.copy2(ROOT/'nanobot/LICENSE', staging/'LICENSE.nanobot.txt')
    from portable_rules import export_packaged_rules
    export_packaged_rules(staging)
    (staging/'data').mkdir()
    # No host paths, SVN credentials, model keys or original workcopy in config.
    (staging/'config.json').write_text('{}\n', encoding='utf-8')
    (staging/'THIRD_PARTY_COMPONENTS.json').write_text(json.dumps(
        {'python':sys.version, 'components':sorted(packages,key=lambda p:p['name'].lower()),
         'license_files':'runtime/Lib/site-packages/*dist-info/licenses and LICENSE files'},
        ensure_ascii=False,indent=2), encoding='utf-8')
    readme = ROOT/'独立版使用说明.md'
    if readme.exists():
        shutil.copy2(readme, staging/readme.name)
    files = {p.relative_to(staging).as_posix():sha256(p.read_bytes()).hexdigest()
             for p in sorted(staging.rglob('*')) if p.is_file()}
    manifest = {'schema_version':1, 'app_version':VERSION, 'release_id':RELEASE_ID,
                'created_at':datetime.now(timezone.utc).isoformat(), 'runtime':'runtime/python.exe',
                'private_history_included':False, 'api_keys_included':False, 'files':files}
    (staging/'bundle-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    # Execute with the bundled interpreter, outside the original source cwd.
    probe = subprocess.run([str(staging/'runtime/python.exe'), '-B', '-c',
        'import sys,json,ssl,fitz,docx,lxml,openai; from web_backend.server import Workspace; '
        'from new_paper_checks import load_teacher_rules; '
        'print(json.dumps({"isolated":sys.flags.isolated,"executable":sys.executable,"rules":len(load_teacher_rules()),"sys_path":sys.path}))'],
        cwd=staging, capture_output=True, text=True,encoding='utf-8')
    if probe.returncode:
        raise RuntimeError(f'Bundled engine import failed; build retained at {staging}:\n{probe.stderr}')
    staging.rename(destination)
    return {'engine':str(destination), 'file_count':len(files), 'bytes':sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()),
            'probe':json.loads(probe.stdout)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/standalone_20261022/engine')
    args = parser.parse_args()
    print(json.dumps(build(args.output),ensure_ascii=False,indent=2))
