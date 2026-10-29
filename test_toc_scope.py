"""TOC evidence must come from OOXML fields/styles, never page-number text."""
from html import escape
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import new_paper_checks as checks

NS = checks.W[1:-1]


def run(text):
    return '<w:r><w:t>' + escape(text) + '</w:t></w:r>'


def field_char(kind):
    return f'<w:r><w:fldChar w:fldCharType="{kind}"/></w:r>'


def instruction(text):
    return '<w:r><w:instrText xml:space="preserve">' + escape(text) + '</w:instrText></w:r>'


def paragraph(contents, style=None):
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return '<w:p>' + props + contents + '</w:p>'


def complex_field(code, contents):
    return field_char('begin') + instruction(code) + field_char('separate') + contents + field_char('end')


class TocScopeTests(unittest.TestCase):
    def extract(self, body, styles=''):
        document = ET.fromstring(f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>')
        style_root = ET.fromstring(f'<w:styles xmlns:w="{NS}">{styles}</w:styles>') if styles else None
        return checks.extract_paragraphs(document, style_root)

    def test_complex_toc_crosses_paragraphs_with_nested_pageref_and_hyperlink(self):
        def entry(title, page):
            number = complex_field(' PAGEREF _Toc123 \\h ', run(page))
            return complex_field(' HYPERLINK \\l _Toc123 ', run(title) + '<w:r><w:tab/></w:r>' + number)

        body = paragraph(field_char('begin') + instruction('TOC \\o "1-3" \\h')
                         + field_char('separate') + entry('5 系统实现', '19'))
        body += paragraph(entry('结论', '31'))
        body += paragraph(entry('参考文献', '32'))
        body += paragraph(field_char('end'))
        body += paragraph(run('5 系统实现')) + paragraph(run('该功能处理测评并计算分数。'))
        rows = self.extract(body)
        self.assertEqual([p['in_toc'] for p in rows], [True, True, True, True, False, False])
        self.assertEqual(rows[1]['text'], '结论\t31')
        self.assertEqual([p['index'] for p in rows], list(range(6)))
        self.assertEqual(set(rows[1]), {'index', 'text', 'heading_level', 'style_id', 'in_table',
                                       'in_textbox', 'in_deleted', 'in_toc'})

    def test_split_toc_instruction_and_end_within_last_entry(self):
        body = paragraph(field_char('begin') + instruction(' T'))
        body += paragraph(instruction('OC \\h ') + field_char('separate') + run('目录项'))
        body += paragraph(run('最后一项') + field_char('end'))
        body += paragraph(run('正文'))
        self.assertEqual([p['in_toc'] for p in self.extract(body)], [True, True, True, False])

    def test_numeric_style_names_and_inheritance_are_resolved(self):
        styles = '''<w:style w:type="paragraph" w:styleId="9"><w:name w:val="toc 1"/></w:style>
                    <w:style w:type="paragraph" w:styleId="11"><w:name w:val="TOC 2"/></w:style>
                    <w:style w:type="paragraph" w:styleId="5"><w:name w:val="目录 3"/></w:style>
                    <w:style w:type="paragraph" w:styleId="Derived"><w:basedOn w:val="9"/></w:style>
                    <w:style w:type="paragraph" w:styleId="Body"><w:name w:val="toc 1 discussion"/></w:style>
                    <w:style w:type="paragraph" w:styleId="CycleA"><w:basedOn w:val="CycleB"/></w:style>
                    <w:style w:type="paragraph" w:styleId="CycleB"><w:basedOn w:val="CycleA"/></w:style>'''
        ids = ['9', '11', '5', 'Derived', 'TOC3', 'Body', 'CycleA']
        rows = self.extract(''.join(paragraph(run('文字'), style) for style in ids), styles)
        self.assertEqual([p['in_toc'] for p in rows], [True, True, True, True, True, False, False])
        self.assertEqual([p['style_id'] for p in rows], ids)

    def test_body_tabs_page_numbers_and_pageref_do_not_establish_toc(self):
        body = paragraph(run('结论') + '<w:r><w:tab/></w:r>' + run('31'))
        body += paragraph(complex_field(' PAGEREF _Toc123 \\h ', run('31')))
        body += paragraph(complex_field(' HYPERLINK \\l _Toc123 ', run('正文交叉引用')))
        body += paragraph(complex_field(' QUOTE "TOC" ', run('TOC')))
        body += paragraph(complex_field(' TOCENTRY ', run('普通域')))
        self.assertFalse(any(p['in_toc'] for p in self.extract(body)))

    def test_simple_toc_field_has_lexical_scope(self):
        body = paragraph('<w:fldSimple w:instr=" TOC \\h ">' + run('目录项') + '</w:fldSimple>')
        body += paragraph('<w:fldSimple w:instr=" PAGEREF _Toc123 ">' + run('正文页码') + '</w:fldSimple>')
        body += paragraph(run('正文'))
        self.assertEqual([p['in_toc'] for p in self.extract(body)], [True, False, False])

    def test_deleted_and_textbox_field_markers_do_not_leak_into_body(self):
        unclosed = field_char('begin') + instruction(' TOC \\h ') + field_char('separate') + run('目录项')
        body = '<w:del>' + paragraph(unclosed) + '</w:del>'
        body += '<w:txbxContent>' + paragraph(unclosed) + '</w:txbxContent>'
        body += paragraph(run('正文'))
        rows = self.extract(body)
        self.assertEqual([p['in_toc'] for p in rows], [True, True, False])
        self.assertTrue(rows[0]['in_deleted'])
        self.assertTrue(rows[1]['in_textbox'])

    def test_toc_cannot_become_np013_semantic_source(self):
        styles = '<w:style w:type="paragraph" w:styleId="Heading"><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        body = paragraph(field_char('begin') + instruction('TOC \\h') + field_char('separate')
                         + run('5 系统实现') + '<w:r><w:tab/></w:r>' + run('19'), 'Heading')
        body += paragraph(run('结论') + '<w:r><w:tab/></w:r>' + run('31'))
        body += paragraph(field_char('end'))
        body += paragraph(run('5 系统实现'), 'Heading')
        body += paragraph(run('该功能处理测评并计算分数。'))
        rules = [{'id': rid, 'title': rid, 'teacher_evidence': [{'quote': 'test'}], 'enabled': True}
                 for rid in ['NP001', 'NP002', 'NP003', 'NP008', 'NP013']]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'toc.docx'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                 '<Override PartName="/word/document.xml" ContentType="' + checks.MAIN_TYPE + '"/></Types>')
                archive.writestr('word/document.xml', f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>')
                archive.writestr('word/styles.xml', f'<w:styles xmlns:w="{NS}">{styles}</w:styles>')
            with patch.object(checks, 'load_teacher_rules', return_value=rules):
                report = checks.execute_checks(path)
        finding = next(f for f in report['findings'] if f['rule_id'] == 'NP013')
        self.assertEqual(finding['paragraph_index'], 4)
        self.assertEqual(finding['text'], '该功能处理测评并计算分数。')
        self.assertEqual([p['index'] for p in report['headings']], [3])
        self.assertEqual(report['requests'], {'llm': 0, 'svn': 0, 'word': 0})


if __name__ == '__main__':
    unittest.main()
