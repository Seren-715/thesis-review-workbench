"""Real OOXML fixtures verify binding safety and transformation provenance."""
import hashlib
import io
import tempfile
from pathlib import Path
import unittest
import warnings
import zipfile

from PIL import Image

from paper_figure_evidence import prepare_figure_evidence

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
V = 'urn:schemas-microsoft-com:vml'
CT = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'


def picture(rid='rId1', extent=True, extra='', anchor=False):
    shape = 'anchor' if anchor else 'inline'
    size = '<wp:extent cx="3600000" cy="1800000"/>' if extent else ''
    return f'<w:r><w:drawing><wp:{shape}>{size}<a:graphic><a:blip r:embed="{rid}"/>{extra}</a:graphic></wp:{shape}></w:drawing></w:r>'


def paragraph(content):
    return f'<w:p>{content}</w:p>'


def text(value):
    return paragraph(f'<w:r><w:t>{value}</w:t></w:r>')


class FigureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.source = self.folder / 'source.docx'
        stream = io.BytesIO()
        Image.new('RGB', (2000, 1000), '#abcdef').save(stream, 'PNG')
        self.raw = stream.getvalue()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, body, target='media/image1.png', mode='', extra_rel='', members=None):
        document = f'<w:document xmlns:w="{W}" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:r="{R}" xmlns:v="{V}"><w:body>{body}</w:body></w:document>'
        rels = f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/image" Target="{target}" {mode}/>{extra_rel}</Relationships>'
        with zipfile.ZipFile(self.source, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', CT)
            z.writestr('word/document.xml', document)
            z.writestr('word/_rels/document.xml.rels', rels)
            z.writestr('word/media/image1.png', self.raw)
            for name, raw in (members or {}).items():
                z.writestr(name, raw)

    def run_evidence(self, index=1, indices=None, caption='图1-1 系统结构'):
        finding = {'rule_id': 'NP007', 'paragraph_index': index, 'text': caption,
                   'image_paragraph_indices': [0] if indices is None else indices}
        before = self.source.read_bytes()
        stats = prepare_figure_evidence(self.source, self.folder / 'outputs', [finding])
        self.assertEqual(self.source.read_bytes(), before)
        return stats, finding['figure_evidence']

    def test_exact_image_provenance_and_a4_reference(self):
        self.write(paragraph(picture()) + text('图1-1 系统结构'))
        stats, e = self.run_evidence()
        self.assertEqual(stats['reviewable'], 1)
        self.assertEqual(e['binding_status'], 'exact_single_nearby_image')
        self.assertEqual((e['width_cm'], e['height_cm']), (10, 5))
        self.assertEqual((e['pixel_width'], e['pixel_height']), (2000, 1000))
        self.assertEqual((e['normalized_pixel_width'], e['normalized_pixel_height']), (1600, 800))
        self.assertEqual(e['source_image_sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(Path(e['selected_candidate']['original_media_path']).read_bytes(), self.raw)
        for path_key, sha_key in [('preview_path', 'preview_sha256'), ('display_preview_path', 'display_preview_sha256')]:
            self.assertEqual(hashlib.sha256(Path(e[path_key]).read_bytes()).hexdigest(), e[sha_key])
        with Image.open(e['display_preview_path']) as page:
            self.assertEqual(page.size, (840, 1188))
        self.assertIn('非Word页', e['display_preview_note'])

    def test_multi_image_same_paragraph_never_grouped_even_same_bytes(self):
        rel = f'<Relationship Id="rId2" Type="{R}/image" Target="media/image2.png"/>'
        self.write(paragraph(picture() + picture('rId2')) + text('图1-1 系统结构'), extra_rel=rel,
                   members={'word/media/image2.png': self.raw})
        stats, e = self.run_evidence()
        self.assertEqual(stats['ambiguous'], 1)
        self.assertFalse(e['reviewable'])
        self.assertEqual(len(e['candidates']), 2)
        self.assertNotIn('preview_path', e)

    def test_opposite_equally_near_images_ambiguous(self):
        self.write(paragraph(picture()) + text('图1-1 系统结构') + paragraph(picture()))
        _, e = self.run_evidence(indices=[0, 2])
        self.assertEqual(e['binding_status'], 'ambiguous')

    def test_unique_nearest_and_caption_barrier(self):
        self.write(paragraph(picture()) + text('图1-1 系统结构') + text('') + paragraph(picture()))
        _, e = self.run_evidence(indices=[0, 3])
        self.assertTrue(e['reviewable'])
        self.assertEqual(e['selected_candidate']['paragraph_index'], 0)
        self.write(paragraph(picture()) + text('图1-2 其他图') + text('图1-1 系统结构'))
        _, e = self.run_evidence(index=2)
        self.assertFalse(e['reviewable'])
        self.assertEqual(e['candidates'], [])

    def test_external_and_encoded_traversal_never_read(self):
        for target, mode in [('https://example.org/image.png', 'TargetMode="External"'),
                             ('../../evil.png', ''), ('media/%2e%2e/%2e%2e/evil.png', ''),
                             ('file:///C:/secret.png', ''), ('/word/media/image1.png', '')]:
            with self.subTest(target=target):
                self.write(paragraph(picture()) + text('图1-1 系统结构'), target=target, mode=mode)
                _, e = self.run_evidence()
                self.assertFalse(e['reviewable'])
                self.assertTrue(e['candidates'][0]['errors'])
                self.assertNotIn('preview_path', e)

    def test_crop_rotation_floating_and_unknown_dimensions_require_review(self):
        for body in [picture(extra='<a:srcRect l="-1000"/>'),
                     picture(extra='<a:xfrm rot="5400000"/>'), picture(anchor=True), picture(extent=False)]:
            with self.subTest(body=body):
                self.write(paragraph(body) + text('图1-1 系统结构'))
                _, e = self.run_evidence()
                self.assertFalse(e['reviewable'])
                self.assertTrue(e['binding_reasons'])
        self.assertIsNone(e['width_cm'])
        self.assertIn('尺寸未知', e['note'])

    def test_valid_ooxml_crop_is_applied_and_hashed(self):
        self.write(paragraph(picture(extra='<a:srcRect l="25000" r="25000" t="10000" b="10000"/>')) + text('图1-1 系统结构'))
        _, e = self.run_evidence()
        self.assertTrue(e['reviewable'])
        self.assertEqual(e['crop']['source_pixel_box'], [500, 100, 1500, 900])
        self.assertEqual(e['crop']['output_pixel_size'], [1000, 800])
        self.assertEqual(e['normalization']['source_pixel_size'], [2000, 1000])
        with Image.open(e['preview_path']) as preview:
            self.assertEqual(preview.size, (1000, 800))
        self.assertEqual(e['crop']['output_png_sha256'], e['preview_sha256'])
        for rect in ['<a:srcRect/>', '<a:srcRect l="0" t="0" r="0" b="0"/>']:
            self.write(paragraph(picture(extra=rect)) + text('图1-1 系统结构'))
            _, e = self.run_evidence()
            self.assertTrue(e['reviewable'])
            self.assertNotIn('crop', e)

    def test_vml_picture_dimensions_and_caption_source_match(self):
        body = paragraph('<w:r><w:pict><v:shape style="width:72pt;height:36pt"><v:imagedata r:id="rId1"/></v:shape></w:pict></w:r>')
        self.write(body + text('图1-1 系统结构'))
        _, e = self.run_evidence()
        self.assertTrue(e['reviewable'])
        self.assertEqual((e['width_cm'], e['height_cm']), (2.54, 1.27))
        _, e = self.run_evidence(caption='图1-1 错误的图题')
        self.assertFalse(e['reviewable'])
        self.assertEqual(e['binding_reasons'], ['caption_text_mismatch'])

    def test_dtd_duplicate_member_and_nonimage_fail_closed(self):
        self.write(paragraph(picture()) + text('图1-1 系统结构'))
        with zipfile.ZipFile(self.source, 'a') as z:
            z.writestr('word/evil.xml', '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><x>&e;</x>')
        # Unused malicious XML cannot be evaluated; the document itself remains local data.
        _, e = self.run_evidence()
        self.assertTrue(e['reviewable'])
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self.write(paragraph(picture()) + text('图1-1 系统结构'), members={'word/media/image1.png': b'not an image'})
        with self.assertRaisesRegex(ValueError, '重复'):
            self.run_evidence()
        self.raw = b'not an image'
        self.write(paragraph(picture()) + text('图1-1 系统结构'))
        _, e = self.run_evidence()
        self.assertFalse(e['reviewable'])
        self.assertTrue(e['candidates'][0]['errors'])
        self.assertNotIn('preview_path', e)
        self.write(paragraph(picture()) + text('图1-1 系统结构'),
                   mode='unused="&x;"')
        # Change the relationship XML (without duplicate ZIP members) to attack the actually read part.
        with zipfile.ZipFile(self.source) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['word/_rels/document.xml.rels'] = b'<!DOCTYPE x [<!ENTITY x "injected">]>' + entries['word/_rels/document.xml.rels']
        with zipfile.ZipFile(self.source, 'w') as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        with self.assertRaisesRegex(ValueError, 'DTD'):
            self.run_evidence()

    def test_oversized_extent_does_not_construct_huge_reference_image(self):
        drawing = picture().replace('cx="3600000"', 'cx="' + '9' * 300 + '"')
        self.write(paragraph(drawing) + text('图1-1 系统结构'))
        _, e = self.run_evidence()
        self.assertTrue(e['reviewable'])
        self.assertNotIn('display_preview_path', e)


if __name__ == '__main__':
    unittest.main()
