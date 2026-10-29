"""CLI for the complete evidence-bound thesis workflow, with resumable batches."""
import argparse
import json
import time
from datetime import datetime
import secrets
from thesis_workflow import ThesisWorkflow, save


def wait_job(workflow, action, options):
    job = workflow.start(action, options)
    print('任务：'+job['label']+' / '+job['id'], flush=True)
    previous = ''
    while True:
        value = next(j for j in workflow.snapshot()['jobs'] if j['id'] == job['id'])
        if value['log_tail'] != previous:
            lines = value['log_tail'][len(previous):].strip()
            if lines:
                print(lines, flush=True)
            previous = value['log_tail']
        if value['status'] not in {'queued', 'running'}:
            print('状态：'+value['status'], flush=True)
            print(json.dumps({'result': value.get('result_summary'), 'error': value.get('error'),
                              'tokens': value['known_total_tokens'], 'unknown_cost_attempts': value['unknown_cost_attempts']}, ensure_ascii=False), flush=True)
            return value
        time.sleep(.5)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['discover', 'collect', 'classify', 'reclassify', 'check-initials', 'refresh'])
    p.add_argument('--student', action='append', default=[])
    p.add_argument('--all-students', action='store_true')
    p.add_argument('--max-versions', type=int, default=5)
    p.add_argument('--agent', action='store_true')
    p.add_argument('--chapter-policy', action='store_true')
    p.add_argument('--review-scope',choices=['full','content','figures'],default='full',
                   help='full核查全部；content仅续审完整章节内容；figures仅续审图片。')
    p.add_argument('--max-reserved-tokens', type=int, default=24000,
                   help='本次命令全部学生共享的模型预算上限（0至24000 token）。')
    args = p.parse_args()
    if not 0 <= args.max_reserved_tokens <= 24000:
        p.error('本次命令的模型预算必须为0至24000 token。')
    if args.action in {'classify', 'reclassify'} and args.max_reserved_tokens < 4000:
        p.error('批注归纳预算至少为4000 token。')
    workflow = ThesisWorkflow()
    try:
        if workflow.active():
            p.error('已有完整流程任务运行中。')
        if args.action in {'discover', 'refresh', 'classify', 'reclassify'}:
            options = {'max_requests': 4, 'max_reserved_tokens': args.max_reserved_tokens} if args.action in {'classify', 'reclassify'} else {}
            result = wait_job(workflow, args.action, options)
            return 0 if result['status'] == 'completed' else 1
        students = [s['id'] for s in workflow.snapshot()['discovery']['students']] if args.all_students else args.student
        students = list(dict.fromkeys(students))
        if not students:
            p.error('先discover，再指定 --student 文件夹名或 --all-students。')
        code = 0
        # One invocation is one budgeted round, including every 10-student job.
        ledger = {'id': 'round_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + secrets.token_hex(3),
                  'action': args.action, 'student_ids': students, 'jobs': [],
                  'max_reserved_token_bound': args.max_reserved_tokens,
                  'budget_charge_token_bound': 0, 'reserved_token_bound': 0, 'known_total_tokens': 0,
                  'unknown_cost_attempts': 0, 'status': 'running'}
        ledger_path = workflow.root/'data/thesis_workflow/cli_rounds'/(ledger['id']+'.json')
        save(ledger_path, ledger)
        for start in range(0, len(students), 10):
            options = {'student_ids': students[start:start+10]}
            if args.action == 'collect':
                options['max_versions'] = args.max_versions
            else:
                remaining = (0 if ledger['unknown_cost_attempts'] else
                             max(0, min(args.max_reserved_tokens - ledger['budget_charge_token_bound'],
                                        args.max_reserved_tokens - ledger['reserved_token_bound'])))
                options.update(use_agent=args.agent, chapter_policy=args.chapter_policy,
                               max_reserved_tokens=remaining, review_scope=args.review_scope)
            result = wait_job(workflow, args.action, options)
            ledger['jobs'].append(result.get('id'))
            charged = result.get('budget_charge_token_bound', result.get('reserved_token_bound', 0))
            reserved = result.get('reserved_token_bound', 0)
            known = result.get('known_total_tokens', 0)
            unknown = result.get('unknown_cost_attempts', 0)
            if not all(type(v) is int and v >= 0 for v in (charged, reserved, known, unknown)):
                ledger.update(status='stopped_invalid_usage', unknown_cost_attempts=1)
                save(ledger_path, ledger)
                return 1
            ledger['budget_charge_token_bound'] += max(charged, known)
            ledger['reserved_token_bound'] += reserved
            ledger['known_total_tokens'] += known
            ledger['unknown_cost_attempts'] += unknown
            ledger['remaining_token_bound'] = (0 if ledger['unknown_cost_attempts'] else
                max(0, min(args.max_reserved_tokens - ledger['budget_charge_token_bound'],
                           args.max_reserved_tokens - ledger['reserved_token_bound'])))
            save(ledger_path, ledger)
            if result['status'] == 'failed':
                ledger['status'] = 'failed'; save(ledger_path, ledger)
                return 1
            if result.get('result_summary', {}).get('failed_count', 0) or result.get('result_summary', {}).get('failed_or_unavailable', 0):
                # An unavailable student's source must not omit later students.
                code = 1
            if result['status'] != 'completed':
                # Valid partial reports must not prevent the remaining students
                # from receiving their own checks. Exit nonzero after all batches
                # to preserve the distinction between coverage and completion.
                code = 1
        ledger['status'] = 'partial' if code else 'completed'
        save(ledger_path, ledger)
        print('本轮预算与批次记录：' + str(ledger_path), flush=True)
        return code
    finally:
        workflow.close()


if __name__ == '__main__':
    raise SystemExit(main())
