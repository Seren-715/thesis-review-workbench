import json
from pathlib import Path
import tempfile
import unittest

from atomic_json import write_json
from review_all_initials import InitialReviewScheduler, report_progress


RULES = [{'id': 'NP001', 'enabled': True, 'scope': 'body'}]


def report(reviewed=0, remaining=2, manual=1):
    return {'source_sha256': 'a'*64, 'status': 'partial',
            'run': {'mode': 'agent', 'progress': {'reviewed': reviewed, 'remaining': remaining},
                    'completion': {'status': 'completed_with_manual' if not remaining else 'budget_remaining'}},
            'coverage': [{'rule_id': 'NP001', 'status': 'partial'}],
            'summary': {'result_groups': {'pending': manual, 'unreviewed': remaining}}, 'findings': []}


class FakeWorkflow:
    def __init__(self, root, responses):
        self.root, self.responses = root, iter(responses)
        self.jobs, self.calls, self.latest = {}, [], {}
        self.students = [{'id': x, 'name': x, 'initial': {'revision': 1, 'repository_path': x+'.docx'}}
                         for x in ['A', 'B']]

    def snapshot(self):
        return {'discovery': {'students': self.students}}

    def active(self):
        return False

    def _reusable_initial(self, student):
        return (self.root/'source.docx', {}, None, None,
                self.latest.get(student['id'], report()), None)

    def start(self, action, options):
        self.calls.append(options)
        sid = options['student_ids'][0]
        response = next(self.responses)
        jid = f'job_{len(self.calls)}'
        path = self.root/(jid+'.json')
        value = response.get('report', report())
        write_json(path, value)
        self.latest[sid] = value
        job = {'id': jid, 'status': response.get('status', 'partial'),
               'known_total_tokens': response.get('tokens', 100),
               'unknown_cost_attempts': response.get('unknown', 0),
               'reserved_token_bound': response.get('reserved', 200),
               'budget_charge_token_bound': response.get('tokens', 100),
               'llm_requests': 1, 'max_reserved_token_bound': 24000,
               'result_summary': {'students': [{'student_id': sid, 'report_json_path': str(path),
                                               **({'error': response['error']} if 'error' in response else {})}]}}
        self.jobs[jid] = job
        return job


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def scheduler(self, responses):
        workflow = FakeWorkflow(self.root, responses)
        return InitialReviewScheduler(workflow, root=self.root, rules=RULES,
                                      sleep=lambda _: None), workflow

    def test_plan_is_unpaid_and_completion_has_two_distinct_meanings(self):
        scheduler, workflow = self.scheduler([])
        state = scheduler.plan()
        self.assertEqual(workflow.calls, [])
        self.assertEqual(state['summary']['students'], 2)
        progress = report_progress(report(2, 0), ['NP001'])
        self.assertTrue(progress['automated_finished'])
        self.assertFalse(progress['strict_fully_completed'])
        self.assertEqual(progress['manual_review_items'], 1)

    def test_fair_bounded_rounds_retain_usage_and_paths(self):
        scheduler, workflow = self.scheduler([{'report': report(1, 1)}, {'report': report(1, 1)},
                                              {'report': report(2, 0)}, {'report': report(2, 0)}])
        state = scheduler.run(max_rounds=4)
        self.assertEqual([c['student_ids'][0] for c in workflow.calls], ['A', 'B', 'A', 'B'])
        self.assertTrue(all(c['max_reserved_tokens'] == 24000 and c['review_scope'] == 'full'
                            for c in workflow.calls))
        self.assertEqual(state['summary']['automated_finished'], 2)
        self.assertEqual(state['summary']['strict_fully_completed'], 0)
        self.assertEqual(state['summary']['known_total_tokens'], 400)
        self.assertTrue(all('report_json_path' in r for r in state['rounds']))

    def test_resume_does_not_repeat_finished_students(self):
        scheduler, workflow = self.scheduler([{'report': report(2, 0)}, {'report': report(1, 1)},
                                              {'report': report(2, 0)}])
        scheduler.run(max_rounds=2)
        resumed = InitialReviewScheduler(workflow, root=self.root, rules=RULES, sleep=lambda _: None)
        state = resumed.run(max_rounds=1)
        self.assertEqual([c['student_ids'][0] for c in workflow.calls], ['A', 'B', 'B'])
        self.assertEqual(state['summary']['automated_finished'], 2)

    def test_unknown_cost_stops_every_student_and_retains_charge(self):
        scheduler, workflow = self.scheduler([{'unknown': 1}])
        with self.assertRaisesRegex(ValueError, '用量未确认'):
            scheduler.run(max_rounds=3)
        state = json.loads(scheduler.path.read_text(encoding='utf-8'))
        self.assertEqual(len(workflow.calls), 1)
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['rounds'][0]['unknown_cost_attempts'], 1)

    def test_network_failure_stops_without_paid_retry(self):
        scheduler, workflow = self.scheduler([{'error': '网络错误'}])
        with self.assertRaisesRegex(ValueError, '任务失败'):
            scheduler.run(max_rounds=3)
        self.assertEqual(len(workflow.calls), 1)

    def test_budget_overrun_stops(self):
        scheduler, workflow = self.scheduler([{'reserved': 24001}])
        with self.assertRaisesRegex(ValueError, '预算记录超过'):
            scheduler.run(max_rounds=3)
        self.assertEqual(len(workflow.calls), 1)

    def test_no_progress_stops_specific_students(self):
        scheduler, workflow = self.scheduler([{}, {}, {}, {}])
        state = scheduler.run(max_rounds=8)
        self.assertEqual(len(workflow.calls), 4)
        self.assertEqual(state['summary']['blocked_students'], 2)
        self.assertFalse(state['students']['A']['automated_finished'])
        resumed = InitialReviewScheduler(workflow, root=self.root, rules=RULES)
        resumed.run(max_rounds=1)
        self.assertEqual(len(workflow.calls), 4)

    def test_changed_rule_contract_forces_current_check(self):
        scheduler, workflow = self.scheduler([{'report': report(2, 0)}, {'report': report(2, 0)}])
        scheduler.run(max_rounds=2)
        updated = InitialReviewScheduler(workflow, root=self.root,
            rules=[{'id': 'NP001', 'scope': 'new scope'}])
        state = updated.plan()
        self.assertEqual(state['summary']['automated_finished'], 0)

    def test_source_change_prevents_dispatch(self):
        scheduler, workflow = self.scheduler([])
        scheduler.plan()
        workflow.students[0]['initial']['revision'] = 2
        with self.assertRaisesRegex(ValueError, '来源变化'):
            scheduler.run()
        self.assertEqual(workflow.calls, [])

    def test_pending_dispatch_intent_cannot_duplicate_paid_request(self):
        scheduler, workflow = self.scheduler([])
        scheduler.state['dispatch_intent'] = 'submitted'
        with self.assertRaisesRegex(RuntimeError, '提交记录'):
            scheduler.run()
        self.assertEqual(workflow.calls, [])

    def test_missing_rules_prevent_automatic_completion(self):
        progress = report_progress(report(2, 0), ['NP001', 'NP002'])
        self.assertFalse(progress['automated_finished'])
        self.assertEqual(progress['missing_rule_ids'], ['NP002'])

    def test_output_block_is_not_automatic_completion(self):
        value = report(1, 0)
        value['run']['progress']['blocked'] = 1
        progress = report_progress(value, ['NP001'])
        self.assertFalse(progress['automated_finished'])

    def test_halt_remains_latched_on_restart(self):
        scheduler, workflow = self.scheduler([{'unknown': 1}])
        with self.assertRaises(ValueError):
            scheduler.run()
        resumed = InitialReviewScheduler(workflow, root=self.root, rules=RULES)
        with self.assertRaisesRegex(RuntimeError, '前次调度'):
            resumed.run()
        self.assertEqual(len(workflow.calls), 1)

    def test_resume_inflight_receipt_is_consumed_only_once(self):
        scheduler, workflow = self.scheduler([{'report': report(2, 0)}, {'report': report(2, 0)}])
        scheduler.plan()
        job = workflow.start('check-initials', {'student_ids': ['A']})
        scheduler.state.update(inflight_job_id=job['id'], inflight_student_id='A')
        scheduler.save()
        resumed = InitialReviewScheduler(workflow, root=self.root, rules=RULES, sleep=lambda _: None)
        state = resumed.run(max_rounds=1)
        self.assertEqual(len(state['rounds']), 2)
        self.assertEqual([r['student_id'] for r in state['rounds']], ['A', 'B'])


if __name__ == '__main__':
    unittest.main()
