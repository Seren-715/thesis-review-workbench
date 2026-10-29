import unittest
import xml.etree.ElementTree as ET

from teacher_remaining_format_checks import (
    W, M, load_remaining_format_capabilities, collect_remaining_format_diagnostics,
)


def library(family, quote, **anchor):
    return {'rules': [{'id': family, 'title': family, 'teacher_evidence': [
        {'evidence_id': 'original-id', 'source_sha256': 'teacher-sha', 'quote': quote, 'anchor': anchor}]}]}


def specimen():
    root = ET.Element(W+'document')
    body = ET.SubElement(root, W+'body')
    rows = []
    def paragraph(text, level=None, parent=None):
        node = ET.SubElement(body if parent is None else parent, W+'p')
        row = dict(index=len(rows), text=text, heading_level=level, location={'section': '系统实现'})
        rows.append(row)
        if text:
            ET.SubElement(ET.SubElement(node, W+'r'), W+'t').text = text
        return node
    paragraph('5 系统实现', 1)
    code = paragraph('def process(data):\n    return data')
    style = ET.SubElement(ET.SubElement(code, W+'pPr'), W+'pStyle')
    style.set(W+'val', 'Code')
    paragraph('后文解释数据的处理结果以及实际界面。')
    formula = paragraph('ℳaxValue')
    for child in list(formula):
        formula.remove(child)
    math = ET.SubElement(formula, M+'oMath')
    mr = ET.SubElement(math, M+'r')
    ET.SubElement(mr, M+'t').text = 'ℳaxValue'
    math_properties = ET.SubElement(mr, M+'rPr')
    ET.SubElement(math_properties, M+'sty').set(M+'val', 'i')
    paragraph('致谢')
    paragraph('def acknowledgements():')
    styles = ET.Element(W+'styles')
    code_style = ET.SubElement(styles, W+'style', {W+'type': 'paragraph', W+'styleId': 'Code'})
    rp = ET.SubElement(code_style, W+'rPr')
    ET.SubElement(rp, W+'sz').set(W+'val', '22')
    ET.SubElement(rp, W+'rFonts').set(W+'ascii', 'Courier New')
    return root, styles, rows


class RemainingFormatDiagnosticsTests(unittest.TestCase):
    def test_vague_feedback_never_becomes_enabled_rule(self):
        for family, quote in (('font_layout', '字号。去检测系统检查'),
                              ('code_layout', '代码有专门的格式！'),
                              ('formula_font', '公式的字体比较奇怪'),
                              ('text_color', '字的颜色不对')):
            caps = load_remaining_format_capabilities(library(family, quote))
            target = next(c for c in caps if c['family'] == family)
            self.assertTrue(target['capability_available'])
            self.assertFalse(target['enabled'])
            self.assertFalse(target['reliable_rule_association'])
            self.assertEqual(target['teacher_evidence'][0]['quote'], quote)

    def test_code_properties_and_later_context_do_not_create_error_cards(self):
        root, styles, rows = specimen()
        result = collect_remaining_format_diagnostics(root, styles, rows,
            library('code_layout', '代码格式', text=rows[1]['text']), 'current-sha')
        self.assertEqual(result['findings'], [])
        record = result['sources']['code_layout']['source_property_records'][0]
        self.assertEqual(record['paragraph_index'], 1)
        self.assertEqual(record['runs'][0]['properties']['font_size_half_points'],
            {'value': '22', 'source': 'paragraph_style:Code'})
        self.assertFalse(record['error_judgment'])
        chunks = result['sources']['code_layout']['content_scope']['chunks']
        self.assertTrue(any(c['paragraph_index'] == 2 for c in chunks))
        self.assertFalse(any(c['paragraph_index'] == 5 for c in chunks))
        self.assertEqual(sum(result[k] for k in ('llm_requests','svn_requests','word_requests')), 0)

    def test_math_unicode_is_recorded_without_calling_it_wrong_font(self):
        root, styles, rows = specimen()
        result = collect_remaining_format_diagnostics(root, styles, rows,
            library('formula_font', '公式的字体比较奇怪', contains_math=True), 'sha')
        records = result['sources']['formula_font']['source_property_records']
        self.assertEqual(len(records), 1)
        values = records[0]['runs'][0]['properties']
        self.assertEqual(values['math_style'], {'sty': 'i'})
        self.assertEqual(values['unicode_math_characters'][0]['character'], 'ℳ')
        self.assertEqual(result['findings'], [])
        self.assertFalse(values['effective_display_verified'])

    def test_module_heading_without_implementation_keyword_keeps_code_and_late_context(self):
        root, styles, rows = specimen()
        rows[0]['text'] = '4.1 用户管理模块'
        root.findall('.//'+W+'p')[0].find(W+'r/'+W+'t').text = rows[0]['text']
        result = collect_remaining_format_diagnostics(root, styles, rows,
            library('code_layout', '代码格式'), 'sha')
        scope = result['sources']['code_layout']['content_scope']
        self.assertTrue({1,2}.issubset(scope['paragraph_indices']))
        self.assertEqual(len(result['sources']['code_layout']['source_property_records']), 1)

    def test_image_background_feedback_is_not_a_body_text_color_rule(self):
        root, styles, rows = specimen()
        result = collect_remaining_format_diagnostics(root, styles, rows,
            library('text_color', '用白色底纹，不要用黑色', text='图4-2 爬取过程'), 'sha')
        cap = next(c for c in result['capabilities'] if c['family'] == 'text_color')
        self.assertEqual(cap['text_teacher_evidence'], [])
        self.assertEqual(len(cap['object_teacher_evidence']), 1)
        self.assertEqual(result['sources']['text_color']['source_property_records'], [])
        self.assertEqual(result['findings'], [])

    def test_text_color_does_not_invent_black_as_expected_color(self):
        root, styles, rows = specimen()
        run = root.findall('.//'+W+'p')[2].find(W+'r')
        color = ET.SubElement(ET.SubElement(run, W+'rPr'), W+'color', {W+'val':'FF0000', W+'themeColor':'accent1'})
        result = collect_remaining_format_diagnostics(root, styles, rows,
            library('text_color', '字的颜色不对', text=rows[2]['text']), 'sha')
        record = next(r for r in result['sources']['text_color']['source_property_records'] if r['paragraph_index']==2)
        self.assertTrue(record['runs'][0]['properties']['theme_dependent'])
        self.assertEqual(record['runs'][0]['properties']['color']['value'], 'FF0000')
        self.assertFalse(record['runs'][0]['properties']['effective_display_verified'])
        self.assertEqual(result['findings'], [])

    def test_hidden_run_is_not_a_visible_code_property(self):
        root, styles, rows = specimen()
        code = root.findall('.//'+W+'p')[1]
        hidden = ET.SubElement(code, W+'del')
        ET.SubElement(ET.SubElement(hidden, W+'r'), W+'t').text = 'deleted_source()'
        result = collect_remaining_format_diagnostics(root, styles, rows,
            library('code_layout', '代码格式'), 'sha')
        runs = result['sources']['code_layout']['source_property_records'][0]['runs']
        self.assertFalse(any('deleted_source' in r['text'] for r in runs))

    def test_index_or_source_mismatch_is_rejected(self):
        root, styles, rows = specimen()
        rows[1]['index'] = 999
        with self.assertRaises(ValueError):
            collect_remaining_format_diagnostics(root, styles, rows, library('code_layout','格式'), 'sha')
        rows[1]['index'] = 1
        with self.assertRaises(ValueError):
            collect_remaining_format_diagnostics(root, styles, rows, library('code_layout','格式'), None)

    def test_missing_feedback_has_no_source_records(self):
        root, styles, rows = specimen()
        result = collect_remaining_format_diagnostics(root, styles, rows, {'rules':[]}, 'sha')
        self.assertEqual(result['sources'], {})
        self.assertTrue(all(c['status']=='not_applicable' for c in result['coverage']))


if __name__ == '__main__':
    unittest.main()
