"""History chronology, bounded selection and durable failure tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
import json
from unittest.mock import patch
from thesis_workflow import ThesisWorkflow, parse_history, save

INFO = b'<info><entry revision="30"><url>svn://host/repo/grad</url><repository><root>svn://host/repo</root></repository></entry></info>'

def history(entries):
    return ('<log>'+''.join(f'<logentry revision="{r}"><author>{a}</author><msg>{m}</msg><paths><path kind="file" action="A">{p}</path></paths></logentry>' for r,a,m,p in entries)+'</log>').encode()


class HistoryTests(TestCase):
    def test_root_paper_initial_and_teacher_versions_are_discovered(self):
        data = parse_history(INFO, history([(19, 'mayy', '', '/grad/123456_安娜羽/123456_安娜羽_三稿.docx'),
            (19, 'mayy', '', '/grad/123456_安娜羽/123456_安娜羽_五稿.docx'),
            (7, 'anny', '初稿', '/grad/123456_安娜羽/123456_安娜羽_初稿.doc')]))
        student = data['students'][0]
        self.assertEqual(student['initial_revision'], 7)
        self.assertEqual(student['teacher_version_count'], 2)
        self.assertEqual(student['document_count'], 3)
        self.assertEqual(data['coverage']['root_document_paths'], 3)
        self.assertEqual(data['discovery_issues'], [])

    def test_excluded_word_history_changes_have_explicit_reasons(self):
        paths = ['/grad/123456_甲/开题/初稿.docx', '/grad/123456_甲/论文/查重报告.docx',
                 '/grad/123456_甲/个人简历.docx', '/grad/参考文档/毕业论文模板.docx',
                 '/grad/123456_甲/论文/../危险.docx']
        data = parse_history(INFO, history([(3, 'student', '', path) for path in paths]))
        self.assertEqual(data['total_students'], 0)
        self.assertEqual({i['repository_path'] for i in data['discovery_issues']}, set(paths))
        self.assertEqual({i['reason'] for i in data['discovery_issues']},
            {'outside_supported_paper_location', 'non_thesis_document_name',
             'unverified_student_root_document_type', 'not_student_directory', 'unsafe_repository_path'})

    def test_earliest_old_doc_beats_later_renamed_graduation_docx(self):
        data = parse_history(INFO, history([(29,'student','初稿','/grad/123456_甲/论文/毕业论文.docx'),
            (12,'mayy','','/grad/123456_甲/论文/系统.doc'),(10,'student','','/grad/123456_甲/论文/系统.doc'),
            (8,'student','','/grad/123456_甲/论文/检测报告.docx'),(9,'student','','/grad/123456_甲/开题/初稿.docx')]))
        student = data['students'][0]
        self.assertEqual(student['initial_revision'],10)
        self.assertEqual(student['teacher_version_count'],1)
        self.assertEqual(student['document_count'],2)
        self.assertEqual(student['initial_selection'],'earliest_available_pre_feedback_candidate')

    def test_teacher_only_no_student_initial(self):
        data=parse_history(INFO,history([(12,'mayy','','/grad/123456_甲/论文/甲.docx')]))
        self.assertIsNone(data['students'][0]['initial'])

    def test_out_of_tree_paths_and_future_revision_rejected(self):
        data=parse_history(INFO,history([(1,'student','','/other/123456_甲/论文/甲.docx')]))
        self.assertEqual(data['total_students'],0)
        with self.assertRaises(ValueError):
            parse_history(INFO,history([(31,'mayy','','/grad/123456_甲/论文/甲.docx')]))

    def test_credentials_not_accepted(self):
        with self.assertRaises(ValueError):
            parse_history(INFO.replace(b'svn://host',b'svn://user:secret@host'),b'<log/>')


class WorkflowTests(TestCase):
    def test_new_external_live_job_is_loaded_and_blocks_new_work(self):
        import os
        with TemporaryDirectory() as directory:
            root=Path(directory);workflow=ThesisWorkflow(root)
            try:
                save(root/'data/thesis_workflow/jobs/job_external.json',{'id':'job_external','action':'collect',
                     'owner_pid':os.getpid(),'status':'running','created_at':'2020','log_tail':''})
                self.assertTrue(workflow.active())
                self.assertEqual(workflow.snapshot()['jobs'][0]['status'],'running')
            finally:workflow.close()

    def test_invalid_selection_and_budget_no_requests(self):
        with TemporaryDirectory() as directory:
            workflow=ThesisWorkflow(Path(directory),invoke=lambda *a,**k:self.fail('must not invoke'))
            try:
                for action,options in [('collect',{'student_ids':['unknown'],'max_versions':1}),
                    ('collect',{'student_ids':['x']*11,'max_versions':1}),
                    ('classify',{'max_requests':5,'max_reserved_tokens':24000}),('discover',{'url':'svn://evil'})]:
                    with self.assertRaises(ValueError): workflow.start(action,options)
            finally: workflow.close()

    def test_restarting_preserves_interrupted_job_not_retry(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'data/thesis_workflow/jobs/job_old.json'
            save(path,{'id':'job_old','status':'running','created_at':'2020','action':'collect'})
            workflow=ThesisWorkflow(Path(directory))
            try:
                value=workflow.snapshot()['jobs'][0]
                self.assertEqual(value['status'],'failed')
                self.assertFalse(workflow.active())
            finally: workflow.close()


if __name__=='__main__':main()
