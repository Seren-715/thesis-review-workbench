"""One submission drains a finite source-bound plan; fake SDK incurs no API costs."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from new_paper_agent import (DEFAULT_REQUESTS, DEFAULT_TOTAL_BOUND, EVIDENCE_REVIEW_SYSTEM,
    build_semantic_units, evidence_packet_item, validate_evidence_batch, review_with_tools,
    reuse_semantic_evidence, reuse_review_blocks, recover_saved_text, bind_unit_context,
    EVIDENCE_REVIEW_SYSTEM_V2)
from paper_vision_review import SYSTEM as VISION_SYSTEM


class FakeProvider:
    def __init__(self, invalid_text=None, invalid_image=None, repair_succeeds=False,
                 unknown_at=None, foreign=False):
        self.calls = []
        self.invalid_text = set(invalid_text or [])
        self.invalid_image = set(invalid_image or [])
        self.repair_succeeds = repair_succeeds
        self.unknown_at = unknown_at
        self.foreign = foreign
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls.append(deepcopy(request))
        number = len(self.calls)
        if number == self.unknown_at:
            return SimpleNamespace(usage=None)
        tools = request.get('tools')
        if tools:
            calls = [SimpleNamespace(id=f'call_{number}_{i}',
                function=SimpleNamespace(name=definition['function']['name'], arguments='{}'))
                for i, definition in enumerate(tools)]
            message_dump = {'role': 'assistant', 'tool_calls': [{'id': c.id, 'type': 'function',
                'function': {'name': c.function.name, 'arguments': '{}'}} for c in calls]}
            content, finish_reason = None, 'tool_calls'
        else:
            calls = None
            system = request['messages'][0]['content']
            rows = []
            if system == EVIDENCE_REVIEW_SYSTEM:
                packet = json.loads(request['messages'][1]['content'])
                repair = 'output_repair' in packet
                for item in packet['items']:
                    row = {'id': item['id'], 'decision': 'not_issue',
                        'evidence_id': item['evidence_snippets'][0]['evidence_id'],
                        'reason': '根据给定原文，此处用法符合当前规则。', 'suggestion': ''}
                    if item['id'] in self.invalid_text and not (repair and self.repair_succeeds):
                        row['evidence_id'] = 'not-provided'
                    rows.append(row)
            elif system == VISION_SYSTEM:
                for block in request['messages'][1]['content']:
                    if block['type'] != 'text':
                        continue
                    item = json.loads(block['text'])
                    row = {'id': item['id'], 'image_kind': 'diagram', 'readable': True,
                        'decision': 'not_issue', 'observations': ['节点和连接线清楚。'],
                        'visible_text': ['用户'], 'reason': '可见标签清晰。', 'suggestion': ''}
                    if item['id'] in self.invalid_image and not ('output_repair' in item and self.repair_succeeds):
                        row['visible_text'] = ['标签'] * 33
                    rows.append(row)
            else:
                raise AssertionError('Unexpected system prompt')
            if self.foreign:
                rows[0]['id'] = 'foreign-source-id'
            content = json.dumps({'items': rows}, ensure_ascii=False)
            finish_reason = 'stop'
            message_dump = {'role': 'assistant', 'content': content}
        message = SimpleNamespace(content=content, tool_calls=calls)
        message.model_dump = lambda **kwargs: deepcopy(message_dump)
        choice = SimpleNamespace(finish_reason=finish_reason, message=message)
        dump = {'choices': [{'finish_reason': finish_reason, 'message': message_dump}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}}
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                                   choices=[choice])
        response.model_dump = lambda **kwargs: deepcopy(dump)
        return response


class OnePassTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def report(self, text_count=0, image_count=0):
        findings = []
        for number in range(text_count):
            text = f'第{number}个实验中，我们完成模型建立。'
            offset = text.index('我')
            findings.append({'id': f'NP001-{number + 1:04d}', 'rule_id': 'NP001', 'status': 'candidate',
                'text': text, 'start': offset, 'end': offset + 1, 'reason': '作者用法待核查',
                'teacher_evidence': [], 'context': [], 'paragraph_index': number})
        for number in range(image_count):
            path = self.output / f'image-{number}.png'
            path.write_bytes(f'fixed verified pixels {number}'.encode())
            findings.append({'id': f'NP007-{number + 1:04d}', 'rule_id': 'NP007', 'status': 'needs_review',
                'text': f'图1-{number + 1} 模型结构图', 'teacher_evidence': [],
                'figure_evidence': {'reviewable': True, 'source_sha256': 'fixed-source',
                    'preview_path': str(path), 'preview_sha256': sha256(path.read_bytes()).hexdigest()}})
        return {'source_sha256': 'fixed-source', 'findings': findings,
            'rules': [{'id': 'NP001', 'scope': '作者正文叙述'}, {'id': 'NP007', 'scope': '图片可读性'},
                      {'id': 'NP013', 'scope': '结论片段'}], 'coverage': [], 'limitations': [],
            'paragraphs': [{'index': n, 'in_table': False} for n in range(text_count)]}

    def state(self, max_requests=DEFAULT_REQUESTS, max_bound=DEFAULT_TOTAL_BOUND):
        return {'source_sha256': 'fixed-source', 'requests': [], 'request_attempts': 0,
            'dispatched_requests': 0, 'known_total_tokens': 0, 'unknown_cost_attempts': 0,
            'reserved_token_bound': 0, 'tool_calls': [], 'review_scope': 'full',
            'max_requests': max_requests, 'max_reserved_token_bound': max_bound}

    def drive(self, report, provider=None, state=None):
        provider = provider or FakeProvider()
        state = state or self.state()
        asyncio.run(review_with_tools(report, provider, self.output, state))
        return provider, state

    def test_forty_text_items_and_thirteen_images_complete_in_one_submission(self):
        report = self.report(40, 13)
        provider, state = self.drive(report)
        self.assertEqual(state['semantic_reviewed'], 40)
        self.assertEqual(state['vision_reviewed'], 13)
        self.assertEqual(state['completion']['status'], 'completed')
        self.assertEqual(state['completion']['reviewed'], 53)
        self.assertEqual(state['completion']['budget_stopped'], 0)
        self.assertEqual(len(provider.calls), 15)
        phases = [r['plan']['phase'] for r in state['requests']]
        self.assertEqual(phases[:5], ['tools', 'text', 'figures', 'text', 'figures'])
        self.assertEqual(state['phase_errors'], [])
        self.assertTrue(all(f.get('semantic_review', {}).get('validated_quote') or
                            f.get('vision_review', {}).get('validated_image') for f in report['findings']))

    def test_explicit_six_request_24k_budget_remains_a_hard_stop(self):
        report = self.report(40, 13)
        provider, state = self.drive(report, state=self.state(6, 24000))
        self.assertEqual(len(provider.calls), 6)
        self.assertEqual(state['completion']['status'], 'budget_remaining')
        self.assertGreater(state['completion']['budget_stopped'], 0)
        self.assertEqual(state['completion']['blocked'], 0)
        self.assertEqual(state['known_total_tokens'], 900)

    def test_repair_only_missing_invalid_id_and_does_not_repeat_successful_rows(self):
        report = self.report(4, 1)
        provider = FakeProvider(invalid_text={'NP001-0002'}, repair_succeeds=True)
        provider, state = self.drive(report, provider)
        text_packets = [json.loads(r['messages'][1]['content']) for r in provider.calls
                        if r['messages'][0]['content'] == EVIDENCE_REVIEW_SYSTEM]
        self.assertEqual([x['id'] for x in text_packets[0]['items']], [f'NP001-{n:04d}' for n in range(1, 5)])
        self.assertEqual([x['id'] for x in text_packets[1]['items']], ['NP001-0002'])
        self.assertIn('output_repair', text_packets[1])
        self.assertEqual(state['completion']['status'], 'completed')
        self.assertEqual(state['phase_errors'], [])
        self.assertEqual(len(state['historical_phase_errors']), 1)
        self.assertEqual(len(state['output_repair_history']), 1)
        self.assertEqual(state['output_repair_history'][0]['usage']['total_tokens'], 150)

    def test_invalid_forever_is_terminal_manual_after_one_finite_repair(self):
        report = self.report(4, 2)
        provider = FakeProvider(invalid_text={'NP001-0002'}, invalid_image={'NP007-0001'})
        provider, state = self.drive(report, provider)
        blocked = [f for f in report['findings'] if f.get('review_blocked')]
        self.assertEqual({f['id'] for f in blocked}, {'NP001-0002', 'NP007-0001'})
        self.assertTrue(all(f['review_blocked']['attempts'] == 2 for f in blocked))
        self.assertEqual(state['completion']['status'], 'completed_with_manual')
        self.assertEqual(state['semantic_unreviewed'], 0)
        self.assertEqual(state['vision_unreviewed'], 0)
        self.assertEqual(state['completion']['blocked'], 2)
        self.assertEqual(len(state['output_repair_history']), 2)
        self.assertEqual(len(provider.calls), 5)

    def test_foreign_id_never_accepted_and_repair_is_finite(self):
        report = self.report(2)
        provider, state = self.drive(report, FakeProvider(foreign=True))
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(state['completion']['reviewed'], 0)
        self.assertEqual(state['completion']['blocked'], 2)
        self.assertTrue(all('semantic_review' not in f for f in report['findings']))

    def test_unknown_usage_prevents_further_text_or_image_requests(self):
        report = self.report(8, 8)
        provider = FakeProvider(unknown_at=2)
        state = self.state()
        with self.assertRaisesRegex(ValueError, '用量'):
            asyncio.run(review_with_tools(report, provider, self.output, state))
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(state['unknown_cost_attempts'], 1)
        self.assertEqual(state['known_total_tokens'], 150)
        self.assertEqual(state['dispatched_requests'], 2)

    def test_long_paragraph_is_fully_partitioned_not_silently_skipped(self):
        report = self.report()
        text = '实验显示模型完成了给定预测工作，但仍存在样本限制。' * 200
        f = {'id': 'NP013-1', 'rule_id': 'NP013', 'status': 'candidate', 'semantic_required': True,
            'text': text, 'start': 0, 'end': len(text), 'reason': '结论审阅', 'teacher_evidence': [],
            'context': [], 'section_complete': True}
        report['findings'].append(f)
        units = build_semantic_units(f, report['rules'][-1])
        self.assertEqual(''.join(u['text'] for u in units), text)
        self.assertGreater(len(units), 1)
        self.assertTrue(all(not u['section_complete'] for u in units))
        provider, state = self.drive(report)
        self.assertTrue(f['semantic_review']['validated_quote'])
        self.assertEqual(f['semantic_review']['fragment_count'], len(units))
        self.assertEqual(state['completion']['reviewed'], 1)
        self.assertEqual(len(f['semantic_review_parts']), len(units))

    def test_repeated_pronoun_precise_evidence_resolves_exact_hit(self):
        f = self.report(1)['findings'][0]
        f.update(text='我完成示范。' + '中间内容。' * 100 + '我们完成对照实验。')
        f['start'] = f['text'].rindex('我')
        f['end'] = f['start'] + 1
        unit = build_semantic_units(f, {})[0]
        row = {'id': unit['id'], 'decision': 'suggested_revision',
            'evidence_id': unit['evidence_snippets'][0]['evidence_id'], 'reason': '作者自述。', 'suggestion': '改为本研究。'}
        rows, errors = validate_evidence_batch(json.dumps({'items': [row]}, ensure_ascii=False), [unit])
        self.assertEqual(errors, [])
        receipt = rows[f['id']]
        self.assertIn('我们完成对照实验', receipt['quote'])
        self.assertNotIn('我完成示范', receipt['quote'])
        row['evidence_id'] = 'foreign-evidence'
        self.assertEqual(validate_evidence_batch(json.dumps({'items': [row]}), [unit])[0], {})

    def test_missing_original_evidence_is_blocked_without_paid_retry(self):
        report = self.report(1, 1)
        report['findings'][0]['start'] = None
        report['findings'][1]['figure_evidence']['reviewable'] = False
        provider, state = self.drive(report)
        self.assertEqual(len(provider.calls), 0)
        self.assertEqual(state['completion']['blocked'], 2)
        self.assertEqual(state['completion']['budget_stopped'], 0)
        self.assertTrue(all(f['review_blocked']['attempts'] == 0 for f in report['findings']))

    def test_blocked_cache_requires_same_source_and_exact_rule_evidence(self):
        prior = self.report(1)
        _, state = self.drive(prior, FakeProvider(invalid_text={'NP001-0001'}))
        current = self.report(1)
        self.assertEqual(reuse_review_blocks(current, prior), 1)
        provider, result = self.drive(current)
        self.assertEqual(len(provider.calls), 0)
        self.assertEqual(result['completion']['blocked'], 1)
        for field in ('source_sha256', 'scope'):
            changed = self.report(1)
            if field == 'source_sha256': changed[field] = 'other-source'
            else: changed['rules'][0]['scope'] = 'changed-scope'
            self.assertEqual(reuse_review_blocks(changed, prior), 0)

    def test_paid_new_evidence_response_recovers_exactly_without_any_requests(self):
        report = self.report(4)
        provider, state = self.drive(report)
        expected = deepcopy(report)
        for f in report['findings']:
            f.pop('semantic_review')
            f.pop('semantic_review_parts')
        original_cost = deepcopy(state['requests'])
        self.assertEqual(recover_saved_text(report, state, self.output), 4)
        self.assertEqual(state['requests'], original_cost)
        self.assertEqual(len(provider.calls), 2)
        self.assertTrue(all(f['semantic_review']['validated_quote'] for f in report['findings']))
        current = self.report(4)
        self.assertEqual(reuse_semantic_evidence(current, expected), 4)
        current['findings'][0]['text'] += 'changed'
        current['findings'][0].pop('semantic_review', None)
        current['findings'][0].pop('semantic_review_parts', None)
        self.assertEqual(reuse_semantic_evidence(current, expected), 0)

    def test_sensitive_old_prompt_suggestion_is_rechecked_but_not_issue_reused(self):
        report = self.report(1)
        f = report['findings'][0]
        f.update(text='我期望你扮演资深投资顾问。', start=0, end=1)
        prior = deepcopy(report)
        prior['findings'][0]['semantic_review'] = {'id': f['id'], 'decision': 'suggested_revision',
            'quote': f['text'], 'reason': '存在我字。', 'suggestion': '去掉我字。', 'validated_quote': True}
        self.assertEqual(reuse_semantic_evidence(report, prior), 0)
        prior['findings'][0]['semantic_review'].update(decision='not_issue', suggestion='', reason='提示词示范。')
        self.assertEqual(reuse_semantic_evidence(report, prior), 1)

    def test_budget_pause_before_output_repair_stays_unreviewed_not_blocked(self):
        report = self.report(4)
        _, state = self.drive(report, FakeProvider(invalid_text={'NP001-0002'}), self.state(2, 24000))
        self.assertEqual(state['completion']['reviewed'], 3)
        self.assertEqual(state['completion']['budget_stopped'], 1)
        self.assertEqual(state['completion']['blocked'], 0)
        self.assertNotIn('review_blocked', report['findings'][1])
        self.assertEqual(state['output_repair_history'], [])

    def test_large_paragraph_resume_reuses_paid_fragments_and_only_reviews_remainder(self):
        report = self.report()
        text = '这里是足够长的实验结论连续原文。' * 500
        f = {'id': 'NP013-long', 'rule_id': 'NP013', 'status': 'candidate', 'semantic_required': True,
            'text': text, 'start': 0, 'end': len(text), 'reason': '结论审阅', 'teacher_evidence': [],
            'context': [], 'section_complete': True}
        report['findings'].append(f)
        original = deepcopy(report)
        _, first_state = self.drive(report, state=self.state(2, 60000))
        self.assertEqual(first_state['completion']['status'], 'budget_remaining')
        self.assertEqual(len(report['findings'][0]['semantic_review_parts']), 4)
        self.assertNotIn('semantic_review', report['findings'][0])
        prior = deepcopy(report)
        self.assertEqual(reuse_semantic_evidence(original, prior), 0)
        self.assertEqual(len(original['findings'][0]['semantic_review_parts']), 4)
        provider, state = self.drive(original)
        packets = [json.loads(r['messages'][1]['content']) for r in provider.calls
                   if r['messages'][0]['content'] == EVIDENCE_REVIEW_SYSTEM]
        sent_ids = [r['id'] for packet in packets for r in packet['items']]
        self.assertTrue(all('#part-001' not in uid and '#part-002' not in uid and
                            '#part-003' not in uid and '#part-004' not in uid for uid in sent_ids))
        self.assertEqual(state['completion']['status'], 'completed')
        self.assertTrue(original['findings'][0]['semantic_review']['validated_quote'])

    def test_np001_cannot_invent_a_second_scope_gate_for_we(self):
        report = self.report(1)
        f = report['findings'][0]
        unit = build_semantic_units(f, report['rules'][0])[0]
        row = {'id': unit['id'], 'decision': 'needs_review',
            'evidence_id': unit['evidence_snippets'][0]['evidence_id'],
            'reason': '作者自述中的“我们”是否属禁用范围需人工确认。', 'suggestion': '核对教师规则。'}
        accepted, errors = validate_evidence_batch(json.dumps({'items': [row]}, ensure_ascii=False), [unit])
        self.assertEqual(accepted, {})
        self.assertIn('不能另造', errors[0]['error'])
        row['reason'] = '该片段可能是引用，缺少相邻段落不能确认作者用途。'
        accepted, errors = validate_evidence_batch(json.dumps({'items': [row]}, ensure_ascii=False), [unit])
        self.assertEqual(errors, [])
        self.assertEqual(accepted[unit['id']]['decision'], 'needs_review')

    def test_actual_prompt_role_and_wrong_scope_cached_rows_only_recheck_three_items(self):
        path = Path(__file__).resolve().parent/'data/new_papers/paper_20261009_132046_390353_e0a908c1/review_20261009_140658_807652_1b3d09/report.json'
        if not path.is_file():
            self.skipTest('Specific paid fixture is absent.')
        before = sha256(path.read_bytes()).hexdigest()
        prior = json.loads(path.read_text(encoding='utf-8'))
        report = deepcopy(prior)
        for f in report['findings']:
            f.pop('semantic_review', None)
            f.pop('semantic_review_parts', None)
        self.assertEqual(reuse_semantic_evidence(report, prior), 29)
        remaining = [f for f in report['findings'] if f['rule_id'] == 'NP001'
                     and not f.get('semantic_review', {}).get('validated_quote')]
        self.assertEqual({f['id'] for f in remaining}, {'NP001-0010', 'NP001-0011', 'NP001-0012'})
        rules = {r['id']: r for r in report['rules']}
        f = next(x for x in remaining if x['id'] == 'NP001-0011')
        unit = bind_unit_context(build_semantic_units(f, rules['NP001']), f, report)[0]
        packet = evidence_packet_item(unit, rules)
        self.assertEqual(packet['source_context']['in_table'], True)
        self.assertEqual(packet['source_context']['nearest_table_caption']['text'], '表4-2 CRISPE框架提示词 ')
        self.assertIn('场景（Context）', packet['source_context']['adjacent_paragraphs'][1]['text'])
        self.assertEqual(packet['evaluation_policy']['author_forms'], ['我', '我们'])
        author = next(x for x in remaining if x['id'] == 'NP001-0010')
        author_unit = bind_unit_context(build_semantic_units(author, rules['NP001']), author, report)[0]
        target = evidence_packet_item(author_unit, rules)['target_occurrence']
        self.assertEqual(target['start'], 63)
        self.assertEqual(target['text'], '我们')
        self.assertTrue(target['right_context'].startswith('将以进行不同提示词框架'))
        self.assertEqual(sha256(path.read_bytes()).hexdigest(), before)

    def test_prior_paid_evidence_contract_still_recovers_verbatim_valid_rows(self):
        report = self.report(2)
        _, state = self.drive(report)
        request_path = self.output/'request_02.json'
        request = json.loads(request_path.read_text(encoding='utf-8'))
        request['messages'][0]['content'] = EVIDENCE_REVIEW_SYSTEM_V2
        packet = json.loads(request['messages'][1]['content'])
        for item in packet['items']:
            item.pop('evaluation_policy', None)
            item.pop('target_occurrence', None)
            item['source_context'].pop('adjacent_paragraphs')
            item['source_context'].pop('nearest_table_caption')
        request['messages'][1]['content'] = json.dumps(packet, ensure_ascii=False)
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        for f in report['findings']:
            f.pop('semantic_review')
            f.pop('semantic_review_parts')
        self.assertEqual(recover_saved_text(report, state, self.output), 2)
        self.assertTrue(all(f['semantic_review']['validated_quote'] for f in report['findings']))


if __name__ == '__main__':
    unittest.main()
