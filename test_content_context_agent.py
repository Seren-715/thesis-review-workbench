from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from content_context_agent import (ContextReviewQueue, CONTENT_POLICY, content_context_key,
                                   reuse_content_evidence, validate_content_receipt,
                                   ReadContentScopeTool)
import asyncio


def sample(lengths=(80, 80), complete=True):
    chunks = [{'id': f'C{i}', 'paragraph_index': i + 43, 'start': 0, 'end': n,
               'text': ('背景' if i == 0 else '功能技术效果') * (n // (2 if i == 0 else 6)) + '。',
               'location': {'label': f'摘要 · 第{i+1}个文字段落'}, 'role': 'body'}
              for i, n in enumerate(lengths)]
    for c in chunks:
        c['end'] = len(c['text'])
    f = {'id': 'NP011-0001', 'rule_id': 'NP011', 'status': 'candidate',
         'text': chunks[0]['text'], 'semantic_required': True,
         'check_dimensions': ['背景', '功能'], 'teacher_evidence': [],
         'content_scope': {'version': 'complete_content_scope_v1', 'source_sha256': 'a' * 64,
                           'chunks': chunks, 'complete': complete,
                           'paragraph_indices': [c['paragraph_index'] for c in chunks],
                           'total_paragraphs': len(chunks),
                           'total_characters': sum(len(c['text']) for c in chunks),
                           'headings': ['摘要']}}
    rule = {'id': 'NP011', 'scope': '摘要全部内容', 'teacher_evidence': []}
    return {'source_sha256': 'a' * 64, 'findings': [f], 'rules': [rule], 'limitations': [], 'coverage': []}, f, rule


def valid_response(packet):
    if packet['mode'] == 'read':
        return {'batch_id': packet['batch_id'],
                'observations': [{'dimension': d, 'evidence_ids': [packet['evidence'][-1]['evidence_id']],
                                  'note': '本批原文已读取，记录具体说明。'} for d in packet['check_dimensions']]}
    # Deliberately cite a later paragraph; the original anchor cannot validate it.
    eid = packet['evidence'][-1]['evidence_id']
    return {'decision': 'not_issue', 'evidence_id': eid, 'reason': '已结合所有提供段落，原文有对应说明。',
            'suggestion': '', 'dimension_checks': [{'dimension': d, 'decision': 'satisfied',
                'evidence_ids': [eid], 'reason': '完整范围包含该项说明。'} for d in packet['check_dimensions']]}


class FakeBudget:
    def __init__(self, state, responder=valid_response, allow=True):
        self.state, self.responder, self.allow = state, responder, allow
        self.packets = []

    def prepare(self, messages, **kwargs):
        return {}, 1

    def can_request(self, upper):
        return self.allow

    def request(self, messages, **kwargs):
        packet = json.loads(messages[-1]['content'])
        self.packets.append(packet)
        self.state['request_attempts'] = self.state.get('request_attempts', 0) + 1
        result = self.responder(packet)
        return SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            tool_calls=None, content=json.dumps(result, ensure_ascii=False)))


class ContentQueueTests(unittest.TestCase):
    def test_complete_reads_second_paragraph_and_cites_it(self):
        report, f, rule = sample()
        state = {}
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state)
            self.assertTrue(queue.step(budget))
            self.assertFalse(queue.has_pending())
            self.assertEqual(len(budget.packets), 1)
            self.assertEqual(budget.packets[0]['chunk_count'], 2)
            self.assertNotIn('chunks', budget.packets[0])
            self.assertTrue(all(e['evidence_id'].startswith('E') for e in budget.packets[0]['evidence']))
            receipt = validate_content_receipt(f['semantic_review'], f, rule)
            self.assertEqual(receipt['quote_paragraph_index'], 44)
            self.assertIn(receipt['quote'], f['content_scope']['chunks'][1]['text'])
            self.assertEqual(state['tool_calls'][0]['name'], 'read_verified_content_scope')
            self.assertTrue((Path(directory) / 'report.json').is_file())

    def test_large_scope_never_finalizes_early_and_reads_all_chunks(self):
        report, f, _ = sample((1000, 1000, 1000, 1000, 1000))
        state = {}
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state)
            self.assertTrue(queue.step(budget))
            self.assertNotIn('semantic_review', f)
            self.assertEqual(len(budget.packets), 1)
            while queue.has_pending():
                self.assertTrue(queue.step(budget))
            self.assertEqual([p['mode'] for p in budget.packets], ['read', 'read', 'synthesize'])
            self.assertEqual(set(f['content_read_progress']['read_chunk_ids']), {f'C{i}' for i in range(5)})

    def test_budget_stop_keeps_unread_scope_without_manual_failure(self):
        report, f, _ = sample()
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            self.assertFalse(queue.step(FakeBudget({}, allow=False)))
            self.assertTrue(queue.has_pending())
            self.assertNotIn('semantic_review', f)
            self.assertNotIn('review_blocked', f)

    def test_context_deferral_once_repaired_with_supplied_second_paragraph(self):
        report, f, _ = sample()
        state = {}
        def responder(packet):
            row = valid_response(packet)
            if not packet.get('output_repair'):
                row['reason'] = '需要结合摘要第二段判断。'
            return row
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state, responder)
            queue.step(budget)
            self.assertNotIn('semantic_review', f)
            queue.step(budget)
            self.assertEqual(len(budget.packets), 2)
            self.assertTrue(f['semantic_review']['validated_scope'])

    def test_invalid_evidence_repaired_once_then_terminal(self):
        report, f, _ = sample()
        state = {}
        def bad(packet):
            return dict(valid_response(packet), evidence_id='fabricated')
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state, bad)
            queue.step(budget); queue.step(budget)
            self.assertFalse(queue.has_pending())
            self.assertEqual(len(budget.packets), 2)
            self.assertEqual(f['review_blocked']['code'], 'output_validation_failed')
            self.assertNotIn('semantic_review', f)

    def test_receipt_reuse_requires_source_scope_and_rule(self):
        report, f, rule = sample()
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            queue.step(FakeBudget({}))
        before = deepcopy(f)
        clean = deepcopy(f); clean.pop('semantic_review')
        self.assertTrue(reuse_content_evidence(clean, before, rule))
        clean['content_scope']['chunks'][1]['text'] += '新增'
        clean.pop('semantic_review')
        self.assertFalse(reuse_content_evidence(clean, before, rule))
        self.assertNotIn('semantic_review', clean)
        self.assertNotEqual(content_context_key(clean, rule), content_context_key(before, rule))
        changed_rule = dict(rule, scope='变更教师要求')
        self.assertNotEqual(content_context_key(before, rule), content_context_key(before, changed_rule))

    def test_partial_checkpoint_resume_does_not_reread(self):
        report, f, rule = sample((1000,) * 5)
        with TemporaryDirectory() as directory:
            state = {}
            queue = ContextReviewQueue(report, state, directory)
            queue.step(FakeBudget(state))
            before = deepcopy(f)
            fresh = deepcopy(f); fresh.pop('content_read_progress')
            self.assertTrue(reuse_content_evidence(fresh, before, rule))
            report['findings'] = [fresh]
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state)
            queue.step(budget)
            self.assertEqual(budget.packets[0]['chunk_count'], 2)
            self.assertEqual(fresh['content_read_progress']['read_batches'][-1]['chunk_ids'], ['C3', 'C4'])

    def test_registry_tool_rejects_foreign_finding_and_chunk(self):
        report, _, _ = sample()
        tool = ReadContentScopeTool(report)
        with self.assertRaises(ValueError):
            asyncio.run(tool.execute('OTHER', ['C0']))
        with self.assertRaises(ValueError):
            asyncio.run(tool.execute('NP011-0001', ['D:/some/file.docx']))

    def test_queue_works_inside_existing_async_dispatcher(self):
        report, f, _ = sample()
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            async def dispatch():
                return queue.step(FakeBudget({}))
            self.assertTrue(asyncio.run(dispatch()))
            self.assertTrue(f['semantic_review']['validated_quote'])

    def test_scope_source_hash_mismatch_prevents_review(self):
        report, f, _ = sample()
        f['content_scope']['source_sha256'] = 'b' * 64
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            self.assertFalse(queue.has_pending())
            self.assertEqual(f['review_blocked']['code'], 'missing_content_scope_evidence')

    def test_invalid_list_fields_use_finite_repair_instead_of_crashing(self):
        for field in ('decision', 'evidence_id'):
            with self.subTest(field=field):
                report, f, _ = sample()
                state = {}
                with TemporaryDirectory() as directory:
                    queue = ContextReviewQueue(report, state, directory)
                    def responder(packet):
                        return dict(valid_response(packet), **{field: ['invalid']})
                    budget = FakeBudget(state, responder)
                    queue.step(budget); queue.step(budget)
                    self.assertEqual(len(budget.packets), 2)
                    self.assertIn('review_blocked', f)

    def test_empty_observations_still_read_code_batches(self):
        report, f, _ = sample((1000,) * 5)
        state = {}
        def responder(packet):
            if packet['mode'] == 'read':
                return {'batch_id': packet['batch_id'], 'observations': []}
            return valid_response(packet)
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state, responder)
            while queue.has_pending():
                self.assertTrue(queue.step(budget))
            self.assertEqual(len(budget.packets), 3)
            self.assertTrue(f['semantic_review']['validated_scope'])

    def test_truncated_provider_output_receives_only_one_repair(self):
        from paper_vision_review import VisionOutputError
        report, f, _ = sample()
        state = {}
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state)
            with patch.object(budget, 'request', side_effect=VisionOutputError('length')):
                queue.step(budget); queue.step(budget)
            self.assertFalse(queue.has_pending())
            self.assertIn('review_blocked', f)

    def test_invalid_chunk_offsets_are_not_reviewed(self):
        report, f, _ = sample()
        f['content_scope']['chunks'][1]['end'] += 1
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            self.assertFalse(queue.has_pending())
            self.assertIn('review_blocked', f)

    def test_corrupt_checkpoint_cannot_claim_unread_evidence(self):
        report, f, rule = sample((1000,) * 5)
        with TemporaryDirectory() as directory:
            state = {}
            queue = ContextReviewQueue(report, state, directory)
            queue.step(FakeBudget(state))
            before = deepcopy(f)
            # Correct context hash, but proof claims evidence was read without
            # the chunk in the persisted reading manifest.
            before['content_read_progress']['read_chunk_ids'] = []
            clean = deepcopy(f); clean.pop('content_read_progress')
            self.assertFalse(reuse_content_evidence(clean, before, rule))
            self.assertNotIn('content_read_progress', clean)

    def test_corrupt_pending_task_is_rejected_on_resume(self):
        report, f, rule = sample()
        before = deepcopy(f)
        before['content_read_progress'] = {'scope_context_key': content_context_key(f, rule),
            'read_chunk_ids': [], 'notes': [],
            'pending_task': {'mode': 'read', 'chunk_ids': [['invalid']], 'repair': True}}
        self.assertFalse(reuse_content_evidence(f, before, rule))

    def test_read_ack_must_match_bound_batch_id(self):
        report, f, _ = sample((1000,) * 5)
        state = {}
        def responder(packet):
            return {'batch_id': 'OTHER-BATCH', 'observations': []}
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state, responder)
            queue.step(budget); queue.step(budget)
            self.assertFalse(queue.has_pending())
            self.assertEqual(f['content_read_progress']['read_chunk_ids'], [])
            self.assertIn('review_blocked', f)

    def test_wire_aliases_keep_canonical_receipts_and_scope_keys(self):
        report, f, rule = sample()
        before_key = content_context_key(f, rule)
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            budget = FakeBudget({})
            queue.step(budget)
        receipt = f['semantic_review']
        self.assertTrue(receipt['evidence_id'].startswith('C1:E'))
        self.assertTrue(all(e.startswith('C1:E') for d in receipt['dimension_checks'] for e in d['evidence_ids']))
        self.assertEqual(before_key, content_context_key(f, rule))
        self.assertEqual(validate_content_receipt(receipt, f, rule)['context_key'], before_key)

    def test_packet_includes_later_distinct_teacher_requirements_without_truncation(self):
        report, f, rule = sample()
        later = '后续要求' * 110
        rule['teacher_evidence'] = [{'quote':'第一项要求'}, {'quote':'第一项要求'},
                                   {'quote':'第二项要求'}, {'quote':later}]
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            packet, _ = queue._packet(f, queue._task(f))
        self.assertEqual(packet['teacher_quotes'], ['第一项要求','第二项要求',later])

    def test_many_short_table_cells_are_finite_batches_and_fully_read(self):
        from content_context_agent import MAX_BATCH_EVIDENCE
        report, f, _ = sample((6,) * 317)
        for c in f['content_scope']['chunks']:
            c['role'] = 'table_cell'
        state = {}
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, state, directory)
            budget = FakeBudget(state)
            while queue.has_pending():
                self.assertTrue(queue.step(budget))
            self.assertEqual(len(budget.packets), 8)
            self.assertTrue(all(len(p['evidence']) <= MAX_BATCH_EVIDENCE
                                for p in budget.packets if p['mode']=='read'))
            self.assertEqual(budget.packets[-1]['mode'],'synthesize')
            self.assertEqual(len(f['content_read_progress']['read_chunk_ids']),317)
            self.assertTrue(f['semantic_review']['validated_scope'])

    def test_complete_text_scope_does_not_claim_unread_images_were_reviewed(self):
        report, f, _ = sample()
        f['content_scope'].update(coverage_basis='visible_body_and_native_table_text',
                                 unread_object_types=['image_text','embedded_ole'])
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            packet, _ = queue._packet(f, queue._task(f))
        self.assertEqual(packet['chapter_scope']['unread_object_types'], ['image_text','embedded_ole'])
        self.assertEqual(packet['chapter_scope']['coverage_basis'],'visible_body_and_native_table_text')

    def test_wire_packet_substantially_reduces_metadata_bytes(self):
        report, f, _ = sample((30,) * 110)
        for c in f['content_scope']['chunks']:
            c['id'] = f"implementation-p{c['paragraph_index']}-0-{len(c['text'])}"
        from content_context_agent import _evidence
        with TemporaryDirectory() as directory:
            queue = ContextReviewQueue(report, {}, directory)
            task = queue._task(f)
            packet, _ = queue._packet(f, task)
        selected = [c for c in f['content_scope']['chunks'] if c['id'] in task['chunk_ids']]
        previous_wire = deepcopy(packet)
        previous_wire.pop('chunk_count')
        previous_wire['chunks'] = [{k: c.get(k) for k in ('id', 'paragraph_index', 'start', 'end', 'role')} for c in selected]
        previous_wire['evidence'] = [{k: e.get(k) for k in ('evidence_id', 'quote', 'paragraph_index', 'chunk_id', 'source_start')}
                                     for e in _evidence(selected)]
        compact_bytes = len(json.dumps(packet, ensure_ascii=False).encode('utf-8'))
        previous_bytes = len(json.dumps(previous_wire, ensure_ascii=False).encode('utf-8'))
        self.assertLess(compact_bytes, previous_bytes * 0.6)


if __name__ == '__main__':
    unittest.main()
