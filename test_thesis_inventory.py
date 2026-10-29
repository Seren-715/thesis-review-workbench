"""Separate SVN snapshot inventory from historical paper paths and commits."""
from pathlib import Path
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from xml.sax.saxutils import escape

from atomic_json import read_json
from thesis_workflow import ThesisWorkflow, normalize_discovery_counts, parse_history, save

INFO = b'<info><entry revision="30"><url>svn://host/repo/grad</url><repository><root>svn://host/repo</root></repository></entry></info>'


def history(rows):
    return ('<log>' + ''.join(
        f'<logentry revision="{rev}"><author>{author}</author><msg>{message}</msg>'
        f'<paths><path kind="file" action="{action}">{escape(path)}</path></paths></logentry>'
        for rev, author, message, action, path in rows) + '</log>').encode('utf-8')


def listing(names, url='svn://host/repo/grad'):
    return ('<lists><list path="' + escape(url) + '">' + ''.join(
        f'<entry kind="file"><name>{escape(name)}</name><size>120</size>'
        '<commit revision="29"><author>student</author></commit></entry>'
        for name in names) + '</list></lists>').encode('utf-8')


class InventoryTests(TestCase):
    def create_verified_workflow(self, root):
        xml = listing(['123456_甲/论文/毕业论文.docx'])
        log = history([(4, 'student', '初稿', 'A', '/grad/123456_甲/论文/毕业论文.docx')])
        workflow = ThesisWorkflow(root, invoke=lambda argv, cwd:
            {'info': INFO, 'log': log, 'list': xml}[argv[1]])
        workflow.discover({'id': 'job_verified', 'svn_requests': 0, 'log_tail': '',
                           'status': 'completed', 'created_at': '2026-10-11'})
        return workflow

    def test_history_only_does_not_claim_current_file_count(self):
        data = parse_history(INFO, history([
            (4, 'student', '初稿', 'A', '/grad/123456_甲/论文/旧题.docx'),
            (12, 'mayy', '', 'M', '/grad/123456_甲/论文/旧题.docx'),
            (20, 'student', '改题', 'A', '/grad/123456_甲/论文/新题.docx')]))
        student = data['students'][0]
        self.assertEqual(student['document_count'], 2)
        self.assertEqual(student['history_document_count'], 2)
        self.assertIsNone(student['current_document_count'])
        self.assertEqual(student['current_inventory_status'], 'not_collected')

    def test_deleted_and_renamed_history_kept_but_not_in_snapshot_count(self):
        old, new = '/grad/123456_甲/论文/旧题.docx', '/grad/123456_甲/论文/新题.docx'
        data = parse_history(INFO, history([
            (4, 'student', '初稿', 'A', old), (12, 'mayy', '', 'M', old),
            (20, 'student', '改题', 'A', new), (20, 'student', '改题', 'D', old)]),
            current_list_bytes=listing(['123456_甲/论文/新题.docx']))
        student = data['students'][0]
        self.assertEqual(student['document_count'], 2)
        self.assertEqual(student['current_document_count'], 1)
        self.assertEqual(student['initial']['repository_path'], old)
        self.assertEqual(student['initial_revision'], 4)
        self.assertEqual(student['teacher_version_count'], 1)
        self.assertEqual(student['student_version_count'], 2)
        self.assertEqual({d['repository_path']: d['present_at_snapshot'] for d in student['documents']},
                         {old: False, new: True})

    def test_repeated_commits_do_not_inflate_current_count(self):
        path = '/grad/123456_甲/论文/毕业论文.docx'
        data = parse_history(INFO, history([
            (4, 'student', '初稿', 'A', path), (8, 'student', '', 'M', path),
            (12, 'mayy', '', 'M', path), (17, 'mayy', '', 'M', path)]),
            current_list_bytes=listing(['123456_甲/论文/毕业论文.docx']))
        student = data['students'][0]
        self.assertEqual(student['current_document_count'], 1)
        self.assertEqual(student['teacher_version_count'], 2)
        self.assertEqual(student['student_version_count'], 2)

    def test_same_basename_at_distinct_existing_paths_are_distinct_files(self):
        names = ['123456_甲/论文/毕业论文.docx', '123456_甲/论文/初稿/毕业论文.docx',
                 '123456_甲/论文/修改稿/毕业论文.docx']
        data = parse_history(INFO, history([(r, 'student', '', 'A', '/grad/' + n)
            for r, n in enumerate(names, 1)]), current_list_bytes=listing(names))
        student = data['students'][0]
        self.assertEqual(student['current_document_count'], 3)
        self.assertEqual(len({d['repository_path'] for d in student['current_documents']}), 3)
        self.assertTrue(all(d['file_name'] == '毕业论文.docx' for d in student['current_documents']))

    def test_owner_locks_and_non_theses_excluded_with_reasons(self):
        names = ['123456_甲/论文/毕业论文.docx', '123456_甲/论文/~$毕业论文.docx',
                 '123456_甲/~$初稿.doc', '123456_甲/论文/查重报告.docx',
                 '123456_甲/开题/初稿.docx', '123456_甲/个人简历.docx']
        data = parse_history(INFO, history([(r, 'student', '', 'A', '/grad/' + n)
            for r, n in enumerate(names, 1)]), current_list_bytes=listing(names))
        student = data['students'][0]
        self.assertEqual(student['current_document_count'], 1)
        self.assertEqual(student['history_document_count'], 1)
        for issues in (data['discovery_issues'], data['current_inventory']['excluded_files']):
            self.assertEqual(sum(i['reason'] == 'temporary_word_owner_file' for i in issues), 2)
            self.assertTrue(any(i['reason'] == 'non_thesis_document_name' for i in issues))

    def test_missing_files_explicit_zero_current_without_losing_student(self):
        data = parse_history(INFO, history([(4, 'student', '初稿', 'A',
            '/grad/123456_甲/论文/毕业论文.docx')]), current_list_bytes=listing([]))
        student = data['students'][0]
        self.assertEqual(student['current_document_count'], 0)
        self.assertEqual(student['current_inventory_status'], 'verified_snapshot')
        self.assertEqual(student['history_document_count'], 1)
        self.assertEqual(data['total_students'], 1)

    def test_copied_directory_files_without_file_history_still_discovered(self):
        data = parse_history(INFO, b'<log/>', current_list_bytes=listing([
            '123456_甲/论文/毕业论文.docx']))
        student = data['students'][0]
        self.assertEqual(student['current_document_count'], 1)
        self.assertEqual(student['history_document_count'], 0)
        self.assertIsNone(student['initial_revision'])
        self.assertEqual(student['teacher_version_count'], 0)

    def test_inventory_source_and_duplicates_validated(self):
        for invalid in [listing([], 'svn://host/repo/other'), b'<lists/>',
                        listing(['123456_甲/论文/毕业论文.docx'] * 2)]:
            with self.assertRaises(ValueError):
                parse_history(INFO, b'<log/>', current_list_bytes=invalid)

    def test_unsafe_inventory_paths_not_used(self):
        data = parse_history(INFO, b'<log/>', current_list_bytes=listing([
            '/123456_甲/论文/毕业论文.docx', '123456_甲/论文/../毕业论文.docx',
            '123456_甲\\论文\\毕业论文.docx']))
        self.assertEqual(data['current_inventory']['document_count'], 0)
        self.assertTrue(all(i['reason'] == 'unsafe_repository_path'
                            for i in data['current_inventory']['excluded_files']))

    def test_old_cache_normalized_as_unknown_without_persisting_fake_inventory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = {'status': 'history_discovered', 'students': [{
                'id': '123456_甲', 'name': '甲', 'document_count': 7, 'documents': []}],
                'snapshot_revision': 30, 'total_students': 1}
            target = root / 'data/thesis_workflow/discovery.json'
            save(target, old)
            before = target.read_bytes()
            workflow = ThesisWorkflow(root)
            try:
                student = workflow.snapshot()['discovery']['students'][0]
                self.assertIsNone(student['current_document_count'])
                self.assertEqual(student['history_document_count'], 7)
                self.assertEqual(target.read_bytes(), before)
            finally:
                workflow.close()

    def test_discovery_reads_same_snapshot_inventory_and_saves_hashed_source(self):
        xml = listing(['123456_甲/论文/毕业论文.docx'])
        calls = []
        def invoke(argv, cwd):
            calls.append(argv)
            return {'info': INFO, 'log': history([(4, 'student', '初稿', 'A',
                '/grad/123456_甲/论文/毕业论文.docx')]), 'list': xml}[argv[1]]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = ThesisWorkflow(root, invoke=invoke)
            try:
                job = {'id': 'job_inventory', 'svn_requests': 0, 'log_tail': ''}
                answer = workflow.discover(job)
                self.assertEqual([c[1] for c in calls], ['info', 'log', 'list'])
                self.assertIn('-r', calls[-1])
                self.assertIn('30', calls[-1])
                self.assertIn('svn://host/repo/grad@30', calls[-1])
                self.assertEqual(job['svn_requests'], 3)
                self.assertEqual(answer['current_document_count'], 1)
                result = read_json(root / 'data/thesis_workflow/discovery.json')
                self.assertEqual(Path(result['current_inventory']['path']).read_bytes(), xml)
                import hashlib
                self.assertEqual(result['current_inventory']['sha256'], hashlib.sha256(xml).hexdigest())
            finally:
                workflow.close()

    def test_failed_list_does_not_replace_last_verified_inventory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'data/thesis_workflow/discovery.json'
            save(target, {'status': 'old_saved_snapshot', 'students': [], 'total_students': 0})
            before = target.read_bytes()
            def invoke(argv, cwd):
                if argv[1] == 'list':
                    raise ValueError('SVN 请求失败')
                return INFO if argv[1] == 'info' else b'<log/>'
            workflow = ThesisWorkflow(root, invoke=invoke)
            try:
                job = {'id': 'job_inventory', 'svn_requests': 0, 'log_tail': ''}
                with self.assertRaises(ValueError):
                    workflow.discover(job)
                self.assertEqual(job['svn_requests'], 3)
                self.assertEqual(target.read_bytes(), before)
            finally:
                workflow.close()

    def test_verified_current_cache_requires_consistent_fields_and_source_receipt(self):
        data = parse_history(INFO, b'<log/>', current_list_bytes=listing([
            '123456_甲/论文/毕业论文.docx']))
        data['current_inventory'].update(path='current_files.xml', sha256='a' * 64)
        for label, mutate in [
            ('wrong_snapshot', lambda d: d['current_inventory'].update(snapshot_revision=29)),
            ('wrong_source', lambda d: d['current_inventory'].update(source_url='svn://other/repo')),
            ('false_count', lambda d: d['students'][0].update(current_document_count=7)),
            ('false_total', lambda d: d['current_inventory'].update(document_count=7)),
            ('missing_documents', lambda d: d['students'][0].pop('current_documents')),
            ('wrong_filename', lambda d: d['students'][0]['current_documents'][0].update(file_name='其他.docx')),
            ('missing_source_hash', lambda d: d['current_inventory'].pop('sha256')),
            ('wrong_hash_shape', lambda d: d['current_inventory'].update(sha256='unverified')),
        ]:
            with self.subTest(label=label):
                invalid = deepcopy(data)
                mutate(invalid)
                normalized = normalize_discovery_counts(invalid, require_source_receipt=True)
                self.assertEqual(normalized['current_inventory']['status'], 'invalid_metadata')
                self.assertIsNone(normalized['students'][0]['current_document_count'])
                self.assertEqual(normalized['students'][0]['current_documents'], [])

    def test_current_inventory_hash_is_verified_before_collection(self):
        with TemporaryDirectory() as directory:
            workflow = self.create_verified_workflow(Path(directory))
            try:
                result = workflow._discovery()
                Path(result['current_inventory']['path']).write_bytes(listing([]))
                with self.assertRaises(ValueError):
                    workflow._discovery()
            finally:
                workflow.close()

    def test_same_count_tampered_file_path_rejected_against_source_list(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.create_verified_workflow(root)
            try:
                target = root / 'data/thesis_workflow/discovery.json'
                data = read_json(target)
                doc = data['students'][0]['current_documents'][0]
                doc.update(file_name='其他论文.docx', relative_path='123456_甲/论文/其他论文.docx',
                           repository_path='/grad/123456_甲/论文/其他论文.docx')
                save(target, data)
                # Internally coherent metadata still must match the immutable source.
                self.assertEqual(workflow.snapshot()['discovery']['current_inventory']['status'], 'verified_snapshot')
                with self.assertRaises(ValueError):
                    workflow._discovery()
            finally:
                workflow.close()

    def test_changed_snapshot_claim_rejected_against_source_info(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.create_verified_workflow(root)
            try:
                target = root / 'data/thesis_workflow/discovery.json'
                data = read_json(target)
                data['snapshot_revision'] = 31
                data['current_inventory']['snapshot_revision'] = 31
                save(target, data)
                with self.assertRaises(ValueError):
                    workflow._discovery()
            finally:
                workflow.close()


if __name__ == '__main__':
    main()
