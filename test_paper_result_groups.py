from unittest import TestCase, main
from paper_report import result_group, render_report

class ResultGroups(TestCase):
    def test_candidate_and_visual_are_not_automatic_changes(self):
        for row in ({'status':'candidate'},{'status':'needs_review'},{'status':'candidate','semantic_review':{'decision':'needs_review'}}):
            self.assertEqual(result_group(row),'pending')
        self.assertEqual(result_group({'status':'candidate','semantic_review':{'decision':'not_issue','validated_quote':True}}),'compliant')
        self.assertEqual(result_group({'status':'candidate','semantic_review':{'decision':'suggested_revision','validated_quote':True}}),'changes')
        self.assertEqual(result_group({'status':'confirmed_issue'}),'changes')

    def test_report_keeps_each_item_once_in_its_own_group(self):
        rows=[{'title':decision,'status':'candidate','reason':'依据','text':'原文','semantic_review':{'decision':decision,'reason':'理由','validated_quote':True}} for decision in ('needs_review','not_issue','suggested_revision')]
        report={'student_name':'甲','file_name':'甲.docx','source_sha256':'hash','findings':rows}
        rendered=render_report(report)
        self.assertLess(rendered.index('### suggested_revision'),rendered.index('## 符合当前规则'))
        self.assertLess(rendered.index('### not_issue'),rendered.index('## 需人工核验'))
        self.assertEqual(rendered.count('### needs_review'),1)

    def test_compliant_item_does_not_repeat_local_revision_advice(self):
        row={'title':'语境核查','status':'candidate','reason':'程序提取线索','text':'原文',
             'suggestion':'原始程序的修改提示',
             'semantic_review':{'decision':'not_issue','reason':'属于致谢','quote':'原文','validated_quote':True,
                                'suggestion':'模型冗余修改提示'}}
        report={'student_name':'甲','file_name':'甲.docx','source_sha256':'hash','findings':[row]}
        rendered=render_report(report)
        self.assertIn('符合当前规则',rendered)
        self.assertIn('属于致谢',rendered)
        self.assertNotIn('原始程序的修改提示',rendered)
        self.assertNotIn('模型冗余修改提示',rendered)

    def test_unreviewed_automatable_items_are_not_manual_or_errors(self):
        image={'rule_id':'NP007','status':'needs_review','figure_evidence':{'reviewable':True}}
        text={'rule_id':'NP016','status':'candidate','semantic_required':True,'semantic_review':{'decision':'needs_review','validated_quote':False}}
        self.assertEqual(result_group(image),'unreviewed')
        self.assertEqual(result_group(text),'unreviewed')
        self.assertEqual(result_group({'rule_id':'NP002','status':'needs_review'}),'pending')
        image['vision_review']={'validated_image':True,'decision':'needs_review'}
        self.assertEqual(result_group(image),'pending')

    def test_unvalidated_decision_cannot_be_a_pass_or_a_revision(self):
        for decision in ('not_issue','suggested_revision'):
            self.assertEqual(result_group({'rule_id':'NP016','status':'candidate','semantic_required':True,
                                          'semantic_review':{'decision':decision,'validated_quote':False}}),'unreviewed')

    def test_merged_heading_family_and_occurrences_render_once(self):
        headings={'id':'NP002-0001','rule_id':'NP002','title':'相关标题疑似合章或处于下级层次','status':'candidate',
                  'reason':'共 2 个相关标题不满足分章要求：…','text':'第三章 需求分析与系统设计','heading_level':1,
                  'headings':[{'title':'第三章 需求分析与系统设计','paragraph_index':3,'heading_level':1,
                               'kind':'compound_heading','location':None},
                              {'title':'3.1 需求分析','paragraph_index':4,'heading_level':2,
                               'kind':'subordinate_heading','location':None}]}
        first={'id':'NP001-0001','rule_id':'NP001','title':'第一人称表述待语境核对','status':'candidate',
               'text':'我完成了实验，我们验证了方法。','reason':'……本段共 2 处，合为一条改写建议。','start':0,'end':1,
               'occurrences':[{'start':0,'end':1,'quoted_context':False},{'start':7,'end':8,'quoted_context':False}]}
        report={'student_name':'甲','file_name':'甲.docx','source_sha256':'hash','findings':[first,headings]}
        rendered=render_report(report)
        self.assertEqual(rendered.count('### 相关标题疑似合章或处于下级层次'),1)
        self.assertIn('涉及标题：“第三章 需求分析与系统设计”（1 级标题）；“3.1 需求分析”（2 级标题）',rendered)
        self.assertEqual(rendered.count('### 第一人称表述待语境核对'),1)
        self.assertIn('本段共 2 处',rendered)

if __name__=='__main__':main()
