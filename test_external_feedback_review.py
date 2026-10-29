from copy import deepcopy
import unittest

from external_feedback_review import review_external_feedback, audit_external_feedback


def comment(text, anchor='对应原文'):
    return {'id': 'a'*64+':7', 'comment_id':'7', 'text':text, 'anchor_text':anchor,
            'source_sha256':'a'*64,'source_path':'teacher.docx','evidence_id':'teacher:7',
            'student_id':'123','student_name':'测试','revision':5}


class ExternalFeedbackTests(unittest.TestCase):
    def test_exact_message_dependency(self):
        c=comment('结构需要修改，看QQ留言')
        original=deepcopy(c)
        r=review_external_feedback(c)
        self.assertEqual(r['status'],'manual_external_information')
        self.assertEqual(r['classification_action'],'skip_llm_external_dependency')
        self.assertEqual(r['dependencies'][0]['quote'],'看QQ留言')
        self.assertEqual(r['evidence']['source_sha256'],c['source_sha256'])
        self.assertEqual(c,original)

    def test_ppt_dependency_preserves_explicit_independent_requirement(self):
        r=review_external_feedback(comment('画UML用例图，参见PPT'))
        self.assertEqual(r['dependencies'][0]['kind'],'instructional_slides')
        self.assertEqual(r['independent_requirement_fragments'][0]['quote'],'画UML用例图')
        self.assertFalse(r['rule_approval'])

    def test_upper_lower_case_and_combined_guidelines(self):
        r=review_external_feedback(comment('图序和图在同一页。格式上看一下规范和上次论文讲座ppt'))
        self.assertEqual(r['dependencies'][0]['quote'],'看一下规范和上次论文讲座ppt')
        self.assertEqual(r['independent_requirement_fragments'][0]['quote'],'图序和图在同一页')

    def test_missing_conversation_not_replaced_by_anchor_guess(self):
        r=review_external_feedback(comment('这个图不行，没有这样画的，晚上联系我吧','这是UML用例图'))
        self.assertEqual(r['dependencies'][0]['kind'],'teacher_conversation')
        self.assertEqual(r['independent_requirement_fragments'],[])
        self.assertNotIn('UML',str(r))

    def test_explicit_report_dependency_and_no_external_execution(self):
        r=review_external_feedback(comment('格式。要把格式放到论文一点通上检测一下，并且提交检测通过的报告给我'))
        self.assertEqual(r['dependencies'][0]['kind'],'format_detection_report')
        self.assertEqual(r['missing_materials'],['教师要求的外部格式检测报告'])
        self.assertEqual(r['independent_requirement_fragments'],[])

    def test_unspecified_standard_assertion_does_not_become_independent_requirement(self):
        r=review_external_feedback(comment('与我们毕业论文摘要的要求不符。你看看规范和PPT以及往届论文'))
        self.assertEqual(r['independent_requirement_fragments'],[])

    def test_optional_help_and_general_sources_are_not_external_instruction_dependencies(self):
        cases=['如有问题，可以晚上联系我吧','如果不懂，请课后找我',
               '参考文献要补充英文文献','说明数据从哪张表获取','软件采用PPT文件格式输出',
               '引用某篇论文作为理论来源','请补充源数据','联系我的API获取接口数据',
               '本文引用PPT中的某项统计数据，应说明原始来源','完善自动格式检测报告功能']
        for text in cases:
            with self.subTest(text=text):self.assertIsNone(review_external_feedback(comment(text)))

    def test_two_materials_retained_as_separate_dependencies(self):
        r=review_external_feedback(comment('看QQ留言，参见PPT'))
        self.assertEqual(len(r['missing_materials']),2)
        self.assertTrue(all(d['quote'] in r['evidence']['comment_text'] for d in r['dependencies']))

    def test_direct_phone_and_in_person_requests_have_specific_route(self):
        for text in ['电话联系我讨论这个图', '请当面找我说明如何修改']:
            with self.subTest(text=text):
                r=review_external_feedback(comment(text))
                self.assertEqual(r['dependencies'][0]['kind'],'teacher_conversation')

    def test_audit_counts_only_matches_and_claims_no_requests(self):
        r=audit_external_feedback({'comments':[comment('参见PPT'),comment('补充英文文献')], 'source_fingerprint':'abc'})
        self.assertEqual(r['comment_count'],2)
        self.assertEqual(r['matching_comments'],1)
        self.assertEqual(r['llm_requests'],0)
        self.assertEqual(r['source_fingerprint'],'abc')


if __name__=='__main__':unittest.main()
