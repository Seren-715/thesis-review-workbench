import unittest
from copy import deepcopy
from xml.etree import ElementTree as E

from remaining_teacher_feedback_checks import (DEFINITIONS, load_remaining_feedback_rules,
    check_remaining_feedback, bind_remaining_figure_context)
from teacher_format_checks import W


def library():
    return {'rules': [dict(id=family, teacher_evidence=[dict(quote='片段',
        comment_text=('字的颜色不对' if family == 'text_color' else '完整教师原话'),
        anchor={'text': '原批注选区'}, canonical_identity=['student', 'path', 'sha'])])
        for _, _, family in DEFINITIONS]}


def rows():
    return [dict(index=0, text='摘要', heading_level=1), dict(index=1, text='短而具体的摘要。'),
        dict(index=2, text='关键词：系统；设计'), dict(index=3, text='1 系统设计', heading_level=1),
        dict(index=4, text='表1-1 数据表'), dict(index=5, text='字段解释', in_table=True),
        dict(index=6, text='后文给出实现与测试依据。'),
        dict(index=7, text='致谢', heading_level=1), dict(index=8, text='我感谢老师。')]


def doc(paragraphs, colored=False):
    root = E.Element(W+'document')
    for p in paragraphs:
        node = E.SubElement(root, W+'p'); run = E.SubElement(node, W+'r')
        if colored and p['index'] == 6:
            props = E.SubElement(run, W+'rPr')
            E.SubElement(props, W+'color', {W+'val':'FF0000'})
        E.SubElement(run, W+'t').text = p['text']
    return root


class RemainingFeedbackTests(unittest.TestCase):
    def test_sources_and_independent_scopes(self):
        lib = library(); before = deepcopy(lib)
        lib['rules'][0]['teacher_evidence'].append(dict(quote='不要用黑底的，用白底', anchor={'text':'图3-1 架构'}))
        rules = load_remaining_feedback_rules(lib)
        self.assertEqual(rules[0]['teacher_evidence'][0]['quote'], '字的颜色不对')
        self.assertEqual(rules[0]['teacher_evidence'][0]['color_requirement_scope'], 'text_unspecified_target')
        self.assertEqual(rules[0]['teacher_evidence'][1]['color_requirement_scope'], 'figure')
        self.assertEqual(before['rules'][0]['teacher_evidence'][0], lib['rules'][0]['teacher_evidence'][0])
        self.assertTrue(all(not r['automatic_error_judgment_enabled'] for r in rules))

    def test_color_is_external_standard_gap_not_auto_red_error(self):
        ps = rows(); fs, cs = check_remaining_feedback(doc(ps, True), E.Element(W+'styles'), ps,
            load_remaining_feedback_rules(library()), 'sha')
        f = next(f for f in fs if f['rule_id'] == 'NP058')
        self.assertEqual(f['status'], 'needs_review')
        self.assertTrue(f['external_material_required'])
        self.assertIn('需要外部资料确认', f['suggestion'])
        self.assertEqual(f['color_evidence'][0]['properties']['val']['value'], 'FF0000')
        self.assertFalse(f['color_evidence'][0]['rendered_color_resolved'])
        self.assertEqual(next(c for c in cs if c['rule_id']=='NP058')['target_text_color'], None)

    def test_no_color_target_and_no_properties_do_not_fabricate_error(self):
        ps=rows(); fs, cs = check_remaining_feedback(doc(ps), E.Element(W+'styles'), ps,
            load_remaining_feedback_rules(library()), 'sha')
        self.assertNotIn('NP058', {f['rule_id'] for f in fs})
        self.assertGreater(next(c for c in cs if c['rule_id']=='NP058')['unknown_color_runs'], 0)

    def test_caption_complete_table_and_later_context_not_acknowledgments(self):
        ps=rows(); fs, _ = check_remaining_feedback(doc(ps), E.Element(W+'styles'), ps,
            load_remaining_feedback_rules(library()), 'sha')
        f = next(f for f in fs if f['rule_id'] == 'NP059')
        self.assertIn(5, f['content_scope']['paragraph_indices'])
        self.assertIn(6, f['content_scope']['paragraph_indices'])
        self.assertNotIn(8, f['content_scope']['paragraph_indices'])
        self.assertTrue(f['semantic_required'])
        self.assertFalse(f['vision_review_plan']['semantic_image_review_complete'])

    def test_short_abstract_not_rejected_and_reuses_true_page_checker(self):
        ps=rows(); fs, _ = check_remaining_feedback(doc(ps), E.Element(W+'styles'), ps,
            load_remaining_feedback_rules(library()), 'sha')
        f=next(f for f in fs if f['rule_id']=='NP060')
        self.assertEqual(f['status'], 'candidate')
        self.assertEqual(f['abstract_evidence']['paragraph_indices'], [1])
        self.assertIsNone(f['abstract_evidence']['minimum_word_count'])
        self.assertEqual(f['abstract_evidence']['page_limit_rule_id'], 'UP001')
        self.assertIn(1, f['content_scope']['paragraph_indices'])
        self.assertIn(6, f['content_scope']['paragraph_indices'])

    def test_unbounded_abstract_not_silently_used(self):
        ps=rows(); ps[2]['text']='只有正文没有关键词'
        fs, cs=check_remaining_feedback(doc(ps), E.Element(W+'styles'), ps,
            load_remaining_feedback_rules(library()), 'sha')
        self.assertNotIn('NP060', {f['rule_id'] for f in fs})
        self.assertEqual(next(c for c in cs if c['rule_id']=='NP060')['status'], 'partial')

    def test_caption_stale_pixels_not_visual_proof(self):
        ps=rows(); rules=load_remaining_feedback_rules(library())
        fs, cs=check_remaining_feedback(doc(ps), E.Element(W+'styles'), ps, rules, 'sha')
        fs.append(dict(id='NP007-1', rule_id='NP007', paragraph_index=4,
            figure_evidence={'source_sha256':'sha', 'preview_sha256':'pixels', 'caption':'图1-1'},
            vision_review={'validated_image':True,'source_sha256':'sha','preview_sha256':'old','context_key':'bad'}))
        report=dict(findings=fs, rules=rules, source_sha256='sha')
        bind_remaining_figure_context(report)
        plan=next(f for f in fs if f['rule_id']=='NP059')['vision_review_plan']
        self.assertFalse(plan['figures'][0]['readability_review_validated'])
        self.assertFalse(plan['semantic_image_review_complete'])
        self.assertNotIn('readability_context', plan['figures'][0])

    def test_disabled_and_empty_dont_generate_empty_cards(self):
        fs, cs=check_remaining_feedback(None,None,[],load_remaining_feedback_rules({'rules':[]}), 'sha')
        self.assertEqual(fs, [])
        self.assertEqual({c['status'] for c in cs}, {'not_applicable'})

    def test_no_figures_tables_or_captions_does_not_create_caption_scope(self):
        ps = [dict(index=0, text='1 引言', heading_level=1), dict(index=1, text='只有普通正文。')]
        fs, cs=check_remaining_feedback(doc(ps), E.Element(W+'styles'), ps,
            load_remaining_feedback_rules(library()), 'sha')
        self.assertNotIn('NP059', {f['rule_id'] for f in fs})
        self.assertEqual(next(c for c in cs if c['rule_id']=='NP059')['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()
