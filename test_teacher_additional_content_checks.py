import unittest
from teacher_additional_content_checks import load_additional_content_rules,check_additional_content

class AdditionalContentTests(unittest.TestCase):
    def test_no_teacher_source_is_not_enabled(self):
        rules=load_additional_content_rules({'rules':[]})
        self.assertTrue(all(not r['enabled'] for r in rules))
        f,c=check_additional_content([],rules,'sha'); self.assertEqual(f,[])
        self.assertEqual({r['status'] for r in c},{'not_applicable'})
    def test_full_later_results_are_included_without_keyword_verdict(self):
        lib={'rules':[{'id':'result_demonstration','teacher_evidence':[{'quote':'结果呢？需要做一个展示。'}]}]}
        rows=[dict(index=0,text='5 测试结果',heading_level=1),dict(index=1,text='前段介绍测试'),
              dict(index=2,text='后文给出实际输入和输出：词A返回向量。')]
        f,c=check_additional_content(rows,load_additional_content_rules(lib),'sha')
        self.assertEqual(len(f),1);self.assertEqual(c[-1]['status'],'partial')
        self.assertTrue(f[0]['semantic_required'])
        self.assertIn(2,f[0]['content_scope']['paragraph_indices'])
        self.assertNotIn('semantic_review',f[0])

if __name__=='__main__':unittest.main()
