"""Real synthetic PDF pagination tests; no Word, network, SVN or LLM."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import fitz
import new_paper_checks as checks
from new_paper_agent import FreshPaperTool, run_review
from teacher_page_requirements import check_page_rules, load_page_rules, load_user_page_rules
from test_new_paper_checks import CT, STYLES, NS, p


class UserAbstractPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source_hash = 'source-current-hash'
        self.rules = load_user_page_rules()
        self.paragraphs = [self.row(9, '论文题目：方法验证'), self.row(0, '摘要', heading_level=0),
                           self.row(1, '本研究建立方法并验证结果。'),
                           self.row(2, '关键词：方法；结果'),
                           self.row(3, 'Abstract', heading_level=0)]

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def row(index, text, **extra):
        return dict(index=index, text=text, heading_level=None, **extra) if 'heading_level' not in extra else dict(index=index, text=text, **extra)

    def pdf(self, pages):
        path = self.folder / 'fixture.pdf'
        with fitz.open() as pdf:
            for lines in pages:
                page = pdf.new_page()
                if '摘要' in lines and '论文题目：方法验证' not in lines:
                    lines = ['论文题目：方法验证'] + lines
                for index, text in enumerate(lines):
                    page.insert_text((60, 60 + 24 * index), text, fontname='china-s', fontsize=12)
            pdf.save(path)
        return dict(status='native_table_layout_collected_visual_pending',
                    source=dict(sha256=self.source_hash), source_unchanged=True,
                    pdf=dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
                    table_results=[], tables_before=[])

    def run_check(self, layout, paragraphs=None):
        return check_page_rules(ET.fromstring(f'<w:document xmlns:w="{NS}"/>'),
                                ET.fromstring(f'<w:styles xmlns:w="{NS}"/>'),
                                paragraphs or self.paragraphs, self.rules, self.source_hash, layout)

    def test_user_policy_not_teacher_evidence_and_english_keeps_existing_enabling(self):
        rules = load_page_rules({'rules': []})
        chinese = next(r for r in rules if r['id'] == 'UP001')
        self.assertEqual(chinese['teacher_evidence'], [])
        evidence = chinese['policy_evidence'][0]
        self.assertEqual(evidence['confirmed_date'], '2026-10-13')
        self.assertEqual(evidence['origin'], 'user_confirmed_requirement')
        self.assertIsNone(evidence['minimum_word_count'])
        self.assertEqual(evidence['configuration_sha256'], hashlib.sha256(Path(evidence['configuration_path']).read_bytes()).hexdigest())
        self.assertFalse(next(r for r in rules if r['id'] == 'NP043')['enabled'])
        enabled = load_page_rules({'rules': [{'id': 'english_abstract_length', 'teacher_evidence': [{'quote': '英文摘要一页'}]}]})
        self.assertTrue(next(r for r in enabled if r['id'] == 'NP043')['enabled'])

    def test_short_single_page_has_no_invented_minimum(self):
        findings, coverage = self.run_check(self.pdf([['摘要', '本研究建立方法并验证结果。', '关键词：方法；结果']]))
        self.assertEqual(findings, [])
        self.assertEqual(coverage[0]['status'], 'checked')
        self.assertEqual(coverage[0]['physical_pages'], [1, 1])
        self.assertIn('不设字数下限', coverage[0]['detail'])

    def test_missing_policy_does_not_break_other_page_rules(self):
        missing = self.folder / 'missing.json'
        self.assertEqual(load_user_page_rules(missing), [])
        with patch('teacher_page_requirements.USER_POLICY_PATH', missing):
            self.assertEqual([r['id'] for r in load_page_rules({'rules': []})], ['NP042', 'NP043'])

    def test_invalid_configuration_abstains_without_throwing(self):
        path = self.folder / 'invalid.json'
        path.write_text('{invalid json', encoding='utf-8')
        self.rules = load_user_page_rules(path)
        findings, coverage = self.run_check(None)
        self.assertEqual(findings, [])
        self.assertEqual(coverage[0]['status'], 'partial')
        self.assertIn('配置需复核', coverage[0]['detail'])

    def test_title_on_previous_page_counts_and_unknown_title_abstains(self):
        layout = self.pdf([['论文题目：方法验证'], ['摘要', '本研究建立方法并验证结果。', '关键词：方法；结果']])
        # pdf helper normally inserts the title; keep only the page-one title.
        with fitz.open(layout['pdf']['path']) as original:
            path = self.folder / 'title-before.pdf'
            with fitz.open() as fixed:
                fixed.insert_pdf(original, from_page=0, to_page=0)
                page = fixed.new_page()
                for i, text in enumerate(['摘要', '本研究建立方法并验证结果。', '关键词：方法；结果']):
                    page.insert_text((60, 60 + 24 * i), text, fontname='china-s')
                fixed.save(path)
        layout['pdf'] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        findings, coverage = self.run_check(layout)
        self.assertEqual(len(findings), 1)
        self.assertEqual(coverage[0]['physical_pages'], [1, 2])
        findings, coverage = self.run_check(layout, self.paragraphs[1:])
        self.assertEqual(findings, [])
        self.assertEqual(coverage[0]['status'], 'partial')
        self.assertIn('题目', coverage[0]['detail'])

    def test_keywords_overflow_is_detected_and_source_bound(self):
        layout = self.pdf([['摘要', '本研究建立方法并验证结果。'], ['关键词：方法；结果']])
        findings, coverage = self.run_check(layout)
        self.assertEqual(coverage[0]['physical_pages'], [1, 2])
        self.assertEqual(findings[0]['rule_id'], 'UP001')
        self.assertEqual(findings[0]['teacher_evidence'], [])
        self.assertEqual(findings[0]['source_page_evidence']['source_sha256'], self.source_hash)
        self.assertEqual(findings[0]['source_page_evidence']['pdf'], layout['pdf'])

    def test_heading_orphan_on_previous_page_counts_as_overflow(self):
        findings, coverage = self.run_check(self.pdf([['摘要'], ['本研究建立方法并验证结果。', '关键词：方法；结果']]))
        self.assertEqual(len(findings), 1)
        self.assertEqual(coverage[0]['physical_pages'], [1, 2])

    def test_no_layout_wrong_source_changed_source_or_wrong_pdf_abstains(self):
        layout = self.pdf([['摘要', '本研究建立方法并验证结果。'], ['关键词：方法；结果']])
        variants = [None, deepcopy(layout), deepcopy(layout), deepcopy(layout)]
        variants[1]['source']['sha256'] = 'other-source'
        variants[2]['source_unchanged'] = False
        variants[3]['pdf']['sha256'] = 'other-pdf'
        for variant in variants:
            with self.subTest(variant=variant):
                findings, coverage = self.run_check(variant)
                self.assertEqual(findings, [])
                self.assertEqual(coverage[0]['status'], 'partial')
                self.assertIn('未核验', coverage[0]['detail'])
                self.assertNotIn('physical_pages', coverage[0])

    def test_toc_heading_never_starts_abstract(self):
        paragraphs = [dict(self.paragraphs[1], in_toc=True), self.row(1, '这是正文，不是摘要。'), self.row(2, '绪论', heading_level=0)]
        findings, coverage = self.run_check(self.pdf([['摘要', '这是正文，不是摘要。', '绪论']]), paragraphs)
        self.assertEqual(findings, [])
        self.assertEqual(coverage[0]['status'], 'partial')
        self.assertIn('没有唯一', coverage[0]['detail'])

    def test_real_heading_after_toc_entry_is_selected(self):
        rows = [self.row(8, '摘要', heading_level=0, in_toc=True)] + self.paragraphs
        findings, coverage = self.run_check(self.pdf([['目录', '摘要……1'], ['摘要', '本研究建立方法并验证结果。', '关键词：方法；结果']]), rows)
        self.assertEqual(findings, [])
        self.assertEqual(coverage[0]['physical_pages'], [2, 2])

    def test_repeated_or_incomplete_pdf_text_abstains(self):
        for pages in ([['摘要', '本研究建立方法并验证结果。', '关键词：方法；结果', '本研究建立方法并验证结果。']],
                      [['摘要', '本研究建立方法并验证结果。']]):
            findings, coverage = self.run_check(self.pdf(pages))
            self.assertEqual(findings, [])
            self.assertEqual(coverage[0]['status'], 'partial')

    def test_missing_keyword_boundary_cannot_claim_whole_abstract_checked(self):
        rows = [r for r in self.paragraphs if not r['text'].startswith('关键词')]
        findings, coverage = self.run_check(self.pdf([['摘要', '本研究建立方法并验证结果。']]), rows)
        self.assertEqual(findings, [])
        self.assertEqual(coverage[0]['status'], 'partial')
        self.assertIn('关键词结束边界', coverage[0]['detail'])

    def test_uploaded_document_executor_and_tool_include_policy(self):
        source = self.folder / 'upload.docx'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('[Content_Types].xml', CT)
            archive.writestr('word/document.xml', f'<w:document xmlns:w="{NS}"><w:body>' +
                             p('论文题目：方法验证') + p('摘要', 'Custom') + p('本研究建立方法并验证结果。') +
                             p('关键词：方法；结果') + p('Abstract', 'Custom') + '</w:body></w:document>')
            archive.writestr('word/styles.xml', STYLES)
        self.source_hash = checks.digest(source)
        layout = self.pdf([['摘要', '本研究建立方法并验证结果。'], ['关键词：方法；结果']])
        base = [dict(id=rid, title=rid, teacher_evidence=[]) for rid in ('NP001', 'NP002', 'NP003')]
        with patch.object(checks, 'load_teacher_rules', return_value=base + self.rules):
            report = checks.execute_checks(source, layout_result=layout)
        self.assertEqual(next(r for r in report['coverage'] if r['rule_id'] == 'UP001')['physical_pages'], [1, 2])
        tool = FreshPaperTool('inspect_extended_teacher_requirements', report)
        payload = json.loads(asyncio.run(tool.execute()))
        self.assertEqual(payload['finding_count'], 1)
        self.assertEqual(next(r for r in payload['coverage'] if r['rule_id'] == 'UP001')['physical_pages'], [1, 2])
        self.assertEqual(report['requests'], dict(llm=0, svn=0, word=0))
        # A text-only thesis still triggers the authorized layout collector.
        # The collector is stubbed; no Word process or external call is made.
        with patch.object(checks, 'load_teacher_rules', return_value=base + self.rules), \
             patch('new_paper_layout.collect_layout', return_value=layout) as collector:
            reviewed = run_review(source, self.folder / 'review', collect_word=True, use_agent=False)
        collector.assert_called_once()
        self.assertEqual(next(r for r in reviewed['coverage'] if r['rule_id'] == 'UP001')['physical_pages'], [1, 2])


if __name__ == '__main__':
    unittest.main()
