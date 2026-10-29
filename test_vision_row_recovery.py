"""Keep valid paid image rows without relaxing provenance or paying again."""
import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from paper_vision_review import SYSTEM, VisionOutputError, recover_saved_vision, \
    reuse_vision_evidence, review_figures, validate_rows, validate_rows_batch


# Exact short-label lists from the paid 2026-10-09 request 02. A UI/table image
# naturally contains more labels than the prompt's desired brief sample.
LABELS = [
    ['后台管理系统', '* 用户名', '* 密码', '登录'],
    ['用户管理', '需求管理', '技能管理', '序号', '用户名', '用户类型', '微信ID', '创建时间', '更新时间', '操作'],
    ['信息', '用户名', '用户类型', '帮扶人群', '志愿者'],
    ['发布', '分类', '清洁', '描述', '垃圾处理', '需求名称', '倒垃圾', '地点', '鼓楼区', '需求图片', '发布'],
]


def model_rows():
    return [{'id': f'NP007-{number:04d}',
             'image_kind': 'table_screenshot' if number == 6 else 'ui_screenshot',
             'readable': True, 'decision': 'not_issue',
             'observations': ['字段标签与内容清晰', '文字可辨识', '界面布局无模糊'],
             'visible_text': list(labels), 'reason': '图内标签清晰，文字与界面内容均可正常读取。',
             'suggestion': ''} for number, labels in zip(range(5, 9), LABELS)]


def envelope(rows, *, wrapper=False):
    result = {'items': rows}
    if wrapper:
        result['type'] = 'json_object'
    return json.dumps(result, ensure_ascii=False)


class VisionRowTests(unittest.TestCase):
    def setUp(self):
        self.rows = model_rows()
        self.findings = [{'id': row['id'], 'rule_id': 'NP007'} for row in self.rows]

    def test_complete_ten_and_eleven_short_labels_are_valid_and_preserved(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                accepted, errors = validate_rows_batch(envelope(self.rows, wrapper=wrapped), self.findings)
                self.assertEqual(errors, [])
                self.assertEqual(len(accepted), 4)
                self.assertEqual([len(accepted[row['id']]['visible_text']) for row in self.rows], [4, 10, 5, 11])
                for row in self.rows:
                    self.assertEqual(accepted[row['id']]['visible_text'], row['visible_text'])
                    self.assertTrue(accepted[row['id']]['validated_image'])
                    self.assertFalse(accepted[row['id']]['human_confirmed'])
                self.assertEqual(validate_rows(envelope(self.rows, wrapper=wrapped), self.findings), accepted)

    def test_visible_label_count_and_length_limits_are_finite(self):
        row = dict(self.rows[0], visible_text=['字' * 80] * 32)
        result = validate_rows(envelope([row]), self.findings[:1])
        self.assertEqual(result[row['id']]['visible_text'], row['visible_text'])
        for labels in (['标签'] * 33, ['字' * 81]):
            with self.subTest(labels_count=len(labels)), self.assertRaises(VisionOutputError):
                validate_rows(envelope([dict(row, visible_text=labels)]), self.findings[:1])

    def test_observation_limits_remain_eight_by_one_hundred_sixty(self):
        row = dict(self.rows[0], observations=['字' * 160] * 8)
        self.assertTrue(validate_rows(envelope([row]), self.findings[:1])[row['id']]['validated_image'])
        for observations in (['观察'] * 9, ['字' * 161], []):
            with self.subTest(count=len(observations)), self.assertRaises(VisionOutputError):
                validate_rows(envelope([dict(row, observations=observations)]), self.findings[:1])

    def test_bad_labels_are_isolated_without_coercion_or_truncation(self):
        for labels in ([None], [7], [{}], [''], ['   '], '标签', ['字' * 81], ['标签'] * 33):
            with self.subTest(labels=labels):
                rows = deepcopy(self.rows)
                rows[1]['visible_text'] = labels
                original = deepcopy(rows)
                accepted, errors = validate_rows_batch(envelope(rows, wrapper=True), self.findings)
                self.assertEqual(set(accepted), {self.rows[index]['id'] for index in (0, 2, 3)})
                self.assertEqual(errors[0]['id'], self.rows[1]['id'])
                self.assertIn('visible_text', errors[0]['error'])
                self.assertEqual(rows, original)
                with self.assertRaises(VisionOutputError):
                    validate_rows(envelope(rows, wrapper=True), self.findings)

    def test_invalid_decision_or_fields_do_not_discard_other_ids(self):
        for invalid in ('decision', 'missing', 'extra', 'conflict'):
            rows = deepcopy(self.rows)
            if invalid == 'decision': rows[1]['decision'] = []
            elif invalid == 'missing': rows[1].pop('reason')
            elif invalid == 'extra': rows[1]['command'] = 'untrusted'
            else: rows[1]['suggestion'] = '放大'
            with self.subTest(invalid=invalid):
                accepted, errors = validate_rows_batch(envelope(rows), self.findings)
                self.assertEqual(len(accepted), 3)
                self.assertEqual(errors[0]['id'], self.rows[1]['id'])
                with self.assertRaises(VisionOutputError):
                    validate_rows(envelope(rows), self.findings)

    def test_unknown_duplicate_missing_or_unbindable_id_blocks_whole_batch(self):
        cases = [self.rows[:-1], self.rows + [self.rows[0]],
                 [dict(self.rows[0], id='foreign')] + self.rows[1:],
                 [dict(self.rows[0], id=None)] + self.rows[1:], [None] + self.rows[1:]]
        for rows in cases:
            for validator in (validate_rows, validate_rows_batch):
                with self.subTest(rows=rows, validator=validator.__name__), self.assertRaises(VisionOutputError):
                    validator(envelope(rows), self.findings)

    def test_top_level_and_duplicate_json_fields_still_block_whole_batch(self):
        contents = [json.dumps({'items': self.rows, 'extra': False}),
                    json.dumps({'type': 'other', 'items': self.rows}),
                    '{"items":[],"items":' + json.dumps(self.rows) + '}',
                    envelope(self.rows).replace('"id": "NP007-0005"', '"id":"NP007-0005","id":"NP007-0005"')]
        for content in contents:
            for validator in (validate_rows, validate_rows_batch):
                with self.subTest(validator=validator.__name__), self.assertRaises(VisionOutputError):
                    validator(content, self.findings)


class VisionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.rows = model_rows()
        findings = []
        for row in self.rows:
            path = self.folder/(row['id'] + '.png')
            path.write_bytes(('fixed pixels:' + row['id']).encode('utf-8'))
            findings.append({'id': row['id'], 'rule_id': 'NP007', 'text': '图题 ' + row['id'],
                             'status': 'needs_review', 'teacher_evidence': [],
                             'figure_evidence': {'source_sha256': 'same-source', 'reviewable': True,
                               'preview_path': str(path), 'preview_sha256': sha256(path.read_bytes()).hexdigest(),
                               'caption': {'text': '图题 ' + row['id']}, 'physical_page': 3,
                               'pdf_sha256': 'old-container', 'width_cm': 12, 'height_cm': 8}})
        self.report = {'source_sha256': 'same-source', 'findings': findings,
                       'rules': [{'id': 'NP007', 'scope': '图片可读性'}], 'limitations': [],
                       'coverage': [{'rule_id': 'NP007', 'status': 'needs_review'}]}
        self.state = {'source_sha256': 'same-source', 'request_attempts': 2, 'dispatched_requests': 2,
                      'known_total_tokens': 1234, 'unknown_cost_attempts': 0, 'reserved_token_bound': 10000,
                      'requests': [{'number': 2, 'finish_reason': 'stop', 'usage': {'total_tokens': 1234}}]}

    def saved_reply(self, rows):
        content = []
        for finding in self.report['findings']:
            content += [{'type': 'text', 'text': json.dumps({'id': finding['id']})},
                        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                           base64.b64encode(Path(finding['figure_evidence']['preview_path']).read_bytes()).decode()}}]
        request = {'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': content}]}
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': envelope(rows)}}]}
        for name, value in [('request_02.json', request), ('response_02.json', response)]:
            (self.folder/name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def cost_fields(self, state):
        return {key: deepcopy(state[key]) for key in ('request_attempts', 'dispatched_requests',
                'known_total_tokens', 'unknown_cost_attempts', 'reserved_token_bound', 'requests')}

    def test_saved_reply_keeps_three_valid_rows_and_records_one_error_without_spending(self):
        rows = deepcopy(self.rows)
        rows[1]['visible_text'] = ['字' * 81]
        self.saved_reply(rows)
        before_cost = self.cost_fields(self.state)
        paths = [self.folder/'request_02.json', self.folder/'response_02.json']
        before_files = {path: sha256(path.read_bytes()).hexdigest() for path in paths}
        with patch('new_paper_agent.BoundedClient.request', side_effect=AssertionError('No model calls')) as request:
            self.assertEqual(recover_saved_vision(self.report, self.state, self.folder), 3)
            self.assertEqual(recover_saved_vision(self.report, self.state, self.folder), 0)
        request.assert_not_called()
        self.assertEqual(self.cost_fields(self.state), before_cost)
        self.assertEqual(before_files, {path: sha256(path.read_bytes()).hexdigest() for path in paths})
        self.assertEqual(self.state['vision_reviewed'], 3)
        self.assertEqual(self.state['vision_unreviewed'], 1)
        self.assertNotIn('vision_review', self.report['findings'][1])
        self.assertEqual(len(self.state['phase_errors']), 1)
        error = self.state['phase_errors'][0]
        self.assertEqual(error['request_number'], 2)
        self.assertEqual(error['item_errors'][0]['id'], self.rows[1]['id'])
        self.assertIn('visible_text', error['item_errors'][0]['error'])

    def test_same_paid_pixels_reuse_complete_labels_without_calling_model(self):
        self.saved_reply(self.rows)
        self.assertEqual(recover_saved_vision(self.report, self.state, self.folder), 4)
        prior = deepcopy(self.report)
        current = deepcopy(self.report)
        for finding in current['findings']:
            finding.pop('vision_review')
            finding['figure_evidence']['pdf_sha256'] = 'new-container'
        with patch('new_paper_agent.BoundedClient.request', side_effect=AssertionError('No model calls')) as request:
            self.assertEqual(reuse_vision_evidence(current, prior), 4)
        request.assert_not_called()
        self.assertEqual([f['vision_review']['visible_text'] for f in current['findings']], LABELS)
        self.assertTrue(all(f['vision_review']['reuse_kind'] == 'pixel-match' for f in current['findings']))

    def test_source_and_pixel_mismatches_block_recovery(self):
        for scenario in ('report-source', 'finding-source', 'pixel-file', 'sent-pixels'):
            with self.subTest(scenario=scenario):
                self.saved_reply(self.rows)
                report, state = deepcopy(self.report), deepcopy(self.state)
                if scenario == 'report-source': report['source_sha256'] = 'foreign'
                elif scenario == 'finding-source': report['findings'][1]['figure_evidence']['source_sha256'] = 'foreign'
                elif scenario == 'pixel-file':
                    changed = self.folder/'changed.png'
                    changed.write_bytes(b'changed bytes')
                    report['findings'][1]['figure_evidence']['preview_path'] = str(changed)
                else:
                    request_path = self.folder/'request_02.json'
                    request = json.loads(request_path.read_text(encoding='utf-8'))
                    request['messages'][1]['content'][1]['image_url']['url'] = 'data:image/png;base64,' + base64.b64encode(b'foreign').decode()
                    request_path.write_text(json.dumps(request), encoding='utf-8')
                self.assertEqual(recover_saved_vision(report, state, self.folder), 0)
                self.assertTrue(all('vision_review' not in f for f in report['findings']))
                self.assertEqual(self.cost_fields(state), self.cost_fields(self.state))

    def budget(self, state, invalid_ids):
        available = {row['id']: row for row in self.rows}
        calls = []
        def request(messages, **options):
            ids = [json.loads(block['text'])['id'] for block in messages[1]['content'] if block['type'] == 'text']
            calls.append(ids)
            state['request_attempts'] += 1
            state['dispatched_requests'] += 1
            state['known_total_tokens'] += 100
            rows = [deepcopy(available[item_id]) for item_id in ids]
            for row in rows:
                if row['id'] in invalid_ids:
                    row['visible_text'] = ['标签'] * 33
            return SimpleNamespace(finish_reason='stop', message=SimpleNamespace(tool_calls=None, content=envelope(rows)))
        return SimpleNamespace(prepare=lambda *args, **kwargs: ({}, 1),
                               can_request=lambda upper: True, request=request), calls

    def test_live_partial_batch_advances_cursor_and_never_repeats_invalid_id(self):
        extra = deepcopy(self.report['findings'][-1])
        extra['id'] = 'NP007-0009'
        self.report['findings'].append(extra)
        self.rows.append(dict(deepcopy(self.rows[-1]), id=extra['id']))
        state = {'request_attempts': 0, 'dispatched_requests': 0, 'known_total_tokens': 0}
        budget, calls = self.budget(state, {self.rows[1]['id']})
        review_figures(self.report, budget, state, self.folder, max_batches=5)
        self.assertEqual(calls, [[row['id'] for row in self.rows[:4]], [extra['id']]])
        self.assertEqual(state['request_attempts'], 2)
        self.assertEqual(state['known_total_tokens'], 200)
        self.assertEqual(state['vision_reviewed'], 4)
        self.assertEqual(state['vision_unreviewed'], 1)
        self.assertNotIn('vision_review', self.report['findings'][1])
        self.assertEqual(state['phase_errors'][0]['item_errors'][0]['id'], self.rows[1]['id'])
        self.assertEqual(json.loads((self.folder/'run.json').read_text(encoding='utf-8')), state)

    def test_live_entire_invalid_batch_is_not_automatically_charged_again(self):
        state = {'request_attempts': 0, 'dispatched_requests': 0, 'known_total_tokens': 0}
        budget, calls = self.budget(state, {row['id'] for row in self.rows})
        review_figures(self.report, budget, state, self.folder, max_batches=5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(state['known_total_tokens'], 100)
        self.assertEqual(state['vision_reviewed'], 0)
        self.assertEqual(state['vision_unreviewed'], 4)
        self.assertEqual(len(state['phase_errors'][0]['item_errors']), 4)

    def test_real_four_paid_rows_recover_offline_without_changing_source_records(self):
        folder = Path(__file__).resolve().parent/'data/new_papers/paper_20261009_124544_120262_1859863b/review_20261009_130033_697426_91536d'
        if not (folder/'response_02.json').is_file():
            self.skipTest('Paid response fixture is not present in this checkout.')
        paths = [folder/name for name in ('request_02.json', 'response_02.json', 'run.json', 'report.json')]
        before = {path: sha256(path.read_bytes()).hexdigest() for path in paths}
        response = json.loads((folder/'response_02.json').read_text(encoding='utf-8'))
        paid_rows = json.loads(response['choices'][0]['message']['content'])['items']
        self.assertEqual([row['visible_text'] for row in paid_rows], LABELS)
        ids = {row['id'] for row in paid_rows}
        report = json.loads((folder/'report.json').read_text(encoding='utf-8'))
        for finding in report['findings']:
            if finding['id'] in ids:
                finding.pop('vision_review', None)
        state = json.loads((folder/'run.json').read_text(encoding='utf-8'))
        state['requests'] = [attempt for attempt in state['requests'] if attempt['number'] == 2]
        costs = self.cost_fields(state)
        with patch('new_paper_agent.BoundedClient.request', side_effect=AssertionError('No model calls')) as request:
            self.assertEqual(recover_saved_vision(report, state, folder), 4)
        request.assert_not_called()
        self.assertEqual(self.cost_fields(state), costs)
        recovered = {f['id']: f['vision_review'] for f in report['findings'] if f['id'] in ids}
        self.assertEqual([recovered[row['id']]['visible_text'] for row in paid_rows], LABELS)
        self.assertTrue(all(row['recovered_request_number'] == 2 for row in recovered.values()))
        self.assertEqual(before, {path: sha256(path.read_bytes()).hexdigest() for path in paths})


if __name__ == '__main__':
    unittest.main()
