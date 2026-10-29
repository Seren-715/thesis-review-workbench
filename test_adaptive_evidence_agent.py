from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace as NS
from unittest import TestCase, main
from adaptive_evidence_agent import context_gaps, propose_next_action, ReadContextTool, reapply_verified_context


def paragraph(i, text, level=None, **kw):
    return dict(index=i, text=text, heading_level=level, **kw)


def report():
    return {'source_sha256': 'a' * 64, 'coverage': [], 'paragraphs': [
        paragraph(0, '系统实现', 1), paragraph(1, '本次对象，我们实现校验。'),
        paragraph(2, '这段说明作者表述是系统角色提示词的例子。'),
        paragraph(3, '另一个合法同节上下文。'), paragraph(4, '系统测试', 1),
        paragraph(5, '其他章节不得读取。')], 'findings': [
        {'id': 'NP001-0001', 'rule_id': 'NP001', 'paragraph_index': 1,
         'text': '本次对象，我们实现校验。', 'context': [], 'status': 'candidate',
         'semantic_review': {'decision': 'needs_review', 'validated_quote': True,
            'reason': '上下文不足，需要后续段落判断作者还是角色例子。', 'suggestion': '结合下文说明。'},
         'semantic_review_parts': {'old': {'decision': 'needs_review'}}}]}


def choice(calls):
    return NS(finish_reason='tool_calls', message=NS(tool_calls=[
        NS(function=NS(name=name, arguments=json.dumps(args)), id=f'call{i}')
        for i, (name, args) in enumerate(calls)]))


class FakeBudget:
    def __init__(self, response, allowed=True):
        self.response, self.allowed, self.calls = response, allowed, []
    def prepare(self, messages, **options): return ({'messages': messages, **options}, 100)
    def can_request(self, upper): return self.allowed
    def request(self, messages, **options):
        self.calls.append((messages, options))
        return self.response


class AdaptiveEvidenceTests(TestCase):
    def test_real_scope_lists_only_new_visible_same_chapter_source(self):
        r = report()
        r['paragraphs'].insert(3, paragraph(6, '目录结果', in_toc=True))
        r['paragraphs'].insert(4, paragraph(7, '文本框', in_textbox=True))
        r['paragraphs'].insert(5, paragraph(8, '删除文字', in_deleted=True))
        r['findings'][0]['context'] = [{'index': 3, 'text': '另一个合法同节上下文。'}]
        ids = [p['index'] for p in context_gaps(r)['NP001-0001']['paragraphs']]
        self.assertEqual(ids, [2])

    def test_same_source_supplement_preserves_receipt_then_requires_rejudgment(self):
        r, state = report(), {}
        budget = FakeBudget(choice([('read_context', {'finding_id': 'NP001-0001', 'paragraph_indices': [2]}),
                                    ('select_next_review', {'phase': 'text'})]))
        result = propose_next_action(r, state, budget, pending_phases=('text',))
        f = r['findings'][0]
        self.assertEqual(result['status'], 'evidence_selected')
        self.assertEqual(result['phase'], 'text')
        self.assertEqual(result['augmented_finding_ids'], ['NP001-0001'])
        self.assertEqual(f['previous_scope_review']['decision'], 'needs_review')
        self.assertNotIn('semantic_review', f)
        self.assertNotIn('semantic_review_parts', f)
        self.assertEqual(f['adaptive_context']['source_sha256'], r['source_sha256'])
        self.assertEqual(f['adaptive_context']['paragraphs'][0]['text'], r['paragraphs'][2]['text'])
        self.assertFalse(f['section_complete'])
        self.assertEqual(state['adaptive_planner_calls'], 1)
        self.assertEqual(len(state['tool_calls']), 2)
        self.assertEqual(len(budget.calls), 1)
        # A supplement is not itself a new correctness verdict, and cannot repeat.
        f['semantic_review'] = {'decision': 'needs_review', 'validated_quote': True, 'reason': '上下文仍不足'}
        self.assertEqual(propose_next_action(r, state, budget)['status'], 'not_needed')
        self.assertEqual(len(budget.calls), 1)

    def test_illegal_second_call_prevents_any_tool_effect(self):
        r, state = report(), {}
        original = deepcopy(r)
        budget = FakeBudget(choice([('read_context', {'finding_id': 'NP001-0001', 'paragraph_indices': [2]}),
                                    ('shell', {'cmd': 'unsafe'})]))
        self.assertEqual(propose_next_action(r, state, budget)['status'], 'invalid_plan')
        self.assertEqual(r, original)
        self.assertFalse(state.get('tool_calls'))

    def test_foreign_heading_or_extra_path_parameter_rejected(self):
        for args in [{'finding_id': 'NP001-0001', 'paragraph_indices': [5]},
                     {'finding_id': 'NP001-0001', 'paragraph_indices': [2], 'path': 'foreign'},
                     {'finding_id': 'NP001-0001', 'paragraph_indices': ['2']},
                     {'finding_id': 'NP001-0001', 'paragraph_indices': [True]}]:
            r, state = report(), {}
            before = deepcopy(r)
            result = propose_next_action(r, state, FakeBudget(choice([('read_context', args)])))
            self.assertEqual(result['status'], 'invalid_plan')
            self.assertEqual(before, r)
            self.assertFalse(state.get('tool_calls'))

    def test_budget_stop_or_no_legal_extra_never_requests_model(self):
        r = report()
        budget = FakeBudget(None, allowed=False)
        self.assertEqual(propose_next_action(r, {}, budget)['status'], 'budget_stopped')
        self.assertFalse(budget.calls)
        r['findings'][0]['context'] = deepcopy(r['paragraphs'])
        budget = FakeBudget(None)
        self.assertEqual(propose_next_action(r, {}, budget)['status'], 'not_needed')
        self.assertFalse(budget.calls)

    def test_whole_run_planner_limit_and_full_content_scope_do_not_duplicate_reads(self):
        r = report()
        budget = FakeBudget(None)
        self.assertEqual(propose_next_action(r, {'adaptive_planner_calls': 2}, budget)['status'], 'not_needed')
        r['findings'][0]['content_scope'] = {'complete': True}
        self.assertEqual(propose_next_action(r, {}, budget)['status'], 'not_needed')
        self.assertFalse(budget.calls)

    def test_read_limit_rejects_big_batch_without_truncation(self):
        r = report()
        r['paragraphs'][2]['text'] = '甲' * 1600
        r['paragraphs'][3]['text'] = '乙' * 1600
        tool = ReadContextTool(r, context_gaps(r), 2400)
        with self.assertRaises(ValueError):
            asyncio.run(tool.execute('NP001-0001', [2, 3]))

    def test_inferred_navigation_never_extends_unknown_scope(self):
        r = report()
        r['paragraphs'] = [paragraph(i, f'正文{i}', location={'heading_path': ['假推断章']}) for i in range(9)]
        r['findings'][0]['paragraph_index'] = 0
        self.assertEqual({p['index'] for p in context_gaps(r)['NP001-0001']['paragraphs']}, {1, 2, 3})

    def test_external_layout_gap_and_final_failure_are_not_text_rereads(self):
        r = report()
        r['findings'][0]['semantic_review']['reason'] = '图片像素过低，无法核实字体。'
        r['findings'][0]['semantic_review']['suggestion'] = '获取原始图片。'
        self.assertFalse(context_gaps(r))

    def test_cached_supplement_requires_current_contract_and_verified_source(self):
        from review_contract import review_identity
        old = report(); old['review_contract'] = review_identity()
        budget = FakeBudget(choice([('read_context', {'finding_id': 'NP001-0001', 'paragraph_indices': [2]})]))
        propose_next_action(old, {}, budget)
        fresh = report(); fresh['review_contract'] = review_identity()
        self.assertEqual(reapply_verified_context(fresh, old), 1)
        self.assertEqual(fresh['findings'][0]['context'], old['findings'][0]['context'])
        self.assertNotIn('semantic_review_parts', fresh['findings'][0])
        for mutation in ('text', 'chapter', 'hash', 'contract', 'previous_context', 'context_hash'):
            target, cached = report(), deepcopy(old)
            target['review_contract'] = review_identity()
            if mutation == 'text': cached['findings'][0]['adaptive_context']['paragraphs'][0]['text'] = '伪造引文'
            elif mutation == 'chapter': target['paragraphs'][2]['heading_level'] = 1
            elif mutation == 'hash': cached['source_sha256'] = 'b' * 64
            elif mutation == 'contract': cached['review_contract']['model'] = 'other'
            elif mutation == 'previous_context': target['findings'][0]['context'] = [{'text': '变动前置上下文'}]
            else: cached['findings'][0]['adaptive_context']['context_sha256'] = '0' * 64
            self.assertEqual(reapply_verified_context(target, cached), 0, mutation)
            self.assertNotIn('adaptive_context', target['findings'][0])
        r = report()
        r['findings'][0]['review_blocked'] = {'reason': 'output_validation_failed'}
        self.assertFalse(context_gaps(r))


if __name__ == '__main__': main()
