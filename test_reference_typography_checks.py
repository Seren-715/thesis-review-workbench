import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

from new_paper_checks import extract_paragraphs
from paper_locations import annotate_paragraphs
from reference_typography_checks import load_reference_typography_rules, check_reference_typography, W

NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MNS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'


class TypographyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root/'data/reference_standards'
        folder.mkdir(parents=True)
        original = self.root/'original.bin'
        original.write_bytes(b'preserved')
        copy = folder/'source.bin'
        copy.write_bytes(original.read_bytes())
        reader = folder/'reader.md'
        reader.write_text('宋体小4号，首行缩进2格，段前、段后距为0行；行距：固定值20磅。\n代码五号文字\n公式宋体五号', encoding='utf8')
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        clauses = [('official_body_typography', reader.read_text(encoding='utf8').splitlines()[0]),
                   ('course_code_as_text', '代码五号文字'), ('official_formula_typography', '公式宋体五号')]
        data = dict(status='verified_reference_materials', sources=[dict(id='norm',
            original_path=str(original), source_copy_path=str(copy), reader_path=str(reader),
            source_sha256=sha(original), reader_sha256=sha(reader))],
            requirements=[dict(id=rid, source_id='norm', quote=quote) for rid, quote in clauses])
        (folder/'catalog.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
        self.rules = load_reference_typography_rules(root=self.root)

    def check(self, xml, styles=None):
        doc = ET.fromstring(f'<w:document xmlns:w="{NS}" xmlns:m="{MNS}"><w:body>{xml}</w:body></w:document>')
        rows = annotate_paragraphs(extract_paragraphs(doc, styles))
        for row in rows:
            if not row.get('location', {}).get('chapter'):
                row['location'] = dict(chapter='第1章 正文', section='正文')
        return check_reference_typography(doc, styles, rows, self.rules)

    def p(self, text, size=24, font='宋体', style='', props=''):
        pstyle = f'<w:pStyle w:val="{style}"/>' if style else ''
        return f'<w:p><w:pPr>{pstyle}{props}</w:pPr><w:r><w:rPr><w:sz w:val="{size}"/><w:rFonts w:eastAsia="{font}" w:ascii="{font}"/></w:rPr><w:t>{text}</w:t></w:r></w:p>'

    def test_body_properties_and_exact_span(self):
        text = '本系统采用模块化设计，实现完整的数据管理功能。'
        props = '<w:spacing w:line="360" w:lineRule="auto" w:before="0" w:after="0"/><w:ind w:firstLineChars="100"/>'
        findings, _ = self.check(self.p(text, size=22, font='黑体', props=props))
        self.assertEqual({f['rule_id'] for f in findings}, {'UP005'})
        for f in findings:
            self.assertEqual(f['text'][f['start']:f['end']], text)
            self.assertTrue(f['format_evidence'])
            self.assertTrue(f['reference_requirements'])
        self.assertEqual(len(findings), 2)

    def test_visible_text_outside_runs_is_unresolved_without_aborting_next_paragraph(self):
        text = '本系统采用模块化设计，实现完整的数据管理功能。'
        # Unsupported content adds visible text outside ordinary run offsets.
        malformed = self.p(text, size=22).replace('</w:p>', '<w:t>附加文字</w:t></w:p>')
        findings, coverage = self.check(malformed + self.p(text, size=22))
        self.assertEqual(coverage[0]['unresolved'], 2)
        self.assertEqual(coverage[0]['checked'], 1)
        self.assertTrue(findings)
        self.assertTrue(all('附加文字' not in f['text'] for f in findings))
        self.assertTrue(all(f['text'][f['start']:f['end']] == text for f in findings))

    def test_correct_body_and_unknown_indent_never_guess(self):
        props = '<w:spacing w:line="400" w:lineRule="exact" w:before="0" w:after="0"/><w:ind w:firstLine="480"/>'
        findings, coverage = self.check(self.p('本系统采用模块化设计，实现完整的数据管理功能。', props=props))
        self.assertFalse(findings)
        self.assertEqual(coverage[0]['unresolved'], 1)

    def test_code_font_name_is_not_required_and_prose_not_code(self):
        findings, coverage = self.check(self.p('const value = 1;', style='Code', font='Consolas', size=21)
            + self.p('这里介绍代码中的def函数及import模块，并说明各个参数。', size=24))
        self.assertFalse([f for f in findings if f['rule_id'] == 'UP006'])
        self.assertEqual(coverage[1]['checked'], 1)
        findings, _ = self.check(self.p('def run():', size=24))
        self.assertEqual(findings[0]['rule_id'], 'UP006')
        self.assertEqual(list(findings[0]['format_evidence']), ['font_size'])

    def test_formula_only_number_checked_not_math_glyphs(self):
        math = '<m:oMathPara><m:oMath><m:r><m:rPr/><m:t>x+y</m:t></m:r></m:oMath></m:oMathPara>'
        label = '<w:r><w:rPr><w:sz w:val="24"/><w:rFonts w:ascii="Arial"/></w:rPr><w:t>（1-1）</w:t></w:r>'
        findings, _ = self.check(f'<w:p>{math}{label}</w:p>')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['rule_id'], 'UP007')
        self.assertEqual((findings[0]['start'], findings[0]['end']), (0, 5))
        self.assertIn('number_font', findings[0]['format_evidence'])

    def test_style_inheritance_and_theme_is_unknown(self):
        styles = ET.fromstring(f'<w:styles xmlns:w="{NS}"><w:style w:type="paragraph" w:styleId="Code"><w:basedOn w:val="Base"/></w:style><w:style w:type="paragraph" w:styleId="Base"><w:rPr><w:sz w:val="22"/></w:rPr></w:style></w:styles>')
        findings, _ = self.check('<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr><w:r><w:t>def run():</w:t></w:r></w:p>', styles)
        self.assertEqual(findings[0]['format_evidence']['font_size']['source'], 'paragraph_style:Base')

    def test_hidden_deleted_and_repeat_offsets(self):
        xml = '<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr><w:del><w:r><w:t>old</w:t></w:r></w:del><w:r><w:rPr><w:vanish/><w:sz w:val="60"/></w:rPr><w:t>hidden</w:t></w:r><w:r><w:rPr><w:sz w:val="21"/></w:rPr><w:t>abc</w:t></w:r><w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>abc</w:t></w:r></w:p>'
        findings, _ = self.check(xml)
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0]['start'], findings[0]['end']), (3, 6))

    def test_heading_caption_table_and_screenshot_excluded(self):
        xml = self.p('图1-1 测试系统总体架构示意图。', size=28)
        xml += '<w:tbl><w:tr><w:tc>'+self.p('本系统采用模块化设计，实现完整的数据管理功能。', size=28)+'</w:tc></w:tr></w:tbl>'
        xml += '<w:p><w:r><w:drawing/></w:r><w:r><w:t>（1-1）</w:t></w:r></w:p>'
        findings, _ = self.check(xml)
        self.assertFalse(findings)

    def test_changed_norm_disables_all(self):
        (self.root/'original.bin').write_bytes(b'changed')
        rules = load_reference_typography_rules(root=self.root)
        self.assertTrue(all(not r['enabled'] for r in rules))
        self.assertTrue(all(not r['teacher_evidence'] for r in rules))

    def test_abstract_and_unknown_code_syntax_not_body_error(self):
        doc = ET.fromstring(f'<w:document xmlns:w="{NS}"><w:body>{self.p("本系统采用模块化设计，实现完整的数据管理功能。", size=28)}</w:body></w:document>')
        rows = extract_paragraphs(doc)
        rows[0]['location'] = dict(chapter='摘要', section='研究方法')
        findings, coverage = check_reference_typography(doc, None, rows, self.rules)
        self.assertFalse(findings)
        self.assertEqual(coverage[0]['checked'], 0)
        findings, coverage = self.check(self.p('import 加载依赖模块，用于执行后续功能。', size=21))
        self.assertEqual(coverage[1]['checked'], 0)


if __name__ == '__main__':
    unittest.main()
