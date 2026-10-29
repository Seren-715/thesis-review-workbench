"""Bounded structural checks and source-backed semantic review requests.

No network, model, Word or SVN calls. Content requests are NOT quality verdicts.
All offsets identify original visible text; document instructions remain data.
"""
from __future__ import annotations

from collections import defaultdict
import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
NUM = r'\d{1,3}\s*[-‐‑–—－]\s*\d{1,3}'
CAPTION = re.compile(r'^\s*([图表])\s*(' + NUM + r')(?:\s+|[：:])\S')
ENTRY = re.compile(r'^\s*\[\s*(\d{1,3})\s*\]\s*\S')
CITATION = re.compile(r'\[\s*(\d{1,3}(?:\s*(?:[,，、]|[-–—])\s*\d{1,3})*)\s*\]')
OUTSIDE = {'目录', '致谢', '谢辞', '独创性声明', '原创性声明', '学术诚信声明', '参考文献', '附录'}


def _key(p):
    return re.sub(r'\s+', '', str(p.get('location', {}).get('section', ''))).lower().strip('：:')


def _eligible(p, bibliography=False):
    if any(p.get(flag) for flag in ('in_deleted', 'in_toc', 'in_textbox', 'in_table')):
        return False
    section = _key(p)
    return section not in OUTSIDE or (bibliography and section == '参考文献')


def _label(text):
    return tuple(int(n) for n in re.split(r'[-‐‑–—－]', re.sub(r'\s+', '', text)))


def load_expanded_rules(library=None):
    """Consume a hash-verified teacher library; never misbind unrelated feedback.

Missing sources keep structural checks explicitly supplemental. A source saying
"add two English references" does not support numeric citation checks.
"""
    families = {r.get('id', r.get('checker')): r for r in (library or {}).get('rules', [])}
    definitions = [
        ('NP008', '参考文献编号与正文引用一致性', 'references', 'reference_consistency',
         '仅明确参考文献范围中以[n]开头的条目和正文[n]数字引用；不鉴定文献真假、不要求引用顺序或外文数量。'),
        ('NP009', '真实章节标题重复与逆序编号', 'headings', 'heading_numbering',
         '仅真实标题样式/大纲中的显式阿拉伯数字编号；排除目录、普通正文和推断导航标题。不要求统一标题模板。'),
        ('NP010', '图表公式引用与目标一致性', 'object_references', 'object_reference_targets',
         '仅“如图…所示”“见表…”及“公式(章-序)”等显式引用与可靠题注/OMML编号；未找到目标不自动断言对象不存在。'),
        ('NP011', '摘要背景、方法与效果说明', 'abstract_keywords', 'abstract_semantics',
         '按照已核验的教师原话核查摘要背景意义、功能、技术、效果；不规定关键词数量或字数。语义审阅后才形成修改建议。'),
        ('NP012', '结论成果总结与不足方向', 'conclusion_writing', 'conclusion_semantics',
         '已识别结论/结束语/总结与展望范围中的成果归纳、作用意义、不足与改进；不强制照搬系统设计范例。'),
        ('NP013', '实现过程的技术与数据说明', 'implementation_explanation', 'implementation_semantics',
         '仅已识别系统实现/功能实现章节内的技术、流程、数据来源说明；论文之外的实际完成情况不能由文稿自动证实。'),
    ]
    semantic = {'abstract_semantics', 'conclusion_semantics', 'implementation_semantics'}
    result = []
    for rid, title, family, checker, scope in definitions:
        source = families.get(family, families.get('implementation_exposition', {}) if family == 'implementation_explanation' else {})
        evidence = source.get('teacher_evidence', [])
        if checker not in semantic:
            # Require the actual quote to discuss the consistency tested here.
            patterns = {'references': r'(?:引用|参考文献).*(?:编号|序号|重复)|(?:编号|序号).*(?:引用|参考文献)',
                        'headings': r'(?:标题|章节).*(?:编号|重复|顺序)',
                        'object_references': r'(?:引用|提到).*(?:图|表|公式)|(?:图|表|公式).*(?:引用|找不到|不存在)'}
            evidence = [e for e in evidence if re.search(patterns[family], e.get('quote', ''))]
        status = source.get('recurrence_status', 'not_evaluated') if evidence else 'not_teacher_evidence'
        result.append({'id': rid, 'title': title, 'checker': checker, 'family': family, 'scope': scope,
                       'teacher_evidence': evidence, 'policy': 'teacher_specific_not_universal' if evidence else 'supplemental_structural_check',
                       'recurrence_status': status, 'supporting_students': sorted({e.get('student_name', '') for e in evidence if e.get('student_name')}),
                       'automatic_error_judgment_enabled': False, 'enabled': bool(evidence) if checker in semantic else True})
    return result


def check_expanded_rules(document, paragraphs, rules, source_sha256=None):
    by_id = {r['id']: r for r in rules if r.get('id', '').startswith('NP')}
    nodes = list(document.iter(W + 'p'))
    if len(nodes) != len(paragraphs):
        raise ValueError('扩展检查的段落索引与当前文稿不一致')
    findings, coverage = [], []

    def add(rid, p, title, reason, suggestion, start=None, end=None, status='candidate', **extras):
        item = {'id': f'{rid}-{1 + sum(x["rule_id"] == rid for x in findings):04d}', 'rule_id': rid,
                'title': title, 'status': status, 'paragraph_index': p['index'] if p else None,
                'text': p['text'] if p else '', 'start': start, 'end': end,
                'location': p.get('location') if p else None, 'reason': reason, 'suggestion': suggestion,
                'teacher_evidence': by_id[rid].get('teacher_evidence', []), 'policy': by_id[rid].get('policy')}
        item.update(extras)
        findings.append(item)
        return item

    def cover(rid, status, detail, **extras):
        coverage.append({'rule_id': rid, 'title': by_id[rid]['title'], 'status': status, 'detail': detail, **extras})

    if 'NP008' in by_id:
        bibliography = [p for p in paragraphs if _eligible(p, True) and _key(p) == '参考文献']
        entries = [(p, ENTRY.match(p['text'])) for p in bibliography]
        entries = [(p, m) for p, m in entries if m]
        numbers = defaultdict(list)
        for p, m in entries:
            numbers[int(m.group(1))].append(p)
        for number, rows in numbers.items():
            if len(rows) > 1:
                for p in rows:
                    m = ENTRY.match(p['text'])
                    add('NP008', p, '参考文献条目编号重复', f'参考文献范围中编号[{number}]对应{len(rows)}个条目。',
                        '核对是否重复记录或编号错误；重新编号后同步检查正文引用。', m.start(), m.end(), related_paragraph_indices=[r['index'] for r in rows])
        refs_count = 0
        if entries:
            for p in paragraphs:
                if not _eligible(p) or p.get('heading_level') or _key(p) in {'封面／前置材料', '摘要', '中文摘要', '英文摘要', 'abstract'}:
                    continue
                for match in CITATION.finditer(p['text']):
                    raw = re.sub(r'\s+', '', match.group(1))
                    cited = []
                    valid = True
                    for part in re.split(r'[,，、]', raw):
                        ends = re.split(r'[-–—]', part)
                        if len(ends) == 1:
                            cited.append(int(ends[0]))
                        elif len(ends) == 2 and 0 < int(ends[0]) <= int(ends[1]) <= int(ends[0]) + 50:
                            cited.extend(range(int(ends[0]), int(ends[1]) + 1))
                        else:
                            valid = False
                    if not valid or any(n <= 0 for n in cited):
                        continue
                    refs_count += 1
                    absent = sorted(set(cited) - set(numbers))
                    if absent:
                        add('NP008', p, '正文引用编号未匹配参考文献条目', '当前可识别参考文献列表中未找到：' + '、'.join(f'[{n}]' for n in absent) + '；需排除公式、程序数组或特殊引用格式。',
                            '核对原文引用性质及参考文献列表，补充遗漏条目或纠正引用编号。', match.start(), match.end(), missing_reference_numbers=absent)
        cover('NP008', 'partial' if entries else 'not_applicable', f'识别[n]文献条目{len(entries)}条、数字引用{refs_count}处；自动编号、脚注和非方括号引文未覆盖；不检查文献真实性。')

    if 'NP009' in by_id:
        headings = []
        for p in paragraphs:
            if not _eligible(p) or not p.get('heading_level'):
                continue
            m = re.match(r'^\s*(?:第\s*(\d{1,3})\s*章\s*|((?:\d{1,3}\.)*\d{1,3})(?:[、.．]|\s+)\s*)(\S)', p['text'])
            if m:
                number = (int(m.group(1)),) if m.group(1) else tuple(int(n) for n in m.group(2).split('.'))
                headings.append((p, m, number))
        groups, previous = defaultdict(list), {}
        for p, m, number in headings:
            groups[(p['heading_level'], number)].append((p, m))
        duplicates = set()
        for (_, number), rows in groups.items():
            if len(rows) < 2:
                continue
            for p, m in rows:
                duplicates.add(p['index'])
                add('NP009', p, '真实标题编号重复', f'同一大纲层级的显式编号{".".join(map(str, number))}出现{len(rows)}次；需要确认是否分属正文和附录独立编号范围。',
                    '核对标题身份与编号范围，再修改重复编号；不修改目录文字代替正文标题。', m.start(), m.end(), related_paragraph_indices=[r[0]['index'] for r in rows])
        for p, m, number in headings:
            scope = (p['heading_level'], number[:-1])
            prior = previous.get(scope)
            if prior and number[-1] < prior[2][-1] and p['index'] not in duplicates:
                add('NP009', p, '同一层次标题编号逆序', f'同一数字父级下前一标题编号为{".".join(map(str, prior[2]))}，此处为{".".join(map(str, number))}。',
                    '核对是否处于同一章节范围，再调整标题编号；跳号本身未判错。', m.start(), m.end(), related_paragraph_indices=[prior[0]['index']])
            previous[scope] = (p, m, number)
        cover('NP009', 'checked' if headings else 'not_applicable', f'已读取{len(headings)}个真实大纲标题中的显式数字编号；目录、普通段落、中文数字和Word自动编号未参加该检查。')

    if 'NP010' in by_id:
        from object_reference_checks import check_object_references
        reference_findings, reference_coverage = check_object_references(document, paragraphs, by_id['NP010'], source_sha256)
        findings.extend(reference_findings)
        coverage.append(reference_coverage)

    for rid, family, dimensions in [
        ('NP011', 'abstract', ['背景和意义', '系统功能或研究任务', '技术或方法', '工作效果']),
        ('NP012', 'conclusion', ['工作内容和成果归纳', '作用和意义', '不足和改进方向']),
        ('NP013', 'implementation', ['实现使用的技术', '逻辑或处理流程', '数据来源或数据库表']),
    ]:
        if rid not in by_id:
            continue
        rule = by_id[rid]
        if not rule.get('enabled', bool(rule.get('teacher_evidence'))):
            cover(rid, 'needs_review', '尚无已核验且与本检查同类的教师要求；未启用语义质量判断。')
            continue
        from content_review_context import collect_content_scope
        selected, content_scope = collect_content_scope(paragraphs, family, source_sha256)
        if selected:
            first = selected[0]
            primary_end = min(1000, len(first['text']))
            context, remaining = [], 1800 - primary_end
            included_characters = primary_end
            for p in selected[1:]:
                if remaining <= 0:
                    break
                excerpt = p['text'][:remaining]
                context.append({'paragraph_index': p['index'], 'text': excerpt, 'location': p.get('location')})
                remaining -= len(excerpt)
                included_characters += len(excerpt)
            preview_complete = included_characters == sum(len(p['text']) for p in selected)
            add(rid, first, rule['title'] + '待语义审阅', '已提取对应章节原文；关键词存在或缺失不构成质量结论，需要结合这些段落按教师原话审阅。',
                '逐项核对原文说明与教师要求，建议必须附原文引文；未说明与实际未实现应区分。', 0, primary_end,
                status='candidate', semantic_required=True, context=context, check_dimensions=dimensions,
                section_complete=True, text_scope='identified_section_complete',
                preview_complete=preview_complete, content_scope=content_scope,
                omitted_paragraph_count=max(0, len(selected) - len(context) - 1))
            cover(rid, 'partial', f'已定位{len(selected)}个相关段落；完整来源{content_scope["total_characters"]}字符、{content_scope["total_chunks"]}个可续读块，预览{included_characters}字符。已保存识别范围内全部可见原文供受控审阅，本地提取阶段未判错。',
                  total_paragraphs=len(selected), total_characters=content_scope['total_characters'],
                  total_chunks=content_scope['total_chunks'], source_scope_complete=True,
                  semantic_review_complete=False)
        else:
            cover(rid, 'partial', '未识别可靠的适用章节范围；不由普通正文关键词推测缺少该章节。')
    return findings, coverage
