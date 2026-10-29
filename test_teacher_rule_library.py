from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from zipfile import ZipFile

from teacher_rule_library import build_library, classify_library, get_library, match_families, publish_classification, _canonicalize_comments, _replay_overlay, _fingerprint


def document(path, comments):
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{w}"><w:body>' + ''.join(f'<w:p><w:commentRangeStart w:id="{i}"/><w:r><w:t>测试原文</w:t></w:r><w:commentRangeEnd w:id="{i}"/><w:r><w:commentReference w:id="{i}"/></w:r></w:p>' for i in range(len(comments))) + '</w:body></w:document>')
        archive.writestr('word/comments.xml', f'<w:comments xmlns:w="{w}">' + ''.join(f'<w:comment w:id="{i}" w:author="老师"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:comment>' for i,text in enumerate(comments)) + '</w:comments>')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(root, dirname, student='123456789_张三', author='mayy', comments=None):
    directory = root / 'data/thesis_workflow/exports' / dirname
    directory.mkdir(parents=True, exist_ok=True)
    sha = document(directory/'paper.docx', comments or ['文中不要出现“我”字', ''])
    manifest = {'sample_id':student,'teacher_author':'mayy','files':[{'revision':1,'author':author,'repository_path':f'/{student}/论文/test.docx','source_url':'svn://example/grad/test.docx@1','exported_path':'paper.docx','sha256':sha,'status':'exported'}]}
    (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    return directory


def pending_library(number):
    return {'schema_version':1,'root':'test','summary':{'documents':1},'rules':[],'coverage':{},'comments':[{'id':str(i),'evidence_id':str(i),'text':'这个部分需要进一步说明'+str(i),'anchor_text':'原文','rule_ids':[],'status':'pending_semantic_classification','student_id':'s1','student_name':'张三','source_path':'test','source_sha256':'abc','comment_id':str(i)} for i in range(number)]}


class FakeClient:
    def __init__(self, behavior='valid'):
        self.chat = NS(completions=NS(create=self.create))
        self.calls = 0
        self.behavior = behavior
    def create(self, **kwargs):
        self.calls += 1
        batch = json.loads(kwargs['messages'][1]['content'])['comments']
        items = [{'id':c['id'],'family_ids':[],'quote':c['text'],'status':'needs_review','reason':'语义信息不足'} for c in batch]
        if self.behavior == 'bad_quote':
            items[0]['quote'] = '不是原话'
        usage = None if self.behavior == 'unknown' else NS(prompt_tokens=10,completion_tokens=5,total_tokens=15)
        return NS(usage=usage, choices=[NS(finish_reason='length' if self.behavior=='length' else 'stop',message=NS(content=json.dumps({'items':items})))])


class CorpusTests(unittest.TestCase):
    def conversion_comments(self):
        first = pending_library(1)['comments'][0]
        first.update(id='derived-a:0', source_sha256='derived-a', original_source_sha256='original',
                     repository_path='/s/paper.doc', revision=2, comment_author='teacher',
                     anchor={'text':'原文', 'paragraph_indices':[2], 'context_paragraphs':[{'index':2,'text':'原文','para_id':'AAAA'}]},
                     comment_media=[])
        second = deepcopy(first)
        second.update(id='derived-b:0',source_sha256='derived-b')
        second['anchor']['context_paragraphs'][0]['para_id'] = 'BBBB'
        return first, second

    def test_converted_original_comments_merge_only_generated_para_id_difference(self):
        comments, conflicts = _canonicalize_comments(list(self.conversion_comments()))
        self.assertEqual(len(comments), 1)
        self.assertEqual(conflicts, [])
        self.assertEqual(comments[0]['classification_alias_ids'], ['derived-a:0','derived-b:0'])
        self.assertEqual(len(comments[0]['derived_lineage']), 2)
        self.assertEqual(comments[0]['source_sha256'], 'derived-a')

    def test_conversion_disagreement_keeps_both_and_records_conflict(self):
        for change in ('text','anchor','author','media'):
            with self.subTest(change=change):
                first, second = self.conversion_comments()
                if change == 'text': second['text'] += '不同'
                if change == 'anchor': second['anchor']['paragraph_indices'] = [3]
                if change == 'author': second['comment_author'] = 'other'
                if change == 'media': second['comment_media'] = [{'sha256':'different'}]
                comments, conflicts = _canonicalize_comments([first,second])
                self.assertEqual(len(comments), 2)
                self.assertEqual(len(conflicts), 1)

    def test_conversion_identity_keeps_student_path_and_revision_scopes_distinct(self):
        for field, value in [('student_id','s2'),('repository_path','/s/other.doc'),('revision',3)]:
            with self.subTest(field=field):
                first, second = self.conversion_comments()
                second[field] = value
                second['id'] = first['id']
                second['source_sha256'] = first['source_sha256']
                comments, conflicts = _canonicalize_comments([first,second])
                self.assertEqual(len(comments), 2)
                self.assertNotEqual(comments[0]['id'],comments[1]['id'])
                self.assertEqual(conflicts, [])

    def test_verified_alias_receipt_replays_but_conflicting_alias_decisions_do_not(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/'data/teacher_rule_library'
            folder.mkdir(parents=True)
            first,second=self.conversion_comments()
            def receipt(comment, families):
                return {'source_sha256':comment['source_sha256'],'comment_id':'0',
                        'text_sha256':hashlib.sha256(comment['text'].encode()).hexdigest(),
                        'status':'semantic_candidate','rule_ids':families,'classification_quote':comment['text']}
            comments,_ = _canonicalize_comments([first,second])
            library={'comments':comments,'coverage':{}}
            (folder/'classification_overlay.json').write_text(json.dumps({'comments':{'derived-b:0':receipt(second,['prediction_target'])}}),encoding='utf8')
            _replay_overlay(library,root)
            self.assertEqual(library['comments'][0]['rule_ids'],['prediction_target'])
            comments,_ = _canonicalize_comments(list(self.conversion_comments()))
            library={'comments':comments,'coverage':{}}
            (folder/'classification_overlay.json').write_text(json.dumps({'comments':{'derived-a:0':receipt(first,['module_inventory']),'derived-b:0':receipt(second,['prediction_target'])}}),encoding='utf8')
            _replay_overlay(library,root)
            self.assertEqual(library['comments'][0]['rule_ids'],[])
            self.assertEqual(library['coverage']['classification_alias_conflicts'], ['derived-a:0'])

    def test_identity_algorithm_changes_invalidate_saved_fingerprint(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            export(root,'v1')
            before=_fingerprint(root)
            with patch('teacher_rule_library.COMMENT_IDENTITY_VERSION','next_identity'):
                self.assertNotEqual(before,_fingerprint(root))

    def test_shared_legacy_alias_does_not_transfer_receipt_to_other_student(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/'data/teacher_rule_library'
            folder.mkdir(parents=True)
            first,second=self.conversion_comments()
            second.update(id=first['id'],source_sha256=first['source_sha256'],student_id='s2')
            comments,_=_canonicalize_comments([first,second])
            receipt={'source_sha256':first['source_sha256'],'comment_id':'0',
                     'text_sha256':hashlib.sha256(first['text'].encode()).hexdigest(),
                     'status':'semantic_candidate','rule_ids':['prediction_target'],'classification_quote':first['text']}
            (folder/'classification_overlay.json').write_text(json.dumps({'comments':{first['id']:receipt}}),encoding='utf8')
            library={'comments':comments,'coverage':{}}
            _replay_overlay(library,root)
            self.assertEqual(library['coverage']['classification_receipts_reused'],0)
            self.assertTrue(all(c['rule_ids']==[] for c in comments))

    def test_conversion_without_verified_original_hash_stays_separate(self):
        first,second=self.conversion_comments()
        first.pop('original_source_sha256')
        second.pop('original_source_sha256')
        comments,conflicts=_canonicalize_comments([first,second])
        self.assertEqual(len(comments),2)
        self.assertEqual(conflicts,[])

    def test_only_teacher_provenance_and_all_empty_comments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(root,'teacher')
            export(root,'student',student='223456789_李四',author='student')
            result = build_library(root)
            self.assertEqual(result['summary']['documents'],1)
            self.assertEqual(result['summary']['comments'],2)
            self.assertEqual(result['coverage']['empty_comments'],1)
            self.assertEqual(result['comments'][0]['anchor']['status'],'complete')

    def test_duplicate_copies_and_same_student_versions_not_recurring(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(root,'v1')
            export(root,'copy')
            export(root,'v2',comments=['正文不要出现“我”字'])
            result = build_library(root)
            self.assertEqual(result['summary']['students'],1)
            self.assertEqual(result['summary']['recurring_families'],0)
            self.assertEqual(result['summary']['comments'],3)

    def test_two_known_students_recurring_but_not_enabled(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            export(root,'v1')
            export(root,'v2',student='223456789_李四',comments=['正文不要出现“我”字'])
            rule=build_library(root)['rules'][0]
            self.assertEqual(rule['recurrence_status'],'cross_student_candidate')
            self.assertFalse(rule['automatic_error_judgment_enabled'])

    def test_common_requires_three_distinct_students_without_enabling_rule(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(3):
                export(root, f'v{n}', student=f'{n + 1}23456789_学生{n}',
                       comments=[f'正文不要出现“我”字：样本{n}'])
            result = build_library(root)
            rule = result['rules'][0]
            self.assertEqual(rule['recurrence_status'], 'common_candidate')
            self.assertEqual(result['summary']['common_candidate_count'], 1)
            self.assertEqual(result['summary']['cross_student_candidate_count'], 0)
            self.assertEqual(rule['status'], 'review_candidate')
            self.assertFalse(rule['same_category_feedback_reviewed'])

    def test_hash_mismatch_explicit_failure(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            target=export(root,'v1')
            (target/'paper.docx').write_bytes(b'changed')
            result=build_library(root)
            self.assertEqual(result['summary']['comments'],0)
            self.assertEqual(result['summary']['failed_sources'],1)

    def test_verified_unsupported_container_recorded_without_aborting_corpus(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            target=export(root,'v1')
            unsupported=b'legacy unsupported binary'
            (target/'paper.docx').write_bytes(unsupported)
            manifest=json.loads((target/'manifest.json').read_text(encoding='utf8'))
            manifest['files'][0]['sha256']=hashlib.sha256(unsupported).hexdigest()
            (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
            result=build_library(root)
            self.assertEqual(result['summary']['failed_sources'],1)
            self.assertEqual(result['summary']['documents'],0)

    def test_generic_keywords_do_not_manufacture_same_category(self):
        self.assertNotIn('formula_numbering',match_families('编号','(一)循'))
        self.assertNotIn('references',match_families('后面还有一个谢辞','参考文献'))
        self.assertNotIn('implementation_explanation',match_families('不要连续贴图，要文字描述+图片+描述+图片，最后是代码','图4-4'))
        self.assertEqual(match_families('公式的字体比较奇怪','原文'),['formula_font'])
        self.assertNotIn('architecture_fidelity',match_families('本系统采用前后端分离设计架构','摘要'))

    def test_cache_revalidates_source_not_just_saved_result(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            target=export(root,'v1')
            self.assertEqual(get_library(root)['summary']['comments'],2)
            (target/'paper.docx').write_bytes(b'changed')
            self.assertEqual(get_library(root)['summary']['comments'],0)

    def test_classification_multiple_batches_and_explicit_remaining(self):
        with TemporaryDirectory() as tmp:
            client=FakeClient()
            result=classify_library(pending_library(17),tmp,client,max_requests=2,max_reserved_tokens=24000)
            self.assertEqual(client.calls,2)
            self.assertEqual(len(result['classification_run']['classified_comment_ids']),16)
            self.assertEqual(result['classification_run']['remaining_comment_ids'],['16'])
            self.assertEqual(result['classification_run']['known_total_tokens'],30)
            self.assertEqual(result['summary']['classified_comments'],16)
            self.assertEqual(result['summary']['unclassified_comments'],1)
            self.assertEqual(result['summary']['needs_semantic_review_comments'],16)
            resumed=classify_library(result,tmp,client,max_requests=1)
            self.assertFalse(resumed['classification_run']['remaining_comment_ids'])

    def test_abnormal_length_unknown_usage_and_bad_quotes_never_retry(self):
        with TemporaryDirectory() as tmp:
            for failure in ('length','unknown','bad_quote'):
                with self.subTest(failure=failure):
                    client=FakeClient(failure)
                    result=classify_library(pending_library(12),tmp,client)
                    self.assertEqual(client.calls,1)
                    self.assertEqual(result['classification_run']['status'],'failed_no_retry')
                    self.assertEqual(len(result['classification_run']['remaining_comment_ids']),12)
                    self.assertEqual(result['summary']['classified_comments'],0)

    def test_zero_model_calls_when_reservation_would_exceed_budget(self):
        with TemporaryDirectory() as tmp:
            client=FakeClient()
            result=classify_library(pending_library(1),tmp,client,max_reserved_tokens=1000)
            self.assertEqual(client.calls,0)
            self.assertEqual(result['classification_run']['remaining_comment_ids'],['0'])

    def test_pre_request_ledger_survives_network_failure_and_never_logs_key(self):
        with TemporaryDirectory() as tmp:
            def fail(**kwargs):
                ledger=json.loads((Path(tmp)/'classification_run.json').read_text(encoding='utf8'))
                self.assertEqual(ledger['request_attempts'],1)
                self.assertEqual(ledger['unknown_cost_attempts'],1)
                self.assertGreater(ledger['reserved_token_bound'],kwargs['max_tokens'])
                self.assertEqual(kwargs['extra_body']['thinking']['type'],'disabled')
                raise RuntimeError('Authorization: Bearer secret-key-content')
            client=NS(chat=NS(completions=NS(create=fail)))
            result=classify_library(pending_library(12),tmp,client)
            run=result['classification_run']
            self.assertEqual(run['unknown_cost_attempts'],1)
            self.assertEqual(run['request_attempts'],1)
            self.assertNotIn('secret-key-content',(Path(tmp)/'classification_run.json').read_text(encoding='utf8'))

    def test_unhandled_process_interruption_retains_attempt_ledger(self):
        with TemporaryDirectory() as tmp:
            def crash(**kwargs):
                raise KeyboardInterrupt('simulated process interruption')
            client=NS(chat=NS(completions=NS(create=crash)))
            with self.assertRaises(KeyboardInterrupt):
                classify_library(pending_library(1),tmp,client)
            ledger=json.loads((Path(tmp)/'classification_run.json').read_text(encoding='utf8'))
            self.assertEqual(ledger['status'],'request_started')
            self.assertEqual(ledger['unknown_cost_attempts'],1)
            self.assertEqual(ledger['remaining_comment_ids'],['0'])

    def test_published_semantics_survive_refresh_and_additional_teacher_scans(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            export(root,'v1',comments=['大模型的部分太少了'])
            original=get_library(root)
            classified=classify_library(original,root/'classification',FakeClient())
            publish_classification(root,classified)
            self.assertEqual(get_library(root)['comments'][0]['status'],'semantic_review_required')
            self.assertEqual(get_library(root,refresh=True)['comments'][0]['status'],'semantic_review_required')
            export(root,'v2',student='223456789_李四',comments=['请补充数据来源'])
            fresh=get_library(root,refresh=True)
            prior=[c for c in fresh['comments'] if c['id']==original['comments'][0]['id']][0]
            self.assertEqual(prior['status'],'semantic_review_required')
            self.assertEqual(fresh['coverage']['classification_receipts_reused'],1)
            self.assertEqual(sum(c['status']=='pending_semantic_classification' for c in fresh['comments']),1)

    def test_publication_rejects_stale_sources_and_fabricated_quotes(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            target=export(root,'v1',comments=['大模型的部分太少了'])
            classified=classify_library(get_library(root),root/'classification',FakeClient())
            changed=deepcopy(classified)
            changed['comments'][0]['classification_quote']='不是原话'
            with self.assertRaises(ValueError):
                publish_classification(root,changed)
            metadata=json.loads((target/'manifest.json').read_text(encoding='utf8'))
            metadata['files'][0]['revision']=2
            (target/'manifest.json').write_text(json.dumps(metadata),encoding='utf8')
            with self.assertRaises(ValueError):
                publish_classification(root,classified)

    def test_duplicate_json_keys_and_duplicate_ids_fail_entire_batch(self):
        with TemporaryDirectory() as tmp:
            client=FakeClient()
            original=client.create
            def duplicate(**kwargs):
                response=original(**kwargs)
                response.choices[0].message.content='{"items":[],"items":[]}'
                return response
            client.chat.completions.create=duplicate
            result=classify_library(pending_library(2),tmp,client)
            self.assertEqual(result['classification_run']['status'],'failed_no_retry')
            self.assertEqual(result['classification_run']['unknown_cost_attempts'],0)
            self.assertEqual(result['summary']['classified_comments'],0)

    def test_short_request_ids_map_back_and_sanitized_response_is_preserved(self):
        with TemporaryDirectory() as tmp:
            client=FakeClient()
            original=client.create
            library=pending_library(1)
            original_id='a'*64+':comment:271'
            library['comments'][0]['id']=original_id
            def check(**kwargs):
                payload=json.loads(kwargs['messages'][1]['content'])['comments']
                self.assertEqual(payload[0]['id'],'C001')
                self.assertNotIn(original_id,kwargs['messages'][1]['content'])
                return original(**kwargs)
            client.chat.completions.create=check
            result=classify_library(library,tmp,client)
            self.assertEqual(result['classification_run']['classified_comment_ids'],[original_id])
            stored=json.loads((Path(tmp)/'response_001.json').read_text(encoding='utf8'))
            self.assertEqual(set(stored),{'finish_reason','content','usage'})
            self.assertEqual(stored['finish_reason'],'stop')

    def test_validation_diagnostic_keeps_message_and_model_response(self):
        with TemporaryDirectory() as tmp:
            result=classify_library(pending_library(1),tmp,FakeClient('bad_quote'))
            self.assertEqual(result['classification_run']['requests'][0]['validation_message'],'Quote not verbatim in original comment')
            stored=json.loads((Path(tmp)/'response_001.json').read_text(encoding='utf8'))
            self.assertEqual(json.loads(stored['content'])['items'][0]['quote'],'不是原话')

    def test_remaining_cli_completes_whole_corpus_and_publishes_each_batch(self):
        from classify_remaining_teacher_comments import run_remaining
        from contextlib import redirect_stdout
        from io import StringIO
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            export(root,'v1',comments=['需要进一步确认具体意见'+str(i) for i in range(17)])
            client=FakeClient()
            with redirect_stdout(StringIO()):
                state=run_remaining(root,root/'complete',client)
            self.assertEqual(state['request_attempts'],3)
            self.assertEqual(state['known_total_tokens'],45)
            self.assertEqual(state['remaining_comments'],0)
            self.assertEqual(state['final_summary']['needs_semantic_review_comments'],17)
            self.assertEqual(get_library(root,refresh=True)['summary']['unclassified_comments'],0)

    def test_remaining_cli_hard_request_limit_leaves_explicit_pending(self):
        from classify_remaining_teacher_comments import run_remaining
        from contextlib import redirect_stdout
        from io import StringIO
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            export(root,'v1',comments=['需要进一步确认具体意见'+str(i) for i in range(10)])
            with redirect_stdout(StringIO()):
                state=run_remaining(root,root/'limited',FakeClient(),max_requests=1)
            self.assertEqual(state['status'],'budget_stopped_remaining_pending')
            self.assertEqual(state['remaining_comments'],2)
            self.assertEqual(state['request_attempts'],1)

    def test_remaining_cli_unknown_usage_stops_without_another_batch(self):
        from classify_remaining_teacher_comments import run_remaining
        from contextlib import redirect_stdout
        from io import StringIO
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            export(root,'v1',comments=['需要进一步确认具体意见'+str(i) for i in range(17)])
            client=FakeClient('unknown')
            with redirect_stdout(StringIO()):
                state=run_remaining(root,root/'failed',client)
            self.assertEqual(client.calls,1)
            self.assertEqual(state['unknown_cost_attempts'],1)
            self.assertEqual(state['remaining_comments'],17)
            self.assertEqual(state['status'],'failed_no_retry')


if __name__=='__main__':
    unittest.main()
