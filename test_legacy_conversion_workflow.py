"""Regression of provenance hashing in a stripped Windows PowerShell runtime.

Exercises just the actual helper functions; never creates a Word instance.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('convert_legacy_doc.ps1')
POWERSHELL = Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')


@unittest.skipUnless(POWERSHELL.is_file(), 'Windows PowerShell is required')
class LegacyConversionWorkflowTests(unittest.TestCase):
    def run_functions(self, code):
        script = SCRIPT.read_text(encoding='utf-8-sig')
        start = script.index('function Get-FileEvidence')
        end = script.index('# Validate destinations')
        # Force UTF-8 on the child console: Windows PowerShell otherwise emits
        # its stdout in the active ANSI code page (e.g. GBK), which breaks the
        # JSON decode below on non-English systems.
        command = ('$ErrorActionPreference="Stop"\n'
                   '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n'
                   + script[start:end] + '\n' + code)
        encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
        env = dict(os.environ)
        env['PSModulePath'] = ''
        process = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                 capture_output=True, timeout=20, env=env)
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', errors='replace'))
        return json.loads(process.stdout.decode('utf-8-sig'))

    def test_file_digest_unicode_path_without_get_file_hash_module(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / '教师原稿.doc'
            original = bytes(range(256)) * 5
            source.write_bytes(original)
            quoted = str(source).replace("'", "''")
            result = self.run_functions(f"Get-FileEvidence '{quoted}' | ConvertTo-Json -Compress")
            self.assertEqual(result['sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual(result['size_bytes'], len(original))
            self.assertEqual(source.read_bytes(), original)
            # Disposed hash stream permits normal exclusive rewrite afterward.
            source.write_bytes(b'new derived-independent bytes')

    def test_fidelity_still_rejects_changed_comment_or_scope(self):
        result = self.run_functions('''
$original = @([ordered]@{author='teacher'; initials='T'; date='2026-10-08'; range_text="full text`r"; scope_text="original scope`r"})
$same = @([ordered]@{author='teacher'; initials='T'; date='2026-10-08'; range_text="full text`n"; scope_text="original scope`n"})
$changed = @([ordered]@{author='teacher'; initials='T'; date='2026-10-08'; range_text="full text`r"; scope_text="changed scope`r"})
@{same=(Test-CommentMultiset $original $same); changed=(Test-CommentMultiset $original $changed); sequence=(Test-CommentSequence $original $same)} | ConvertTo-Json -Compress
''')
        self.assertTrue(result['same'])
        self.assertTrue(result['sequence'])
        self.assertFalse(result['changed'])


if __name__ == '__main__':
    unittest.main()
