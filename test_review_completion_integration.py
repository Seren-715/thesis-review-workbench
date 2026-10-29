"""Read progress independently of report commits and keep result groups honest."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from hashlib import sha256
import unittest
from atomic_json import write_json
from paper_report import result_group,render_report
from web_backend.new_paper_service import NewPapers
from web_backend.test_new_paper_service import docx_bytes

class CompletionIntegrationTests(unittest.TestCase):
    def test_live_progress_available_before_first_report_and_does_not_rewrite(self):
        entered,release=Event(),Event()
        def runner(source,output,**options):
            run={'source_sha256':sha256(source.read_bytes()).hexdigest(),'status':'running',
                 'progress':{'stage':'figures','total':40,'reviewed':8,'blocked':1,'remaining':31,
                             'requests_used':3,'request_limit':16,'known_tokens':2050,'token_limit':60000}}
            write_json(output/'run.json',run);entered.set();release.wait(3)
            return {'student_name':options['student_name'],'status':'partial','findings':[],'run':run}
        with TemporaryDirectory() as directory:
            service=NewPapers(Path(directory),runner)
            try:
                created=service.upload(docx_bytes(),'student.docx','测试学生')
                self.assertTrue(entered.wait(2))
                path=service.folder(created['id'])/created['review_id']/'run.json'
                before=path.read_bytes()
                paper=service.get(created['id'])
                self.assertEqual(paper['status'],'running')
                self.assertEqual(paper['run']['progress']['remaining'],31)
                self.assertNotIn('report',paper)
                self.assertEqual(before,path.read_bytes())
            finally:release.set();service.close()

    def test_foreign_progress_source_rejected(self):
        with TemporaryDirectory() as directory:
            service=NewPapers(Path(directory),lambda *args,**kwargs: {})
            try:
                paper={'id':'paper_test','student_name':'甲','file_name':'a.docx','sha256':'expected',
                       'status':'completed','review_id':'review_test','created_at':'2026'}
                service.papers[paper['id']]=paper
                output=service.folder(paper['id'])/paper['review_id'];output.mkdir(parents=True)
                write_json(output/'run.json',{'source_sha256':'different'})
                with self.assertRaisesRegex(ValueError,'原件不一致'):service.get(paper['id'])
            finally:service.close()

    def test_structural_decisions_and_failed_outputs_are_distinct(self):
        finding={'status':'candidate','rule_id':'NP022','local_review':{'decision':'suggested_revision','validated_structure':True}}
        self.assertEqual(result_group(finding),'changes')
        self.assertNotEqual(finding['status'],'confirmed_issue')
        finding['local_review']['decision']='not_issue'
        self.assertEqual(result_group(finding),'compliant')
        finding['local_review']['validated_structure']=False
        self.assertEqual(result_group(finding),'pending')
        blocked={'status':'candidate','rule_id':'NP001','semantic_required':True,'review_blocked':{'reason':'具体命中证据未能可靠判断'}}
        self.assertEqual(result_group(blocked),'pending')
        del blocked['review_blocked']
        self.assertEqual(result_group(blocked),'unreviewed')

    def test_report_contains_completion_reason_and_exact_blocked_reason(self):
        report={'student_name':'甲','file_name':'a.docx','source_sha256':'fixed','findings':[
            {'rule_id':'NP001','status':'candidate','text':'我们研究。','title':'作者表述','reason':'候选',
             'suggestion':'','review_blocked':{'reason':'第二次输出仍未指向指定证据ID。'}}],
             'coverage':[],'rules':[],'limitations':[]}
        state={'dispatched_requests':2,'known_total_tokens':100,'unknown_cost_attempts':0,
               'completion':{'status':'completed_with_manual','reviewable':1,'reviewed':0,'blocked':1,
                             'budget_stopped':0,'reason':'可处理队列已结束，1项无法自动判断。'}}
        text=render_report(report,state)
        self.assertIn('可处理队列已结束',text)
        self.assertIn('第二次输出仍未指向指定证据ID',text)
        self.assertIn('Agent 尚未审阅（0 项）',text)

if __name__=='__main__':unittest.main()
