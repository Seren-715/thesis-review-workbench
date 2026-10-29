"""Check model trust boundaries and cost accounting without external requests."""
import json
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from new_paper_agent import BoundedClient, TOOL_RULES, review_with_tools, validate_decisions, validate_decision_batch, semantic_packet_item, recover_saved_text, REVIEW_SYSTEM, reuse_semantic_evidence


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.item = {'id': 'NP001-0001', 'rule_id': 'NP001', 'text': '本研究中我们建立预测模型。', 'start': 4, 'end': 6}
        self.row = {'id': self.item['id'], 'decision': 'suggested_revision', 'quote': '我们建立预测模型',
                    'reason': '作者叙述使用第一人称。', 'suggestion': '改为“本研究建立预测模型”。'}
    def check(self, rows):
        return validate_decisions(json.dumps({'items': rows}, ensure_ascii=False), [self.item])
    def test_literal_quote_required(self):
        self.assertTrue(self.check([self.row])[self.item['id']]['validated_quote'])
        self.row['quote'] = '我建立预测模型'
        with self.assertRaises(ValueError): self.check([self.row])
    def test_quote_must_include_target(self):
        self.row['quote'] = '建立预测模型'
        with self.assertRaises(ValueError): self.check([self.row])
    def test_quote_cannot_bind_another_occurrence_in_same_paragraph(self):
        self.item.update(text='我完成实验，我们提出模型。', start=0, end=1)
        self.row['quote'] = '我们提出模型'
        with self.assertRaises(ValueError): self.check([self.row])
    def test_missing_duplicate_or_foreign_source_rejected(self):
        for rows in ([], [self.row, self.row], [dict(self.row, id='foreign')]):
            with self.assertRaises(ValueError): self.check(rows)
    def test_cannot_self_confirm_or_add_arbitrary_fields(self):
        for row in (dict(self.row, decision='confirmed_issue'), dict(self.row, command='run')):
            with self.assertRaises(ValueError): self.check([row])

    def test_one_bad_quote_keeps_other_valid_rows_without_allowing_foreign_ids(self):
        other=dict(self.item,id='NP001-0002')
        bad=dict(self.row,id=other['id'],quote='不存在的文字')
        payload=json.dumps({'items':[self.row,bad]},ensure_ascii=False)
        accepted,errors=validate_decision_batch(payload,[self.item,other])
        self.assertEqual(set(accepted),{self.item['id']})
        self.assertEqual(errors[0]['id'],other['id'])
        for rows in ([self.row,self.row],[self.row,dict(bad,id='foreign')],[self.row]):
            with self.assertRaises(ValueError):validate_decision_batch(json.dumps({'items':rows}),[self.item,other])

    def test_saved_partial_text_recovery_requires_exact_rule_text_and_context(self):
        from copy import deepcopy
        item=dict(self.item,reason='线索',teacher_evidence=[],status='candidate',semantic_required=True)
        other=dict(item,id='NP001-0002')
        rules=[{'id':'NP001','scope':'正文'}]
        report={'source_sha256':'same','rules':rules,'findings':[item,other]}
        state={'source_sha256':'same','requests':[{'number':1,'finish_reason':'stop','usage':{'total_tokens':10}}],
               'request_attempts':1,'known_total_tokens':10}
        with TemporaryDirectory() as folder:
            output=Path(folder)
            request={'messages':[{'role':'system','content':REVIEW_SYSTEM},{'role':'user','content':json.dumps({'items':[semantic_packet_item(f,{'NP001':rules[0]}) for f in [item,other]]},ensure_ascii=False)}]}
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'items':[self.row,dict(self.row,id=other['id'],quote='不存在')]},ensure_ascii=False)}}]}
            (output/'request_01.json').write_text(json.dumps(request),encoding='utf-8')
            (output/'response_01.json').write_text(json.dumps(response),encoding='utf-8')
            changed=deepcopy(report);changed['rules'][0]['scope']='致谢'
            self.assertEqual(recover_saved_text(changed,deepcopy(state),output),0)
            self.assertEqual(recover_saved_text(report,state,output),1)
            self.assertEqual(state['request_attempts'],1)
            self.assertEqual(state['known_total_tokens'],10)
            self.assertEqual(state['semantic_unreviewed'],1)
            self.assertNotIn('semantic_review',other)


class CostTests(unittest.TestCase):
    def test_agent_initial_counter_handles_structural_candidates_without_semantic_flags(self):
        from copy import deepcopy
        from hashlib import sha256
        from unittest.mock import AsyncMock, patch
        from new_paper_agent import run_review
        with TemporaryDirectory() as directory:
            source=Path(directory)/'source.docx'; source.write_bytes(b'fixed-source')
            findings=[]
            for rid,extra in [('NP022',{}),('NP004',{'semantic_required':None}),
                              ('NP010',{'semantic_required':False}),('NP016',{'semantic_required':True}),
                              ('NP001',{})]:
                findings.append(dict(id=rid,rule_id=rid,status='candidate',title=rid,text='原文',
                                     reason='本地线索',suggestion='',**extra))
            report={'source_sha256':sha256(source.read_bytes()).hexdigest(),'student_name':'学生',
                    'file_name':'source.docx','findings':findings,'rules':[],'coverage':[],
                    'summary':{'candidate_count':5,'pending_count':0},'limitations':[],'table_count':0}
            async def inspect_count(value,client,output,state,cancel_check=None):
                self.assertEqual(state['semantic_candidates'],2)
                self.assertEqual(state['request_attempts'],0)
            runner=AsyncMock(side_effect=inspect_count)
            with patch('new_paper_checks.execute_checks',return_value=deepcopy(report)),patch('new_paper_agent.review_with_tools',runner):
                result=run_review(source,Path(directory)/'review',use_agent=True,client=object(),collect_word=False)
            runner.assert_awaited_once()
            self.assertEqual(result['run']['semantic_candidates'],2)
            self.assertEqual(result['requests']['llm'],0)
            self.assertTrue((Path(directory)/'review/report.json').is_file())

    def test_resume_requires_same_source_scope_and_verbatim_decision(self):
        from copy import deepcopy
        finding={'id':'NP016-1','rule_id':'NP016','text':'这里是一句原文。','start':0,'end':9,'context':[],'check_dimensions':['通顺'],'section_complete':False}
        rule={'id':'NP016','scope':'正文片段','enabled':True,'checker':'language'}
        report={'source_sha256':'same','findings':[finding],'rules':[rule]}
        prior=deepcopy(report)
        prior['findings'][0]['semantic_review']={'id':'NP016-1','decision':'not_issue','quote':'这里是一句原文。','reason':'句子完整。','suggestion':'','validated_quote':True}
        for mutate in ('source','scope','quote','context'):
            current=deepcopy(report); old=deepcopy(prior)
            if mutate=='source':old['source_sha256']='different'
            if mutate=='scope':old['rules'][0]['scope']='致谢'
            if mutate=='quote':old['findings'][0]['semantic_review']['quote']='不存在的原文'
            if mutate=='context':old['findings'][0]['context']=[{'text':'不同上下文'}]
            self.assertEqual(reuse_semantic_evidence(current,old),0)
        self.assertEqual(reuse_semantic_evidence(report,prior),1)

    def test_empty_completed_work_does_not_pay_for_another_tool_dispatch(self):
        names = list(TOOL_RULES)
        calls = [SimpleNamespace(id=f'call_{i}', function=SimpleNamespace(name=name, arguments='{}')) for i, name in enumerate(names)]
        message = SimpleNamespace(tool_calls=calls)
        message.model_dump = lambda **kwargs: {'role': 'assistant', 'tool_calls': [
            {'id': call.id, 'type': 'function', 'function': {'name': call.function.name, 'arguments': '{}'}} for call in calls]}
        choice = SimpleNamespace(message=message, finish_reason='tool_calls')
        response = SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        response.model_dump = lambda **kwargs: {'usage': {'total_tokens': 15}}
        requests = []
        def create(**kwargs):
            requests.append(kwargs)
            return response
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        report = {'source_sha256': 'current-source', 'findings': [], 'coverage': [], 'rules': [], 'limitations': []}
        state = {'requests': [], 'request_attempts': 0, 'known_total_tokens': 0,
                 'unknown_cost_attempts': 0, 'reserved_token_bound': 0, 'tool_calls': []}
        with TemporaryDirectory() as folder:
            asyncio.run(review_with_tools(report, client, Path(folder), state))
        self.assertEqual(len(requests), 0)
        self.assertEqual(state['tool_calls'], [])
        self.assertEqual(state['tool_selection']['policy'], 'no_pending_paid_work')
        self.assertEqual(state['known_total_tokens'], 0)
        self.assertEqual(state['semantic_candidates'], 0)

    def test_budget_blocks_before_request_and_unknown_usage_remains_unknown(self):
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        state = {'requests': [], 'request_attempts': 0, 'known_total_tokens': 0,
                 'unknown_cost_attempts': 0, 'reserved_token_bound': 24000}
        with TemporaryDirectory() as folder:
            limiter = BoundedClient(client, Path(folder), state)
            with self.assertRaises(ValueError): limiter.request([{'role': 'user', 'content': 'test'}])
            self.assertEqual(calls, [])
            state['reserved_token_bound'] = 0
            with self.assertRaises(ValueError): limiter.request([{'role': 'user', 'content': 'test'}])
            self.assertEqual(len(calls), 1)
            self.assertEqual(state['unknown_cost_attempts'], 1)
            self.assertEqual(state['known_total_tokens'], 0)


if __name__ == '__main__': unittest.main()
