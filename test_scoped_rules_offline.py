"""Scoped evidence execution/refusal tests; no real API/SVN/Word."""
import asyncio
import contextlib
import io
from pathlib import Path
import tempfile
from unittest.mock import patch
from nanobot.agent.tools.registry import ToolRegistry,is_tool_error_result
import reviewed_rule_catalog as catalog_reader
import scoped_rule_checks as checks
from check_rules_pilot import check_formula_labels


def main():
    catalog=catalog_reader.load_catalog(catalog_reader.BATCH)
    assert len(catalog['rules'])==6
    assert [r['ref'] for r in catalog['rules'][1]['sources']]==['C004','C006']
    assert [r['ref'] for r in catalog['rules'][2]['sources']]==['C011']
    assert [r['ref'] for r in catalog['rules'][4]['sources']]==['C010']
    assert [r['ref'] for r in catalog['rules'][5]['sources']]==['C012']
    assert all(r['automatic_error_judgment_enabled'] is False for r in catalog['rules'])
    real_load=checks.load_catalog
    with tempfile.TemporaryDirectory(dir=r'D:\svn_agent\data') as temporary:
        with patch.object(checks,'load_catalog',side_effect=lambda unused:real_load(catalog_reader.BATCH)),contextlib.redirect_stdout(io.StringIO()):
            folder,state=asyncio.run(checks.execute(Path(temporary)))
        assert state['status']=='scoped_checks_completed_review_pending',state
        assert len(state['tool_attempts'])==6 and all(t['status']=='completed' for t in state['tool_attempts'])
        from paper_pipeline import read
        results=read(folder/'checks.json')['rules']
        first=results[0]['result']; formula=results[1]['result']['formula_label_evidence']
        assert first['reviewed_change_candidate_count']==11 and first['user_confirmed_count']==1 and first['codex_reviewed_count']==10
        assert formula['status']=='partial' and formula['formula_label_count']==0 and formula['duplicate_group_count'] is None
        assert formula['image_label_count']==4 and formula['unbound_label_count']==1 and formula['text_label_count']==5
        assert all(r['result']['confirmed_error_count'] is None for r in results[2:])
        assert [r['result']['execution_status'] for r in results[2:]]==['manual_scope_alignment_required']*2+['visual_review_required']*2
        assert state['model_requests']==state['svn_requests']==state['word_launches']==state['new_tokens']==0
    print('PASS source-bound six local tools, attribution, partial formula and pending scopes')
    try:
        catalog_reader.load_catalog(catalog_reader.BATCH.parent/'unreviewed-batch')
        raise AssertionError('New sample inherited scoped policy')
    except ValueError: print('PASS unreviewed batch rejected')
    real_digest=catalog_reader.digest
    victim=catalog_reader.BATCH/'semantic_review.json'
    with patch.object(catalog_reader,'digest',side_effect=lambda path:'0'*64 if Path(path).resolve()==victim.resolve() else real_digest(path)):
        try: catalog_reader.load_catalog(catalog_reader.BATCH);raise AssertionError('Changed review admitted')
        except ValueError: print('PASS changed scoped review rejected (mocked)')
    import copy
    wrong=copy.deepcopy(catalog['initial_source']);wrong['revision']=195
    document=checks.read_check_document(Path(wrong['document_path']),wrong)
    try:
        checks.first_person_result(wrong,document['body_paragraphs'])
        raise AssertionError('Source revision inherited judgment')
    except ValueError: print('PASS judgment transfer to changed revision rejected')
    # Two identical saved OMML labels generate clues, no automatic verdict.
    paragraphs=[{'index':i,'text':'x\t公式（2-1）','contains_math':True,'contains_nonempty_math':True,
                 'image_count':0,'image_relationship_ids':[], 'in_table':False,'in_textbox':False,'in_deleted':False} for i in (1,2)]
    result=check_formula_labels(paragraphs)
    assert result['duplicate_group_count']==1 and result['sequence_flags'][0]['status']=='needs_review'
    registry=ToolRegistry();tool=checks.ScopedRuleTool(catalog['rules'][0],catalog['initial_source']);registry.register(tool)
    _,_,error=registry.prepare_call(tool.name,{'edit':True})
    assert error is not None and tool.result is None
    print('PASS duplicate-label clues and unexpected tool parameters rejected')
    print('All offline checks passed; temporary outputs removed. No model/SVN/Word requests.')


if __name__=='__main__':main()
