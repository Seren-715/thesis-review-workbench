"""Distribution rules tested without Word, SVN, network, or model calls."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from portable_rules import (export_packaged_rules, load_packaged_rules, packaged_mode,
                            PACKAGE, DIGEST, MANIFEST, MODE)


class PortableRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        cls.bundle = export_packaged_rules(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_export_contains_no_historical_evidence_or_verified_original_claim(self):
        rows = load_packaged_rules(self.root)
        self.assertGreater(len(rows), 40)
        for row in rows:
            self.assertEqual(row['teacher_evidence'], [])
            self.assertEqual(row['reference_evidence'], [])
            self.assertFalse(row['packaged_provenance']['originals_verified_at_runtime'])
            self.assertNotIn('supporting_students', row)
            self.assertNotIn('same_category_feedback_reviewed', row)
            if row['id'].startswith('UP') or row['id'] in {'NP042', 'NP043'}:
                self.assertFalse(row['enabled'])
        text = (self.root / PACKAGE).read_text(encoding='utf8')
        for field in ('student_name', 'repository_path', 'document_path', 'source_path', 'teacher_quote'):
            self.assertNotIn('"' + field + '"', text)
        self.assertNotIn('D:\\', text)

    def test_missing_corrupt_or_wrong_mode_fails_closed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertFalse(packaged_mode(root))
            (root / MANIFEST).write_text(json.dumps({'mode': MODE, 'schema_version': 1}))
            with self.assertRaises(ValueError):
                load_packaged_rules(root)
            for filename in (PACKAGE, DIGEST):
                (root / filename).write_bytes((self.root / filename).read_bytes())
            (root / PACKAGE).write_bytes((root / PACKAGE).read_bytes() + b' ')
            with self.assertRaises(ValueError):
                load_packaged_rules(root)
            (root / MANIFEST).write_text('{"mode":"unknown"}')
            with self.assertRaises(ValueError):
                packaged_mode(root)

    def test_false_original_claim_rejected_even_with_matching_digest(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for filename in (PACKAGE, DIGEST, MANIFEST):
                (root / filename).write_bytes((self.root / filename).read_bytes())
            value = json.loads((root / PACKAGE).read_bytes())
            value['rules'][0]['packaged_provenance']['originals_verified_at_runtime'] = True
            raw = json.dumps(value).encode()
            (root / PACKAGE).write_bytes(raw)
            (root / DIGEST).write_text(hashlib.sha256(raw).hexdigest())
            with self.assertRaises(ValueError):
                load_packaged_rules(root)

    def test_docx_full_local_review_without_private_library(self):
        from docx import Document
        from new_paper_agent import run_review
        from web_backend.portable_startup import require_executable_rules, packaged_workspace
        with TemporaryDirectory() as output, ExitStack() as stack:
            stack.enter_context(patch('new_paper_checks.ROOT', self.root))
            stack.enter_context(patch('teacher_rule_library.get_library', side_effect=AssertionError('private library accessed')))
            stack.enter_context(patch('reference_standards.reference_standards_view', side_effect=AssertionError('private references accessed')))
            path = Path(output) / 'synthetic.docx'
            doc = Document()
            doc.add_heading('系统实现', 1)
            doc.add_paragraph('我设计了输入校验模块，输入无效时给出提示。')
            doc.save(path)
            report = run_review(path, Path(output) / 'review', use_agent=False, collect_word=False)
            self.assertEqual(report['requests'], {'llm': 0, 'svn': 0, 'word': 0})
            self.assertTrue(any(f['rule_id'] == 'NP001' for f in report['findings']))
            self.assertTrue((Path(output) / 'review' / 'report.md').is_file())
            self.assertEqual(len(require_executable_rules(self.root)), len(self.bundle['rules']))
            data, assets = packaged_workspace(self.root)
            self.assertTrue(data['startup']['checks_enabled'])
            self.assertFalse(data['startup']['historical_originals_verified'])
            self.assertEqual(assets, {})

    def test_pdf_local_review_without_private_library(self):
        import fitz
        from pdf_paper_review import run_review
        with TemporaryDirectory() as output, patch('new_paper_checks.ROOT', self.root), \
                patch('teacher_rule_library.get_library', side_effect=AssertionError('private library accessed')):
            path = Path(output) / 'synthetic.pdf'
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((72, 72), 'System implementation: input validation and error reporting.')
            pdf.save(path)
            pdf.close()
            report = run_review(path, Path(output) / 'review', use_agent=False)
            self.assertEqual(report['run']['dispatched_requests'], 0)
            self.assertTrue((Path(output) / 'review' / 'report.json').is_file())
            self.assertEqual(len(report['rules']), len(self.bundle['rules']))


if __name__ == '__main__':
    unittest.main()
