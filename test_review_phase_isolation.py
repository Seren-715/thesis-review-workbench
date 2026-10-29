"""Exercise fair dispatch/text/vision queues and finite repairs without API costs."""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from new_paper_agent import TOOL_RULES, EVIDENCE_REVIEW_SYSTEM, review_with_tools
from paper_vision_review import SYSTEM as VISION_SYSTEM


def response(content=None, calls=None, finish='stop', usage=True):
    message=SimpleNamespace(content=content,tool_calls=calls)
    message.model_dump=lambda **kwargs: {'role':'assistant','tool_calls':[
        {'id':c.id,'type':'function','function':{'name':c.function.name,'arguments':'{}'}} for c in calls or []]}
    result=SimpleNamespace(choices=[SimpleNamespace(message=message,finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=40,completion_tokens=20,total_tokens=60) if usage else None)
    result.model_dump=lambda **kwargs: {'choices':[{'finish_reason':finish,'message':{'content':content}}]}
    return result


class PhaseIsolationTests(unittest.TestCase):
    def run_flow(self, *, unknown_cost=False, tamper=False, finish='stop'):
        with TemporaryDirectory() as folder:
            output=Path(folder);image=output/'image.png';image.write_bytes(b'fixed-preview')
            figure={'id':'NP007-1','rule_id':'NP007','text':'图1-1 用例图','status':'needs_review',
                'figure_evidence':{'reviewable':True,'source_sha256':'source','preview_path':str(image),
                                  'preview_sha256':sha256(image.read_bytes()).hexdigest()}}
            text={'id':'NP016-1','rule_id':'NP016','text':'本研究建立预测模型。','status':'candidate',
                  'semantic_required':True,'teacher_evidence':[],'reason':'核查语句','context':[]}
            report={'source_sha256':'source','findings':[figure,text],'coverage':[],
                    'rules':[{'id':'NP007','scope':'可读性'},{'id':'NP016','scope':'正文语句'}],'limitations':[]}
            state={'requests':[],'request_attempts':0,'known_total_tokens':0,'unknown_cost_attempts':0,
                   'reserved_token_bound':0,'tool_calls':[]}
            calls=[SimpleNamespace(id=f'call_{i}',function=SimpleNamespace(name=name,arguments='{}'))
                   for i,name in enumerate(TOOL_RULES)]
            actual=[]
            def create(**kwargs):
                actual.append(kwargs)
                if kwargs.get('tools'):
                    return response(calls=calls,finish='tool_calls')
                system=kwargs['messages'][0]['content']
                if system==EVIDENCE_REVIEW_SYSTEM:
                    item=json.loads(kwargs['messages'][1]['content'])['items'][0]
                    return response(json.dumps({'items':[{'id':item['id'],'decision':'not_issue',
                        'evidence_id':item['evidence_snippets'][0]['evidence_id'],
                        'reason':'句子完整，陈述清楚。','suggestion':''}]},ensure_ascii=False))
                if system==VISION_SYSTEM:
                    return response('{"items":"invalid"}',finish=finish,usage=not unknown_cost)
                raise AssertionError('Unexpected review phase')
            if tamper:image.write_bytes(b'changed')
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            if unknown_cost or tamper:
                with self.assertRaises(ValueError):asyncio.run(review_with_tools(report,client,output,state))
                self.assertEqual(len(actual),3 if unknown_cost else 0)
                if unknown_cost:
                    self.assertEqual(state['unknown_cost_attempts'],1)
                    self.assertEqual(state['known_total_tokens'],120)
                    self.assertTrue(text['semantic_review']['validated_quote'])
                    self.assertNotIn('review_blocked',figure)
                else:
                    self.assertNotIn('semantic_review',text)
            else:
                asyncio.run(review_with_tools(report,client,output,state))
                self.assertEqual(len(actual),4)
                self.assertEqual(state['known_total_tokens'],240)
                self.assertEqual(state['phase_errors'][0]['phase'],'figures')
                self.assertTrue(text['semantic_review']['validated_quote'])
                self.assertEqual(state['semantic_unreviewed'],0)
                self.assertEqual(state['vision_unreviewed'],0)
                self.assertEqual(state['vision_blocked'],1)
                self.assertEqual(figure['review_blocked']['attempts'],2)
                self.assertEqual(state['completion']['status'],'completed_with_manual')
                repairs=[r for r in state['requests'] if r.get('plan',{}).get('kind')=='output_repair']
                self.assertEqual(len(repairs),1)
                self.assertEqual(repairs[0]['plan']['ids'],['NP007-1'])
                self.assertEqual(repairs[0]['usage']['total_tokens'],60)
                self.assertNotIn('vision_review',figure)
                self.assertTrue((output/'report.json').is_file())

    def test_bad_image_shape_preserves_cost_and_does_not_block_text(self):self.run_flow()
    def test_truncated_image_output_gets_at_most_one_recorded_repair(self):self.run_flow(finish='length')
    def test_unknown_usage_stops_remaining_work_without_discarding_prior_text(self):self.run_flow(unknown_cost=True)
    def test_changed_image_still_stops_before_model_call(self):self.run_flow(tamper=True)


if __name__=='__main__':unittest.main()
