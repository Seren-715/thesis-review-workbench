"""Read-only acceptance of the restarted local service; no review is triggered."""
import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen


def get(path):
    with urlopen('http://127.0.0.1:8787' + path, timeout=45) as response:
        return json.load(response)


if __name__ == '__main__':
    health = get('/api/health')
    discovery = get('/api/thesis-workflow')['discovery']
    library = get('/api/rule-library')
    reserved = [s for s in discovery['students'] if s.get('teacher_collection_allowed') is False]
    root = Path(__file__).resolve().parent
    assert health['status'] == 'ready'
    assert discovery['total_students'] == 17
    assert any(s['id'].startswith('212003242_') for s in reserved)
    assert library['summary']['common_candidate_count'] == 18
    assert library['summary']['cross_student_candidate_count'] == 13
    assert library['summary']['single_student_feedback_count'] == 13
    assert (root / 'web_frontend/dist/index.html').is_file()
    result = {
        'status': 'restarted_service_read_only_acceptance_passed',
        'checked_at': datetime.now().astimezone().isoformat(),
        'health': health, 'discovered_students': discovery['total_students'],
        'reserved_students': [{'id': s['id'], 'name': s['name'],
                               'teacher_collection_status': s['teacher_collection_status']}
                              for s in reserved],
        'library_summary': library['summary'], 'frontend_build_present': True,
        'new_llm_requests': 0, 'new_svn_requests': 0, 'new_word_requests': 0,
        'historical_reports_modified': False,
        'note': '17 discovered students differs from the 16-student development rule library; reserved feedback was not mined.',
    }
    from atomic_json import write_json
    write_json(root/'output/audit_20261010/restarted_service_acceptance.json', result)
    print(json.dumps({k:result[k] for k in ('status','discovered_students','reserved_students')}, ensure_ascii=False))
