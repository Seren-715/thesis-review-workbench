"""Offline persistence and dispatch safety regression tests; no external calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import ctypes
from ctypes import wintypes
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Timer
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import atomic_json
from atomic_json import AtomicJSONWriteError, path_lock, read_json, write_json
from new_paper_agent import BoundedClient, run_review


def windows_denial(code=5):
    error = PermissionError(errno.EACCES, 'simulated Windows replacement denial')
    error.winerror = code
    return error


class AtomicJSONTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.target = self.folder/'run.json'
        self.old = {'version': 'old', 'text': '已有结果'}
        write_json(self.target, self.old)
        self.original = self.target.read_bytes()

    def test_unicode_roundtrip_and_success_has_no_temp(self):
        value = {'version': 'new', 'text': '正文检查：我与我们', 'rows': [1, None, False]}
        write_json(self.target, value)
        self.assertEqual(read_json(self.target), value)
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_temporary_windows_denials_retry_only_the_same_commit(self):
        real_replace = os.replace
        attempted = []
        def replace(source, target):
            attempted.append(Path(source))
            if len(attempted) <= 3:
                raise windows_denial((5, 32, 33)[len(attempted)-1])
            real_replace(source, target)
        with patch('atomic_json.os.replace', side_effect=replace), patch('atomic_json.time.sleep') as sleep:
            write_json(self.target, {'version': 'new'})
        self.assertEqual(len(attempted), 4)
        self.assertEqual(len(set(attempted)), 1)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(read_json(self.target), {'version': 'new'})
        self.assertFalse(attempted[0].exists())

    def test_permanent_denial_keeps_old_json_and_complete_recovery_file(self):
        replacement = {'version': 'recoverable', 'text': '新结果'}
        with patch('atomic_json.os.replace', side_effect=windows_denial()) as replace, patch('atomic_json.time.sleep') as sleep:
            with self.assertRaises(AtomicJSONWriteError) as caught:
                write_json(self.target, replacement)
        error = caught.exception
        self.assertEqual(replace.call_count, len(atomic_json._COMMIT_RETRY_DELAYS) + 1)
        self.assertEqual(sleep.call_count, len(atomic_json._COMMIT_RETRY_DELAYS))
        self.assertLess(sum(atomic_json._COMMIT_RETRY_DELAYS), 1)
        self.assertEqual(error.commit_attempts, replace.call_count)
        self.assertEqual(error.phase, 'replace')
        self.assertEqual(error.target_path, self.target.resolve())
        self.assertEqual(error.temporary_path.parent, self.target.parent.resolve())
        self.assertIn(str(error.temporary_path), str(error))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(read_json(error.temporary_path), replacement)

    def test_non_sharing_failure_has_no_retry(self):
        with patch('atomic_json.os.replace', side_effect=OSError(errno.ENOSPC, 'disk full')) as replace, patch('atomic_json.time.sleep') as sleep:
            with self.assertRaises(AtomicJSONWriteError) as caught:
                write_json(self.target, {'version': 'new'})
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(read_json(caught.exception.temporary_path), {'version': 'new'})

    def test_failed_writes_use_distinct_recovery_files(self):
        recoveries = []
        with patch('atomic_json.os.replace', side_effect=windows_denial()), patch('atomic_json.time.sleep'):
            for version in (1, 2):
                with self.assertRaises(AtomicJSONWriteError) as caught:
                    write_json(self.target, {'version': version})
                recoveries.append(caught.exception.temporary_path)
        self.assertNotEqual(*recoveries)
        self.assertEqual([read_json(path) for path in recoveries], [{'version': 1}, {'version': 2}])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_serialization_failure_does_not_touch_target_or_create_temp(self):
        with self.assertRaises(TypeError):
            write_json(self.target, {'invalid': object()})
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_same_path_lock_blocks_reader_and_writer_but_other_path_proceeds(self):
        other = self.folder/'paper.json'
        reader_started, writer_started = Event(), Event()
        def read():
            reader_started.set()
            return read_json(self.target)
        def write():
            writer_started.set()
            write_json(self.target, {'version': 'new'})
        with ThreadPoolExecutor(max_workers=3) as executor:
            with path_lock(self.target):
                reader = executor.submit(read)
                writer = executor.submit(write)
                self.assertTrue(reader_started.wait(2))
                self.assertTrue(writer_started.wait(2))
                independent = executor.submit(write_json, other, {'version': 'independent'})
                independent.result(timeout=2)
                self.assertFalse(reader.done())
                self.assertFalse(writer.done())
                # Reentrancy is required by read-modify-write callers.
                self.assertEqual(read_json(self.target), self.old)
            self.assertIn(reader.result(timeout=2), [self.old, {'version': 'new'}])
            writer.result(timeout=2)
        self.assertEqual(read_json(other), {'version': 'independent'})
        self.assertEqual(read_json(self.target), {'version': 'new'})
        self.assertEqual(atomic_json._path_locks, {})

    def test_concurrent_readers_and_writers_never_see_partial_json(self):
        barrier = Barrier(6)
        def writer(worker):
            barrier.wait(timeout=5)
            for sequence in range(40):
                tag = f'{worker}:{sequence}'
                write_json(self.target, {'tag': tag, 'payload': tag * 200})
        def reader():
            barrier.wait(timeout=5)
            for _ in range(160):
                value = read_json(self.target)
                if 'tag' in value:
                    self.assertEqual(value['payload'], value['tag'] * 200)
                else:
                    self.assertEqual(value, self.old)
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(writer, worker) for worker in range(3)]
            futures += [executor.submit(reader) for _ in range(3)]
            for future in futures:
                future.result(timeout=10)
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    @unittest.skipUnless(os.name == 'nt', 'Real Windows sharing flags required')
    def test_real_windows_open_reader_lock_releases_during_commit_retry(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        # Share reading/writing, but deliberately withhold FILE_SHARE_DELETE.
        handle = create(str(self.target), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        closed = Event()
        def release():
            close(handle)
            closed.set()
        timer = Timer(0.06, release)
        timer.start()
        try:
            real_replace = os.replace
            with patch('atomic_json.os.replace', wraps=real_replace) as replace:
                write_json(self.target, {'version': 'released'})
            self.assertGreater(replace.call_count, 1)
            self.assertTrue(closed.wait(2))
            self.assertEqual(read_json(self.target), {'version': 'released'})
        finally:
            timer.cancel()
            timer.join()
            if not closed.is_set():
                release()


class DispatchStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.create = Mock(return_value=SimpleNamespace(usage=None))
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))
        self.state = {'requests': [], 'request_attempts': 0, 'known_total_tokens': 0,
                      'unknown_cost_attempts': 0, 'reserved_token_bound': 0}

    def test_either_pre_dispatch_save_failure_is_proven_zero_cost(self):
        real_write = write_json
        for failing_name in ('run.json', 'request_01.json'):
            with self.subTest(failing_name=failing_name):
                state = deepcopy(self.state)
                calls = []
                def save(path, data):
                    calls.append(Path(path).name)
                    if calls.count(failing_name) == 1 and Path(path).name == failing_name:
                        raise windows_denial()
                    real_write(path, data)
                bounded = BoundedClient(self.client, self.output, state)
                with patch('new_paper_agent.save', side_effect=save):
                    with self.assertRaises(OSError):
                        bounded.request([{'role': 'user', 'content': 'test'}])
                self.create.assert_not_called()
                self.assertEqual(state['request_attempts'], 1)
                self.assertEqual(state['dispatched_requests'], 0)
                self.assertEqual(state['unknown_cost_attempts'], 0)
                self.assertEqual(state['reserved_token_bound'], 0)
                self.assertEqual(state['known_total_tokens'], 0)
                attempt = state['requests'][0]
                self.assertEqual(attempt['status'], 'not_dispatched')
                self.assertFalse(attempt['dispatched'])
                self.assertEqual(attempt['reserved_upper_bound'], 0)
                self.assertEqual(attempt['usage']['total_tokens'], 0)
                self.assertGreater(attempt['released_reserved_upper_bound'], 0)
                self.assertEqual(read_json(self.output/'run.json'), state)

    def test_permanent_pre_dispatch_failure_retains_corrected_recovery_ledger(self):
        bounded = BoundedClient(self.client, self.output, self.state)
        with patch('atomic_json.os.replace', side_effect=windows_denial()), patch('atomic_json.time.sleep'):
            with self.assertRaises(AtomicJSONWriteError):
                bounded.request([{'role': 'user', 'content': 'test'}])
        self.create.assert_not_called()
        recovery_ledgers = [read_json(path) for path in self.output.glob('*.tmp')]
        # Privacy-map temporary files are retained too; only run ledgers have
        # request accounting. No provider dispatch may result from either failure.
        corrected = [row for row in recovery_ledgers if row.get('requests') and row['requests'][0]['status'] == 'not_dispatched']
        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0]['dispatched_requests'], 0)
        self.assertEqual(corrected[0]['unknown_cost_attempts'], 0)
        self.assertEqual(corrected[0]['reserved_token_bound'], 0)

    def test_commit_retries_do_not_retry_provider_and_unknown_usage_stays_unknown(self):
        real_replace = os.replace
        attempts = 0
        def replace(source, target):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise windows_denial()
            real_replace(source, target)
        bounded = BoundedClient(self.client, self.output, self.state)
        with patch('atomic_json.os.replace', side_effect=replace), patch('atomic_json.time.sleep'):
            with self.assertRaisesRegex(ValueError, '成本未知'):
                bounded.request([{'role': 'user', 'content': 'test'}])
        self.create.assert_called_once()
        self.assertEqual(self.state['request_attempts'], 1)
        self.assertEqual(self.state['dispatched_requests'], 1)
        self.assertEqual(self.state['unknown_cost_attempts'], 1)
        self.assertGreater(self.state['reserved_token_bound'], 0)

    def test_legacy_counter_preserves_ambiguous_dispatches(self):
        legacy = dict(self.state, request_attempts=3,
                      requests=[{'status': 'response_received'}, {'status': 'started'},
                                {'status': 'not_dispatched'}])
        BoundedClient(self.client, self.output, legacy)
        self.assertEqual(legacy['dispatched_requests'], 2)
        empty_legacy = dict(self.state, request_attempts=2)
        BoundedClient(self.client, self.output, empty_legacy)
        self.assertEqual(empty_legacy['dispatched_requests'], 2)

    def test_third_not_dispatched_releases_only_its_own_reservation(self):
        prior = [{'number': number, 'status': 'response_received',
                  'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
                  'reserved_upper_bound': 3000} for number in (1, 2)]
        state = dict(self.state, requests=deepcopy(prior), request_attempts=2,
                     known_total_tokens=30, reserved_token_bound=6000)
        real_write = write_json
        def save(path, data):
            if Path(path).name == 'request_03.json':
                raise windows_denial()
            real_write(path, data)
        bounded = BoundedClient(self.client, self.output, state)
        with patch('new_paper_agent.save', side_effect=save):
            with self.assertRaises(OSError):
                bounded.request([{'role': 'user', 'content': 'third'}])
        self.create.assert_not_called()
        self.assertEqual(state['requests'][:2], prior)
        self.assertEqual(state['request_attempts'], 3)
        self.assertEqual(state['dispatched_requests'], 2)
        self.assertEqual(state['known_total_tokens'], 30)
        self.assertEqual(state['unknown_cost_attempts'], 0)
        self.assertEqual(state['reserved_token_bound'], 6000)
        self.assertEqual(state['requests'][2]['status'], 'not_dispatched')
        self.assertFalse((self.output/'response_03.json').exists())

    def test_post_dispatch_storage_failure_preserves_paid_usage_without_repeating_api(self):
        self.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model_dump=lambda **kwargs: {'usage': {'total_tokens': 15}})
        bounded = BoundedClient(self.client, self.output, self.state)
        real_write = write_json
        def save(path, data):
            if Path(path).name == 'response_01.json':
                raise windows_denial()
            real_write(path, data)
        with patch('new_paper_agent.save', side_effect=save):
            with self.assertRaises(OSError):
                bounded.request([{'role': 'user', 'content': 'test'}])
        self.create.assert_called_once()
        self.assertEqual(self.state['dispatched_requests'], 1)
        self.assertEqual(self.state['known_total_tokens'], 15)
        self.assertEqual(self.state['unknown_cost_attempts'], 0)
        self.assertEqual(self.state['requests'][0]['status'], 'response_received')
        self.assertEqual(self.state['requests'][0]['usage']['total_tokens'], 15)
        self.assertGreater(self.state['reserved_token_bound'], 0)

    def local_report(self):
        source = self.output/'source.docx'
        source.write_bytes(b'fixed local source')
        report = {'source_sha256': sha256(source.read_bytes()).hexdigest(), 'student_name': '学生',
                  'file_name': 'source.docx', 'findings': [], 'rules': [], 'coverage': [],
                  'summary': {'candidate_count': 0, 'pending_count': 0}, 'limitations': [],
                  'table_count': 0, 'image_count': 0}
        return source, report

    def test_review_reports_local_storage_failure_with_recovery_path_and_zero_dispatch(self):
        source, report = self.local_report()
        real_replace = os.replace
        def replace(temporary, target):
            if Path(target).name == 'request_01.json':
                raise windows_denial()
            real_replace(temporary, target)
        async def review(value, client, output, state, cancel_check=None):
            BoundedClient(client, output, state).request([{'role': 'user', 'content': 'test'}])
        with patch('new_paper_checks.execute_checks', return_value=deepcopy(report)), \
             patch('new_paper_agent.review_with_tools', side_effect=review), \
             patch('atomic_json.os.replace', side_effect=replace), patch('atomic_json.time.sleep'):
            result = run_review(source, self.output/'review', use_agent=True,
                                client=self.client, collect_word=False)
        self.create.assert_not_called()
        self.assertIn('本地运行记录保存失败', result['run']['error'])
        self.assertNotIn('外部服务', result['run']['error'])
        self.assertEqual(result['run']['status'], 'partial')
        self.assertEqual(result['requests']['llm'], 0)
        self.assertEqual(result['run']['request_attempts'], 1)
        recovery = Path(result['run']['storage_error']['temporary_path'])
        self.assertTrue(recovery.is_file())
        self.assertEqual(result['run']['storage_error']['commit_attempts'], 6)
        self.assertEqual(read_json(self.output/'review/report.json'), result)

    def test_final_report_permanent_save_failure_propagates_and_keeps_old_report(self):
        source, report = self.local_report()
        review = self.output/'review'
        review.mkdir()
        previous = {'status': 'previous valid report'}
        write_json(review/'report.json', previous)
        real_replace = os.replace
        def replace(temporary, target):
            if Path(target).name == 'report.json':
                raise windows_denial()
            real_replace(temporary, target)
        with patch('new_paper_checks.execute_checks', return_value=deepcopy(report)), \
             patch('atomic_json.os.replace', side_effect=replace), patch('atomic_json.time.sleep'):
            with self.assertRaises(AtomicJSONWriteError) as caught:
                run_review(source, review, use_agent=False, collect_word=False)
        self.create.assert_not_called()
        self.assertEqual(read_json(review/'report.json'), previous)
        self.assertEqual(caught.exception.target_path.name, 'report.json')
        self.assertIn('source_sha256', read_json(caught.exception.temporary_path))


if __name__ == '__main__':
    unittest.main()
