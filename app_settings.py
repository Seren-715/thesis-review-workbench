"""Project-local deployment settings; validate paths without changing sources."""
from __future__ import annotations
import json
import os
from pathlib import Path

DEFAULT_WORKCOPY = Path(r'D:\SVN虚拟助教')
DEFAULT_SVN = Path(r'C:\Program Files\TortoiseSVN\bin\svn.exe')


def load_settings(root=None, environ=None):
    root = Path(root or Path(__file__).resolve().parent).resolve()
    env = os.environ if environ is None else environ
    config_path = root / 'config.json'
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict):
            raise ValueError('config.json must contain one JSON object')
        if any('key' in k.lower() or 'password' in k.lower() or 'token' == k.lower() for k in config):
            raise ValueError('config.json must not store API keys/passwords/tokens')
        if set(config) - {'workcopy', 'svn_executable'}:
            raise ValueError('unsupported config.json setting')
    result = {}
    # A standalone installation must never discover the developer's workcopy
    # merely because that historical drive happens to exist on the target PC.
    from portable_rules import packaged_mode
    default_workcopy = root/'data/svn-workcopy' if packaged_mode(root) else DEFAULT_WORKCOPY
    for name, env_key, default, kind in (
        ('workcopy', 'THESIS_WORKCOPY', default_workcopy, 'directory'),
        ('svn_executable', 'THESIS_SVN_EXECUTABLE', DEFAULT_SVN, 'file')):
        value = env.get(env_key) or config.get(name)
        if value is None:
            # Historical defaults remain available for importing offline modules;
            # their availability is checked at actual SVN execution as before.
            result[name] = default
            continue
        if not isinstance(value, str) or not value.strip() or '\x00' in value:
            raise ValueError(name + ' must be a non-empty local path')
        path = Path(value).expanduser()
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if (kind == 'directory' and not path.is_dir()) or (kind == 'file' and not path.is_file()):
            raise ValueError(name + ' path does not exist or has wrong type')
        result[name] = path
    return result
