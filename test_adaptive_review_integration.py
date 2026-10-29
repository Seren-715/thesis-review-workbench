"""Offline integration: model chooses a subset, reads a gap and rejudges once."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from new_paper_agent import review_with_tools,EVIDENCE_REVIEW_SYSTEM
from adaptive_evidence_agent import SYSTEM as PLANNER_SYSTEM


class SourceGapProvider:
    def __init__(self):
        self.calls=[];self.text_calls=0
        self.chat=NS(completions=NS(create=self.create))
    def create(self,**request):
        self.calls.append(deepcopy(request));system=request['messages'][0]['content']
        if system==PLANNER_SYSTEM:
            packet=json.loads(request['messages'][1]['content']);gap=packet['gaps'][0]
            chosen=gap['extra_paragraphs'][-1]['paragraph_index']
            tc=[NS(id='extra_read',function=NS(name='read_context',arguments=json.dumps({
                'finding_id':gap['finding_id'],'paragraph_indices':[chosen]})))]
            content=None;finish='tool_calls'
        elif request.get('tools'):
            tc=[NS(id='chosen_tool',function=NS(name='inspect_first_person',arguments='{}'))]
            content=None;finish='tool_calls'
        else:
            assert system==EVIDENCE_REVIEW_SYSTEM
            self.text_calls+=1;packet=json.loads(request['messages'][1]['content']);item=packet['items'][0]
            content=json.dumps({'items':[{'id':item['id'],'decision':'needs_review' if self.text_calls==1 else 'not_issue',
                'evidence_id':item['evidence_snippets'][0]['evidence_id'],
                'reason':'上下文不足，需查看后续段落。' if self.text_calls==1 else '补读原文说明这是角色提示词例子。',
                'suggestion':'结合下文判断。' if self.text_calls==1 else ''}]},ensure_ascii=False)
            tc=None;finish='stop'
        dump={'role':'assistant','content':content}
        if tc:dump['tool_calls']=[{'id':c.id,'type':'function','function':{'name':c.function.name,'arguments':c.function.arguments}} for c in tc]
        message=NS(content=content,tool_calls=tc);message.model_dump=lambda **kw:deepcopy(dump)
        response=NS(choices=[NS(finish_reason=finish,message=message)],
            usage=NS(prompt_tokens=80,completion_tokens=20,total_tokens=100))
        response.model_dump=lambda **kw:{'choices':[{'finish_reason':finish,'message':deepcopy(dump)}]}
        return response


class AdaptiveReviewIntegrationTests(unittest.TestCase):
    def test_selected_tools_preserve_coverage_and_new_evidence_is_rejudged(self):
        text='我们完成模型建立。'
        report={'source_sha256':'a'*64,'student_name':'合成验收','rules':[{'id':'NP001','scope':'作者正文叙述'}],
            'coverage':[],'limitations':[],
            'paragraphs':[{'index':0,'text':'系统实现','heading_level':1},
                          {'index':1,'text':text},{'index':2,'text':'说明中存在一个角色。'},
                          {'index':3,'text':'后续说明：此处是对话例子，不是作者本人叙述。'}],
            'findings':[{'id':'NP001-1','rule_id':'NP001','status':'candidate','paragraph_index':1,
                         'text':text,'start':0,'end':1,'context':[],'teacher_evidence':[],'reason':'第一人称线索'}]}
        state={'source_sha256':report['source_sha256'],'requests':[],'request_attempts':0,
            'known_total_tokens':0,'unknown_cost_attempts':0,'reserved_token_bound':0,'tool_calls':[],
            'max_requests':6,'max_reserved_token_bound':60000,'review_scope':'full'}
        provider=SourceGapProvider()
        with TemporaryDirectory() as directory:
            asyncio.run(review_with_tools(report,provider,Path(directory),state))
        self.assertEqual(state['tool_selection']['selected'],['inspect_first_person'])
        self.assertTrue(state['tool_selection']['local_check_coverage_preserved'])
        self.assertEqual(state['adaptive_planner_calls'],1)
        self.assertEqual([r['plan']['phase'] for r in state['requests']],['tools','text','adaptive_evidence','text'])
        finding=report['findings'][0]
        self.assertEqual(finding['previous_scope_review']['decision'],'needs_review')
        self.assertEqual(finding['semantic_review']['decision'],'not_issue')
        self.assertTrue(any('此处是对话例子' in p['text'] for p in finding['context']))
        self.assertEqual(state['known_total_tokens'],400)
        self.assertEqual(len(provider.calls),4)


if __name__=='__main__':unittest.main()
