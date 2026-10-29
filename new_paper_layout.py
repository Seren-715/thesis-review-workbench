"""One bounded, read-only Word layout collection for a newly uploaded source."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def collect_layout(source, output_dir, timeout=90):
    source, folder = Path(source).resolve(), Path(output_dir).resolve()
    if not folder.is_relative_to(ROOT / 'data'):
        raise ValueError('Layout evidence must be saved under project data')
    folder.mkdir(parents=True, exist_ok=False)
    baseline = hashlib.sha256(source.read_bytes()).hexdigest()
    audit, pdf = folder / 'word_layout.json', folder / 'preview.pdf'
    if os.name != 'nt':
        return {'status': 'unavailable', 'error': '当前系统无法运行 Microsoft Word。'}
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(ROOT / 'inspect_word_all_tables.ps1'), '-InputPath', str(source),
               '-AuditPath', str(audit), '-PdfPath', str(pdf)]
    try:
        # No shell expansion and no termination of unrelated Word processes.
        with (folder / 'word.log').open('wb') as log:
            outcome = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=timeout,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not audit.is_file():
            return {'status': 'failed', 'error': 'Word 未生成可核验分页记录。'}
        result = json.loads(audit.read_text(encoding='utf-8-sig'))
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        if (outcome.returncode != 0 or result.get('status') != 'native_table_layout_collected_visual_pending'
                or not result.get('source_unchanged') or baseline != after
                or result.get('source', {}).get('sha256') != baseline):
            return {'status': 'failed', 'error': 'Word 分页或原件保留核验未通过。',
                    'details': result.get('errors', [])}
        evidence = result.get('pdf', {})
        if (Path(evidence.get('path', '')).resolve() != pdf
                or not pdf.is_file() or hashlib.sha256(pdf.read_bytes()).hexdigest() != evidence.get('sha256')):
            return {'status': 'failed', 'error': 'Word PDF 来源核验未通过。'}
        return result
    except subprocess.TimeoutExpired:
        return {'status': 'failed', 'error': 'Word 分页采集超时，保留待复核；未自动重试。'}
    except (OSError, ValueError) as error:
        return {'status': 'failed', 'error': str(error)}
