"""Negative-reference and scope cases for additional evidence-backed checks."""
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import additional_teacher_checks as checks
from new_paper_checks import extract_paragraphs
from paper_locations import annotate_paragraphs

WNS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MNS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'


def p(text='', extra='', heading=None, style=None):
    import html
    props = ''
    if heading is not None:
        props += f'<w:outlineLvl w:val="{heading - 1}"/>'
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    return f'<w:p><w:pPr>{props}</w:pPr>{extra}<w:r><w:t>{html.escape(text)}</w:t></w:r></w:p>'


def formula(label):
    return p('\t公式（' + label + '）', '<m:oMathPara><m:oMath><m:r><m:t>x=y</m:t></m:r></m:oMath></m:oMathPara>')


class AdditionalChecks(unittest.TestCase):
    def run_checks(self, body):
        document = ET.fromstring(f'<w:document xmlns:w="{WNS}" xmlns:m="{MNS}"><w:body>{body}</w:body></w:document>')
        paragraphs = annotate_paragraphs(extract_paragraphs(document))
        rules = [{'id': f'NP00{i}', 'title': str(i), 'teacher_evidence': [{'quote': 'test'}]} for i in range(4, 8)]
        return checks.check_additional_rules(document, paragraphs, rules)

    def test_formula_references_are_not_formula_duplicates(self):
        body = p('第2章 模型', heading=1) + formula('2-1') + p('代入公式（2-1）可知结果。') + formula('2-2')
        findings, coverage = self.run_checks(body)
        self.assertFalse(any(f['rule_id'] == 'NP004' for f in findings))
        self.assertIn('绑定明确编号2处', coverage[0]['detail'])

    def test_duplicate_omml_labels_have_exact_offsets_and_locations(self):
        findings, _ = self.run_checks(p('第2章 模型', heading=1) + formula('2-1') + formula('2－1'))
        hits = [f for f in findings if f['rule_id'] == 'NP004']
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]['location']['chapter'], '第2章 模型')
        self.assertEqual(hits[0]['text'][hits[0]['start']:hits[0]['end']], '公式（2-1）')
        self.assertTrue(all(f['status'] == 'candidate' for f in hits))

    def test_reverse_labels_are_candidates_but_gaps_are_not_errors(self):
        findings, _ = self.run_checks(p('第2章 模型', heading=1) + formula('2-1') + formula('2-5') + formula('2-3'))
        hits = [f for f in findings if f['rule_id'] == 'NP004']
        self.assertEqual(len(hits), 1)
        self.assertIn('逆序', hits[0]['title'])

    def test_unbound_picture_formula_not_reported_as_numbering_error(self):
        findings, coverage = self.run_checks(p('公式（2-1）', '<w:r><w:drawing/></w:r>') + p('公式（2-1）', '<w:r><w:drawing/></w:r>'))
        self.assertFalse(any(f['rule_id'] == 'NP004' for f in findings))
        self.assertEqual(coverage[0]['status'], 'partial')

    def test_caption_duplicates_but_prose_references_not_counted(self):
        image = p('', '<w:r><w:drawing/></w:r>')
        body = p('第3章 测试', heading=1) + image + p('图3-1 损失曲线') + p('图3-1表示模型的损失变化') + image + p('图3-1 误差曲线')
        findings, _ = self.run_checks(body)
        hits = [f for f in findings if f['rule_id'] == 'NP005']
        self.assertEqual(len(hits), 2)
        self.assertFalse(any('表示' in f['text'] for f in hits))

    def test_caption_style_can_establish_caption_but_bare_reference_cannot(self):
        findings, _ = self.run_checks(p('图3-1 损失曲线', style='Caption') + p('正文引用图3-1') + p('图3-1 误差曲线', style='Caption'))
        self.assertEqual(sum(f['rule_id'] == 'NP005' for f in findings), 2)
        self.assertFalse(any(f['rule_id'] == 'NP007' for f in findings))

    def test_table_caption_after_is_layout_review_not_confirmed_error(self):
        table = '<w:tbl><w:tr><w:tc>' + p('结果') + '</w:tc></w:tr></w:tbl>'
        findings, _ = self.run_checks(table + p('') + p('表3-1 训练结果'))
        hits = [f for f in findings if f['rule_id'] == 'NP006']
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['status'], 'needs_review')
        findings, _ = self.run_checks(p('表3-1 训练结果') + table)
        self.assertFalse(any(f['rule_id'] == 'NP006' for f in findings))

    def test_acknowledgments_toc_and_deleted_content_excluded(self):
        image = p('', '<w:r><w:drawing/></w:r>')
        body = p('致谢') + formula('2-1') + formula('2-1') + image + p('图3-1 谢辞照片') + image + p('图3-1 谢辞照片')
        findings, _ = self.run_checks(body)
        self.assertFalse(findings)
        body = p('图3-1 目录条目', style='TOC1') + p('图3-1 目录条目', style='TOC1')
        body += '<w:del>' + formula('2-1') + formula('2-1') + '</w:del>'
        findings, _ = self.run_checks(body)
        self.assertFalse(findings)

    def test_visual_checks_bounded_and_do_not_claim_all_images_wrong(self):
        body = ''.join(p('', '<w:r><w:drawing/></w:r>') + p(f'图3-{i} 结果曲线') for i in range(1, checks.MAX_VISUAL_ITEMS+5))
        findings, coverage = self.run_checks(body)
        hits = [f for f in findings if f['rule_id'] == 'NP007']
        self.assertEqual(len(hits), checks.MAX_VISUAL_ITEMS)
        self.assertTrue(all(f['status'] == 'needs_review' for f in hits))
        self.assertIn('其余4处未展开', coverage[-1]['detail'])

    def test_actual_pinned_teacher_evidence_verified_without_catalog_changes(self):
        rules = checks.load_additional_rules()
        self.assertEqual([r['id'] for r in rules], ['NP004', 'NP005', 'NP006', 'NP007'])
        self.assertTrue(all(r['recurrence_status'] == 'single_student_feedback' for r in rules))
        with patch.object(checks, '_digest', return_value='wrong'):
            with self.assertRaises(ValueError):
                checks.load_additional_rules()


if __name__ == '__main__':
    unittest.main()
