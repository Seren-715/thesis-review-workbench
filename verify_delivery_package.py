"""Extract only the self-created, hash-verified delivery archive into a new folder."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def extract(manifest, destination):
    manifest = json.loads(Path(manifest).read_text(encoding='utf8'))
    archive = Path(manifest['archive_path'])
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise ValueError('delivery archive hash mismatch')
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('extraction destination must be new')
    expected = {r['path']: r for r in manifest['files']}
    with ZipFile(archive) as zipped:
        if set(zipped.namelist()) != set(expected):
            raise ValueError('archive file set differs from manifest')
        for name in zipped.namelist():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('archive path escapes target')
            value = zipped.read(name)
            if hashlib.sha256(value).hexdigest() != expected[name]['sha256']:
                raise ValueError('archive member hash mismatch')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(extract(args.manifest, args.destination))
