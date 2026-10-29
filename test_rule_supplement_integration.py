import unittest
from pathlib import Path
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch
from reference_standards import reference_standards_view, attach_report_reference_evidence
from new_paper_checks import load_teacher_rules, execute_checks
from web_backend.test_new_paper_service import docx_bytes, fake_runner, wait_complete
from web_backend.new_paper_service import NewPapers

ROOT = Path(__file__).resolve().parent

class SupplementIntegrationTests(unittest.TestCase):
    def test_legacy_text_snapshot_does_not_open_original_as_zip(self):
        import hashlib
        import xml.etree.ElementTree as ET
        from new_paper_checks import W
        doc = ET.fromstring(f'<w:document xmlns:w="{W[1:-1]}"><w:body><w:p><w:r><w:t>论文正文说明系统设计。</w:t></w:r></w:p></w:body></w:document>')
        with TemporaryDirectory() as folder:
            path = Path(folder)/'legacy.doc'
            path.write_bytes(b'legacy binary source')
            parsed = (doc, None, hashlib.sha256(path.read_bytes()).hexdigest(), 0)
            report = execute_checks(path, parsed_source=parsed)
        self.assertEqual(len(report['coverage']), len({c['rule_id'] for c in report['coverage']}))
        self.assertEqual(next(c for c in report['coverage'] if c['rule_id']=='UP002')['status'], 'partial')

    def test_invalid_reference_disables_only_its_checks(self):
        with patch('reference_structure_rules.load_structure_rules', side_effect=ValueError('changed')):
            rules = load_teacher_rules()
        self.assertTrue(all(not r['enabled'] for r in rules if r['id'] in {'UP002', 'UP003', 'UP004'}))
        self.assertTrue(any(r.get('enabled', True) for r in rules if r['id'] == 'NP001'))
        import xml.etree.ElementTree as ET
        from reference_structure_rules import check_structure_rules
        findings, coverage = check_structure_rules(ET.fromstring('<x/>'), None, [], rules, 'sha')
        self.assertFalse(findings)
        self.assertEqual(len(coverage), 3)
        self.assertEqual({c['status'] for c in coverage}, {'reference_verification_required'})

    def test_all_rules_execute_once_with_verified_sources(self):
        rules = load_teacher_rules()
        ids = [r['id'] for r in rules]
        self.assertEqual(len(ids), len(set(ids)))
        expected = {'NP055', 'NP056', 'NP057', 'UP001', 'UP002', 'UP003', 'UP004', 'UP005', 'UP006', 'UP007'}
        self.assertTrue(expected <= set(ids))
        with TemporaryDirectory() as folder:
            path = Path(folder)/'paper.docx'
            path.write_bytes(docx_bytes())
            report = execute_checks(path, paper_type='software')
        coverage = [row['rule_id'] for row in report['coverage']]
        self.assertEqual(len(coverage), len(set(coverage)))
        self.assertTrue(expected <= set(coverage))
        self.assertTrue(all(isinstance(f.get('teacher_evidence'), list) for f in report['findings']))
        from paper_report import render_report
        self.assertIn('检查覆盖', render_report(report))

    def test_reference_reader_binding_rejects_unregistered_quote_and_path(self):
        catalog = reference_standards_view(ROOT)
        self.assertEqual(catalog['status'], 'verified_reference_materials')
        for source in catalog['sources']:
            source['reader_asset_id'] = 'trusted-'+source['id']
        clause = deepcopy(catalog['requirements'][0])
        clause['reader_asset_id'] = 'untrusted'
        report = {'findings': [{'id': 'test', 'reference_evidence': [clause, dict(clause, quote='无原文依据的要求')]}]}
        attach_report_reference_evidence(report, catalog)
        bound = report['findings'][0]['reference_requirements']
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]['reader_asset_id'], 'trusted-'+clause['source_id'])
        attach_report_reference_evidence(report, {'status': 'source_review_required'})
        self.assertEqual(report['findings'][0]['reference_requirements'], [])

    def test_paper_type_persisted_and_invalid_type_rejected(self):
        with TemporaryDirectory() as folder:
            seen = []
            def runner(path, output_dir, **options):
                seen.append(options.get('paper_type'))
                return fake_runner(path, output_dir, **options)
            service = NewPapers(Path(folder)/'papers', runner)
            try:
                paper = service.upload(docx_bytes(), '论文.docx', paper_type='software')
                completed = wait_complete(service, paper['id'])
                self.assertEqual(completed['paper_type'], 'software')
                self.assertEqual(seen, ['software'])
                with self.assertRaises(ValueError):
                    service.upload(docx_bytes(), '论文.docx', paper_type='invalid')
            finally:
                service.close()

if __name__ == '__main__':
    unittest.main()
