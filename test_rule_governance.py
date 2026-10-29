import unittest
from rule_governance import build_review_package


class RuleGovernanceTests(unittest.TestCase):
    def test_real_associations_not_taxonomy_checker_strings(self):
        library = {'comments': [], 'rules': [
            {'id': 'known', 'title': 'Known', 'checker': 'known', 'supporting_student_ids': ['1', '2', '3'], 'teacher_evidence': []},
            {'id': 'unknown', 'title': 'Unknown', 'checker': 'unknown', 'supporting_student_ids': ['4', '5'], 'teacher_evidence': []}]}
        package = build_review_package(library, [{'id': 'NP001', 'family': 'known', 'scope': 'bounded',
                                                'teacher_evidence': [{'quote': 'teacher literal'}]}])
        self.assertEqual(package['summary']['no_direct_checker_link'], 1)
        self.assertEqual(package['rules'][1]['checker_association'], 'no_direct_checker_link')
        self.assertEqual(package['rules'][0]['recurrence_status'], 'common_candidate')
        self.assertEqual(package['rules'][1]['recurrence_status'], 'cross_student_candidate')
        self.assertEqual(package['status'], 'human_review_pending')
        self.assertTrue(all(r['human_signoff']['status'] == 'pending' for r in package['rules']))
        self.assertTrue(all(not r['automatic_error_judgment_enabled'] for r in package['rules']))

    def test_source_examples_are_not_promoted_to_human_truth(self):
        source = {'source_sha256': 'a' * 64, 'comment_id': '2', 'student_id': '1',
                  'quote': 'teacher literal', 'anchor': {'text': 'original literal'}}
        package = build_review_package({'rules': [{'id': 'known', 'title': 'Known',
                                                  'supporting_student_ids': ['1'], 'teacher_evidence': [source]}]}, [])
        rule = package['rules'][0]
        self.assertEqual(rule['observed_examples'][0]['teacher_quote'], 'teacher literal')
        self.assertEqual(rule['observed_examples'][0]['original_excerpt'], 'original literal')
        self.assertEqual(rule['positive_examples']['items'], [])
        self.assertEqual(rule['negative_examples']['items'], [])
        self.assertFalse(rule['checker_ids'])

    def test_supplemental_or_disabled_checker_is_not_teacher_rule_link(self):
        library = {'rules': [{'id':'known', 'title':'Known', 'supporting_student_ids':['1'],
                              'teacher_evidence':[{'quote':'teacher literal'}]}]}
        methods = [{'id':'NP009','family':'known','enabled':True,'teacher_evidence':[]},
                   {'id':'NP042','family':'known','enabled':False,'teacher_evidence':[{'quote':'teacher literal'}]}]
        package = build_review_package(library, methods)
        self.assertEqual(package['summary']['no_direct_checker_link'], 1)
        self.assertFalse(package['rules'][0]['checker_ids'])


if __name__ == '__main__': unittest.main()
