import html
import unittest
import xml.etree.ElementTree as ET

from expanded_paper_checks import check_expanded_rules, load_expanded_rules, W, M
from new_paper_checks import extract_paragraphs
from paper_locations import annotate_paragraphs


def p(text, level=None, extra='', style=None):
    props = '<w:pPr>' + (f'<w:outlineLvl w:val="{level - 1}"/>' if level else '') + (f'<w:pStyle w:val="{style}"/>' if style else '') + '</w:pPr>'
    return '<w:p>' + props + '<w:r><w:t>' + html.escape(text) + '</w:t></w:r>' + extra + '</w:p>'


def execute(body, library=None):
    doc = ET.fromstring(f'<w:document xmlns:w="{W[1:-1]}" xmlns:m="{M[1:-1]}"><w:body>{body}</w:body></w:document>')
    rows = annotate_paragraphs(extract_paragraphs(doc))
    return check_expanded_rules(doc, rows, load_expanded_rules(library))


class ExpandedChecksTests(unittest.TestCase):
    def test_library_does_not_bind_english_reference_count_to_citation_checker(self):
        rules = load_expanded_rules({'rules': [{'id': 'references', 'teacher_evidence': [{'quote': '要添加2篇英文文献'}]}]})
        rule = next(r for r in rules if r['id'] == 'NP008')
        self.assertEqual(rule['teacher_evidence'], [])
        self.assertEqual(rule['policy'], 'supplemental_structural_check')

    def test_reference_targets_ranges_and_duplicate_entries(self):
        body = p('1 绪论', 1) + p('研究已有[1,2]和[3-4]结果。') + p('参考文献', 1)
        body += p('[1] 第一条文献') + p('[2] 第二条文献') + p('[2] 第三条文献') + p('[3] 第四条文献')
        findings, _ = execute(body)
        rows = [r for r in findings if r['rule_id'] == 'NP008']
        self.assertEqual(len(rows), 3)
        missing = next(r for r in rows if r.get('missing_reference_numbers'))
        self.assertEqual(missing['missing_reference_numbers'], [4])
        self.assertEqual(missing['text'][missing['start']:missing['end']], '[3-4]')

    def test_no_explicit_bibliography_never_claims_missing_citations(self):
        findings, coverage = execute(p('1 研究', 1) + p('数组[9]以及引用[1]。'))
        self.assertFalse(any(r['rule_id'] == 'NP008' for r in findings))
        self.assertEqual(next(c for c in coverage if c['rule_id'] == 'NP008')['status'], 'not_applicable')

    def test_thanks_citations_and_toc_titles_not_scored(self):
        body = p('1 第一章', 1) + p('普通正文提到2.1 标题') + p('参考文献', 1) + p('[1] 文献')
        body += p('谢辞', 1) + p('感谢导师推荐[99]。')
        findings, _ = execute(body)
        self.assertFalse(any(r['rule_id'] == 'NP008' for r in findings))
        self.assertFalse(any(r['rule_id'] == 'NP009' for r in findings))

    def test_heading_duplicates_and_reverse_only_same_parent(self):
        body = p('1 第一章', 1) + p('1.2 高', 2) + p('1.1 低', 2) + p('2 第二章', 1) + p('2.1 正常', 2) + p('2.1 重复', 2)
        findings, _ = execute(body)
        rows = [r for r in findings if r['rule_id'] == 'NP009']
        self.assertEqual(len(rows), 3)
        self.assertTrue(any('1.1 低' == r['text'] for r in rows))
        self.assertFalse(any('2 第二章' == r['text'] for r in rows))

    def test_ordinary_numbered_paragraphs_not_real_headings(self):
        findings, _ = execute(p('1 研究', 1) + p('1.2 普通正文') + p('1.1 普通正文'))
        self.assertFalse(any(r['rule_id'] == 'NP009' for r in findings))

    def test_explicit_references_match_reliable_caption_and_formula(self):
        body = p('1 方法', 1) + p('', extra='<w:r><w:drawing/></w:r>') + p('图1-1 方法图')
        body += p('如图1-1所示，如图1-2所示。公式（1-1）给出定义。')
        body += p('（1-1）', extra='<m:oMathPara><m:oMath/></m:oMathPara>')
        findings, _ = execute(body)
        rows = [r for r in findings if r['rule_id'] == 'NP010']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['object_number'], [1, 2])
        self.assertEqual(rows[0]['text'][rows[0]['start']:rows[0]['end']], '如图1-2')

    def test_prose_mentions_are_not_captions_and_no_invented_targets(self):
        body = p('1 方法', 1) + p('图1-1 是一种常见形式而非本图题。') + p('如图1-1所示。')
        findings, _ = execute(body)
        rows = [r for r in findings if r['rule_id'] == 'NP010']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['object_number'], [1, 1])

    def test_semantic_requests_only_teacher_backed_in_real_scope(self):
        evidence = [{'quote': '先描述背景，再说明功能技术和效果', 'student_name': '学生'}]
        library = {'rules': [{'id': 'abstract_keywords', 'teacher_evidence': evidence, 'recurrence_status': 'single_student_feedback'}]}
        findings, coverage = execute(p('摘要') + p('本系统完成股票分析。') + p('关键词：股票；系统') + p('1 绪论', 1) + p('感谢所有人。'), library)
        rows = [r for r in findings if r['rule_id'] == 'NP011']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['semantic_required'])
        self.assertEqual(rows[0]['status'], 'candidate')
        self.assertEqual(rows[0]['text'], '本系统完成股票分析。')
        self.assertTrue(rows[0]['section_complete'])
        self.assertEqual(next(c for c in coverage if c['rule_id'] == 'NP012')['status'], 'needs_review')

    def test_semantic_request_complete_evidence_is_separate_from_truncated_preview(self):
        library = {'rules': [{'id': 'conclusion_writing', 'teacher_evidence': [{'quote': '总结成果和不足'}]}]}
        findings, _ = execute(p('6 总结与展望', 1) + p('成果' * 1200) + p('不足' * 800), library)
        row = next(f for f in findings if f['rule_id'] == 'NP012')
        self.assertTrue(row['section_complete'])
        self.assertFalse(row['preview_complete'])
        self.assertEqual(row['text_scope'], 'identified_section_complete')
        self.assertEqual(row['content_scope']['total_characters'], 4000)
        self.assertEqual(''.join(c['text'] for c in row['content_scope']['chunks']), '成果' * 1200 + '不足' * 800)
        self.assertLessEqual(row['end'] + sum(len(c['text']) for c in row['context']), 1800)

    def test_title_scope_missing_does_not_become_absent_conclusion_error(self):
        library = {'rules': [{'id': 'conclusion_writing', 'teacher_evidence': [{'quote': '总结成果和不足'}]}]}
        findings, coverage = execute(p('1 第一章', 1) + p('正文提到结论很好。'), library)
        self.assertFalse(any(f['rule_id'] == 'NP012' for f in findings))
        self.assertEqual(next(c for c in coverage if c['rule_id'] == 'NP012')['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
