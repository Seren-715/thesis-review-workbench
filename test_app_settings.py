import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from app_settings import load_settings, DEFAULT_WORKCOPY


class AppSettingsTests(unittest.TestCase):
    def test_defaults_importable_without_installed_svn(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(load_settings(tmp, {})['workcopy'], DEFAULT_WORKCOPY)

    def test_utf8_local_paths_and_environment_override(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '工作区').mkdir()
            (root / '工具.exe').write_bytes(b'not executed')
            (root / 'config.json').write_text(json.dumps({'workcopy': '工作区', 'svn_executable': '工具.exe'}, ensure_ascii=False), encoding='utf-8-sig')
            result = load_settings(root, {})
            self.assertEqual(result['workcopy'], (root / '工作区').resolve())
            alternate = root / '另一工作区'
            alternate.mkdir()
            self.assertEqual(load_settings(root, {'THESIS_WORKCOPY': str(alternate)})['workcopy'], alternate.resolve())

    def test_invalid_path_type_and_secrets_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for config in [{'workcopy': 'missing'}, {'workcopy': 4}, {'api_key': 'never-store'}, {'unknown': 'x'}]:
                (root / 'config.json').write_text(json.dumps(config), encoding='utf8')
                with self.assertRaises(ValueError): load_settings(root, {})


if __name__ == '__main__': unittest.main()
