"""Current-source delivery tests; no private data or application services."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from build_delivery_package import build
from verify_delivery_package import extract


class DeliveryPackageTests(unittest.TestCase):
    def fixture(self, root):
        content = {
            'run_agent_ui.py': '# current code\n',
            '论文核查工作台使用说明.md': '# 使用说明\n',
            'config.example.json': '{}',
            'deliverables/部署安装与迁移边界.md': '# 部署\n',
            'deliverables/独立评测与规则审定操作说明.md': '# 评测\n',
            'deliverables/人工验收清单.md': '# 人工验收\n',
            'web_frontend/dist/index.html': '<script src="/assets/current.js"></script>',
            'web_frontend/dist/assets/current.js': 'console.log("current")',
            'web_frontend/src/App.tsx': 'export default function App(){return null}',
            'web_frontend/package.json': '{}',
            'web_frontend/package-lock.json': '{}',
            'web_frontend/tsconfig.json': '{}',
            'desktop-app/src/main.cjs': '// desktop source',
            'desktop-app/package.json': '{}',
            'desktop-app/package-lock.json': '{}',
            'desktop-app/node_modules/private_cache.txt': 'not an artifact',
            'desktop-app/README.md': '# Desktop',
            '桌面应用使用说明.md': '# 桌面说明',
            'data/private_student.docx': 'private',
            'config.json': '{"DEEPSEEK_API_KEY":"private-test-value"}',
            'web_frontend/node_modules/private_cache.txt': 'not an artifact',
        }
        for relative, value in content.items():
            file = root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(value, encoding='utf-8')

    def test_current_source_frontend_and_private_exclusion(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / 'project'
            self.fixture(root)
            output = Path(temporary) / 'delivery'
            receipt = build(root, output, '20261011')
            self.assertEqual(Path(receipt['archive_path']).name, 'thesis_review_source_20261011.zip')
            names = {item['path'] for item in receipt['files']}
            self.assertIn('web_frontend/src/App.tsx', names)
            self.assertIn('web_frontend/package-lock.json', names)
            self.assertIn('deliverables/人工验收清单.md', names)
            self.assertIn('desktop-app/src/main.cjs', names)
            self.assertIn('desktop-app/package-lock.json', names)
            self.assertIn('桌面应用使用说明.md', names)
            self.assertFalse(any(name.startswith('data/') or 'node_modules' in name for name in names))
            self.assertNotIn('config.json', names)
            for item in receipt['files']:
                self.assertEqual(item['sha256'], hashlib.sha256((root / item['path']).read_bytes()).hexdigest())
            destination = extract(output / 'source_package_manifest.json', Path(temporary) / 'verified')
            self.assertEqual((destination / 'web_frontend/dist/assets/current.js').read_bytes(), (root / 'web_frontend/dist/assets/current.js').read_bytes())

    def test_refuses_existing_delivery_without_modifying_it(self):
        with TemporaryDirectory() as temporary:
            root, output = Path(temporary) / 'project', Path(temporary) / 'delivery'
            self.fixture(root)
            receipt = build(root, output, '20261011')
            old_bytes = Path(receipt['archive_path']).read_bytes()
            (root / 'run_agent_ui.py').write_text('# changed later\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                build(root, output, '20261011')
            self.assertEqual(Path(receipt['archive_path']).read_bytes(), old_bytes)

    def test_rejects_missing_asset_before_creating_package(self):
        with TemporaryDirectory() as temporary:
            root, output = Path(temporary) / 'project', Path(temporary) / 'delivery'
            self.fixture(root)
            (root / 'web_frontend/dist/index.html').write_text('<script src="/assets/absent.js"></script>', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'missing or invalid asset'):
                build(root, output, '20261011')
            self.assertFalse(output.exists())

    def test_rejects_invalid_release_name(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / 'project'
            self.fixture(root)
            for release in ('../../old', '2026-10-11', '20261311'):
                with self.assertRaises(ValueError):
                    build(root, Path(temporary) / 'delivery', release)


if __name__ == '__main__':
    unittest.main()
