"""Selective upgrades keep evidence receipts and originals immutable."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document
from docx.oxml import OxmlElement
from atomic_json import write_json,read_json
from new_paper_checks import execute_checks
from reference_review_refresh import refreshed_references, copy_verified_assets, refresh_saved_references


class ReferenceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'source.docx'
        d=Document();d.add_heading('5 系统实现',1);d.add_heading('修改题目',2)
        d.add_paragraph('管理员通过编辑按钮修改题目，见图4-8。')
        d.add_paragraph()._p.append(OxmlElement('w:drawing'));d.add_paragraph('图5-5 修改题目')
        d.save(self.source)
        self.prior=execute_checks(self.source);self.prior['run']={'known_total_tokens':99,'table_policy_version':'explicit_three_line_tables_v3'}
        self.prior['findings'].append({'id':'NP001-cached','rule_id':'NP001','title':'原有已审阅','status':'candidate','text':'我们进行研究。',
            'reason':'原有判断','suggestion':'改为客观表述','semantic_review':{'validated_quote':True,'decision':'suggested_revision','quote':'我们进行研究。','reason':'作者叙述'}})

    def tearDown(self):self.temp.cleanup()

    def test_selective_update_preserves_paid_reviews(self):
        before=deepcopy(self.prior); original=self.source.read_bytes()
        result=refreshed_references(self.source,self.prior)
        self.assertEqual(before,self.prior);self.assertEqual(original,self.source.read_bytes())
        self.assertEqual([f for f in before['findings'] if f['rule_id']!='NP010'],[f for f in result['findings'] if f['rule_id']!='NP010'])
        self.assertEqual(next(f for f in result['findings'] if f['rule_id']=='NP010')['reference_diagnostic']['candidates'][0]['label'],'图5-5')

    def test_source_mismatch_stops_update(self):
        self.prior['source_sha256']='different'
        with self.assertRaises(ValueError):refreshed_references(self.source,self.prior)

    def test_simple_label_is_included_without_duplicate_methods(self):
        d=Document(); d.add_heading('2 调查数据',1)
        d.add_paragraph('表2国内参与度。图源：调查报告')
        d.save(self.source)
        prior=execute_checks(self.source)
        result=refreshed_references(self.source,prior)
        self.assertEqual(sum(r['id']=='NP050' for r in result['rules']),1)
        self.assertEqual(sum(c['rule_id']=='NP050' for c in result['coverage']),1)
        findings=[f for f in result['findings'] if f['rule_id']=='NP050']
        self.assertEqual(len(findings),1)
        self.assertFalse(findings[0]['reference_diagnostic']['object_absence_proven'])

    def test_copy_rebinds_only_hash_verified_assets(self):
        old=self.root/'review_old';old.mkdir();pdf=old/'preview.pdf';pdf.write_bytes(b'%PDF fixture')
        image=old/'image.png';image.write_bytes(b'png fixture')
        self.prior.update(pdf_path=str(pdf),pdf_sha256=sha256(pdf.read_bytes()).hexdigest())
        self.prior['findings'].append({'id':'NP007-image','rule_id':'NP007','figure_evidence':{'source_sha256':self.prior['source_sha256'],
            'preview_path':str(image),'preview_sha256':sha256(image.read_bytes()).hexdigest(),'preview_asset_id':'old-id'}})
        result=deepcopy(self.prior);output=self.root/'review_new';output.mkdir()
        self.assertTrue(copy_verified_assets(self.root,self.prior,result,output))
        self.assertEqual(Path(result['pdf_path']).read_bytes(),pdf.read_bytes())
        self.assertNotIn('preview_asset_id',result['findings'][-1]['figure_evidence'])
        self.assertTrue(Path(result['findings'][-1]['figure_evidence']['preview_path']).is_relative_to(output))
        result=deepcopy(self.prior);result['findings'][-1]['figure_evidence']['preview_sha256']='bad'
        copy_verified_assets(self.root,self.prior,result,output)
        self.assertNotIn('preview_path',result['findings'][-1]['figure_evidence'])

    def test_new_receipt_never_rewrites_original_or_old_report(self):
        previous=self.root/'review_old';previous.mkdir();write_json(previous/'report.json',self.prior)
        write_json(self.root/'paper.json',{'id':'paper_test','student_name':'测试学生','status':'completed','review_id':'review_old','sha256':self.prior['source_sha256']})
        old_hash=sha256((previous/'report.json').read_bytes()).hexdigest();original=self.source.read_bytes()
        result=refresh_saved_references(self.root)
        self.assertEqual(result['new_llm_requests'],0);self.assertEqual(result['new_word_requests'],0)
        self.assertEqual(old_hash,sha256((previous/'report.json').read_bytes()).hexdigest());self.assertEqual(original,self.source.read_bytes())
        self.assertEqual(read_json(self.root/'paper.json')['previous_review_id'],'review_old')
        self.assertIsNone(refresh_saved_references(self.root))


if __name__=='__main__':unittest.main()
