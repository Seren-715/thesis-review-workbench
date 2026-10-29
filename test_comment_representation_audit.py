"""Exercise recovered real comments and fail-closed negative representation cases."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from comment_representation_audit import audit_parser_comment_representation
from docx_comment_reader import extract_docx

ROOT = Path(__file__).parent / 'data/thesis_workflow/exports'


class CommentRepresentationTests(unittest.TestCase):
    def load(self, number):
        candidates = list(ROOT.glob(f'*/job_20261008_184822_959306_264c55/*r{number}*_conversion.json'))
        if not candidates:
            self.skipTest('Preserved real conversion fixture not present')
        audit = json.loads(candidates[0].read_text(encoding='utf-8-sig'))
        path = Path(audit['derived']['path'])
        extraction = extract_docx(path, {'sha256': audit['derived']['sha256']})
        return audit, extraction, path

    def test_real_terminal_paragraph_mapping_retains_exact_xml_text(self):
        audit, extraction, path = self.load(216)
        before = deepcopy(extraction['comments'])
        result = audit_parser_comment_representation(audit, extraction, path)
        transformed = [r for r in result['comments'] if r['text_condition'] != 'exact']
        self.assertEqual({r['comment_id'] for r in transformed}, {'15', '69'})
        self.assertTrue(all(r['raw_xml_text'].endswith('\n') and not r['normalized_word_range_text'].endswith('\n') for r in transformed))
        self.assertEqual(extraction['comments'], before)

    def test_real_image_mapping_verifies_packaged_asset_bytes(self):
        audit, extraction, path = self.load(356)
        result = audit_parser_comment_representation(audit, extraction, path)
        transformed = [r for r in result['comments'] if r['text_condition'] != 'exact']
        self.assertEqual(len(transformed), 1)
        self.assertEqual(transformed[0]['comment_id'], '2')
        self.assertEqual(transformed[0]['normalized_word_range_text'].count('\x01'), 1)
        self.assertTrue(transformed[0]['media'][0]['content_verified'])
        self.assertFalse(transformed[0]['media'][0]['visual_semantics_verified'])

    def test_unverified_body_gate_rejected_without_stripping(self):
        audit, extraction, path = self.load(216)
        audit['body_text_hash_equal'] = False
        with self.assertRaisesRegex(ValueError, 'preservation gates'):
            audit_parser_comment_representation(audit, extraction, path)

    def test_missing_or_tampered_image_asset_rejected(self):
        audit, extraction, path = self.load(356)
        media = next(r['comment']['media'][0] for r in extraction['comments'] if r['comment']['id'] == '2')
        extraction['assets'][media['export_path']] = b'changed image'
        with self.assertRaisesRegex(ValueError, 'asset mismatch'):
            audit_parser_comment_representation(audit, extraction, path)

    def test_external_relationship_metadata_rejected(self):
        audit, extraction, path = self.load(356)
        media = next(r['comment']['media'][0] for r in extraction['comments'] if r['comment']['id'] == '2')
        media['external'] = True
        with self.assertRaisesRegex(ValueError, 'not verified as embedded'):
            audit_parser_comment_representation(audit, extraction, path)

    def test_multiple_word_placeholders_not_silently_removed(self):
        audit, extraction, path = self.load(356)
        for rows in (audit['comments_before'], audit['comments_after']):
            row = next(r for r in rows if '字数不足' in r['range_text'])
            row['range_text'] = row['range_text'].replace('\x01', '\x01\x01')
        with self.assertRaisesRegex(ValueError, 'unexplained'):
            audit_parser_comment_representation(audit, extraction, path)

    def test_real_word_before_after_change_is_not_representation_issue(self):
        audit, extraction, path = self.load(216)
        audit['comments_after'][0]['scope_text'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'before/after'):
            audit_parser_comment_representation(audit, extraction, path)

    def test_unexplained_text_delta_rejected_even_if_flags_true(self):
        audit, extraction, path = self.load(216)
        for rows in (audit['comments_before'], audit['comments_after']):
            row = next(r for r in rows if '不要出现我们' in r['range_text'])
            row['range_text'] = row['range_text'].replace('我们', '其他内容')
        with self.assertRaisesRegex(ValueError, 'unexplained'):
            audit_parser_comment_representation(audit, extraction, path)


if __name__ == '__main__':
    unittest.main()
