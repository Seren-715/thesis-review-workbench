import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from new_paper_agent import BoundedClient, review_with_tools, reuse_review_blocks, run_review
from content_context_agent import content_context_key
from test_content_context_agent import sample
from teacher_page_requirements import load_page_rules, check_page_rules, W
import xml.etree.ElementTree as ET
import hashlib


class FullRemediationTests(unittest.TestCase):
    def test_zero_budget_full_entry_needs_no_credentials(self):
        from docx import Document
        with TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True), patch('openai.OpenAI', side_effect=AssertionError('No provider authorized')):
            source = Path(folder)/'test.docx'
            document = Document()
            document.add_heading('摘要', level=1)
            document.add_paragraph('本文设计教学辅助系统，结合完整摘要及后续章节进行核查。')
            document.add_paragraph('关键词：教学辅助；系统设计')
            document.add_heading('绪论', level=1)
            document.add_paragraph('本文研究教学辅助系统的设计与实现。')
            document.save(source)
            report = run_review(source, Path(folder)/'review', use_agent=True, max_total_bound=0, collect_word=False)
            self.assertEqual(report['requests']['llm'], 0)
            self.assertNotIn('error', report['run'])
            self.assertEqual(report['run']['status'], 'partial')

    def test_strict_round_reservation_cannot_be_released_by_cheap_actual_usage(self):
        state = dict(requests=[{'reserved_upper_bound':23000,'usage':{'total_tokens':20}}],
                     reserved_token_bound=23000, known_total_tokens=20, unknown_cost_attempts=0,
                     request_attempts=1, max_requests=10, max_reserved_token_bound=24000,
                     strict_reservation_budget=True)
        with TemporaryDirectory() as folder:
            self.assertFalse(BoundedClient(None, Path(folder), state).can_request(1001))
            self.assertTrue(BoundedClient(None, Path(folder), state).can_request(1000))

    def test_zero_round_budget_retains_local_findings_without_any_provider_call(self):
        report, finding, rule = sample()
        before = deepcopy(report['findings'])
        create = Mock(side_effect=AssertionError('No request authorized'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        state = dict(requests=[],tool_calls=[],request_attempts=0,known_total_tokens=0,
                     unknown_cost_attempts=0,reserved_token_bound=0,max_reserved_token_bound=0)
        with TemporaryDirectory() as folder:
            asyncio.run(review_with_tools(report, client, Path(folder), state))
        create.assert_not_called()
        self.assertEqual(report['findings'], before)

    def test_content_block_reuses_actual_content_key_and_rejects_changed_scope(self):
        report, finding, rule = sample()
        old = deepcopy(report)
        old['findings'][0]['review_blocked'] = dict(phase='content',code='invalid_output',
            source_sha256=report['source_sha256'], context_key=content_context_key(finding,rule))
        self.assertEqual(reuse_review_blocks(report, old), 1)
        current, f, r = sample()
        f['content_scope']['chunks'][1]['text'] += '新增证据'
        self.assertEqual(reuse_review_blocks(current, old), 0)

    def test_bare_format_comment_does_not_invent_cover_or_abstract_threshold(self):
        library = {'rules':[{'id':'cover_layout','teacher_evidence':[{'quote':'格式'}]},
                            {'id':'english_abstract_length','teacher_evidence':[{'quote':'英文摘要太长'}]}]}
        # This verifies inference from vague teacher comments; a separately
        # confirmed user policy is independent evidence, not an invented rule.
        with patch('teacher_page_requirements.load_user_page_rules', return_value=[]):
            self.assertTrue(all(not r['enabled'] for r in load_page_rules(library)))

    def test_explicit_date_alignment_uses_actual_paragraph_format_only(self):
        document = ET.Element(W+'document');body = ET.SubElement(document,W+'body')
        p = ET.SubElement(body,W+'p');pr = ET.SubElement(p,W+'pPr');ET.SubElement(pr,W+'jc',{W+'val':'center'})
        paragraphs=[dict(index=0,text='2026年10月12日',heading_level=None)]
        rules=load_page_rules({'rules':[{'id':'cover_layout','teacher_evidence':[{'quote':'居中，写答辩日期'}]}]})
        findings, coverage = check_page_rules(document,None,paragraphs,rules,'sha')
        self.assertFalse(findings)
        self.assertEqual(coverage[0]['status'],'checked')
        pr.find(W+'jc').set(W+'val','left')
        findings, _ = check_page_rules(document,None,paragraphs,rules,'sha')
        self.assertEqual(len(findings),1)
        # Tab-centered covers cannot be judged from paragraph jc alone.
        paragraphs[0]['text']='\t2026年10月12日'
        findings, coverage = check_page_rules(document,None,paragraphs,rules,'sha')
        self.assertFalse(findings)
        self.assertEqual(coverage[0]['status'],'partial')

    def test_english_page_count_rejects_unbound_layout(self):
        rules=load_page_rules({'rules':[{'id':'english_abstract_length','teacher_evidence':[{'quote':'英文摘要一页内'}]}]})
        findings, coverage=check_page_rules(ET.Element(W+'document'),None,[],rules,'sha',
                                           {'source':{'sha256':'other'},'source_unchanged':True})
        self.assertFalse(findings)
        self.assertEqual(coverage[1]['status'],'partial')

    def test_english_length_uses_unique_pdf_endpoints_and_detects_tampering(self):
        import fitz
        rules=load_page_rules({'rules':[{'id':'english_abstract_length','teacher_evidence':[{'quote':'英文摘要一页内'}]}]})
        paragraphs=[dict(index=0,text='Abstract',heading_level=1),
                    dict(index=1,text='This research develops a teaching assistant with contextual analysis.',heading_level=None),
                    dict(index=2,text='Keywords: teaching assistant; contextual analysis',heading_level=None)]
        with TemporaryDirectory() as folder:
            pdf=Path(folder)/'pages.pdf'
            for split in (False,True):
                doc=fitz.open();page=doc.new_page();page.insert_text((40,40),paragraphs[1]['text'])
                if split:page=doc.new_page()
                page.insert_text((40,80),paragraphs[2]['text']);doc.save(pdf);doc.close()
                layout={'source':{'sha256':'sha'},'source_unchanged':True,
                        'pdf':{'path':str(pdf),'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}}
                findings,coverage=check_page_rules(ET.Element(W+'document'),None,paragraphs,rules,'sha',layout)
                self.assertEqual(coverage[1]['status'],'checked')
                self.assertEqual(len(findings), int(split))
                layout['pdf']['sha256']='wrong'
                findings,coverage=check_page_rules(ET.Element(W+'document'),None,paragraphs,rules,'sha',layout)
                self.assertFalse(findings);self.assertEqual(coverage[1]['status'],'partial')


if __name__ == '__main__': unittest.main()
