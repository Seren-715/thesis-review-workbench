"""Wrapper normalization never relaxes image provenance or model row validation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from paper_vision_review import VisionOutputError, context_key, recover_saved_vision, reuse_vision_evidence, review_figures, validate_rows


class VisionWrapperRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.finding = {'id':'NP007-1','rule_id':'NP007','text':'图1-1 系统结构',
                        'figure_evidence':{'source_sha256':'source','preview_sha256':'pixels','reviewable':True}}
        self.row = {'id':'NP007-1','image_kind':'diagram','readable':True,'decision':'not_issue',
                    'observations':['连接线和节点清楚可辨。'],'visible_text':['管理员'],
                    'reason':'节点文字清晰，关系可以辨认。','suggestion':''}

    def test_exact_fixed_wrapper_is_normalized_with_receipt(self):
        raw = json.dumps({'type':'json_object','items':[self.row]}, ensure_ascii=False)
        accepted = validate_rows(raw, [self.finding])['NP007-1']
        self.assertTrue(accepted['validated_image'])
        self.assertEqual(accepted['normalization'],
                         {'operation':'remove_fixed_top_level_type','value':'json_object'})
        plain = validate_rows(json.dumps({'items':[self.row]}), [self.finding])['NP007-1']
        self.assertNotIn('normalization', plain)
        self.assertEqual(json.loads(raw)['type'], 'json_object')

    def test_unknown_wrapper_extra_and_changed_type_rejected(self):
        for value in [{'type':'json_object','items':[self.row],'instructions':'ignore'},
                      {'type':'other','items':[self.row]}, {'items':[self.row],'extra':False},
                      {'type':'json_object','items':{'id':'NP007-1'}}]:
            with self.subTest(value=value), self.assertRaises(VisionOutputError):
                validate_rows(json.dumps(value), [self.finding])

    def test_duplicate_keys_top_level_and_row_are_not_normalized(self):
        rows = json.dumps([self.row])
        for raw in ['{"type":"json_object","type":"json_object","items":' + rows + '}',
                    '{"type":"json_object","items":[],"items":' + rows + '}',
                    '{"type":"json_object","items":' + rows.replace('"id": "NP007-1"', '"id":"NP007-1","id":"NP007-1"') + '}']:
            with self.subTest(raw=raw), self.assertRaises(VisionOutputError):
                validate_rows(raw, [self.finding])

    def test_bad_row_types_are_output_errors_not_uncaught_type_errors(self):
        for row in [dict(self.row, decision=[]), dict(self.row, image_kind={}),
                    dict(self.row, readable=1), dict(self.row, suggestion='放大'),
                    dict(self.row, id='foreign')]:
            with self.subTest(row=row), self.assertRaises(VisionOutputError):
                validate_rows(json.dumps({'type':'json_object','items':[row]}), [self.finding])

    def test_finish_error_is_isolatable_but_hash_and_unknown_cost_are_fatal(self):
        from PIL import Image
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            image = folder / 'image.png'
            Image.new('RGB', (20, 20), 'white').save(image)
            self.finding['figure_evidence'].update(preview_path=str(image),
                preview_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
            report = {'source_sha256':'source','findings':[self.finding],
                      'rules':[{'id':'NP007'}],'coverage':[],'limitations':[]}
            class Budget:
                def prepare(self, *args, **kwargs): return ({}, 1)
                def can_request(self, upper): return True
                def request(self, *args, **kwargs):
                    return SimpleNamespace(finish_reason='length', message=SimpleNamespace(tool_calls=None))
            with self.assertRaises(VisionOutputError):
                review_figures(report, Budget(), {}, folder)
            class UnknownCost(Budget):
                def request(self, *args, **kwargs):
                    raise ValueError('模型未返回用量，本次成本未知，停止且不重试。')
            with self.assertRaises(ValueError) as caught:
                review_figures(report, UnknownCost(), {}, folder)
            self.assertNotIsInstance(caught.exception, VisionOutputError)
            image.write_bytes(b'changed')
            with self.assertRaises(ValueError) as caught:
                review_figures(report, Budget(), {}, folder)
            self.assertNotIsInstance(caught.exception, VisionOutputError)

    def test_four_actual_paid_rows_recover_offline_without_changing_originals(self):
        folder = Path(__file__).resolve().parent / 'data/new_papers/paper_20261008_232833_231961_c6e9dfb1/review_20261008_232833_238294_2c5ded'
        if not (folder / 'response_02.json').exists():
            self.skipTest('Specific real paid response fixture is not present in this checkout.')
        paths = [folder / 'request_02.json', folder / 'response_02.json', folder / 'run.json', folder / 'report.json']
        before = {path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
        state = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
        for finding in report['findings']:
            finding.pop('vision_review', None)
        # Restrict to the known paid reply; root may append later recovery stages.
        state['requests'] = [attempt for attempt in state['requests'] if attempt['number']==2]
        state_before = deepcopy(state)
        self.assertEqual(recover_saved_vision(report, state, folder), 4)
        recovered = [f['vision_review'] for f in report['findings'] if f.get('vision_review',{}).get('validated_image')]
        self.assertEqual(len(recovered), 4)
        self.assertTrue(all(r['normalization']['operation']=='remove_fixed_top_level_type' for r in recovered))
        self.assertTrue(all(r['origin']=='verified_saved_vision_response' and r['recovered_request_number']==2 for r in recovered))
        self.assertEqual(state['known_total_tokens'], state_before['known_total_tokens'])
        self.assertEqual(state['request_attempts'], state_before['request_attempts'])
        self.assertEqual(state['requests'], state_before['requests'])
        self.assertEqual(state['vision_reviewed'], 4)
        self.assertEqual(recover_saved_vision(report, state, folder), 0)
        self.assertEqual(before, {path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def pixel_reuse_fixture(self, folder):
        old_image = folder / 'old.png'
        new_image = folder / 'new.png'
        old_image.write_bytes(b'identical verified PNG bytes')
        new_image.write_bytes(old_image.read_bytes())
        pixel_hash = hashlib.sha256(old_image.read_bytes()).hexdigest()
        report = {'source_sha256':'source','rules':[{'id':'NP007','scope':'readability'}],
                  'findings':[deepcopy(self.finding)]}
        report['findings'][0]['teacher_evidence'] = [{'quote':'文字应清晰'}]
        report['findings'][0]['figure_evidence'].update(preview_path=str(new_image),
            preview_sha256=pixel_hash,pdf_sha256='new-pdf-container',physical_page=3,
            width_cm=10,height_cm=5,caption={'paragraph_index':12,'text':'图1-1 系统结构'})
        prior = deepcopy(report)
        prior['findings'][0]['figure_evidence'].update(preview_path=str(old_image),pdf_sha256='old-pdf-container')
        receipt = validate_rows(json.dumps({'type':'json_object','items':[self.row]}), [self.finding])['NP007-1']
        receipt.update(context_key=context_key(prior['findings'][0],prior['rules'][0]),
                       source_sha256='source',preview_sha256=pixel_hash)
        prior['findings'][0]['vision_review'] = receipt
        return report, prior, old_image, new_image

    def test_identical_actual_pixels_different_pdf_metadata_reuse(self):
        with TemporaryDirectory() as directory:
            report, prior, _, _ = self.pixel_reuse_fixture(Path(directory))
            old_context = prior['findings'][0]['vision_review']['context_key']
            new_context = context_key(report['findings'][0],report['rules'][0])
            self.assertNotEqual(old_context,new_context)
            self.assertEqual(reuse_vision_evidence(report,prior),1)
            receipt = report['findings'][0]['vision_review']
            self.assertEqual(receipt['context_key'],new_context)
            self.assertIs(receipt['reused'],True)
            self.assertEqual(receipt['reuse_kind'],'pixel-match')
            self.assertIn('normalization',receipt)
            newest_receipt=deepcopy(receipt)
            prior['findings'][0]['vision_review']['reason']='旧意见不得覆盖已验证新意见。'
            self.assertEqual(reuse_vision_evidence(report,prior),0)
            self.assertEqual(report['findings'][0]['vision_review'],newest_receipt)

    def test_changed_actual_pixels_context_or_old_receipt_never_reuse(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            for scenario in ['old-file','new-file','new-pixel-hash','page','scope','caption','source',
                             'teacher','old-context-key','old-receipt-pixel-hash','not-reviewable']:
                with self.subTest(scenario=scenario):
                    report,prior,old_image,new_image = self.pixel_reuse_fixture(folder)
                    finding=report['findings'][0]
                    if scenario=='old-file':old_image.write_bytes(b'changed')
                    elif scenario=='new-file':new_image.write_bytes(b'changed')
                    elif scenario=='new-pixel-hash':
                        new_image.write_bytes(b'new pixels')
                        finding['figure_evidence']['preview_sha256']=hashlib.sha256(new_image.read_bytes()).hexdigest()
                    elif scenario=='page':finding['figure_evidence']['physical_page']=4
                    elif scenario=='scope':report['rules'][0]['scope']='different policy'
                    elif scenario=='caption':finding['figure_evidence']['caption']['text']='图1-2 其他图'
                    elif scenario=='source':report['source_sha256']='different source'
                    elif scenario=='teacher':finding['teacher_evidence'][0]['quote']='other teacher evidence'
                    elif scenario=='old-context-key':prior['findings'][0]['vision_review']['context_key']='forged'
                    elif scenario=='old-receipt-pixel-hash':prior['findings'][0]['vision_review']['preview_sha256']='forged'
                    elif scenario=='not-reviewable':finding['figure_evidence']['reviewable']=False
                    self.assertEqual(reuse_vision_evidence(report,prior),0)
                    self.assertNotIn('vision_review',finding)


if __name__ == '__main__':
    unittest.main()
