"""Meaningful refusal/recovery checks, temporary mock ledgers; no SVN/API/Word."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from openai.types.chat import ChatCompletion
import pipeline_candidates as candidate
import paper_pipeline as pipeline

EXTRACTED={'samples':[
 {'sample_id':'P001','teacher_batch':r'D:\svn_agent\data\svn_exports\P001_20261007_103650_661507',
  'evidence_dir':r'D:\svn_agent\data\svn_exports\P001_20261007_103650_661507\comment_evidence_20261007_104915_363894'},
 {'sample_id':'P003','teacher_batch':r'D:\svn_agent\data\svn_exports\cross_student_20261007_113217_741043\P003',
  'evidence_dir':r'D:\svn_agent\data\svn_exports\cross_student_20261007_113217_741043\P003\comment_evidence_20261007_113843_679890'}]}


class FakeModel:
    def __init__(self,mode):
        self.mode=mode;self.calls=0
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self,**request):
        self.calls+=1
        if self.mode=='network_error': raise RuntimeError('private-http-body-must-not-be-saved')
        items=json.loads(request['messages'][1]['content'])['items']
        count=1 if self.mode=='single_student' else 2
        sources=[{'ref':row['ref'],'teacher_quote':row['teacher_comment'][:30],
                  'original_quote':row['original_segments'][0][:30] if row['original_segments'] else ''} for row in items[:count]]
        if self.mode=='bad_quote': sources[0]['teacher_quote']='invented evidence not in source'
        answer={'groups':[{'category':'example','check_item':'mock candidate','scope':'pilot only',
                           'check_mode':'manual','check_method':'manual review','reason':'mock proposal','sources':sources}]}
        raw={'id':'offline','object':'chat.completion','created':0,'model':'offline',
             'choices':[{'index':0,'finish_reason':'length' if self.mode=='length' else 'stop',
                         'message':{'role':'assistant','content':json.dumps(answer)}}],
             'usage':None if self.mode=='missing_usage' else {'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
        response=ChatCompletion.model_validate(raw)
        if self.mode=='invalid_usage': response.usage.total_tokens=999
        return response


def main():
    data=candidate.payload(EXTRACTED)
    assert len(data['items'])<=12 and len({r['sample'] for r in data['items'][:2]})==2
    for mode in ('valid','single_student','bad_quote','length','missing_usage','invalid_usage','network_error'):
        with tempfile.TemporaryDirectory(dir=r'D:\svn_agent\data') as temp:
            folder=Path(temp); model=FakeModel(mode)
            result=candidate.classify(EXTRACTED,folder,model)
            assert model.calls==1 and result['request_attempts']==1
            if mode in ('valid','single_student'):
                assert result['status']=='validated_candidates_review_required'
                expected='single_student_candidate' if mode=='single_student' else 'cross_student_feedback_candidate'
                assert result['groups'][0]['recurrence_status']==expected
                assert result['groups'][0]['activation_status']=='not_activated_pending_semantic_and_scope_review'
            else:
                assert result['status']=='failed_no_retry'
                assert not (folder/'candidate_rules.json').exists()
                assert result['unknown_cost_attempts']==(1 if mode in ('missing_usage','invalid_usage','network_error') else 0)
                if mode!='network_error': assert (folder/'response.json').exists()
                assert 'private-http-body' not in (folder/'run.json').read_text()
        print('PASS candidate mock:',mode)
    with tempfile.TemporaryDirectory(dir=r'D:\svn_agent\data') as temp:
        original_save=candidate.save
        def fail_response(path,value):
            if Path(path).name=='response.json': raise OSError('mock response write failure')
            original_save(path,value)
        with patch.object(candidate,'save',side_effect=fail_response):
            result=candidate.classify(EXTRACTED,Path(temp),FakeModel('valid'))
        assert result['status']=='failed_no_retry' and result['known_total_tokens']==130 and result['unknown_cost_attempts']==0
    print('PASS returned usage survives raw-response write failure')
    # Stage resume checks use stubs; actual selected evidence above remains real.
    import pipeline_sources as sources
    config=json.loads((pipeline.ROOT/'pipeline_demo.json').read_text(encoding='utf-8'))
    sources.validate_config(config)
    with tempfile.TemporaryDirectory(dir=r'D:\svn_agent\data') as temp:
        with patch.object(pipeline,'RUNS',Path(temp)):
            folder,state=pipeline.new_run('new_batch',config)
        calls=[]
        def stage(name,value):
            def execute(*args): calls.append(name); return value
            return execute
        def fail_check(*args): calls.append('check'); raise ValueError('mock check failure')
        cost={'status':'validated_candidates_review_required','request_attempts':0,'known_total_tokens':0,'unknown_cost_attempts':0,'groups':[]}
        with patch.object(sources,'export_samples',side_effect=stage('export',{'samples':[]})), \
             patch.object(sources,'extract_samples',side_effect=stage('extract',{'samples':[]})), \
             patch.object(candidate,'payload',return_value={'items':[]}), \
             patch.object(candidate,'classify',side_effect=stage('candidates',cost)), \
             patch.object(sources,'check_samples',side_effect=fail_check):
            _,state=pipeline.run_pipeline(folder,state)
            assert state['status']=='failed_no_retry' and calls==['export','extract','candidates','check']
            try: pipeline.run_pipeline(folder,state); raise AssertionError('Failed stage retried')
            except ValueError: pass
            assert calls==['export','extract','candidates','check']
        # Completed predecessors can resume before the next stage starts.
        (folder/'check').rename(folder/'failed_check_mock')
        state['steps']=state['steps'][:3];state['status']='running'
        with patch.object(sources,'check_samples',side_effect=stage('check-resumed',{'status':'clues_only'})):
            _,state=pipeline.run_pipeline(folder,state)
            assert state['status']=='awaiting_rule_and_context_review' and calls[-1]=='check-resumed'
        artifact=Path(state['steps'][0]['artifacts'][0]['path']);artifact.write_text('{}')
        try: pipeline.run_pipeline(folder,state); raise AssertionError('Changed artifact admitted')
        except ValueError: pass
    print('PASS checkpoint resume, failed-stage no-retry and completed artifact change refusal')
    # Full integration uses real original DOCX bytes with mocked SVN transport.
    p001=Path(EXTRACTED['samples'][0]['teacher_batch'])
    documents={300:p001/'P001_r300_mayy_01.docx',247:p001/'P001_r247_mayy_02.docx',
               194:pipeline.REVIEW.parents[0]/'P001_20261007_103650_661507'/'absent'}
    from verified_context_review_pilot import load_verified_context_review
    documents[194]=Path(load_verified_context_review(pipeline.REVIEW)['review']['documents'][0]['source']['document_path'])
    def svn_mock(argv,*,cwd):
        if argv[1]=='info':
            return b'<info><entry revision="502"><repository><root>svn://101.33.242.194/grad</root></repository></entry></info>'
        if argv[1]=='log': return (p001/'svn_log.xml').read_bytes()
        if argv[1]=='export':
            revision=int(argv[argv.index('-r')+1]); Path(argv[-2]).write_bytes(documents[revision].read_bytes()); return b''
        raise AssertionError('Unexpected SVN operation')
    with tempfile.TemporaryDirectory(dir=r'D:\svn_agent\data') as temp:
        with patch.object(pipeline,'RUNS',Path(temp)):
            folder,state=pipeline.new_run('new_batch',config)
        model=FakeModel('valid')
        with patch.object(sources,'_default_invoke',side_effect=svn_mock):
            _,state=pipeline.run_pipeline(folder,state,client_factory=lambda:model)
        assert state['status']=='awaiting_rule_and_context_review',state
        assert model.calls==1 and state['new_api_requests']==1 and state['new_known_tokens']==130
        exported=pipeline.read(folder/'export'/'result.json')
        assert exported['svn_request_attempts']==5
        checked=pipeline.read(folder/'check'/'result.json')['samples'][0]
        assert checked['first_person_clues']['retained_context_candidates']==11
        assert checked['rule_activation_status']=='not_activated' and checked['confirmed_error_count'] is None
        assert pipeline.read(folder/'candidates'/'candidate_rules.json')['groups'][0]['recurrence_status']=='single_student_candidate'
    print('PASS full configurable P001 integration with mock SVN/model, real source bytes, review gate')
    print('No real API/SVN/Word calls; mocks are not actual scientific results or cost.')


if __name__=='__main__': main()
