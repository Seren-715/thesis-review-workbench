import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from paper_vision_review import validate_rows,context_key,reuse_vision_evidence,review_figures,recover_saved_vision
from new_paper_agent import BoundedClient
from paper_report import result_group

class VisionTests(unittest.TestCase):
    def setUp(self):
        from hashlib import sha256
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.preview=Path(self.temp.name)/'preview.png';self.preview.write_bytes(b'fixed-preview')
        self.f={'id':'NP007-1','rule_id':'NP007','text':'图1-1 用例图','status':'needs_review','teacher_evidence':[],
                'figure_evidence':{'source_sha256':'source','preview_sha256':sha256(self.preview.read_bytes()).hexdigest(),'preview_path':str(self.preview),'reviewable':True,'caption':{'text':'图1-1 用例图'},'width_cm':12}}
        self.row={'id':'NP007-1','image_kind':'diagram','readable':True,'decision':'not_issue','observations':['参与者和连接关系可辨认。'],
                  'visible_text':['管理员'],'reason':'标签清晰，连接线可以区分。','suggestion':''}
    def accepted(self,row=None):return validate_rows(json.dumps({'items':[row or self.row]},ensure_ascii=False),[self.f])[self.f['id']]
    def test_clear_image_is_grouped_as_compliant_not_bigger(self):
        self.f['vision_review']=self.accepted()
        self.assertEqual(result_group(self.f),'compliant')
        self.assertEqual(self.f['vision_review']['suggestion'],'')
        self.assertFalse(self.f['vision_review']['human_confirmed'])
    def test_uncertain_is_not_an_error_or_pass(self):
        self.f['vision_review']=self.accepted(dict(self.row,readable=None,decision='needs_review',suggestion='请查看原图。'))
        self.assertEqual(result_group(self.f),'pending')
    def test_conflicting_readability_and_foreign_duplicate_id_rejected(self):
        for row in [dict(self.row,suggestion='放大'),dict(self.row,readable=False),dict(self.row,id='another'),dict(self.row,decision='confirmed_issue')]:
            with self.assertRaises(ValueError):self.accepted(row)
        with self.assertRaises(ValueError):validate_rows(json.dumps({'items':[self.row,self.row]}),[self.f])
    def test_reuse_requires_exact_source_pixels_caption_scope(self):
        report={'source_sha256':'source','rules':[{'id':'NP007','scope':'可读性'}],'findings':[self.f]}
        prior=deepcopy(report);r=self.accepted();r['context_key']=context_key(self.f,report['rules'][0]);r['preview_sha256']=self.f['figure_evidence']['preview_sha256'];prior['findings'][0]['vision_review']=r
        self.assertEqual(reuse_vision_evidence(report,prior),1)
        for key in ['preview_sha256','width_cm','caption']:
            current=deepcopy(report);current['findings'][0].pop('vision_review');current['findings'][0]['figure_evidence'][key]='changed'
            self.assertEqual(reuse_vision_evidence(current,prior),0)
        changed=deepcopy(report);changed['source_sha256']='other';self.assertEqual(reuse_vision_evidence(changed,prior),0)
    def test_pixels_are_sent_and_tampering_stops_before_call(self):
        from PIL import Image
        from hashlib import sha256
        with TemporaryDirectory() as directory:
            output=Path(directory);path=output/'figure.png';Image.new('RGB',(40,40),'white').save(path)
            self.f['figure_evidence'].update(preview_path=str(path),preview_sha256=sha256(path.read_bytes()).hexdigest(),reviewable=True)
            report={'source_sha256':'source','rules':[{'id':'NP007','scope':'可读性'}],'findings':[self.f],'coverage':[],'limitations':[]}
            state={'requests':[],'request_attempts':0,'known_total_tokens':0,'unknown_cost_attempts':0,'reserved_token_bound':0,'max_requests':6,'max_reserved_token_bound':24000}
            calls=[]
            def create(**kwargs):
                calls.append(kwargs)
                response=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=40,completion_tokens=20,total_tokens=60),choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps({'items':[self.row]},ensure_ascii=False),tool_calls=None))])
                response.model_dump=lambda **kwargs:{'usage':{'total_tokens':60}}
                return response
            budget=BoundedClient(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),output,state)
            review_figures(report,budget,state,output)
            image=calls[0]['messages'][1]['content'][1]
            self.assertEqual(image['type'],'image_url');self.assertTrue(image['image_url']['url'].startswith('data:image/png;base64,'))
            self.assertEqual(state['vision_reviewed'],1)
            self.f.pop('vision_review');path.write_bytes(b'changed')
            with self.assertRaises(ValueError):review_figures(report,budget,state,output)
            self.assertEqual(len(calls),1)
    def test_image_budget_counts_documented_tokens_not_base64(self):
        state={'requests':[],'request_attempts':0,'known_total_tokens':0,'reserved_token_bound':0}
        budget=BoundedClient(None,None,state)
        a=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'a'*100}}]}]
        b=deepcopy(a);b[0]['content'][0]['image_url']['url']='data:image/png;base64,'+'a'*100000
        self.assertEqual(budget.prepare(a)[1],budget.prepare(b)[1])
        self.assertIn('a'*100,a[0]['content'][0]['image_url']['url'])
        b[0]['content'][0]['image_url']['url']='https://unknown/image.png'
        with self.assertRaises(ValueError):budget.prepare(b)
    def test_unknown_request_reservation_is_never_released(self):
        state={'requests':[{'reserved_upper_bound':23000,'usage':None}],'request_attempts':1,'known_total_tokens':0,'reserved_token_bound':23000}
        self.assertFalse(BoundedClient(None,None,state).can_request(2000))
        state['requests'][0]['usage']={'total_tokens':500};state['known_total_tokens']=500
        self.assertTrue(BoundedClient(None,None,state).can_request(2000))
    def test_saved_reply_recovery_checks_exact_sent_pixels_without_spending(self):
        import base64
        from hashlib import sha256
        from paper_vision_review import SYSTEM
        with TemporaryDirectory() as directory:
            p=Path(directory);image=p/'preview.png';image.write_bytes(b'known-sent-pixels')
            self.f['figure_evidence'].update(preview_path=str(image),preview_sha256=sha256(image.read_bytes()).hexdigest())
            report={'source_sha256':'source','findings':[self.f],'rules':[{'id':'NP007','scope':'readability'}],'coverage':[]}
            content=[{'type':'text','text':json.dumps({'id':self.f['id']})},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()}}]
            request={'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':content}]}
            row=dict(self.row,visible_text=['管理员','学生','登录','退出'])
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'items':[row]},ensure_ascii=False)}}]}
            (p/'request_01.json').write_text(json.dumps(request),encoding='utf-8');(p/'response_01.json').write_text(json.dumps(response),encoding='utf-8')
            state={'source_sha256':'source','known_total_tokens':100,'requests':[{'number':1,'finish_reason':'stop','usage':{'total_tokens':100}}]}
            self.assertEqual(recover_saved_vision(report,state,p),1)
            self.assertEqual(state['known_total_tokens'],100)
            self.f.pop('vision_review');image.write_bytes(b'changed')
            self.assertEqual(recover_saved_vision(report,state,p),0)
    def test_localized_blur_is_a_suggestion_not_human_confirmation(self):
        row=dict(self.row,readable=False,decision='suggested_revision',observations=['下方节点的标签模糊。'],reason='下方标签无法辨认。',suggestion='更换清晰的矢量导出图。')
        self.f['vision_review']=self.accepted(row)
        self.assertEqual(result_group(self.f),'changes')
        self.assertFalse(self.f['vision_review']['human_confirmed'])

class WordPageTests(unittest.TestCase):
    def test_unique_pdf_caption_source_hash_and_ambiguous_caption(self):
        import fitz
        from hashlib import sha256
        from paper_word_figure_pages import prefer_verified_word_pages
        with TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir();source=root/'source';source.write_bytes(b'source')
            pdf=root/'data/preview.pdf';doc=fitz.open();page=doc.new_page();page.insert_text((50,50),'Figure 1-1 Diagram');doc.save(pdf);doc.close()
            report={'source_sha256':sha256(source.read_bytes()).hexdigest(),'findings':[{'id':'NP007-1','rule_id':'NP007','text':'Figure 1-1 Diagram','figure_evidence':{}}]}
            layout={'source_unchanged':True,'source':{'sha256':report['source_sha256']},'pdf':{'path':str(pdf),'sha256':sha256(pdf.read_bytes()).hexdigest()}}
            output=root/'figures';output.mkdir()
            with patch('paper_word_figure_pages.ROOT',root):
                self.assertEqual(prefer_verified_word_pages(source,output,report,layout),1)
                self.assertEqual(report['findings'][0]['figure_evidence']['physical_page'],1)
                doc=fitz.open(pdf);doc.new_page().insert_text((50,50),'Figure 1-1 Diagram');doc.save(root/'data/duplicate.pdf');doc.close()
                layout['pdf']={'path':str(root/'data/duplicate.pdf'),'sha256':sha256((root/'data/duplicate.pdf').read_bytes()).hexdigest()}
                self.assertEqual(prefer_verified_word_pages(source,output,report,layout),0)
                source.write_bytes(b'changed');self.assertEqual(prefer_verified_word_pages(source,output,report,layout),0)

if __name__=='__main__':unittest.main()
