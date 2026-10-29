"""Preserve other evidence and stop incorrect page bindings during NP022 repair."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from new_paper_checks import execute_checks
from table_policy_refresh import attach_verified_table_pages, refreshed_report, refresh_saved_paper
from atomic_json import write_json, read_json


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.docx'
        doc = Document()
        doc.add_heading('系统设计', level=1)
        doc.add_paragraph('表4-10 exam_paper表')
        table = doc.add_table(rows=2, cols=2)
        for cell, text in zip([c for r in table.rows for c in r.cells], ['字段名','数据类型','Id','Int']):
            cell.text = text
        borders = OxmlElement('w:tblBorders')
        for edge in ('top','bottom','insideH','insideV'):
            node = OxmlElement('w:'+edge); node.set(qn('w:val'),'single');node.set(qn('w:sz'),'4');borders.append(node)
        table._tbl.tblPr.append(borders)
        doc.save(self.source)
        self.prior = execute_checks(self.source, three_line_tables=True)
        self.prior['student_name'] = '验收学生'
        self.prior['run'] = {'known_total_tokens':100, 'dispatched_requests':1, 'status':'partial'}
        self.prior['findings'].append({'id':'NP001-saved', 'rule_id':'NP001','title':'原审阅',
            'status':'candidate','text':'我们实现了系统。', 'reason':'作者叙述', 'suggestion':'改为客观描述',
            'semantic_review':{'decision':'suggested_revision','validated_quote':True,'quote':'我们实现了系统。','reason':'作者叙述'}})
        self.pdf = self.root / 'preview.pdf'; self.pdf.write_bytes(b'%PDF-fixture')
        self.layout = {'status':'native_table_layout_collected_visual_pending','source_unchanged':True,
            'source':{'sha256':self.prior['source_sha256']},'pdf':{'path':str(self.pdf),'sha256':sha256(self.pdf.read_bytes()).hexdigest()},
            'word_page_count':23, 'tables_before':[{'index':1,'nesting_level':1,'text':'字段名 数据类型 Id Int','first_physical_page':22,'last_physical_page':22}]}

    def tearDown(self): self.temp.cleanup()

    def test_policy_refresh_preserves_other_reviews_and_original_bytes(self):
        original = self.source.read_bytes()
        before = deepcopy(self.prior)
        refreshed = refreshed_report(self.source,self.prior,self.layout)
        self.assertEqual(self.prior,before)
        self.assertEqual(original,self.source.read_bytes())
        self.assertEqual([f for f in refreshed['findings'] if f['rule_id']!='NP022'], [f for f in before['findings'] if f['rule_id']!='NP022'])
        self.assertEqual(refreshed['summary']['result_groups']['changes'],1)
        self.assertFalse(any(f['rule_id']=='NP022' for f in refreshed['findings']))
        self.assertEqual(refreshed['requests'],{'llm':0,'svn':0,'word':0})
        coverage=next(c for c in refreshed['coverage'] if c['rule_id']=='NP022')
        self.assertEqual(coverage['checked_objects'][0]['physical_pages'],[22])

    def test_source_mismatch_rejects_cached_reviews(self):
        self.prior['source_sha256']='wrong'
        with self.assertRaises(ValueError):refreshed_report(self.source,self.prior)

    def test_explicit_policy_retains_actual_table_suggestion(self):
        result=refreshed_report(self.source,self.prior,self.layout,True)
        table=next(f for f in result['findings'] if f['rule_id']=='NP022')
        self.assertEqual(table['local_review']['decision'],'suggested_revision')
        self.assertEqual(table['physical_pages'],[22])

    def test_changed_pdf_source_nested_grid_or_text_does_not_bind_page(self):
        for key in ('source','pdf','nested','text','layout_change','count'):
            layout=deepcopy(self.layout)
            report=deepcopy(self.prior)
            if key=='source':layout['source']['sha256']='wrong'
            if key=='pdf':layout['pdf']['sha256']='wrong'
            if key=='nested':layout['tables_before'][0]['nesting_level']=2
            if key=='text':layout['tables_before'][0]['text']='other table'
            if key=='layout_change':layout['layout_changed_during_export']=True
            if key=='count':report['table_count']=2
            self.assertEqual(attach_verified_table_pages(report,layout),0,key)

    def test_saved_refresh_creates_new_receipt_and_preserves_old_files(self):
        previous=self.root/'review_original';previous.mkdir()
        write_json(previous/'report.json',self.prior);write_json(previous/'run.json',self.prior['run'])
        write_json(previous/'layout_result.json',self.layout)
        write_json(self.root/'paper.json',{'id':'paper_test','student_name':'验收学生','file_name':'test.docx',
            'sha256':self.prior['source_sha256'],'review_id':'review_original','status':'completed'})
        old={p.name:p.read_bytes() for p in previous.iterdir()}
        result=refresh_saved_paper(self.root)
        self.assertNotEqual(result['review_id'],'review_original')
        self.assertEqual(old,{p.name:p.read_bytes() for p in previous.iterdir()})
        saved=read_json(self.root/'paper.json');self.assertFalse(saved['three_line_tables'])
        new_report=read_json(self.root/result['review_id']/'report.json')
        self.assertEqual(Path(new_report['pdf_path']).read_bytes(), self.pdf.read_bytes())
        self.assertTrue(new_report['run']['layout_evidence_reused'])
        self.assertIsNone(refresh_saved_paper(self.root))

    def test_cross_upload_layout_reuses_the_current_papers_verified_pdf_copy(self):
        previous=self.root/'review_original';previous.mkdir()
        self.prior['pdf_path']=str(self.pdf)
        self.prior['pdf_sha256']=sha256(self.pdf.read_bytes()).hexdigest()
        layout=deepcopy(self.layout)
        layout['pdf']['path']=str(self.root.parent/'other_paper'/'preview.pdf')
        write_json(previous/'report.json',self.prior);write_json(previous/'run.json',self.prior['run'])
        write_json(previous/'layout_result.json',layout)
        write_json(self.root/'paper.json',{'id':'paper_test','student_name':'验收学生','file_name':'test.docx',
            'sha256':self.prior['source_sha256'],'review_id':'review_original','status':'completed'})
        result=refresh_saved_paper(self.root)
        report=read_json(self.root/result['review_id']/'report.json')
        self.assertEqual(Path(report['pdf_path']).read_bytes(),self.pdf.read_bytes())
        self.assertTrue(Path(report['pdf_path']).is_relative_to(self.root/result['review_id']))

    def test_saved_refresh_rebinds_verified_image_without_changing_vision_judgment(self):
        image=self.root/'old.png';image.write_bytes(b'PNG-preserved-fixture')
        vision={'validated_image':True,'decision':'not_issue','reason':'图中文字可读',
                'image_kind':'diagram','observations':['可辨认文字'],'visible_text':['字段名'],'suggestion':''}
        self.prior['findings'].append({'id':'NP007-saved','rule_id':'NP007','title':'图片审阅','text':'图1-1',
            'status':'candidate','reason':'原图证据','suggestion':'','vision_review':vision,
            'figure_evidence':{'source_sha256':self.prior['source_sha256'],'preview_path':str(image),
                               'preview_sha256':sha256(image.read_bytes()).hexdigest(),'preview_asset_id':'old_asset'}})
        previous=self.root/'review_original';previous.mkdir()
        write_json(previous/'report.json',self.prior);write_json(previous/'run.json',self.prior['run'])
        write_json(self.root/'paper.json',{'id':'paper_test','student_name':'验收学生','file_name':'test.docx',
            'sha256':self.prior['source_sha256'],'review_id':'review_original','status':'completed'})
        result=refresh_saved_paper(self.root)
        report=read_json(self.root/result['review_id']/'report.json')
        figure=next(f for f in report['findings'] if f['rule_id']=='NP007')
        self.assertEqual(figure['vision_review'],vision)
        saved=Path(figure['figure_evidence']['preview_path'])
        self.assertTrue(saved.is_relative_to(self.root/result['review_id']/'figure_evidence'))
        self.assertEqual(saved.read_bytes(),image.read_bytes())
        self.assertNotIn('preview_asset_id',figure['figure_evidence'])


if __name__=='__main__':unittest.main()
