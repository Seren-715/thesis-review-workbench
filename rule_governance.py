"""Create source-bound rule review packages, never fabricate human approval."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from atomic_json import write_json

VERSION = 'rule_review_package_v1'


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def build_review_package(library, checker_rules):
    """Read actual family-to-checker associations; no keyword guesswork."""
    linked = {}
    for checker in checker_rules:
        family = checker.get('family')
        # Supplemental structure checks may share a family label without
        # implementing any literal teacher feedback. They cannot count as a
        # reliable teacher-rule association merely because their IDs match.
        if family and checker.get('enabled', True) and checker.get('teacher_evidence'):
            linked.setdefault(family, []).append(checker)
    entries = []
    for rule in library['rules']:
        methods = linked.get(rule['id'], [])
        count = len(set(rule.get('supporting_student_ids', [])))
        recurrence = ('common_candidate' if count >= 3 else
                      'cross_student_candidate' if count == 2 else
                      'single_student_feedback' if count == 1 else 'unidentified_student_feedback')
        # These are observed erroneous examples from teacher-annotated material,
        # not independent labels and not assertions about current draft quality.
        examples = [dict(source_sha256=e.get('source_sha256'),
                         comment_id=e.get('comment_id'), student_id=e.get('student_id'),
                         original_excerpt=(e.get('anchor') or {}).get('text',
                                         (e.get('anchor') or {}).get('selected_text', '')),
                         teacher_quote=e.get('quote'), source_path=e.get('source_path'),
                         status='observed_teacher_feedback_not_independent_ground_truth')
                    for e in rule.get('teacher_evidence', [])]
        item = {
            'id': rule['id'], 'title': rule['title'], 'status': 'review_candidate',
            'recurrence_status': recurrence, 'distinct_supporting_students': count,
            'minimum_common_students': 3,
            'supporting_student_ids': rule.get('supporting_student_ids', []),
            'checker_association': 'direct_checker_linked' if methods else 'no_direct_checker_link',
            'checker_ids': [m['id'] for m in methods],
            'checker_scopes': [{'id': m['id'], 'scope': m.get('scope'),
                                'enabled': m.get('enabled', True),
                                'policy': m.get('policy')} for m in methods],
            'scope': rule.get('scope'), 'teacher_evidence': rule.get('teacher_evidence', []),
            'observed_examples': examples,
            'positive_examples': {'status': 'human_label_pending', 'items': []},
            'negative_examples': {'status': 'human_label_pending', 'items': []},
            'misfire_boundaries': {
                'status': 'human_review_pending',
                'existing_checker_scope_limits': [m.get('scope', '') for m in methods],
                'requirements': ['确认论文类型和教师/模板适用条件',
                                 '不得由同源教师批注推为独立初稿真值',
                                 '局部批注不得推广为整篇通用规范']},
            'refusal_conditions': ['来源哈希或批注引文无法核验', '论文未声明采用该教师/模板要求',
                                   '对象/章节/版面尚未可靠定位', '证据不足以区分未说明与未实现'],
            'human_signoff': {'status': 'pending', 'reviewer': None, 'signed_at': None,
                             'decision': None, 'approved_scope': None},
            'automatic_error_judgment_enabled': False,
        }
        item['entry_sha256'] = canonical_sha(item)
        entries.append(item)
    summary = Counter(r['recurrence_status'] for r in entries)
    package = {
        'schema_version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'human_review_pending', 'source_fingerprint': library.get('source_fingerprint'),
        'library_sha256': canonical_sha(library), 'minimum_common_students': 3,
        'summary': {'rule_families': len(entries), **dict(summary),
                    'no_direct_checker_link': sum(not r['checker_ids'] for r in entries),
                    'human_signed_rules': 0},
        'rules': entries,
        'limitations': ['分类和跨学生频次不等于可靠规则审定',
                        '无直接检查关联不等于完全没有间接检查',
                        '尚未提供逐项人工正反例、适用边界及签审；不可宣布规则冻结'],
    }
    package['package_sha256'] = canonical_sha(package)
    return package


def write_package(output, package):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'rule_review_package.json', package)
    summary = package['summary']
    lines = ['# 规则审定包', '', f"状态：待人工签审；版本：{VERSION}。",
             '每类保留实际教师引文、来源、已实现检查范围、待补正反例及签审空栏。', '',
             f"共 {summary['rule_families']} 类；三人及以上 {summary.get('common_candidate', 0)} 类；"
             f"两人 {summary.get('cross_student_candidate', 0)} 类；一人 {summary.get('single_student_feedback', 0)} 类。",
             f"没有直接关联检查方法：{summary['no_direct_checker_link']} 类。所有类别仍是候选，未自动审批。", '',
             '| 类别 | 支持学生数 | 频次档位 | 直接检查方法 | 人工签审 |',
             '| --- | ---: | --- | --- | --- |']
    for r in package['rules']:
        lines.append(f"| {r['title']} | {r['distinct_supporting_students']} | {r['recurrence_status']} | "
                     f"{', '.join(r['checker_ids']) or '尚无直接关联'} | pending |")
    (output / '规则审定包说明.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from teacher_rule_library import get_library
    from new_paper_checks import load_teacher_rules
    result = build_review_package(get_library(args.root), load_teacher_rules())
    write_package(args.output, result)
    print(json.dumps(result['summary'], ensure_ascii=False))
