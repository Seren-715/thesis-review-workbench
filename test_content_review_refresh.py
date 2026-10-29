from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import asyncio

from docx import Document
from atomic_json import write_json, read_json
from new_paper_checks import execute_checks
from content_review_refresh import refreshed_content, refresh_saved_content, content_rule_ids
from paper_report import render_report


class ContentRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.docx'
        d = Document()
        d.add_heading('摘要',1)
        d.add_paragraph('本研究设计教学辅助系统，解决学习进度监督问题。')
        d.add_paragraph('使用Spring和MySQL实现题目管理与学生练习，提高教学效率。')
        d.add_paragraph('关键词：教学；系统')
        d.add_heading('绪论',1)
        d.add_heading('论文组织',2)
        d.add_paragraph('第五章是实现部分。')
        d.add_paragraph('最后是论文对本课题进行总结。')
        d.add_heading('系统实现',1)
        d.add_heading('用户登录',2)
        d.add_paragraph('使用Spring查询user表，校验密码后进入主页。')
        d.add_heading('结论',1)
        d.add_paragraph('实现题目管理；后续完善性能测试。')
        d.save(self.source)
        self.prior = execute_checks(self.source)
        self.prior['findings'].append({'id':'NP010-paid','rule_id':'NP010','status':'candidate','title':'旧引用诊断',
            'text':'见图4-8','reason':'已核对','suggestion':'改为图5-5',
            'semantic_review':{'validated_quote':True,'decision':'suggested_revision','quote':'见图4-8','reason':'编号错误'}})

    def tearDown(self):
        self.temp.cleanup()

    def test_true_implementation_excludes_chapter_organization(self):
        result = refreshed_content(self.source, self.prior)
        f = next(f for f in result['findings'] if f['rule_id']=='NP013')
        text = ''.join(c['text'] for c in f['content_scope']['chunks'])
        self.assertIn('查询user表',text)
        self.assertNotIn('最后是论文',text)
        self.assertEqual(f['location']['chapter'],'系统实现')

    def test_selective_refresh_preserves_paid_non_content_decisions_and_source(self):
        before = deepcopy(self.prior)
        digest = sha256(self.source.read_bytes()).hexdigest()
        result = refreshed_content(self.source,self.prior)
        self.assertEqual(self.prior,before)
        self.assertEqual(digest,sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(next(f for f in result['findings'] if f['id']=='NP010-paid'),before['findings'][-1])

    def test_no_model_update_creates_new_receipt_without_changing_old(self):
        previous = self.root / 'review_old';previous.mkdir()
        write_json(previous/'report.json',self.prior)
        write_json(self.root/'paper.json',{'id':'paper_test','student_name':'测试学生','status':'completed',
            'review_id':'review_old','sha256':self.prior['source_sha256']})
        old_hash = sha256((previous/'report.json').read_bytes()).hexdigest()
        result = refresh_saved_content(self.root)
        self.assertEqual(result['new_requests'],0)
        self.assertEqual(result['completion']['reviewed'],0)
        self.assertGreaterEqual(result['completion']['total'],3)
        self.assertEqual(old_hash,sha256((previous/'report.json').read_bytes()).hexdigest())
        report = read_json(self.root/result['review_id']/'report.json')
        self.assertEqual(result['completion']['total'], sum(bool(f.get('content_scope')) for f in report['findings']))
        self.assertIn('实际阅读 0/',render_report(report))
        self.assertEqual(read_json(self.root/'paper.json')['previous_review_id'],'review_old')

    def test_source_mismatch_is_rejected(self):
        self.prior['source_sha256']='changed'
        with self.assertRaises(ValueError):refreshed_content(self.source,self.prior)

    def test_new_registered_content_rules_refresh_and_preserve_unrelated_receipts(self):
        current = execute_checks(self.source)
        current['rules'].append({'id':'NP099','checker':'complete_section_semantics'})
        current['findings'].append({'id':'NP099-0001','rule_id':'NP099','status':'candidate',
            'semantic_required':True,'content_scope':{'chunks':[]},'paragraph_index':1})
        current['coverage'].append({'rule_id':'NP099','status':'partial'})
        with patch('content_review_refresh.execute_checks',return_value=current):
            result = refreshed_content(self.source,self.prior)
        self.assertIn('NP099', content_rule_ids(result))
        self.assertEqual(sum(f['rule_id']=='NP099' for f in result['findings']),1)
        self.assertEqual(next(f for f in result['findings'] if f['id']=='NP010-paid'),self.prior['findings'][-1])

    def test_content_scope_is_discovered_without_a_registered_checker_name(self):
        report = {'rules':[{'id':'NP099','checker':'future_content_checker'}],
            'findings':[{'rule_id':'NP099','semantic_required':True,'content_scope':{'chunks':[]}},
                        {'rule_id':'NP002','semantic_required':True}]}
        self.assertEqual(content_rule_ids(report), {'NP099'})

    def test_content_dispatch_does_not_review_other_objects(self):
        from test_content_context_agent import sample, FakeBudget
        from new_paper_agent import review_with_tools
        report, finding, _ = sample()
        unrelated = {'id':'NP001-unread','rule_id':'NP001','status':'candidate','text':'我们完成研究。',
                     'semantic_required':True,'start':0,'end':1}
        report['findings'].append(unrelated)
        state = {'review_scope':'content','requests':[]}
        with patch('new_paper_agent.BoundedClient',side_effect=lambda client, output, ledger, names=():FakeBudget(ledger)):
            asyncio.run(review_with_tools(report, None, self.root, state))
        self.assertTrue(finding['semantic_review']['validated_scope'])
        self.assertNotIn('semantic_review',unrelated)
        self.assertEqual(state['completion']['total'],1)
        self.assertEqual(state['completion']['reviewed'],1)
        self.assertEqual([c['name'] for c in state['tool_calls']],['read_verified_content_scope'])

    def test_old_fragment_receipt_does_not_settle_complete_scope(self):
        from test_content_context_agent import sample
        from paper_report import result_group
        report, finding, _ = sample()
        finding['semantic_review'] = {'validated_quote':True,'decision':'not_issue'}
        self.assertEqual(result_group(finding),'unreviewed')

    def test_coverage_requires_complete_review_and_tracks_actual_reads(self):
        from test_content_context_agent import sample, FakeBudget
        from content_context_agent import ContextReviewQueue
        from new_paper_agent import update_review_progress
        report, finding, _ = sample()
        report['coverage']=[{'rule_id':'NP011','detail':'原文已提取','status':'partial'}]
        state={}
        update_review_progress(report,state)
        self.assertFalse(report['coverage'][0]['semantic_review_complete'])
        queue=ContextReviewQueue(report,state,self.root)
        queue.step(FakeBudget(state))
        self.assertTrue(report['coverage'][0]['semantic_review_complete'])
        self.assertEqual(report['coverage'][0]['read_chunks'],2)
        self.assertEqual(report['coverage'][0]['status'],'checked')


if __name__=='__main__':unittest.main()
