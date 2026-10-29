import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atomic_json import read_json, write_json
from review_all_initials import (InitialReviewScheduler, request_stop,
    clear_stop_marker, main)
from test_review_all_initials import FakeWorkflow, RULES, report


class SafeStopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def scheduler(self,responses):
        wf=FakeWorkflow(self.root,responses)
        return InitialReviewScheduler(wf,root=self.root,rules=RULES,sleep=lambda _:None),wf

    def test_stop_marker_prevents_dispatch_and_persists_across_runs(self):
        s,w=self.scheduler([]); request_stop(self.root,s.path)
        first=s.run(max_rounds=2); second=s.run(max_rounds=2)
        self.assertEqual(first['status'],'stopped_for_review')
        self.assertEqual(second['status'],'stopped_for_review')
        self.assertEqual(w.calls,[])
        self.assertTrue(s.path.with_suffix('.stop').exists())
        self.assertNotIn('halt_requires_operator_review',s.state)

    def test_stop_during_job_waits_for_consumed_receipt_before_return(self):
        s,w=self.scheduler([{'report':report(1,1)}])
        start=w.start
        def stop_after_dispatch(action,options):
            job=start(action,options); request_stop(self.root,s.path); return job
        w.start=stop_after_dispatch
        result=s.run(max_rounds=5)
        self.assertEqual(len(w.calls),1)
        self.assertEqual(result['status'],'stopped_for_review')
        self.assertEqual(result['rounds'][0]['known_total_tokens'],100)
        self.assertEqual(result['students']['A']['reviewed'],1)
        self.assertIsNone(result['inflight_job_id'])
        self.assertEqual(result['summary']['rounds'],1)
        self.assertTrue(result['rounds'][0]['report_json_path'])

    def test_existing_inflight_is_consumed_even_when_marker_exists(self):
        s,w=self.scheduler([{'report':report(1,1)}]); s.plan()
        job=w.start('check-initials',{'student_ids':['A']})
        s.state.update(inflight_job_id=job['id'],inflight_student_id='A')
        s.save(); request_stop(self.root,s.path)
        result=s.run()
        self.assertEqual(result['status'],'stopped_for_review')
        self.assertEqual(len(w.calls),1)
        self.assertEqual(len(result['rounds']),1)

    def test_stop_does_not_wait_for_active_scheduler_lock(self):
        s,w=self.scheduler([])
        with s.exclusive():
            result=request_stop(self.root,s.path)
            self.assertEqual(result['status'],'stop_requested')
            self.assertTrue(s.path.with_suffix('.lock').exists())
        self.assertEqual(w.calls,[])

    def test_resume_removes_only_known_marker_without_dispatch(self):
        s,w=self.scheduler([{'report':report(1,1)}]); s.plan()
        s.state['halt_requires_operator_review']=True; s.save()
        before=read_json(s.path); request_stop(self.root,s.path)
        clear_stop_marker(self.root,s.path)
        self.assertEqual(read_json(s.path),before)
        self.assertFalse(s.path.with_suffix('.stop').exists())
        with self.assertRaisesRegex(RuntimeError,'费用'):
            s.run()
        self.assertEqual(w.calls,[])

    def test_unknown_cost_is_not_hidden_by_stop_or_cleared_by_resume(self):
        s,w=self.scheduler([{'unknown':1}]); start=w.start
        def stop_after_dispatch(action,options):
            job=start(action,options); request_stop(self.root,s.path); return job
        w.start=stop_after_dispatch
        with self.assertRaisesRegex(ValueError,'用量未确认'):s.run(max_rounds=4)
        self.assertEqual(s.state['status'],'stopped')
        self.assertTrue(s.state['halt_requires_operator_review'])
        self.assertEqual(s.state['rounds'][0]['unknown_cost_attempts'],1)
        clear_stop_marker(self.root,s.path)
        with self.assertRaisesRegex(RuntimeError,'费用'):s.run()
        self.assertEqual(len(w.calls),1)

    def test_unrecognized_marker_is_not_deleted(self):
        s,w=self.scheduler([]); marker=s.path.with_suffix('.stop')
        write_json(marker,{'unrelated':'user file'})
        with self.assertRaisesRegex(ValueError,'已知标记'):clear_stop_marker(self.root,s.path)
        self.assertTrue(marker.exists())
        self.assertEqual(s.run()['status'],'stopped_for_review')
        self.assertEqual(w.calls,[])

    def test_cli_stop_and_resume_do_not_construct_workflow(self):
        for action in ('stop','resume'):
            with patch('sys.argv',['review_all_initials.py',action,'--root',str(self.root)]), \
                patch('thesis_workflow.ThesisWorkflow',side_effect=AssertionError('不可构建工作流')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
                main()
                self.assertIn('stop_',output.getvalue())


if __name__=='__main__':unittest.main()
