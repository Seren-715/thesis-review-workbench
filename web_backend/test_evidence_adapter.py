"""Evidence/UI contract checks against the accepted local experiment."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_backend.evidence_adapter import Assets, build_workspace
from cross_student_rule_pilot import digest


class EvidenceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace, cls.assets = build_workspace()

    def test_actual_counts_and_review_origins(self):
        w = self.workspace
        first = [f for f in w['findings'] if f['family'] == 'first_person']
        self.assertEqual(len(first), 30)
        self.assertEqual(len({(f['student_id'], f['location']['paragraph_index']) for f in first}), 23)
        self.assertEqual(sum(f['review_origin'] == 'user' for f in first), 3)
        self.assertEqual(sum(f['review_origin'] == 'codex' for f in first), 27)
        self.assertEqual(len({f['id'] for f in w['findings']}), len(w['findings']))
        self.assertEqual([s['counts']['first_person_occurrences'] for s in w['students']], [11, 1, 18])

    def test_unicode_highlights_are_exact_and_source_bound(self):
        for f in self.workspace['findings']:
            for h in f['excerpt']['highlights']:
                text = f['excerpt']['text'][h['start']:h['end']]
                self.assertIn(text, ['我', '我们', '我的'])
            for t in f['teacher_evidence']:
                self.assertNotEqual(t['source']['revision'], f['location']['revision'])
                self.assertNotEqual(self.assets[t['source']['asset_id']]['sha256'], f['source']['sha256'])
                if f['family'] == 'first_person':
                    self.assertEqual(t['relationship'], 'policy_support')

    def test_partial_pending_and_visual_receipts(self):
        findings = self.workspace['findings']
        tables = [f for f in findings if f['family'] == 'table']
        self.assertEqual(len(tables), 2)
        self.assertTrue(all(f['student_id'] == 'P002' for f in tables))
        self.assertEqual([f['location']['physical_pages'] for f in tables], [[14, 15], [16, 17]])
        for f in tables:
            self.assertEqual(len([a for a in f['artifacts'] if a['kind'] == 'image']), 2)
            self.assertEqual([a['page'] for a in f['artifacts'] if a['kind'] == 'image'], f['location']['physical_pages'])
        self.assertEqual(sum(f['status'] == 'pending' for f in findings), 4)
        self.assertEqual(sum(f['status'] == 'partial' for f in findings), 1)
        absent = next(f for f in findings if f['id'].endswith('C009'))
        self.assertEqual(absent['status'], 'trigger_absent')
        self.assertEqual(absent['location']['paragraph_index'], 175)
        for ref in ('C010', 'C011'):
            item = next(f for f in findings if f['id'].endswith(ref))
            image = next(a for a in item['artifacts'] if a['kind'] == 'image')
            self.assertIn('教师 r247 '+ref, image['label'])
            self.assertEqual(item['status'], 'pending')
            self.assertEqual(item['excerpt']['text'], '')
        self.assertIn('未见续表标识', tables[0]['reason'])

    def test_complete_comments_and_allowlisted_assets(self):
        comments = self.workspace['teacher_comments']
        self.assertEqual(len(comments), 42)
        self.assertEqual(sum(c['student_id'] == 'P001' for c in comments), 18)
        self.assertEqual(sum(c['student_id'] == 'P002' for c in comments), 6)
        self.assertEqual(sum(c['student_id'] == 'P003' for c in comments), 18)
        self.assertTrue(any(not c['comment']['text'].strip() for c in comments))
        for ident, asset in self.assets.items():
            self.assertEqual(len(ident), 24)
            self.assertEqual(digest(Path(asset['path'])), asset['sha256'])
        public = json.dumps(self.workspace, ensure_ascii=False)
        self.assertNotIn('D:\\', public)
        self.assertNotIn('api_key', public.lower())
        for f in self.workspace['findings']:
            self.assertIn(f['source']['asset_id'], self.assets)
        with self.assertRaises(ValueError):
            Assets().add(next(iter(self.assets.values()))['path'], '0'*64)

    def test_original_reader_registry_covers_every_button(self):
        w = self.workspace
        docs = {d['asset_id']: d for d in w['documents']}
        self.assertEqual(len(docs), len(w['documents']))
        for item in w['findings'] + w['teacher_comments'] + w['students']:
            source = item['source']
            doc = docs[source['asset_id']]
            self.assertEqual(source['sha256'], doc['sha256'])
            self.assertTrue(doc['display_name'].startswith(doc['student_name']))
            self.assertNotIn(doc['student_id'], doc['display_name'])
            self.assertTrue(doc['file_name'].endswith('.docx'))
        teachers = [d for d in docs.values() if d['role'] == 'teacher']
        self.assertEqual(sum(len(d['comments']) for d in teachers), 42)
        self.assertTrue(any(not c['text'].strip() for d in teachers for c in d['comments']))
        initial = next(d for d in docs.values() if d['student_id'] == 'P002' and d['role'] == 'initial')
        self.assertEqual(initial['student_name'], '李晨波')
        self.assertEqual(initial['pdf_assets'][0]['kind'], 'pdf')


if __name__ == '__main__':
    unittest.main()
