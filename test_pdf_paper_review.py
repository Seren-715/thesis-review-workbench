import json
from copy import deepcopy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import fitz

from pdf_paper_review import extract_pdf, run_review, validate_reviews


def pdf_bytes(text='1 Introduction\nWe describe the system.'):
    with fitz.open() as doc:
        page=doc.new_page(); page.insert_text((60,60),text)
        doc.new_page()
        return doc.tobytes()


class Client:
    def __init__(self, missing_usage=False):
        self.calls=0; self.missing_usage=missing_usage
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        self.calls+=1
        message=SimpleNamespace(content='{"findings":[]}',tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=message)],
            usage=None if self.missing_usage else SimpleNamespace(prompt_tokens=10,completion_tokens=5,total_tokens=15),
            model_dump=lambda **kwargs: {'choices':[{'message':{'content':'{"findings":[]}'}}]})


RULES=[{'id':'NP001','title':'正文第一人称','general_requirements':{'requirements':['正文应客观叙述']}}]


class PDFReviewTests(unittest.TestCase):
    def test_upload_service_pdf_restart_and_source_hash(self):
        from web_backend.new_paper_service import NewPapers
        from web_backend.test_new_paper_service import fake_runner, wait_complete
        with TemporaryDirectory() as directory:
            root=Path(directory)/'papers'; data=pdf_bytes()
            service=NewPapers(root,fake_runner)
            try:
                paper=service.upload(data,'student.pdf',student_name='Student')
                saved=wait_complete(service,paper['id'])
                self.assertEqual(saved['source_format'],'pdf')
                self.assertEqual((root/paper['id']/'source.pdf').read_bytes(),data)
                self.assertFalse((root/paper['id']/'source.docx').exists())
            finally: service.close()
            reopened=NewPapers(root,fake_runner)
            try:self.assertEqual(reopened.get(paper['id'])['source_format'],'pdf')
            finally:reopened.close()
    def test_real_physical_pages_and_blank_detection(self):
        parsed=extract_pdf(pdf_bytes())
        self.assertEqual(parsed['page_count'],2)
        self.assertEqual(parsed['unrecognized_pages'],[2])
        self.assertEqual(parsed['paragraphs'][0]['physical_page'],1)
        self.assertEqual(len(parsed['paragraphs'][0]['bbox']),4)
    def test_invalid_encrypted_and_extension_spoof_rejected(self):
        with self.assertRaises(ValueError): extract_pdf(b'%PDF-invalid')
        with self.assertRaises(ValueError): extract_pdf(b'PK zip docx')
        with fitz.open() as document:
            document.new_page()
            encrypted=document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,user_pw='secret',owner_pw='owner')
        with self.assertRaisesRegex(ValueError,'加密'):extract_pdf(encrypted)
    def test_acknowledgments_state_survives_pages(self):
        with fitz.open() as doc:
            p=doc.new_page();p.insert_text((60,60),'Acknowledgments')
            p=doc.new_page();p.insert_text((60,60),'We thank our teachers.')
            parsed=extract_pdf(doc.tobytes())
        self.assertEqual(parsed['paragraphs'][-1]['pdf_section_kind'],'excluded')
    def test_quote_scope_and_human_confirmation_rejected(self):
        pieces=[{'paragraph_index':0,'text':'We thank teachers.','start':0,'physical_page':1,'section_kind':'excluded'}]
        row={'rule_id':'NP001','paragraph_index':0,'quote':'We thank teachers.','decision':'suggested_revision','reason':'author voice','suggestion':'change'}
        with self.assertRaises(ValueError):validate_reviews(json.dumps({'findings':[row]}),pieces,{'NP001':{}})
        row.update(rule_id='NP055',decision='confirmed_issue')
        with self.assertRaises(ValueError):validate_reviews(json.dumps({'findings':[row]}),pieces,{'NP055':{}})
    @patch('new_paper_checks.load_teacher_rules',return_value=RULES)
    def test_local_review_preserves_source_and_explicit_pending(self,_):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf';data=pdf_bytes();path.write_bytes(data)
            report=run_review(path,Path(directory)/'result')
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(report['run']['request_attempts'],0)
            self.assertEqual(report['source_format'],'pdf')
            self.assertIsNone(report['table_count'])
            self.assertTrue(any(f['rule_id']=='PDF_TEXT' for f in report['findings']))
            self.assertTrue(any(f['rule_id']=='PDF_OCR' for f in report['findings']))
            for finding in report['findings']:
                self.assertIsInstance(finding['teacher_evidence'],list)
                self.assertIsInstance(finding['reference_requirements'],list)
                self.assertIsInstance(finding['family'],str)
    def test_local_requirement_classification_cannot_assert_full_chapter_absence(self):
        pieces=[{'paragraph_index':0,'text':'登录应在两秒内完成。','start':0,'physical_page':1,'section_kind':'body'}]
        row={'rule_id':'NP056','paragraph_index':0,'quote':'登录应在两秒内完成。','decision':'suggested_revision','reason':'章节缺少非功能性需求','suggestion':'补全需求'}
        with self.assertRaisesRegex(ValueError,'需求缺失'):validate_reviews(json.dumps({'findings':[row]},ensure_ascii=False),pieces,{'NP056':{}})
        row['reason']='原文将响应时间归类为功能需求，该要求描述性能约束。'
        accepted=validate_reviews(json.dumps({'findings':[row]},ensure_ascii=False),pieces,{'NP056':{}})
        self.assertEqual(len(accepted),1)
    def test_valid_model_finding_preserves_teacher_provenance(self):
        from pdf_paper_review import _add_findings
        evidence=[{'comment_id':'C001','quote':'原始批注','source':'original.docx'}]
        report={'paragraphs':[{'text':'Example','location':{'label':'page1'}}],'findings':[]}
        _add_findings(report,[{'paragraph_index':0,'rule_id':'NP055','decision':'not_issue','start':0,'quote':'Example','physical_page':1,'reason':'context supports statement','suggestion':''}],
            {'NP055':{'title':'Evaluation','family':'evaluative_language','teacher_evidence':evidence}})
        self.assertEqual(report['findings'][0]['teacher_evidence'],evidence)
        self.assertIsNot(report['findings'][0]['teacher_evidence'],evidence)
    @patch('new_paper_checks.load_teacher_rules',return_value=RULES)
    def test_verified_resume_reuses_only_matching_source_and_contract(self,_):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf';path.write_bytes(pdf_bytes())
            client=Client();first=run_review(path,Path(directory)/'one',use_agent=True,client=client)
            self.assertEqual(first['run']['known_total_tokens'],15)
            second=run_review(path,Path(directory)/'two',use_agent=True,client=client,prior_report=first)
            self.assertEqual(client.calls,1)
            self.assertEqual(second['run']['pdf_chunks_reviewed'],1)
            changed=dict(first,pdf_rule_contract='other')
            run_review(path,Path(directory)/'three',use_agent=True,client=client,prior_report=changed)
            self.assertEqual(client.calls,2)
    @patch('new_paper_checks.load_teacher_rules',return_value=RULES)
    def test_pdf_masks_known_identity_before_dispatch_and_restores_quote(self,_):
        class EchoClient(Client):
            def create(self, **kwargs):
                self.calls += 1
                self.sent = deepcopy(kwargs)
                piece = json.loads(kwargs['messages'][1]['content'])['pieces'][0]
                content = json.dumps({'findings':[{'rule_id':'NP001',
                    'paragraph_index':piece['paragraph_index'], 'quote':piece['text'],
                    'decision':'not_issue','reason':'The wording is objective.','suggestion':''}]},ensure_ascii=False)
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
                    message=SimpleNamespace(content=content,tool_calls=None))],
                    usage=SimpleNamespace(prompt_tokens=10,completion_tokens=5,total_tokens=15),
                    model_dump=lambda **kwargs:{'choices':[{'message':{'content':content}}]})
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf'
            data=pdf_bytes('1 Introduction\nZhangSan describes the system.')
            path.write_bytes(data)
            client=EchoClient(); output=Path(directory)/'result'
            result=run_review(path,output,student_name='ZhangSan',use_agent=True,client=client)
            self.assertEqual(client.calls,1)
            self.assertNotIn('ZhangSan',json.dumps(client.sent,ensure_ascii=False))
            self.assertNotIn('ZhangSan',(output/'request_01.json').read_text(encoding='utf-8'))
            self.assertIn('ZhangSan',result['pdf_chunk_receipts'][0]['rows'][0]['quote'])
            self.assertEqual(path.read_bytes(),data)
            self.assertIn('ZhangSan',json.loads((output/'privacy_map.json').read_text(encoding='utf-8'))['mapping'])
    @patch('new_paper_checks.load_teacher_rules',return_value=RULES)
    def test_pdf_resume_rejects_changed_model_parameters_or_engine(self,_):
        from review_contract import MODEL_PARAMETERS, ENGINE_FILES
        changes = [('model',patch('new_paper_agent.MODEL','changed-model')),
            ('parameters',patch('review_contract.MODEL_PARAMETERS',dict(MODEL_PARAMETERS,max_tokens=600))),
            ('engine',patch('review_contract.ENGINE_FILES',ENGINE_FILES[:-1]))]
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf';path.write_bytes(pdf_bytes())
            first=run_review(path,Path(directory)/'first',use_agent=True,client=Client())
            for name,change in changes:
                with self.subTest(change=name), change:
                    client=Client()
                    current=run_review(path,Path(directory)/name,use_agent=True,client=client,prior_report=first)
                    self.assertEqual(client.calls,1)
                    self.assertNotEqual(current['pdf_rule_contract'],first['pdf_rule_contract'])
                    self.assertEqual(current['run']['request_attempts'],1)
    @patch('new_paper_checks.load_teacher_rules',return_value=RULES)
    def test_pdf_resume_rejects_missing_identity_or_conflicting_model_ledger(self,_):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf';path.write_bytes(pdf_bytes())
            first=run_review(path,Path(directory)/'first',use_agent=True,client=Client())
            missing=deepcopy(first);missing.pop('review_contract')
            conflicting=deepcopy(first);conflicting['run']['model']='different-model'
            for index,prior in enumerate((missing,conflicting)):
                with self.subTest(index=index):
                    client=Client()
                    run_review(path,Path(directory)/str(index),use_agent=True,client=client,prior_report=prior)
                    self.assertEqual(client.calls,1)
    @patch('new_paper_checks.load_teacher_rules',return_value=RULES)
    def test_unknown_usage_stops_and_keeps_pending(self,_):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf';path.write_bytes(pdf_bytes())
            client=Client(missing_usage=True)
            with self.assertRaisesRegex(ValueError,'用量'):run_review(path,Path(directory)/'result',use_agent=True,client=client)
            state=json.loads((Path(directory)/'result/run.json').read_text(encoding='utf-8'))
            self.assertEqual(client.calls,1)
            self.assertEqual(state['unknown_cost_attempts'],1)
            self.assertEqual(state['pdf_chunks_reviewed'],0)
            prior=json.loads((Path(directory)/'result/report.json').read_text(encoding='utf-8'))
            result=run_review(path,Path(directory)/'resume',use_agent=True,client=client,prior_report=prior)
            self.assertEqual(result['run']['status'],'unknown_cost_blocked')
            self.assertEqual(client.calls,1)
    @patch('new_paper_checks.load_teacher_rules',return_value=RULES)
    def test_cancel_before_dispatch_and_budget_stop(self,_):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf';path.write_bytes(pdf_bytes())
            client=Client()
            cancelled=run_review(path,Path(directory)/'one',use_agent=True,client=client,cancel_check=lambda:True)
            self.assertEqual(cancelled['run']['status'],'cancelled')
            report=run_review(path,Path(directory)/'two',use_agent=True,client=client,max_total_bound=1000)
            self.assertEqual(report['run']['status'],'budget_exhausted')
            self.assertEqual(client.calls,0)


if __name__=='__main__': unittest.main()
