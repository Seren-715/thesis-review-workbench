"""Run the standalone offline regression scripts under ``unittest`` discovery.

The seven scripts below were written as ``python check.py`` programs with a
``main()`` entry point, so ``unittest discover`` imported them but executed
zero tests ("Ran 0 tests"). They are offline by construction (mocked SVN,
model and Word), but generating their fixtures is slow, so set
``SKIP_OFFLINE_REGRESSIONS=1`` to skip them locally. Each test launches the
script in a subprocess with the current interpreter, which keeps their
``sys.stdout.reconfigure`` and module-level state isolated.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGRESSIONS = (
    'test_paper_pipeline_offline.py',
    'test_scoped_rules_offline.py',
    'test_object_alignment_offline.py',
    'test_remaining_initial_layout_offline.py',
    'test_three_rule_pilot_offline.py',
    'test_three_rule_pilot_v2_offline.py',
    'test_three_rule_pilot_v3_offline.py',
)
SKIP = os.environ.get('SKIP_OFFLINE_REGRESSIONS') == '1'


@unittest.skipIf(SKIP, 'SKIP_OFFLINE_REGRESSIONS=1')
class OfflineRegressionTests(unittest.TestCase):
    maxDiff = None

    def run_script(self, name):
        process = subprocess.run(
            [sys.executable, str(HERE / name)],
            cwd=str(HERE), capture_output=True, timeout=300,
        )
        output = (process.stdout + process.stderr).decode('utf-8', errors='replace')
        self.assertEqual(process.returncode, 0,
                         f'{name} exited {process.returncode}:\n{output[-4000:]}')
        self.assertNotIn('Traceback', output, f'{name} raised:\n{output[-4000:]}')


def _make_case(name):
    def case(self):
        self.run_script(name)
    case.__name__ = 'test_' + Path(name).stem.removeprefix('test_')
    return case


for _name in REGRESSIONS:
    _case = _make_case(_name)
    setattr(OfflineRegressionTests, _case.__name__, _case)


if __name__ == '__main__':
    unittest.main()
