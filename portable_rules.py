"""Build-time projection of verified rules; no private evidence in distributions.

The digest detects corruption, not malicious replacement. Historical originals
are verified only by the build-side strict loader, never claimed present at run time.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

MODE = 'packaged_general_rules_v1'
MANIFEST = 'rules_mode.json'
PACKAGE = 'portable_rules.json'
DIGEST = 'portable_rules.sha256'
BOUNDARY = ('可分发通用检查配置；历史原件与个案引文未随包提供，运行时未核验教师原件。'
            '检查结果为候选建议，适用范围须由当前论文及教师要求确认；不代表通用评分标准。'
            '学校格式数值和原项目用户确认要求未迁移。')
FIELDS = {'id', 'title', 'family', 'checker', 'scope_selector', 'requires_policy', 'policy_scope'}
DISABLED_IDS = {'NP042', 'NP043'}


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')


def packaged_mode(root):
    path = Path(root) / MANIFEST
    if not path.exists():
        return False
    try:
        value = json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError) as error:
        raise ValueError('可分发规则模式配置损坏。') from error
    if value != {'mode': MODE, 'schema_version': 1}:
        raise ValueError('不支持的规则模式；不能回退到其他规则来源。')
    return True


def export_packaged_rules(destination):
    """Call only on the configured build machine, before copying the app files."""
    from new_paper_checks import ROOT, load_teacher_rules
    if packaged_mode(ROOT):
        raise ValueError('规则导出必须使用严格原件核验工作区，不能再导出已打包规则。')
    originals = load_teacher_rules()
    rules = []
    private_names = set()
    for original in originals:
        private_names.update(e.get('student_name', '') for e in original.get('teacher_evidence', []))
        row = {k: deepcopy(original[k]) for k in FIELDS if k in original}
        general = deepcopy(original['general_requirements'])
        general['reference_requirement_ids'] = []
        general['evidence_role'] = 'build_time_derived_configuration_without_distributed_originals'
        row.update(scope='；'.join(general['requirements']),
                   check_dimensions=deepcopy(general['requirements']), general_requirements=general,
                   enabled=bool(original.get('enabled', True)) and not original['id'].startswith('UP')
                           and original['id'] not in DISABLED_IDS,
                   teacher_evidence=[], reference_evidence=[], reference_requirements=[], reference_sources=[],
                   policy_evidence=[], policy='packaged_general_candidate_requires_applicability_confirmation',
                   automatic_error_judgment_enabled=False,
                   packaged_provenance={'original_rule_id': original['id'],
                       'original_rule_sha256': hashlib.sha256(_bytes(original)).hexdigest(),
                       'originals_verified_at_runtime': False, 'boundary': BOUNDARY})
        if not row['enabled']:
            row['disabled_reason'] = '当前配置未启用；学校规范及原项目用户确认要求不能自动迁移。'
        rules.append(row)
    value = {'schema_version': 1, 'mode': MODE, 'boundary': BOUNDARY,
             'rules': rules, 'source_rule_count': len(originals)}
    raw = _bytes(value)
    text = raw.decode('utf8')
    if any(name and len(name) >= 2 and name in text for name in private_names):
        raise ValueError('规则通用字段仍包含来源个人姓名；导出已停止。')
    if re.search(r'[A-Za-z]:[\\/]|(?:source|reader|document|repository)_path', text):
        raise ValueError('规则包仍含来源路径；导出已停止。')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / PACKAGE).write_bytes(raw)
    (destination / DIGEST).write_text(hashlib.sha256(raw).hexdigest() + '\n', encoding='ascii')
    (destination / MANIFEST).write_bytes(_bytes({'mode': MODE, 'schema_version': 1}))
    load_packaged_rules(destination)
    return value


def load_packaged_rules(root):
    root = Path(root)
    if not packaged_mode(root):
        raise ValueError('没有显式启用可分发规则模式。')
    try:
        raw = (root / PACKAGE).read_bytes()
        expected = (root / DIGEST).read_text(encoding='ascii').strip()
        if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('规则包哈希不符。')
        value = json.loads(raw)
        rules = value['rules']
        if (value['schema_version'] != 1 or value['mode'] != MODE or value['boundary'] != BOUNDARY
                or not isinstance(rules, list) or not rules or len(rules) != value['source_rule_count']):
            raise ValueError('规则包结构不符。')
        seen = set()
        for row in rules:
            rid = row['id']
            if not isinstance(rid, str) or not re.fullmatch(r'(?:NP|UP)\d{3}', rid) or rid in seen:
                raise ValueError('规则标识无效。')
            seen.add(rid)
            if any(row.get(k) != [] for k in ('teacher_evidence', 'reference_evidence', 'reference_requirements', 'reference_sources', 'policy_evidence')):
                raise ValueError('可分发包不能声明私人或学校原件证据。')
            provenance = row['packaged_provenance']
            if (provenance['original_rule_id'] != rid or provenance['originals_verified_at_runtime'] is not False
                    or provenance['boundary'] != BOUNDARY
                    or not re.fullmatch(r'[0-9a-f]{64}', provenance['original_rule_sha256'])
                    or type(row['enabled']) is not bool
                    or row['automatic_error_judgment_enabled'] is not False
                    or not isinstance(row['general_requirements']['requirements'], list)
                    or not row['general_requirements']['requirements']):
                raise ValueError('规则来源边界无效。')
            if (rid.startswith('UP') or rid in DISABLED_IDS) and row['enabled']:
                raise ValueError('未携带来源的专属要求不能启用。')
        if not {'NP001', 'NP002', 'NP003'} <= seen or not any(r['enabled'] for r in rules):
            raise ValueError('规则包缺少基本检查项。')
        return deepcopy(rules)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        raise ValueError('可分发规则包缺失或损坏；论文检查已禁用。') from error
