"""Run the complete project offline regressions and save an exact receipt."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    names = sorted(p.stem for p in root.glob('test_*.py') if p.stem != 'test_deepseek')
    names += ['web_backend.' + p.stem for p in sorted((root / 'web_backend').glob('test_*.py'))]
    start = datetime.now().astimezone().isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    from review_contract import review_identity
    frozen = root / 'legacy_doc_provenance.py'
    frozen_sha = hashlib.sha256(frozen.read_bytes()).hexdigest()
    expected = 'bd04d164a0af60ab1f654180dc910383b55a8fa45541823b09abc90ecf9179cc'
    receipt = {
        'started_at': start, 'finished_at': datetime.now().astimezone().isoformat(),
        'status': 'passed' if result.wasSuccessful() and frozen_sha == expected else 'failed',
        'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'modules': names,
        'failure_details': [{'test': str(t), 'traceback': trace} for t, trace in result.failures],
        'error_details': [{'test': str(t), 'traceback': trace} for t, trace in result.errors],
        'legacy_provenance_sha256': frozen_sha, 'legacy_provenance_unchanged': frozen_sha == expected,
        'review_contract': review_identity(), 'independent_accuracy_test': False,
        'scope': 'Offline project regressions with mocks; not student grading accuracy.',
    }
    output = root / 'output' / 'audit_20261010' / 'remediation_tests.json'
    from atomic_json import write_json
    write_json(output, receipt)
    print(json.dumps({k:receipt[k] for k in ('status','tests_run','failures','errors','skipped','legacy_provenance_unchanged')}, ensure_ascii=False))
    raise SystemExit(0 if receipt['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
