"""Empty installations are usable workbenches, never verified paper reviews.

Absence is determined before reading historical evidence. Once any historical
directory is present the ordinary strict reader must run; corruption is never
converted into an empty workspace.
"""
from datetime import datetime
from pathlib import Path

HISTORY_DIRECTORIES = (
    'data/pipeline_runs/new_batch_20261007_205126_048522',
    'data/svn_exports/cross_student_rules_20261007_121139_462691',
    'data/svn_exports/cross_student_20261007_113217_741043',
)
CONFIGURATION_MESSAGE = (
    '尚未配置可执行的教师规则及其来源证据，当前不能受理论文审阅。'
    '请先配置有访问权限的 SVN 工作副本，导出教师批改稿并提取批注；'
    '随后核验规则适用范围，接入已核验的检查目录及对应原件。'
    '迁移现有规则时，须保留完整来源清单、文件哈希和原件，并核对目标机器路径。'
    '仅导入分类结果或配置 API Key 不会启用论文检查。'
)


def historical_workspace_present(root):
    return any((Path(root) / relative).exists() for relative in HISTORY_DIRECTORIES)


def empty_workspace():
    startup = {'mode': 'empty_workspace', 'status': 'configuration_required',
               'checks_enabled': False, 'message': CONFIGURATION_MESSAGE,
               'requirements': ['配置合法授权的 SVN 工作副本及访问权限',
                                '取得教师原件、来源清单及可核验文件哈希',
                                '确认检查项及适用范围，完成规则接入',
                                '独立核验 Word 分页、模型接入及论文效果']}
    return {'schema_version': 1, 'title': 'SVN论文审阅工作台',
            'generated_at': datetime.now().astimezone().isoformat(),
            'students': [], 'findings': [], 'teacher_comments': [],
            'documents': [], 'runs': [], 'actions': [], 'startup': startup,
            'summary': {key: 0 for key in (
                'first_person_occurrences', 'first_person_paragraphs',
                'user_confirmations', 'codex_reviews', 'chapter_issues',
                'table_issues', 'unresolved_object_targets', 'scoped_conditions')},
            'warnings': ['当前为无历史数据工作区；未生成论文检查结论。',
                         CONFIGURATION_MESSAGE]}, {}


def require_executable_rules(root):
    """Missing catalog gives setup guidance; existing invalid sources fail closed."""
    from new_paper_checks import CATALOG, load_teacher_rules
    from portable_rules import packaged_mode, load_packaged_rules
    if packaged_mode(root):
        from new_paper_checks import ROOT
        if Path(ROOT).resolve() != Path(root).resolve():
            raise ValueError('可分发规则所属工作区不一致。')
        return load_packaged_rules(root)
    catalog = Path(root) / 'data/svn_exports/cross_student_rules_20261007_121139_462691/reviewed_pilot_20261007_121835_788699/reviewed_rules.json'
    if not catalog.exists():
        raise ValueError(CONFIGURATION_MESSAGE)
    if Path(CATALOG).resolve() != catalog.resolve():
        raise ValueError('检查目录所属工作区不一致；请核验部署路径，不能引用其他机器的规则。')
    rules = load_teacher_rules()
    if not any(rule.get('enabled', True) for rule in rules):
        raise ValueError(CONFIGURATION_MESSAGE)
    return rules


def packaged_workspace(root):
    """Ready upload workbench without claiming historical evidence verification."""
    from portable_rules import load_packaged_rules, BOUNDARY
    rules = load_packaged_rules(root)
    data, assets = empty_workspace()
    data['startup'] = {'mode': 'packaged_workspace', 'status': 'ready',
                       'checks_enabled': True, 'message': BOUNDARY,
                       'rule_count': len(rules), 'enabled_rule_count': sum(r['enabled'] for r in rules),
                       'historical_originals_verified': False}
    data['warnings'] = [BOUNDARY]
    return data, assets
