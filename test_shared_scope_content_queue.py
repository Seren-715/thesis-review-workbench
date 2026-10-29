from copy import deepcopy
import json
from tempfile import TemporaryDirectory
import unittest

from content_context_agent import validate_content_receipt
from shared_scope_content_queue import SharedScopeContextReviewQueue
from test_content_context_agent import sample, FakeBudget, valid_response


def pair(lengths=(1000,1000,1000,1000,1000)):
    report,f,rule=sample(lengths)
    rule['teacher_evidence']=[dict(quote='教师甲要求保留全部摘要信息')]
    g=deepcopy(f); g['id']='NP012-0001'; g['rule_id']='NP012'; g['check_dimensions']=['实现说明']
    for c in g['content_scope']['chunks']:
        c['id']='OTHER-'+c['id']
    r=dict(id='NP012',scope='完整实现说明',teacher_evidence=[dict(quote='教师乙要求区分说明与实际实现')])
    report['findings'].append(g); report['rules'].append(r)
    return report,f,g


class SharedScopeTests(unittest.TestCase):
    def test_same_scope_read_once_but_rules_judged_separately(self):
        report,f,g=pair(); state={}
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,state,directory); b=FakeBudget(state)
            while q.has_pending(): self.assertTrue(q.step(b))
            self.assertEqual([p['mode'] for p in b.packets],['read','read','synthesize','synthesize'])
            self.assertEqual(set(b.packets[0]['check_dimensions']),{'背景','功能','实现说明'})
            self.assertEqual(len(b.packets[0]['teacher_quotes']),2)
            finals=[p for p in b.packets if p['mode']=='synthesize']
            self.assertEqual(len(finals[0]['teacher_quotes']),1)
            self.assertNotEqual(finals[0]['rule_id'],finals[1]['rule_id'])
            for x in (f,g):
                validate_content_receipt(x['semantic_review'],x,next(r for r in report['rules'] if r['id']==x['rule_id']))
                self.assertEqual(set(x['content_read_progress']['read_chunk_ids']),{c['id'] for c in x['content_scope']['chunks']})

    def test_partial_joint_read_restarts_from_unread_not_old_single_rule_notes(self):
        report,f,g=pair(); state={}
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,state,directory); b=FakeBudget(state); q.step(b)
            q=SharedScopeContextReviewQueue(report,state,directory)
            before=len(b.packets); q.step(b)
            self.assertEqual(b.packets[before]['chunk_count'],2)
            self.assertFalse(f.get('semantic_review'))

    def test_resume_after_one_rule_final_preserves_shared_contract(self):
        report,f,g=pair((80,80)); state={}
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,state,directory); b=FakeBudget(state)
            q.step(b); q.step(b)
            self.assertEqual(sum(bool(x.get('semantic_review')) for x in (f,g)),1)
            q=SharedScopeContextReviewQueue(report,state,directory)
            q.step(b)
            self.assertEqual([p['mode'] for p in b.packets],['read','synthesize','synthesize'])
            self.assertFalse(q.has_pending())

    def test_corrupt_cache_source_or_note_does_not_claim_read(self):
        for corruption in ('source','evidence','batch'):
            report,f,g=pair(); state={}
            with TemporaryDirectory() as directory:
                q=SharedScopeContextReviewQueue(report,state,directory); q.step(FakeBudget(state))
                cache=next(iter(report['shared_content_reads'].values()))
                if corruption=='source': cache['source_sha256']='b'*64
                elif corruption=='batch': cache['batches'][0]['batch_id']='invented'
                else: cache['batches'][0]['notes'][0]['evidence_ids']=['invented']
                SharedScopeContextReviewQueue(report,state,directory)
                self.assertEqual(f['content_read_progress']['read_chunk_ids'],[])
                self.assertEqual(g['content_read_progress']['read_chunk_ids'],[])

    def test_changed_teacher_requirement_invalidates_joint_read(self):
        report,f,g=pair(); state={}
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,state,directory); q.step(FakeBudget(state))
            report['rules'][1]['teacher_evidence'][0]['quote']='后来新增了不同要求'
            SharedScopeContextReviewQueue(report,state,directory)
            self.assertEqual(f['content_read_progress']['read_chunk_ids'],[])

    def test_different_scope_or_source_is_never_shared(self):
        for change in ('text','heading','source'):
            report,f,g=pair()
            if change=='text': g['content_scope']['chunks'][0]['text']='A'*len(g['content_scope']['chunks'][0]['text'])
            elif change=='heading': g['content_scope']['headings']=['其他章节']
            else: g['content_scope']['source_sha256']='b'*64
            with TemporaryDirectory() as directory:
                q=SharedScopeContextReviewQueue(report,{},directory)
                self.assertEqual(q.groups,{})

    def test_zero_budget_never_marks_read(self):
        report,f,g=pair()
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,{},directory)
            self.assertFalse(q.step(FakeBudget({},allow=False)))
            self.assertEqual(f['content_read_progress']['read_chunk_ids'],[])
            self.assertNotIn('review_blocked',f)

    def test_remaining_budget_shrinks_by_complete_chunks(self):
        report,f,g=pair(); state={}
        class SmallBudget(FakeBudget):
            def prepare(self,messages,**kw):
                return {},json.loads(messages[-1]['content'])['chunk_count']
            def can_request(self,upper):return upper<=1
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,state,directory); b=SmallBudget(state)
            self.assertTrue(q.step(b))
            self.assertEqual(b.packets[0]['chunk_count'],1)
            self.assertEqual(set(b.packets[0]['check_dimensions']),{'背景','功能','实现说明'})
            self.assertEqual(len(f['content_read_progress']['read_chunk_ids']),1)

    def test_irreducible_packet_exceeds_round_limit_is_explicitly_blocked(self):
        report,f,g=pair(); state={'max_reserved_token_bound':2}
        class LargePacket(FakeBudget):
            def prepare(self,messages,**kw):return {},3
            def can_request(self,upper):return False
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,state,directory)
            self.assertFalse(q.step(LargePacket(state)))
            self.assertEqual(f['review_blocked']['code'],'shared_minimum_packet_exceeds_budget')
            self.assertNotIn('semantic_review',g)

    def test_invalid_output_does_not_propagate_and_only_one_repair(self):
        report,f,g=pair(); state={}
        def bad(packet):return dict(batch_id='wrong',observations=[])
        with TemporaryDirectory() as directory:
            q=SharedScopeContextReviewQueue(report,state,directory); b=FakeBudget(state,bad)
            self.assertTrue(q.step(b)); self.assertTrue(q.step(b))
            self.assertEqual(f['content_read_progress']['read_chunk_ids'],[])
            self.assertEqual(g['review_blocked']['code'],'shared_output_validation_failed')
            self.assertFalse(q.has_pending())

    def test_real_cross_round_reuse_preserves_reading_and_separate_synthesis(self):
        from new_paper_agent import reuse_semantic_evidence
        from review_contract import review_identity
        previous,f,g=pair(); previous['review_contract']=review_identity()
        fresh=deepcopy(previous)
        with TemporaryDirectory() as directory:
            state={}; q=SharedScopeContextReviewQueue(previous,state,directory); b=FakeBudget(state)
            q.step(b); q.step(b)
            self.assertEqual([p['mode'] for p in b.packets],['read','read'])
            self.assertNotIn('semantic_review',f)
            self.assertEqual(reuse_semantic_evidence(fresh,previous),0)
            self.assertEqual(fresh['shared_content_reads'],previous['shared_content_reads'])
            q=SharedScopeContextReviewQueue(fresh,{},directory); next_budget=FakeBudget({})
            while q.has_pending():self.assertTrue(q.step(next_budget))
            self.assertEqual([p['mode'] for p in next_budget.packets],['synthesize','synthesize'])
            self.assertEqual({p['rule_id'] for p in next_budget.packets},{'NP011','NP012'})
            for finding in fresh['findings']:
                validate_content_receipt(finding['semantic_review'],finding,
                    next(r for r in fresh['rules'] if r['id']==finding['rule_id']))
            next(iter(previous['shared_content_reads'].values()))['source_sha256']='changed'
            self.assertEqual(next(iter(fresh['shared_content_reads'].values()))['source_sha256'],'a'*64)

    def test_real_reuse_rejects_incompatible_contract_and_changed_source(self):
        from new_paper_agent import reuse_semantic_evidence
        from review_contract import review_identity
        for mismatch in ('contract','source'):
            previous,f,g=pair(); previous['review_contract']=review_identity()
            fresh=deepcopy(previous)
            with TemporaryDirectory() as directory:
                q=SharedScopeContextReviewQueue(previous,{},directory); q.step(FakeBudget({}))
                if mismatch=='contract':fresh['review_contract']['engine_version']='incompatible-engine'
                else:fresh['source_sha256']='b'*64
                self.assertEqual(reuse_semantic_evidence(fresh,previous),0)
                self.assertNotIn('shared_content_reads',fresh)
                self.assertTrue(all(not f.get('content_read_progress') for f in fresh['findings']))


if __name__=='__main__':unittest.main()
