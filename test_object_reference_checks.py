"""Reference causes, exact target choices and preservation without live services."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

from test_expanded_paper_checks import p, W, M
from new_paper_checks import extract_paragraphs
from paper_locations import annotate_paragraphs
from expanded_paper_checks import load_expanded_rules
from object_reference_checks import check_object_references, attach_reference_pages
from new_paper_agent import build_semantic_units, evidence_packet_item, validate_decisions, validate_evidence_batch, semantic_context_key, reuse_semantic_evidence, SemanticReviewQueue

RULE = next(r for r in load_expanded_rules() if r['id']=='NP010')


def execute(body):
    doc = ET.fromstring(f'<w:document xmlns:w="{W[1:-1]}" xmlns:m="{M[1:-1]}"><w:body>{body}</w:body></w:document>')
    return check_object_references(doc, annotate_paragraphs(extract_paragraphs(doc)), RULE, 'source-hash')


def example():
    body = p('4 系统设计', 1)+p('表4-8 paper_question表',style='Caption')
    body += '<w:tbl><w:tr><w:tc>'+p('字段名')+'</w:tc></w:tr></w:tbl>'
    body += p('5 系统实现',1)+p('5.2 修改题目',2)+p('管理员点击编辑按钮修改题目。见图4-8。')
    body += p('',extra='<w:r><w:drawing/></w:r>')+p('图5-5 修改题目')
    return execute(body)[0][0]


def valid_row(f):
    target=f['reference_diagnostic']['candidates'][0]
    return {'id':f['id'],'decision':'suggested_revision','quote':f['text'],
            'target_id':target['id'],'reason':'所述编辑题目与紧邻图5-5题注相符，表4-8是数据库表。',
            'suggestion':'将见图4-8改为见图5-5，并保留修改题目的说明。'}


class ReferenceTests(unittest.TestCase):
    def test_nearby_correct_kind_before_far_same_number_wrong_kind(self):
        f=example(); d=f['reference_diagnostic']
        self.assertTrue(f['semantic_required'])
        self.assertEqual(d['classification'],'nearby_number_mismatch')
        self.assertEqual(d['candidates'][0]['text'],'图5-5 修改题目')
        self.assertEqual(d['candidates'][1]['label'],'表4-8')
        self.assertEqual(d['source_sha256'],'source-hash')
        self.assertFalse(d['object_absence_proven'])

    def test_correct_refs_do_not_generate_per_object_review_noise(self):
        f,c=execute(p('1 方法',1)+p('如图1-1所示。')+p('',extra='<w:r><w:drawing/></w:r>')+p('图1-1 方法'))
        self.assertEqual(f,[]); self.assertEqual(c['matched_count'],1)

    def test_same_number_other_kind_is_candidate_not_certain_correction(self):
        f,c=execute(p('1 方法',1)+p('如图1-2所示。')+p('表1-2 字段')+'<w:tbl><w:tr><w:tc>'+p('字段名')+'</w:tc></w:tr></w:tbl>')
        self.assertEqual(f[0]['reference_diagnostic']['classification'],'possible_kind_mismatch')
        self.assertNotIn('local_review',f[0])

    def test_duplicate_caption_is_not_a_unique_match(self):
        body=p('1 方法',1)+p('见图1-1。')
        body+=p('',extra='<w:r><w:drawing/></w:r>')+p('图1-1 A')+p('',extra='<w:r><w:drawing/></w:r>')+p('图1-1 B')
        f,c=execute(body)
        self.assertEqual(f[0]['reference_diagnostic']['exact_target_count'],2)
        self.assertEqual(f[0]['reference_diagnostic']['classification'],'duplicate_target')

    def test_object_without_caption_differs_from_missing_target(self):
        f,_=execute(p('1 方法',1)+p('见图1-2。')+p('',extra='<w:r><w:drawing/></w:r>'))
        self.assertEqual(f[0]['reference_diagnostic']['classification'],'unlabelled_nearby_object')
        f,_=execute(p('1 方法',1)+p('见图1-2。'))
        self.assertEqual(f[0]['reference_diagnostic']['classification'],'target_not_found')
        self.assertFalse(f[0]['reference_diagnostic']['object_absence_proven'])

    def test_textbox_caption_is_recorded_as_gap(self):
        body=p('1 方法',1)+p('见图1-2。')+p('',extra='<w:r><w:pict><w:txbxContent>'+p('图1-2 文本框题注')+'</w:txbxContent></w:pict></w:r>')
        f,c=execute(body)
        self.assertEqual(f[0]['reference_diagnostic']['unparsed_caption_count'],1)
        self.assertEqual(c['matched_count'],0)

    def test_omml_and_normalized_dash_match(self):
        body=p('1 方法',1)+p('公式（1－2）给出结果。')+p('（1-2）',extra='<m:oMathPara><m:oMath/></m:oMathPara>')
        f,c=execute(body);self.assertFalse(f);self.assertEqual(c['matched_count'],1)

    def test_target_id_and_literal_quote_required(self):
        f=example();row=valid_row(f)
        good=validate_decisions(json.dumps({'items':[row]},ensure_ascii=False),[f])[f['id']]
        self.assertEqual(good['target_label'],'图5-5')
        for changed in (dict(row,target_id='invented'),dict(row,target_id=''),dict(row,quote='管理员点击编辑按钮'),dict(row,suggestion='改成图9-9'),dict(row,decision='not_issue',suggestion='')):
            with self.assertRaises(ValueError):validate_decisions(json.dumps({'items':[changed]},ensure_ascii=False),[f])

    def test_precise_reference_near_end_is_in_one_evidence_slice(self):
        f=example();f['text']='原文。'*600+f['text'];f['start']+=1800;f['end']+=1800
        u=build_semantic_units(f,RULE)
        self.assertEqual(len(u),1)
        packet=evidence_packet_item(u[0],{'NP010':RULE})
        self.assertEqual(packet['matched_text'],'见图4-8')
        self.assertIn('见图4-8',packet['evidence_snippets'][0]['quote'])
        row=valid_row(f);row.pop('quote');row['evidence_id']=u[0]['evidence_snippets'][0]['evidence_id']
        accepted,errors=validate_evidence_batch(json.dumps({'items':[row]},ensure_ascii=False),u)
        self.assertFalse(errors);self.assertEqual(accepted[u[0]['id']]['target_label'],'图5-5')

    def test_changed_candidate_invalidates_only_reference_cache(self):
        f=example();before={'source_sha256':'source-hash','rules':[RULE],'findings':[deepcopy(f)]}
        before['findings'][0]['semantic_review']=validate_decisions(json.dumps({'items':[valid_row(f)]},ensure_ascii=False),[f])[f['id']]
        report=deepcopy(before);report['findings'][0].pop('semantic_review')
        self.assertEqual(reuse_semantic_evidence(report,before),1)
        report=deepcopy(before);report['findings'][0].pop('semantic_review')
        report['findings'][0]['reference_diagnostic']['candidates'][0]['text']='图5-5 另一内容'
        self.assertEqual(reuse_semantic_evidence(report,before),0)
        self.assertNotEqual(semantic_context_key(report['findings'][0],RULE),semantic_context_key(f,RULE))

    def test_selective_queue_does_not_touch_other_pending_rules(self):
        f=example();other=dict(f,id='NP013-0001',rule_id='NP013');other.pop('reference_diagnostic')
        report={'findings':[f,other],'rules':[RULE,dict(RULE,id='NP013')],'paragraphs':[]}
        with TemporaryDirectory() as folder:
            queue=SemanticReviewQueue(report,{},folder,allowed_rule_ids={'NP010'})
        self.assertEqual(set(queue.by_parent),{f['id']})

    def test_unique_caption_pages_require_current_pdf_hash(self):
        import fitz
        f=example(); c=f['reference_diagnostic']['candidates'][0]
        c['text']='FIGURE 5-5 EDIT QUESTION'
        with TemporaryDirectory() as folder:
            pdf=Path(folder)/'p.pdf';doc=fitz.open();page=doc.new_page();page.insert_text((50,80),c['text']);doc.save(pdf);doc.close()
            report={'source_sha256':'source-hash','pdf_path':str(pdf),'pdf_sha256':sha256(pdf.read_bytes()).hexdigest(),'findings':[f]}
            self.assertEqual(attach_reference_pages(report),1);self.assertEqual(c['physical_page'],1)
            c.pop('physical_page');report['pdf_sha256']='bad'
            self.assertEqual(attach_reference_pages(report),0);self.assertNotIn('physical_page',c)


if __name__=='__main__':unittest.main()
