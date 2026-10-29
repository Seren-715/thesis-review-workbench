import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_teacher_rule_library import FakeClient, pending_library
from teacher_rule_library import classify_library, classification_contract


class ContextualClassificationTests(unittest.TestCase):
    def corpus(self):
        library = pending_library(5)
        for c in library['comments']:
            c.update(status='semantic_review_required',
                     anchor={'status': 'complete', 'context_paragraphs': [{'index': 2, 'text': '原文的下一段说明'}]})
        return library

    def test_old_classification_does_not_repeat_uncertain_receipts(self):
        with TemporaryDirectory() as temp:
            client = FakeClient()
            result = classify_library(self.corpus(), temp, client)
            self.assertEqual(client.calls, 0)
            self.assertEqual(result['summary']['needs_semantic_review_comments'], 5)

    def test_explicit_context_mode_retains_uncertainty_and_has_finite_batches(self):
        with TemporaryDirectory() as temp:
            client = FakeClient()
            result = classify_library(self.corpus(), temp, client, review_existing=True, max_requests=1)
            run = result['classification_run']
            self.assertEqual(client.calls, 1)
            self.assertEqual(len(run['classified_comment_ids']), 4)
            self.assertEqual(len(run['remaining_comment_ids']), 1)
            self.assertEqual(len(run['still_uncertain_comment_ids']), 5)
            request = json.loads((Path(temp)/'request_001.json').read_text(encoding='utf8'))
            item = json.loads(request['messages'][1]['content'])['comments'][0]
            self.assertEqual(item['context'][0]['text'], '原文的下一段说明')
            self.assertEqual(item['anchor_status'], 'complete')
            self.assertFalse(result['rules'])

    def test_known_names_are_masked_and_restored_before_verbatim_validation(self):
        with TemporaryDirectory() as temp:
            library = self.corpus()
            library['comments'][0]['text'] = '张三需要进一步说明'
            result = classify_library(library, temp, FakeClient(), review_existing=True, max_requests=1)
            request = (Path(temp)/'request_001.json').read_text(encoding='utf8')
            self.assertNotIn('张三', request)
            self.assertEqual(result['comments'][0]['classification_quote'], '张三需要进一步说明')

    def test_previously_rechecked_ids_are_not_dispatched_again(self):
        with TemporaryDirectory() as temp:
            client = FakeClient()
            result = classify_library(self.corpus(), temp, client, review_existing=True,
                                      reviewed_comment_ids=['0', '1', '2', '3', '4'])
            self.assertEqual(client.calls, 0)
            self.assertEqual(result['classification_run']['selected_comment_ids'], [])

    def test_abnormal_response_does_not_erase_previous_classification(self):
        with TemporaryDirectory() as temp:
            library = self.corpus()
            result = classify_library(library, temp, FakeClient('bad_quote'), review_existing=True)
            self.assertEqual(result['classification_run']['status'], 'failed_no_retry')
            self.assertEqual(result['comments'], library['comments'])

    def test_repeat_contract_changes_when_source_context_or_model_changes(self):
        library = self.corpus()
        before = classification_contract(library)
        library['comments'][0]['anchor']['context_paragraphs'][0]['text'] = '新增的证据'
        self.assertNotEqual(before, classification_contract(library))
        self.assertNotEqual(classification_contract(library), classification_contract(library, 'other-model'))

    def test_oversized_batch_shrinks_without_omitting_unprocessed_comments(self):
        library = pending_library(8)
        for comment in library['comments']:
            comment['anchor_text'] = '原文语境' * 225
        with TemporaryDirectory() as temp:
            client = FakeClient()
            result = classify_library(library, temp, client, max_reserved_tokens=10000)
            run = result['classification_run']
            self.assertGreater(client.calls, 0)
            self.assertLess(len(run['classified_comment_ids']), 8)
            self.assertEqual(set(run['classified_comment_ids']) | set(run['remaining_comment_ids']),
                             {c['id'] for c in library['comments']})
            self.assertFalse(set(run['classified_comment_ids']) & set(run['remaining_comment_ids']))
            self.assertLessEqual(run['reserved_token_bound'], 10000)


if __name__ == '__main__':
    unittest.main()
