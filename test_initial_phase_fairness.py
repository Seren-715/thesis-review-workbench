"""Offline tests for stage opportunities and conservative token reservations."""
from copy import deepcopy
import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock
from atomic_json import read_json

from new_paper_agent import BoundedClient, fair_queue_pass, restore_phase_cursor, PHASE_CURSOR_POLICY, reusable_tool_selection


def state():
    return {'requests': [], 'request_attempts': 0, 'known_total_tokens': 0,
            'reserved_token_bound': 10000, 'strict_reservation_budget': True,
            'max_reserved_token_bound': 24000, 'max_requests': 16,
            'unknown_cost_attempts': 0}


class Queue:
    def __init__(self, name, run, events, upper=8000, unknown=False):
        self.name, self.run, self.events = name, run, events
        self.upper, self.unknown = upper, unknown
        self.done = False

    def has_pending(self):
        return not self.done

    def step(self, budget):
        self.events.append(self.name)
        if not budget.can_request(self.upper):
            return False
        self.run['reserved_token_bound'] += self.upper
        self.run['request_attempts'] += 1
        self.run['requests'].append({'reserved_upper_bound': self.upper,
                                    'usage': {'total_tokens': 100}})
        self.run['known_total_tokens'] += 100
        self.run['unknown_cost_attempts'] = int(self.unknown)
        self.done = True
        return True


class PhaseFairnessTests(unittest.TestCase):
    def queues(self, run, events, **kwargs):
        return {name: Queue(name, run, events, **kwargs)
                for name in ('content', 'text', 'figures')}

    def test_first_round_serves_content_before_short_text(self):
        run, events = state(), []
        queues = self.queues(run, events)
        persisted = []
        self.assertTrue(fair_queue_pass(queues, BoundedClient(None, Path('.'), run), run,
                                        lambda: persisted.append(deepcopy(run['phase_cursor']))))
        self.assertEqual(events, ['content', 'text', 'figures'])
        self.assertTrue(queues['content'].done)
        self.assertFalse(queues['text'].done)
        self.assertEqual(run['phase_cursor']['next_phase'], 'text')
        self.assertEqual(run['reserved_token_bound'], 18000)
        self.assertEqual(run['known_total_tokens'], 100)
        self.assertEqual(run['max_reserved_token_bound'], 24000)
        self.assertEqual(len(persisted), 3)

    def test_cross_round_cursor_gives_each_phase_a_funded_turn(self):
        prior, served = None, []
        for _ in range(3):
            report = {'source_sha256': 'same'}
            run, events = state(), []
            run['phase_cursor'] = restore_phase_cursor(report, prior)
            queues = self.queues(run, events)
            fair_queue_pass(queues, BoundedClient(None, Path('.'), run), run)
            served += [name for name, q in queues.items() if q.done]
            self.assertLessEqual(run['reserved_token_bound'], 24000)
            self.assertEqual(run['max_reserved_token_bound'], 24000)
            prior = dict(report, run=deepcopy(run))
        self.assertEqual(served, ['content', 'text', 'figures'])

    def test_unknown_usage_stops_before_any_queue_or_charge(self):
        run, events = state(), []
        run['unknown_cost_attempts'] = 1
        before = deepcopy(run)
        with self.assertRaisesRegex(ValueError, '用量未知'):
            fair_queue_pass(self.queues(run, events), BoundedClient(None, Path('.'), run), run)
        self.assertEqual(events, [])
        for key in ('reserved_token_bound', 'known_total_tokens', 'request_attempts'):
            self.assertEqual(run[key], before[key])

    def test_unknown_created_by_first_queue_prevents_other_requests(self):
        run, events = state(), []
        queues = self.queues(run, events)
        queues['content'].unknown = True
        with self.assertRaisesRegex(ValueError, '用量未知'):
            fair_queue_pass(queues, BoundedClient(None, Path('.'), run), run)
        self.assertEqual(events, ['content'])
        self.assertEqual(run['request_attempts'], 1)

    def test_unfunded_phase_retains_priority_even_if_later_small_task_fits(self):
        run, events = state(), []
        queues = self.queues(run, events)
        queues['content'].upper = 15000
        queues['text'].upper = 1000
        queues['figures'].upper = 1000
        fair_queue_pass(queues, BoundedClient(None, Path('.'), run), run)
        self.assertEqual(run['phase_cursor']['next_phase'], 'content')
        self.assertFalse(queues['content'].done)
        self.assertTrue(queues['text'].done)
        self.assertTrue(queues['figures'].done)
        self.assertEqual(run['reserved_token_bound'], 12000)

    def test_source_or_policy_mismatch_does_not_adopt_cursor(self):
        prior = {'source_sha256': 'old', 'run': {'phase_cursor': {
            'policy': PHASE_CURSOR_POLICY, 'next_phase': 'figures'}}}
        self.assertFalse(restore_phase_cursor({'source_sha256': 'new'}, prior)['reused'])
        prior['source_sha256'] = 'new'
        prior['run']['phase_cursor']['policy'] = 'untrusted'
        self.assertFalse(restore_phase_cursor({'source_sha256': 'new'}, prior)['reused'])

    def test_cursor_is_only_scheduling_not_semantic_evidence_reuse(self):
        prior = {'source_sha256': 'same', 'semantic_review': {'validated_scope': True},
                 'run': {'phase_cursor': {'policy': PHASE_CURSOR_POLICY, 'next_phase': 'text'}}}
        cursor = restore_phase_cursor({'source_sha256': 'same'}, prior)
        self.assertEqual(cursor['next_phase'], 'text')
        self.assertNotIn('semantic_review', cursor)
        self.assertFalse(restore_phase_cursor({'source_sha256': 'same'}, prior, 'content')['reused'])

    def test_figure_only_queue_keeps_one_stage_and_original_budget(self):
        run, events = state(), []
        queues = {'figures': Queue('figures', run, events)}
        fair_queue_pass(queues, BoundedClient(None, Path('.'), run), run)
        self.assertEqual(events, ['figures'])
        self.assertEqual(run['phase_cursor']['next_phase'], 'figures')
        self.assertEqual(run['max_reserved_token_bound'], 24000)

    def test_local_preflight_failure_writes_proven_zero_operation_receipt(self):
        from new_paper_agent import run_review
        from thesis_workflow import review_budget_charge
        for exception in (ValueError('位置不一致'), OSError('读取失败'), KeyError('缺少属性'), TypeError('格式错误')):
            with self.subTest(exception=type(exception).__name__), TemporaryDirectory() as folder:
                output = Path(folder)/'review'
                with patch('new_paper_checks.execute_checks', side_effect=exception) as checks, \
                        patch('new_paper_agent.configured_budget') as paid:
                    with self.assertRaises(type(exception)):
                        run_review(Path(folder)/'paper.docx', output, use_agent=True,
                                   collect_word=True, max_total_bound=24000, strict_reservation_budget=True)
                    checks.assert_called_once()
                    paid.assert_not_called()
                receipt = read_json(output/'run.json')
                self.assertEqual(receipt['status'], 'local_preflight_failed')
                for key in ('request_attempts', 'dispatched_requests', 'known_total_tokens',
                            'unknown_cost_attempts', 'reserved_token_bound', 'word_launches'):
                    self.assertEqual(receipt[key], 0)
                self.assertEqual(review_budget_charge(receipt), (0, 0))

    def test_unknown_failure_after_preflight_does_not_receive_zero_operation_stamp(self):
        from new_paper_agent import run_review
        with TemporaryDirectory() as folder:
            output = Path(folder)/'review'
            with patch('new_paper_checks.execute_checks', side_effect=RuntimeError('未识别失败')):
                with self.assertRaises(RuntimeError):
                    run_review(Path(folder)/'paper.docx', output, use_agent=True)
            self.assertFalse((output/'run.json').exists())

    def selection_pair(self):
        from review_contract import review_identity
        value = {'source_sha256': 'same', 'rules': [{'id': 'NP001', 'scope': '正文'}],
                 'review_contract': review_identity()}
        prior = deepcopy(value)
        prior['run'] = {'tool_selection': {'policy': 'model_selected_read_only_tools_v1',
            'selected': ['inspect_first_person'], 'available': ['inspect_first_person'],
            'schema_sha256': 'a'*64}, 'tool_calls': [{'name': 'inspect_first_person',
                                                  'source_sha256': 'same', 'arguments': {}}]}
        return value, prior

    def test_verified_selection_is_eligible_without_any_new_model_call(self):
        current, prior = self.selection_pair()
        receipt = reusable_tool_selection(current, prior)
        self.assertEqual(receipt['selected'], ['inspect_first_person'])
        self.assertIsNot(receipt, prior['run']['tool_selection'])

    def test_old_contract_or_source_or_rules_cannot_reuse_selection(self):
        for field in ('source_sha256', 'review_contract', 'rules'):
            with self.subTest(field=field):
                current, prior = self.selection_pair()
                prior[field] = 'old' if field == 'source_sha256' else {} if field == 'review_contract' else []
                self.assertIsNone(reusable_tool_selection(current, prior))

    def test_malicious_missing_or_nonempty_tool_arguments_are_rejected(self):
        for replacement in ({'command': 'run'}, None, 'not-a-map'):
            with self.subTest(replacement=replacement):
                current, prior = self.selection_pair()
                prior['run']['tool_calls'][0]['arguments'] = replacement
                self.assertIsNone(reusable_tool_selection(current, prior))
        current, prior = self.selection_pair()
        prior['run']['tool_selection']['available'] = [{}]
        self.assertIsNone(reusable_tool_selection(current, prior))

    def test_legacy_or_duplicate_selection_receipts_are_rejected(self):
        current, prior = self.selection_pair()
        del prior['run']['tool_selection']['schema_sha256']
        self.assertIsNone(reusable_tool_selection(current, prior))
        current, prior = self.selection_pair()
        prior['run']['tool_selection']['selected'] *= 2
        self.assertIsNone(reusable_tool_selection(current, prior))

    def test_actual_cached_dispatch_runs_read_only_tool_without_paid_request(self):
        from new_paper_agent import review_with_tools, FreshPaperTool, TOOL_RULES
        from nanobot.agent.tools.registry import ToolRegistry
        report = {'source_sha256': 'same', 'findings': [], 'rules': [], 'coverage': []}
        names = list(TOOL_RULES)
        registry = ToolRegistry()
        for name in names:
            registry.register(FreshPaperTool(name, report))
        digest = hashlib.sha256(json.dumps(registry.get_definitions(), ensure_ascii=False,
                                           sort_keys=True).encode()).hexdigest()
        run = state()
        run.update(tool_calls=[], review_scope='full', tool_selection_reuse_candidate={
            'selected': ['inspect_first_person'], 'available': names, 'schema_sha256': digest})
        text = Mock(units={}, findings=[])
        text.has_pending.return_value = True
        text.step.return_value = False
        figures = Mock(findings=[])
        figures.has_pending.return_value = False
        budget = Mock()
        with TemporaryDirectory() as folder, \
                patch('new_paper_agent.SemanticReviewQueue', return_value=text), \
                patch('paper_vision_review.FigureReviewQueue', return_value=figures), \
                patch('paper_vision_review.update_vision_coverage'), \
                patch('adaptive_evidence_agent.context_gaps', return_value=[]), \
                patch('new_paper_agent.configured_budget', return_value=budget), \
                patch('new_paper_agent._save_review_progress'), \
                patch('new_paper_agent.update_review_progress'), \
                patch('new_paper_agent._finalize_phase_errors'):
            asyncio.run(review_with_tools(report, None, Path(folder), run))
        budget.request.assert_not_called()
        self.assertEqual(run['tool_calls'][0]['name'], 'inspect_first_person')
        self.assertTrue(run['tool_calls'][0]['selection_receipt_reused'])
        self.assertTrue(run['tool_selection']['selection_receipt_reused'])
        self.assertEqual(run['reserved_token_bound'], 10000)
        self.assertEqual(run['known_total_tokens'], 0)


if __name__ == '__main__':
    unittest.main()
