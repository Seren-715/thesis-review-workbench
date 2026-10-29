"""Missing private evidence permits setup, never silent acceptance/corruption."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from web_backend.portable_startup import (HISTORY_DIRECTORIES, CONFIGURATION_MESSAGE,
                                         empty_workspace, require_executable_rules)


class PortableStartupTest(unittest.TestCase):
    def test_empty_workspace_has_no_paper_success_or_fixed_actions(self):
        data, assets = empty_workspace()
        self.assertEqual(data['startup']['status'], 'configuration_required')
        self.assertFalse(data['startup']['checks_enabled'])
        for key in ('students', 'findings', 'teacher_comments', 'documents', 'runs', 'actions'):
            self.assertEqual(data[key], [])
        self.assertEqual(assets, {})
        self.assertTrue(all(count == 0 for count in data['summary'].values()))

    def test_no_history_skips_historical_reader(self):
        from web_backend.evidence_adapter import build_workspace
        with TemporaryDirectory() as folder, patch('web_backend.evidence_adapter.ROOT', Path(folder)), \
                patch('web_backend.evidence_adapter.build_workspace_async') as reader:
            data, _ = build_workspace()
            self.assertEqual(data['startup']['mode'], 'empty_workspace')
            reader.assert_not_called()

    def test_any_partial_history_keeps_strict_error(self):
        from web_backend.evidence_adapter import build_workspace
        async def corrupt():
            raise ValueError('Evidence asset changed')
        for relative in HISTORY_DIRECTORIES:
            with self.subTest(relative=relative), TemporaryDirectory() as folder:
                root = Path(folder)
                (root / relative).mkdir(parents=True)
                with patch('web_backend.evidence_adapter.ROOT', root), \
                        patch('web_backend.evidence_adapter.build_workspace_async', corrupt):
                    with self.assertRaisesRegex(ValueError, 'Evidence asset changed'):
                        build_workspace()

    def test_missing_rules_rejected_before_loading(self):
        with TemporaryDirectory() as folder, patch('new_paper_checks.load_teacher_rules') as loader:
            with self.assertRaisesRegex(ValueError, '尚未配置可执行'):
                require_executable_rules(Path(folder))
            loader.assert_not_called()

    def test_existing_corrupt_rule_source_not_reported_as_unconfigured(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            catalog = root / 'data/svn_exports/cross_student_rules_20261007_121139_462691/reviewed_pilot_20261007_121835_788699/reviewed_rules.json'
            catalog.parent.mkdir(parents=True)
            catalog.write_text('{}', encoding='utf-8')
            with patch('new_paper_checks.CATALOG', catalog), \
                    patch('new_paper_checks.load_teacher_rules', side_effect=ValueError('教师证据源文件哈希不符')):
                with self.assertRaisesRegex(ValueError, '教师证据源文件哈希不符'):
                    require_executable_rules(root)


if __name__ == '__main__':
    unittest.main()
