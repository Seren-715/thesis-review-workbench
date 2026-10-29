from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from thesis_workflow import ThesisWorkflow, save, digest


class InitialReuseTests(TestCase):
    def fixture(self, root):
        original = root/'data/original.docx'
        original.parent.mkdir(parents=True)
        original.write_bytes(b'original')
        folder = root/'data/thesis_workflow/initial_checks/s1/job1'
        source = dict(revision=5, repository_path='/s1/paper.docx', exported_path=str(original), sha256=digest(original))
        save(folder/'source.json', source)
        report = dict(source_sha256=digest(original), run={'finished_at': '2026-10-11'})
        save(folder/'review/report.json', report)
        return dict(id='s1', initial={'revision':5, 'repository_path':'/s1/paper.docx'}), original, folder, source

    def test_bound_original_is_reused_without_export_or_word(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); student, original, _, _ = self.fixture(root)
            workflow=ThesisWorkflow(root)
            try:
                with patch.object(workflow, '_export', side_effect=AssertionError('unexpected SVN')):
                    result=workflow._reusable_initial(student)
                self.assertEqual(result[0], original)
                self.assertIsNone(result[2])
            finally: workflow.close()

    def test_changed_source_or_different_revision_or_repository_path_is_not_reused(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); student, original, _, _ = self.fixture(root)
            workflow=ThesisWorkflow(root)
            try:
                student['initial']['revision']=6
                self.assertIsNone(workflow._reusable_initial(student))
                student['initial']['revision']=5; student['initial']['repository_path']='/other.docx'
                self.assertIsNone(workflow._reusable_initial(student))
                student['initial']['repository_path']='/s1/paper.docx'; original.write_bytes(b'changed')
                self.assertIsNone(workflow._reusable_initial(student))
            finally: workflow.close()

    def test_unrenewed_conversion_or_invalid_snapshot_cannot_be_reused(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); student, _, base, source = self.fixture(root)
            workflow=ThesisWorkflow(root)
            try:
                source['conversion']={'status':'prior_success'};save(base/'source.json',source)
                self.assertIsNone(workflow._reusable_initial(student))
            finally: workflow.close()
