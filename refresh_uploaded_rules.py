"""Recheck stored uploads locally, preserving prior reviews and verified layout."""
import argparse
import json
from pathlib import Path
import time

from new_paper_agent import run_review, save, validate_decisions
from web_backend.new_paper_service import NewPapers


def refresh(root, paper_ids):
    previous = {}
    service = None

    def runner(source, output, **options):
        item = previous[source.parent.name]
        options.pop('layout_result', None)
        options.pop('prior_report', None)
        report = run_review(source, output, **options, collect_word=False, layout_result=item['layout'],prior_report=item.get('report'))
        # Use the same source/context/scope checks as the real agent. Local
        # refresh must preserve valid vision receipts without weakening reuse.
        reused = report['run'].get('semantic_evidence_reused',0)
        report['run']['previous_review_id'] = item['review_id']
        report['run']['semantic_evidence_reused'] = reused
        report['run']['refresh_kind'] = 'local_new_rules_and_readable_locations'
        report['summary']['content_review_pending_count'] = sum(1 for f in report['findings']
            if f.get('semantic_required') and f.get('semantic_review', {}).get('decision') not in {'suggested_revision','not_issue'})
        report['run']['semantic_reviewed'] = reused
        report['run']['semantic_unreviewed'] = sum(f['status']=='candidate' and (f.get('semantic_required') or f['rule_id'] in {'NP001','NP002'}) and not f.get('semantic_review') for f in report['findings'])
        save(output / 'run.json', report['run'])
        save(output / 'report.json', report)
        from paper_report import render_report
        (output / 'report.md').write_text(render_report(report), encoding='utf-8')
        return report

    service = NewPapers(root, runner)
    try:
        for paper_id in paper_ids:
            paper = service.get(paper_id)
            review_dir = service.folder(paper_id) / paper['review_id']
            if not (review_dir/'report.json').is_file() and paper.get('previous_review_id'):
                review_dir = service.folder(paper_id)/paper['previous_review_id']
                if (review_dir/'report.json').is_file():
                    paper['report']=json.loads((review_dir/'report.json').read_text(encoding='utf-8'))
                    paper['review_id']=review_dir.name
            layout_path = review_dir / 'layout_result.json'
            layout = json.loads(layout_path.read_text(encoding='utf-8')) if layout_path.is_file() else None
            previous[paper_id] = {'review_id': paper['review_id'], 'layout': layout, 'report': paper.get('report')}
            service.review(paper_id, use_agent=False, chapter_policy=paper.get('chapter_policy', False))
            while True:
                with service.lock:
                    active = service.papers[paper_id]['status'] in {'queued', 'running'}
                if not active:
                    break
                time.sleep(.1)
            result = service.get(paper_id)
            if result['status'] != 'completed':
                raise RuntimeError(result.get('error', 'Local refresh failed'))
            report = result['report']
            print(json.dumps({'paper_id': paper_id, 'file': result['file_name'], 'rules': len(report['rules']),
                              'llm_requests': report['requests']['llm'], 'word_requests': report['requests']['word'],
                              'findings_by_rule': {r['id']: sum(f['rule_id'] == r['id'] for f in report['findings']) for r in report['rules']},
                              'review_id': result['review_id']}, ensure_ascii=False))
    finally:
        service.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parent / 'data/new_papers'))
    parser.add_argument('--paper-id', action='append', required=True)
    args = parser.parse_args()
    refresh(args.root, args.paper_id)
