from copy import deepcopy
import unittest
from tempfile import TemporaryDirectory

from general_review_rules import attach_general_requirements, apply_general_packet
from content_context_agent import ContextReviewQueue
from shared_scope_content_queue import SharedScopeContextReviewQueue
from test_content_context_agent import sample
from test_shared_scope_content_queue import pair, FakeBudget


class GeneralRuleTests(unittest.TestCase):
    def test_stock_example_preserved_as_provenance_but_never_sent_as_requirement(self):
        original = '摘要示例：股票分析系统使用AKShare、Vue、Flask。'
        report,f,rule = sample()
        rule.update(family='abstract_keywords', title='摘要内容', teacher_evidence=[{'quote':original}])
        f['teacher_evidence'] = deepcopy(rule['teacher_evidence'])
        report['paragraphs'] = [{'index':1,'text':'题目：公共设施在线预约系统的设计与实现'},
            {'index':2,'text':'摘要','heading_level':1},
            {'index':3,'text':'本文实现公共设施在线预约功能。','location':{'section':'摘要'}}]
        attach_general_requirements(report['rules'])
        with TemporaryDirectory() as folder:
            q=ContextReviewQueue(report,{},folder)
            packet,_ = q._packet(f,q._task(f))
        self.assertEqual(packet['teacher_quotes'],[])
        self.assertNotIn('AKShare',str(packet['general_requirements']))
        self.assertIn('预约',packet['current_paper_context']['title']['text'])
        self.assertEqual(rule['teacher_evidence'][0]['quote'],original)
        self.assertFalse(rule['general_requirements']['case_specific_transfer_enabled'])

    def test_general_only_applies_even_when_current_paper_is_stock_related(self):
        rules=[{'id':'NP011','family':'abstract_keywords','title':'摘要',
                'teacher_evidence':[{'quote':'所有股票分析必须用AKShare'}]}]
        attach_general_requirements(rules)
        packet={'current_paper_title':'股票分析系统','teacher_quotes':['所有股票分析必须用AKShare']}
        apply_general_packet(packet,rules[0])
        self.assertEqual(packet['teacher_quotes'],[])
        self.assertNotIn('AKShare',str(packet))

    def test_shared_reader_also_projects_only_general_requirements(self):
        report,f,g=pair()
        attach_general_requirements(report['rules'])
        with TemporaryDirectory() as folder:
            q=SharedScopeContextReviewQueue(report,{},folder)
            budget=FakeBudget({})
            self.assertTrue(q.step(budget))
        self.assertEqual(budget.packets[0]['teacher_quotes'],[])
        self.assertEqual(len(budget.packets[0]['general_requirements']['rules']),2)
        self.assertEqual(set(budget.packets[0]['check_dimensions']),{'背景','功能','实现说明'})

    def test_unknown_color_standard_does_not_invent_black_requirement(self):
        rules=[{'id':'NP058','family':'text_color','title':'字体颜色','teacher_evidence':[{'quote':'颜色不对'}]}]
        attach_general_requirements(rules)
        self.assertIn('外部资料确认',str(rules[0]['general_requirements']))
        self.assertNotIn('必须黑色',str(rules[0]['general_requirements']))


if __name__=='__main__':unittest.main()
