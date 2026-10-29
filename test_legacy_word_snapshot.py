from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from legacy_word_snapshot import build_parsed
from new_paper_checks import extract_paragraphs


class LegacySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        self.source, self.snapshot = folder/'original.doc', folder/'snapshot.json'
        self.source.write_bytes(b'original DOC bytes fixture')
        self.rows = [{'index':0,'raw_text':'第1章 方法\r','range_start':0,'range_end':8,'heading_level':1,'style_name':'标题 1','in_table':False},
                     {'index':1,'raw_text':'leaf\x02wise内容\r','range_start':8,'range_end':20,'heading_level':None,'style_name':'正文','in_table':False},
                     {'index':2,'raw_text':'表格文字\r\x07','range_start':20,'range_end':26,'heading_level':None,'style_name':'正文','in_table':True}]
        self.raw = ''.join(r['raw_text'] for r in self.rows)
        self.payload = {'status':'read_only_snapshot_collected','errors':[],'cleanup_errors':[],'source_sha256_unchanged':True,
                        'derived_sha256_unchanged':True,'source':{'path':str(self.source),'sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),
                        'raw_body_text':self.raw,'body_utf8_sha256':hashlib.sha256(self.raw.encode()).hexdigest(),'read_only':True,'paragraphs':self.rows}}
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        self.snapshot.write_text(json.dumps(self.payload,ensure_ascii=False),encoding='utf8')

    def test_controls_real_outline_table_membership_preserved_without_docx(self):
        parsed, info = build_parsed(self.snapshot,self.source)
        rows=extract_paragraphs(parsed[0],parsed[1])
        self.assertEqual(''.join(r['text'] for r in rows), self.raw)
        self.assertEqual(rows[0]['heading_level'],1)
        self.assertIsNone(rows[1]['heading_level'])
        self.assertTrue(rows[2]['in_table'])
        self.assertIn('\x02',rows[1]['text'])
        self.assertFalse(info['docx_conversion_eligible'])
        self.assertFalse(info['image_count_known'])
        self.assertEqual(list(Path(self.temp.name).glob('*.docx')),[])

    def test_source_tampering_rejected(self):
        self.source.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'来源哈希'):
            build_parsed(self.snapshot,self.source)

    def test_raw_body_tampering_rejected(self):
        self.payload['source']['raw_body_text']+='changed';self.save()
        with self.assertRaisesRegex(ValueError,'完整源正文'):
            build_parsed(self.snapshot,self.source)

    def test_paragraph_text_not_complete_rejected(self):
        self.payload['source']['paragraphs'][1]['raw_text']='changed';self.save()
        with self.assertRaisesRegex(ValueError,'完整重构'):
            build_parsed(self.snapshot,self.source)

    def test_invalid_heading_metadata_rejected(self):
        self.payload['source']['paragraphs'][0]['heading_level']=10;self.save()
        with self.assertRaisesRegex(ValueError,'标题大纲'):
            build_parsed(self.snapshot,self.source)


if __name__ == '__main__':
    unittest.main()
