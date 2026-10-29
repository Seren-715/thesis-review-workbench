"""Build an application source package; keep private history and keys outside."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED


def build(root, output, release_date=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    release_date = release_date or datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')
    if not isinstance(release_date, str) or not re.fullmatch(r'\d{8}', release_date):
        raise ValueError('Release date must use YYYYMMDD format')
    datetime.strptime(release_date, '%Y%m%d')
    target = output / f'thesis_review_source_{release_date}.zip'
    manifest_path = output / 'source_package_manifest.json'
    if target.exists() or manifest_path.exists():
        raise ValueError('Delivery output already exists; use a new output directory')
    index = root / 'web_frontend/dist/index.html'
    if not index.is_file():
        raise ValueError('Build the frontend before creating a delivery package')
    for relative in re.findall(r'(?:src|href)=[\"\']/?(assets/[^\"\']+)', index.read_text(encoding='utf-8')):
        asset = (index.parent / relative).resolve()
        if not asset.is_relative_to(index.parent.resolve()) or not asset.is_file():
            raise ValueError('Frontend index references a missing or invalid asset: ' + relative)
    output.mkdir(parents=True, exist_ok=True)
    files = set(root.glob('*.py')) | set(root.glob('*.ps1')) | set(root.glob('*.cmd'))
    files.update(root.glob('requirements*.txt'))
    files.update(root / name for name in ('config.example.json', '论文核查工作台使用说明.md'))
    # This project policy is separate from the private teacher-comment corpus.
    abstract_policy = root / 'data/policies/user_abstract_page_requirement.json'
    if abstract_policy.is_file():
        files.add(abstract_policy)
    desktop_guide = root / '桌面应用使用说明.md'
    if desktop_guide.is_file(): files.add(desktop_guide)
    for sub in ('web_backend', 'nanobot/nanobot', 'web_frontend/dist', 'web_frontend/src', 'web_frontend/tests',
                'desktop-app/src', 'desktop-app/assets', 'desktop-app/test'):
        base = root / sub
        files.update(p for p in base.rglob('*') if p.is_file() and
                     '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'})
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml'):
        if (root / 'nanobot' / name).exists(): files.add(root / 'nanobot' / name)
    for name in ('package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts',
                 'tailwind.config.js', 'postcss.config.js', 'index.html'):
        if (root / 'web_frontend' / name).is_file(): files.add(root / 'web_frontend' / name)
    for name in ('package.json', 'package-lock.json', 'README.md'):
        if (root / 'desktop-app' / name).is_file(): files.add(root / 'desktop-app' / name)
    for name in ('部署安装与迁移边界.md', '独立评测与规则审定操作说明.md'):
        files.add(root / 'deliverables' / name)
    checklist = root / 'deliverables/人工验收清单.md'
    if checklist.is_file(): files.add(checklist)
    receipt = {'created_at': datetime.now(timezone.utc).isoformat(),
               'release_date': release_date,
               'status': 'source_runtime_package_private_data_not_bundled', 'files': [],
               'limitations': ['Private historical data and credentials deliberately excluded',
                              'Historical demo buttons require separately verified private data',
                              'Wheelhouse is a separate platform-specific dependency artifact',
                              'Source package is not proof of independent model accuracy']}
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(root).as_posix()
            value = path.read_bytes()
            archive.writestr(relative, value)
            receipt['files'].append({'path': relative, 'sha256': hashlib.sha256(value).hexdigest(),
                                     'bytes': len(value)})
    receipt.update(archive_path=str(target), archive_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                   archive_bytes=target.stat().st_size, file_count=len(receipt['files']))
    manifest_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release-date', help='Release date in YYYYMMDD format; defaults to Asia/Shanghai date')
    args = parser.parse_args()
    result = build(args.root, args.output, args.release_date)
    print(json.dumps({'files': result['file_count'], 'bytes': result['archive_bytes'],
                      'archive': result['archive_path']}, ensure_ascii=False))
