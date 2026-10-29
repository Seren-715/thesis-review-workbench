from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from model_privacy import LocalPIIMask
from review_contract import review_identity,compatible_prior
from new_paper_agent import configured_budget,run_review,reuse_semantic_evidence
from atomic_json import read_json
from docx import Document


class ContractPrivacyTests(unittest.TestCase):
    def test_contract_tracks_actual_model_prompt_parameters_and_parser(self):
        baseline=review_identity()
        from new_paper_agent import MODEL,EVIDENCE_REVIEW_SYSTEM
        with patch('new_paper_agent.MODEL',MODEL+'-new'):
            self.assertNotEqual(baseline,review_identity())
        with patch('new_paper_agent.EVIDENCE_REVIEW_SYSTEM',EVIDENCE_REVIEW_SYSTEM+'改提示'):
            self.assertNotEqual(baseline,review_identity())
        with patch('review_contract.MODEL_PARAMETERS',{'max_tokens':200}):
            self.assertNotEqual(baseline,review_identity())
        with patch('review_contract.PARSE_FILES',('new_paper_checks.py',)):
            self.assertNotEqual(baseline,review_identity())

    def test_changed_run_metadata_or_missing_contract_cannot_reuse(self):
        current={'review_contract':review_identity()}
        prior={'review_contract':deepcopy(current['review_contract']),'run':{'model':current['review_contract']['model']}}
        self.assertTrue(compatible_prior(current,prior))
        for key,value in [('model','wrong'),('parse_version','old'),('prompt_sha256','old'),('model_parameters',{'max_tokens':10})]:
            other=deepcopy(prior);other['run'][key]=value
            self.assertFalse(compatible_prior(current,other))
        self.assertFalse(compatible_prior(current,{'run':{}}))
        f={'id':'NP016-1','rule_id':'NP016','status':'candidate','text':'正确正文。','semantic_required':True,'start':None,'end':None,'context':[],'check_dimensions':[],'teacher_evidence':[]}
        report={'source_sha256':'same','findings':[f],'rules':[{'id':'NP016'}]}
        old=deepcopy(report);old['run']={'model':'old-model'}
        old['findings'][0]['semantic_review']={'validated_quote':True,'id':f['id'],'decision':'not_issue','quote':f['text'],'reason':'完整','suggestion':''}
        self.assertEqual(reuse_semantic_evidence(report,old),0)

    def test_plain_text_and_tool_output_masking_does_not_mutate_source(self):
        masker=LocalPIIMask(['张测试'])
        text='联系人：张测试，学号212000001，邮箱audit@example.invalid，手机13812345678。'
        messages=[{'role':'system','content':'固定系统提示'},{'role':'user','content':text},
                  {'role':'tool','content':text}]
        result=masker.mask_messages(messages)
        for secret in ('张测试','212000001','audit@example.invalid','13812345678'):
            self.assertNotIn(secret,result[1]['content']);self.assertNotIn(secret,result[2]['content'])
        self.assertEqual(masker.restore(result[1]['content']),text)
        self.assertEqual(messages[1]['content'],text)
        self.assertEqual(result[0],messages[0])

    def test_system_message_is_masked_even_if_it_carries_identity_text(self):
        masker=LocalPIIMask(['张测试'])
        messages=[{'role':'system','content':'批注原文：学生姓名：张测试，学号212000001。'},{'role':'user','content':'正文'}]
        result=masker.mask_messages(messages)
        for secret in ('张测试','212000001'):
            self.assertNotIn(secret,result[0]['content'])
        self.assertEqual(masker.restore(result[0]['content']),messages[0]['content'])

    def test_constant_system_prompts_are_unchanged_by_masking(self):
        from new_paper_agent import SYSTEM,REVIEW_SYSTEM,EVIDENCE_REVIEW_SYSTEM,EVIDENCE_REVIEW_SYSTEM_V2,REFERENCE_REVIEW_SYSTEM
        masker=LocalPIIMask(['张测试'])
        for prompt in (SYSTEM,REVIEW_SYSTEM,EVIDENCE_REVIEW_SYSTEM,EVIDENCE_REVIEW_SYSTEM_V2,REFERENCE_REVIEW_SYSTEM):
            self.assertEqual(masker.mask_messages([{'role':'system','content':prompt}])[0]['content'],prompt)

    def test_identity_names_skip_placeholder_and_include_teacher_evidence(self):
        from model_privacy import identity_names
        report={'student_name':'未填写姓名','rules':[{'teacher_evidence':[{'student_name':'张测试'}]}]}
        self.assertEqual(identity_names(report),['张测试'])
        self.assertEqual(identity_names({'student_name':'李小明','rules':[]}),['李小明'])

    def test_configured_budget_carries_teacher_evidence_names(self):
        report={'student_name':'','rules':[{'teacher_evidence':[{'student_name':'张老师'}]}]}
        with TemporaryDirectory() as directory:
            budget=configured_budget(None,Path(directory),{},report)
        self.assertEqual(budget.privacy.names,['张老师'])
        self.assertNotIn('张老师',budget.privacy.mask_text('批注：张老师要求分章。'))

    def test_request_dispatch_is_masked_and_quote_restored_locally(self):
        seen=[]
        def create(**request):
            seen.append(request)
            content=json.dumps({'quote':request['messages'][1]['content']},ensure_ascii=False)
            response=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=content,tool_calls=None))],
                usage=SimpleNamespace(prompt_tokens=10,completion_tokens=4,total_tokens=14))
            response.model_dump=lambda **kwargs:{'choices':[{'finish_reason':'stop','message':{'content':content}}]}
            return response
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        state={'requests':[],'request_attempts':0,'unknown_cost_attempts':0,'reserved_token_bound':0,'known_total_tokens':0}
        source='张测试的学号212000001，邮箱audit@example.invalid。'
        with TemporaryDirectory() as directory:
            budget=configured_budget(client,Path(directory),state,{'student_name':'张测试'})
            choice=budget.request([{'role':'system','content':'审阅'},{'role':'user','content':source}])
            self.assertEqual(json.loads(choice.message.content)['quote'],source)
            for secret in ('张测试','212000001','audit@example.invalid'):
                self.assertNotIn(secret,seen[0]['messages'][1]['content'])
                self.assertNotIn(secret,(Path(directory)/'request_01.json').read_text(encoding='utf-8'))
            self.assertTrue(read_json(Path(directory)/'privacy_map.json')['local_only'])

    def test_cancelled_before_start_preserves_report_and_has_no_paid_requests(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.docx';out=Path(directory)/'review'
            d=Document();d.add_heading('摘要',1);d.add_paragraph('本文开发教学系统。');d.save(path)
            result=run_review(path,out,use_agent=True,collect_word=False,cancel_check=lambda:True)
            self.assertEqual(result['run']['status'],'cancelled')
            self.assertEqual(result['run']['completion']['status'],'cancelled')
            self.assertEqual(result['requests']['llm'],0)
            self.assertTrue((out/'report.json').is_file())
            self.assertEqual(result['review_contract'],review_identity())

    def test_small_budget_stops_before_dispatch_without_provider_failure(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.docx';out=Path(directory)/'review'
            d=Document();d.add_heading('系统实现',1);d.add_paragraph('我实现了输入校验。');d.save(path)
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:self.fail('No request budget'))))
            result=run_review(path,out,use_agent=True,client=client,collect_word=False,max_total_bound=1000)
            self.assertEqual(result['requests']['llm'],0)
            self.assertNotIn('error',result['run'])
            self.assertEqual(result['run']['completion']['status'],'budget_remaining')
            self.assertTrue(result['run']['budget_stop_reason'])

    def test_cancel_during_paid_dispatch_keeps_usage_and_stops_next_request(self):
        from threading import Event
        from hashlib import sha256
        cancelled=Event();calls=[]
        def create(**request):
            calls.append(request);cancelled.set()
            call=SimpleNamespace(id='chosen',function=SimpleNamespace(name='inspect_first_person',arguments='{}'))
            dump={'role':'assistant','tool_calls':[{'id':'chosen','type':'function','function':{'name':'inspect_first_person','arguments':'{}'}}]}
            message=SimpleNamespace(content=None,tool_calls=[call]);message.model_dump=lambda **kw:deepcopy(dump)
            response=SimpleNamespace(choices=[SimpleNamespace(finish_reason='tool_calls',message=message)],
                usage=SimpleNamespace(prompt_tokens=10,completion_tokens=4,total_tokens=14))
            response.model_dump=lambda **kw:{'choices':[{'finish_reason':'tool_calls','message':dump}]}
            return response
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with TemporaryDirectory() as directory:
            path=Path(directory)/'source.docx';out=Path(directory)/'review'
            d=Document();d.add_heading('系统实现',1);d.add_paragraph('我实现了输入校验。');d.save(path)
            before=sha256(path.read_bytes()).hexdigest()
            result=run_review(path,out,use_agent=True,client=client,collect_word=False,cancel_check=cancelled.is_set)
            self.assertEqual(result['run']['status'],'cancelled')
            self.assertEqual(result['requests']['llm'],1)
            self.assertEqual(result['run']['known_total_tokens'],14)
            self.assertEqual(result['run']['unknown_cost_attempts'],0)
            self.assertEqual(len(calls),1)
            self.assertEqual(sha256(path.read_bytes()).hexdigest(),before)
            self.assertTrue((out/'response_01.json').is_file())


if __name__=='__main__':unittest.main()
