import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from new_paper_checks import extract_paragraphs
from reference_structure_rules import ROOT, W, check_structure_rules, load_structure_rules


def paragraph(text, heading=False, toc=False, extra=''):
    props = '<w:outlineLvl w:val="0"/>' if heading else ''
    if toc:
        props += '<w:pStyle w:val="TOC1"/>'
    return f'<w:p><w:pPr>{props}{extra}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'


def document(*parts, sect=''):
    return ET.fromstring('<w:document xmlns:w="'+W[1:-1]+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'+
                         ''.join(parts)+sect+'</w:body></w:document>')


class StructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_structure_rules()

    def check(self, doc, **kwargs):
        return check_structure_rules(doc, None, extract_paragraphs(doc), self.rules, kwargs.pop('sha', 'test'), **kwargs)

    def selected(self, findings, rid, status=None):
        return [f for f in findings if f['rule_id'] == rid and (status is None or f['status'] == status)]

    def references(self, count, **kwargs):
        return document(paragraph('第1章 绪论', True), paragraph('参考文献', True),
                        *(paragraph(f'[{i}] Author. Book.') for i in range(1, count+1)), **kwargs)

    def test_bound_clauses_and_separate_provenance(self):
        by = {r['id']: r for r in self.rules}
        self.assertIn('宋体5号、居中', by['UP002']['reference_evidence'][0]['quote'])
        self.assertIn('参考文献不少于15篇', by['UP004']['reference_evidence'][1]['quote'])
        self.assertEqual(by['UP004']['teacher_evidence'], [])
        real = {'quote': '页眉怎么没有', 'student_name': '真实来源'}
        rules = load_structure_rules({'rules': [{'teacher_evidence': [real]}]})
        self.assertEqual(rules[0]['teacher_evidence'], [real])

    def test_source_copy_tamper_and_catalog_hash_rewrite_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT/'data/reference_standards', root/'data/reference_standards')
            path = root/'data/reference_standards/catalog.json'
            cat = json.loads(path.read_text(encoding='utf8'))
            for s in cat['sources']:
                for key in ['source_copy_path', 'reader_path']:
                    s[key] = str(root/Path(s[key]).relative_to(ROOT))
            path.write_text(json.dumps(cat), encoding='utf8')
            self.assertEqual(len(load_structure_rules(root=root)), 3)
            source = next(s for s in cat['sources'] if s['id'] == 'institution_norm')
            Path(source['reader_path']).write_text('tampered', encoding='utf8')
            with self.assertRaises(ValueError):
                load_structure_rules(root=root)
            source['reader_sha256'] = hashlib.sha256(b'tampered').hexdigest()
            path.write_text(json.dumps(cat), encoding='utf8')
            with self.assertRaises(ValueError):
                load_structure_rules(root=root)

    def test_order_inversion_and_toc_exclusion(self):
        doc = document(paragraph('参考文献', toc=True), paragraph('第1章 绪论', True),
                       paragraph('谢辞', True), paragraph('参考文献', True))
        f, c = self.check(doc)
        candidates = self.selected(f, 'UP003', 'candidate')
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['paragraph_index'], 3)

    def test_prose_body_keyword_not_chapter(self):
        doc = document(paragraph('参考文献', True), paragraph('第1章 绪论'))
        f, c = self.check(doc)
        self.assertFalse(self.selected(f, 'UP003', 'candidate'))

    def test_complete_order_optional_appendix(self):
        labels = ['封面', '独创性声明', '摘要', 'Abstract', '目录', '第1章 绪论', '参考文献', '谢辞']
        doc = document(*(paragraph(s, True) for s in labels))
        f, c = self.check(doc)
        self.assertFalse(self.selected(f, 'UP003'))
        self.assertEqual(next(x for x in c if x['rule_id'] == 'UP003')['status'], 'checked')

    def test_type_unknown_never_guesses_threshold(self):
        f, c = self.check(self.references(1), paper_type='')
        self.assertFalse(self.selected(f, 'UP004', 'candidate'))
        self.assertTrue(self.selected(f, 'UP004', 'needs_review'))

    def test_type_controls_threshold(self):
        doc = self.references(12)
        languages = {str(i): 'foreign' if i <= 2 else 'chinese' for i in range(1, 13)}
        f, c = self.check(doc, paper_type='software', reference_languages=languages)
        self.assertFalse(self.selected(f, 'UP004'))
        f, c = self.check(doc, paper_type='experimental_research', reference_languages=languages)
        self.assertEqual(self.selected(f, 'UP004', 'candidate')[0]['required_minimum'], 15)

    def test_foreign_not_inferred_from_latin(self):
        f, c = self.check(self.references(10), paper_type='software')
        self.assertFalse(self.selected(f, 'UP004', 'candidate'))
        self.assertTrue(self.selected(f, 'UP004', 'needs_review'))

    def test_confirmed_language_shortfall(self):
        f, c = self.check(self.references(10), paper_type='software',
                          reference_languages={str(i): 'chinese' for i in range(1, 11)})
        self.assertEqual(self.selected(f, 'UP004', 'candidate')[0]['required_foreign_minimum'], 2)

    def test_civil_engineering_no_foreign_threshold(self):
        f, c = self.check(self.references(10), paper_type='civil_engineering')
        self.assertFalse(self.selected(f, 'UP004', 'candidate'))
        self.assertIn('所在系', self.selected(f, 'UP004', 'needs_review')[0]['reason'])

    def test_duplicates_and_unnumbered_not_false_shortfall(self):
        for extra in [paragraph('[1] duplicate'), paragraph('Author. Unnumbered entry.')]:
            doc = self.references(1)
            doc.find(W+'body').append(ET.fromstring('<w:root xmlns:w="'+W[1:-1]+'">'+extra+'</w:root>')[0])
            f, c = self.check(doc, paper_type='software')
            self.assertFalse(self.selected(f, 'UP004', 'candidate'))

    def header_doc(self, tmp, *, text='错误页眉', abstract=False, mixed=False, inherited=False, hidden=False):
        header = ('<w:hdr xmlns:w="'+W[1:-1]+'"><w:p><w:pPr><w:jc w:val="left"/></w:pPr>'
                  '<w:r><w:rPr><w:rFonts w:eastAsia="黑体"/><w:sz w:val="24"/>'+('<w:vanish/>' if hidden else '')+
                  '</w:rPr><w:t>'+text+'</w:t></w:r></w:p></w:hdr>')
        sect = '<w:sectPr>'+('' if inherited else '<w:headerReference w:type="even" r:id="rId1"/>')+'</w:sectPr>'
        parts = [paragraph('摘要' if abstract else '第1章 绪论', True), paragraph('内容')]
        if mixed:
            parts.append(paragraph('第1章 绪论', True))
        doc = document(*parts, sect=sect)
        path = Path(tmp)/'source.docx'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('word/document.xml', ET.tostring(doc))
            z.writestr('word/header1.xml', header)
            z.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>')
        return doc, path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_actual_header_parts_content_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, path, sha = self.header_doc(tmp)
            f, c = self.check(doc, source_path=path, sha=sha)
            candidates = self.selected(f, 'UP002', 'candidate')
            self.assertEqual(len(candidates), 4)
            self.assertTrue(all(x['header_part'] == 'word/header1.xml' for x in candidates))
            self.assertEqual(next(x for x in c if x['rule_id'] == 'UP002')['status'], 'partial')

    def test_abstract_nonempty_and_mixed_section_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, path, sha = self.header_doc(tmp, abstract=True)
            f, c = self.check(doc, source_path=path, sha=sha)
            self.assertEqual(len(self.selected(f, 'UP002', 'candidate')), 1)
            doc, path, sha = self.header_doc(tmp, abstract=True, mixed=True)
            f, c = self.check(doc, source_path=path, sha=sha)
            self.assertFalse(self.selected(f, 'UP002', 'candidate'))

    def test_inherited_header_not_claimed_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, path, sha = self.header_doc(tmp, inherited=True)
            f, c = self.check(doc, source_path=path, sha=sha)
            self.assertFalse(self.selected(f, 'UP002', 'candidate'))
            self.assertTrue(self.selected(f, 'UP002', 'needs_review'))

    def test_hidden_abstract_header_not_nonempty(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, path, sha = self.header_doc(tmp, abstract=True, hidden=True)
            f, c = self.check(doc, source_path=path, sha=sha)
            self.assertFalse(self.selected(f, 'UP002', 'candidate'))

    def test_original_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, path, sha = self.header_doc(tmp)
            with self.assertRaises(ValueError):
                self.check(doc, source_path=path, sha='0'*64)


if __name__ == '__main__':
    unittest.main()
