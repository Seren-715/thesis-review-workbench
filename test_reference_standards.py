import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from copy import deepcopy

from atomic_json import write_json
from reference_standards import load_reference_standards, reference_standards_view, attach_reference_parameters, annotate_local_material_candidates
from teacher_rule_library import classify_library, _refresh_summary
from test_teacher_rule_library import pending_library, FakeClient


class ReferenceTests(unittest.TestCase):
    def fixture(self, root):
        folder=root/'data/reference_standards'
        folder.mkdir(parents=True)
        original=root/'original.docx'; original.write_bytes(b'preserved-original')
        copy=folder/'copy.docx'; copy.write_bytes(original.read_bytes())
        reader=folder/'reader.md'; reader.write_text('代码使用五号字体',encoding='utf8')
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        data={'status':'verified_reference_materials','title':'指定参考材料','applicability':'代码对象',
              'sources':[dict(id='course',title='教师PPT',source_kind='teacher_lecture',original_path=str(original),
                  source_copy_path=str(copy),source_sha256=digest(original),reader_path=str(reader),reader_sha256=digest(reader))],
              'requirements':[dict(id='code',source_id='course',quote='代码使用五号字体',feedback_families=['code_layout'])]}
        write_json(folder/'catalog.json',data)
        return data,original,copy,reader

    def test_unconfigured_workspace_remains_usable(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(reference_standards_view(Path(tmp))['status'],'not_registered')

    def test_original_and_literal_reader_must_both_match(self):
        for changed in ('original','copy','reader','quote'):
            with self.subTest(changed=changed),TemporaryDirectory() as tmp:
                root=Path(tmp);data,original,copy,reader=self.fixture(root)
                self.assertEqual(load_reference_standards(root)['status'],'verified_reference_materials')
                if changed=='quote':
                    data['requirements'][0]['quote']='代码必须九磅'
                    write_json(root/'data/reference_standards/catalog.json',data)
                else:
                    {'original':original,'copy':copy,'reader':reader}[changed].write_bytes(b'changed')
                with self.assertRaises(ValueError):load_reference_standards(root)
                view=reference_standards_view(root)
                self.assertEqual(view['status'],'source_review_required')
                self.assertEqual(view['requirements'],[])

    def test_registered_reader_cannot_escape_material_directory(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);data,original,*_=self.fixture(root)
            data['sources'][0]['reader_path']=str(original)
            write_json(root/'data/reference_standards/catalog.json',data)
            with self.assertRaises(ValueError):load_reference_standards(root)

    def test_available_parameters_do_not_approve_rule_or_emit_findings(self):
        with TemporaryDirectory() as tmp:
            catalog,*_=self.fixture(Path(tmp))
            diagnostic={'capabilities':[{'family':'code_layout','enabled':False,'automatic_error_judgment_enabled':False},
                                        {'family':'text_color','enabled':False}],
                        'coverage':[{'family':'code_layout','status':'standard_not_specified'},
                                    {'family':'text_color','status':'standard_not_specified'}], 'findings':[]}
            attach_reference_parameters(diagnostic,catalog)
            self.assertEqual(diagnostic['coverage'][0]['status'],'reference_standard_available')
            self.assertEqual(diagnostic['coverage'][1]['status'],'standard_not_specified')
            self.assertFalse(diagnostic['capabilities'][0]['enabled'])
            self.assertEqual(diagnostic['findings'],[])
            attach_reference_parameters(diagnostic,{'status':'source_review_required'})
            self.assertEqual(diagnostic['coverage'][0]['status'],'standard_not_specified')
            self.assertNotIn('reference_requirements',diagnostic['capabilities'][0])

    def test_ppt_candidate_is_not_claimed_to_be_the_requested_material(self):
        from external_feedback_review import review_external_feedback
        with TemporaryDirectory() as tmp:
            catalog,*_=self.fixture(Path(tmp))
            route=review_external_feedback({'id':'1','text':'看PPT要求修改'})
            annotate_local_material_candidates(route,catalog)
            self.assertEqual(route['available_reference_candidates'][0]['source_id'],'course')
            self.assertIn('尚未证明',route['reason'])
            self.assertFalse(route['rule_approval'])


class ExternalSchedulingTests(unittest.TestCase):
    def test_external_only_pending_does_not_request_model(self):
        library=pending_library(1)
        library['comments'][0]['text']='看看我QQ里的留言'
        original=deepcopy(library['comments'][0])
        client=FakeClient()
        with TemporaryDirectory() as tmp:
            result=classify_library(library,tmp,client)
            run=result['classification_run']
        self.assertEqual(client.calls,0)
        self.assertEqual(result['summary']['external_information_review_comments'],1)
        self.assertEqual(result['summary']['unclassified_comments'],0)
        self.assertEqual(run['external_information_review_comment_ids'],['0'])
        self.assertEqual(run['selected_comment_ids'],[])
        self.assertEqual(result['comments'][0]['text'],original['text'])
        self.assertEqual(result['comments'][0]['status'],original['status'])

    def test_external_and_local_requirements_are_separate_without_erasing_prior_classification(self):
        library=pending_library(2)
        library['comments'][0].update(text='图与图题必须同页。见讲座PPT。',status='semantic_review_required')
        with TemporaryDirectory() as tmp:
            client=FakeClient();result=classify_library(library,tmp,client,review_existing=True)
            run=result['classification_run']
        self.assertEqual(client.calls,1)
        self.assertEqual(run['selected_comment_ids'],['1'])
        self.assertEqual(result['summary']['needs_semantic_review_comments'],1)
        route=result['external_manual_reviews'][0]
        self.assertEqual(route['independent_requirement_fragments'][0]['quote'],'图与图题必须同页')
        _refresh_summary(result)
        self.assertEqual(len(result['external_manual_reviews']),1)


if __name__=='__main__':unittest.main()
