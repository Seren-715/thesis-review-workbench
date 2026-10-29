"""Use an original-DOC read-only snapshot when a conversion changed body text."""
import hashlib
import json
from pathlib import Path
import subprocess
from legacy_word_snapshot import build_parsed

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def recover(original, folder, on_word=None):
    original,folder=Path(original).resolve(),Path(folder).resolve()
    audit_path=folder/(original.stem+'_conversion.json')
    audit=json.loads(audit_path.read_text(encoding='utf-8-sig'))
    if (audit.get('status')!='review_required_body_text_changed' or audit.get('errors') or audit.get('cleanup_errors')
            or audit.get('source_sha256_unchanged') is not True or audit.get('comment_fidelity_verified') is not True
            or audit.get('comment_metadata_sequence_verified') is not True
            or audit['source']['sha256']!=digest(original)):
        raise ValueError('此转换失败不满足原DOC文字快照回退条件。')
    derived=Path(audit['derived']['path']).resolve()
    if not derived.is_relative_to(folder) or digest(derived)!=audit['derived']['sha256']:
        raise ValueError('保留的转换副本已变化，不启用文字回退。')
    saved=sorted(folder.glob('read_only_body_snapshot_*.json'))
    snapshot=saved[0] if saved else folder/'original_doc_read_only_snapshot.json'
    if not snapshot.exists():
        if on_word:on_word()
        subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',
            str(Path(__file__).parent/'diagnose_legacy_body_text.ps1'),'-SourcePath',str(original),
            '-DerivedPath',str(derived),'-ResultPath',str(snapshot)],capture_output=True,timeout=90,check=True)
    parsed,info=build_parsed(snapshot,original)
    if info['original_body_utf8_sha256']!=audit['original_body_text_utf8_sha256']:
        raise ValueError('源正文快照与原Word转换前正文哈希不一致。')
    reader=folder/'original_doc_text_reader.md'
    text=json.loads(snapshot.read_text(encoding='utf-8-sig'))['source']['raw_body_text']
    for code in range(32):
        if code not in {9,10,13}:text=text.replace(chr(code),f'[原Word控制字符U+{code:04X}]')
    reader.write_text('# 原DOC只读正文\n\n转换副本正文保留核验未通过，以下读取的是源DOC正文。图片、公式与分页没有完成核查。\n\n'+text.replace('\r','\n'),encoding='utf-8')
    info.update(source_reader_path=str(reader),source_reader_sha256=digest(reader),
                conversion_audit_path=str(audit_path),conversion_audit_sha256=digest(audit_path))
    return parsed,info
