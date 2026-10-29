"""Bind the bundled tool implementation to a fresh application environment."""
from pathlib import Path
import site
import sys

root = Path(sys.argv[1]).resolve()
if not (root / 'nanobot/agent/tools/registry.py').is_file():
    raise SystemExit('bundled tool registry is missing')
Path(site.getsitepackages()[0], 'thesis_nanobot_tools.pth').write_text(str(root) + '\n', encoding='utf8')
