"""Explicit teacher page/cover requirements; no guessed school-wide defaults."""
import hashlib
import json
import re
from pathlib import Path

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
USER_POLICY_PATH = Path(__file__).resolve().parent / 'data' / 'policies' / 'user_abstract_page_requirement.json'


def load_user_page_rules(policy_path=None):
    """Keep user requirements separate from teacher-comment evidence."""
    path = Path(policy_path) if policy_path is not None else USER_POLICY_PATH
    if not path.is_file():
        return []
    raw = path.read_bytes()
    configuration_error = None
    try:
        policy = json.loads(raw)
        if (not isinstance(policy, dict) or policy.get('id') != 'UP001'
                or policy.get('origin') != 'user_confirmed_requirement'
                or policy.get('maximum_physical_pages') != 1
                or policy.get('minimum_word_count') is not None):
            raise ValueError('用户中文摘要规则配置无效')
    except (ValueError, UnicodeError) as error:
        policy = dict(origin='invalid_user_requirement_configuration')
        configuration_error = str(error)
    evidence = dict(policy, configuration_path=str(path),
                    configuration_sha256=hashlib.sha256(raw).hexdigest())
    return [dict(id='UP001', title='中文摘要不超过一页', family='chinese_abstract_page_limit',
                 enabled=True, checker='user_confirmed_page_requirement', teacher_evidence=[],
                 policy='user_confirmed_project_requirement', policy_evidence=[evidence],
                 configuration_error=configuration_error,
                 scope='本项目论文中文摘要；仅以当前源文件绑定的真实PDF分页核查一页上限，不设字数下限。',
                 automatic_error_judgment_enabled=False)]


def load_page_rules(library):
    families = {r['id']: r for r in library.get('rules', [])}
    definitions = (
        ('NP042', '封面答辩日期居中', 'cover_layout', r'居中.{0,20}答辩日期|答辩日期.{0,20}居中'),
        ('NP043', '英文摘要一页要求', 'english_abstract_length', r'一页|1页|一页面'),
    )
    rules = []
    for rid, title, family, pattern in definitions:
        evidence = [e for e in families.get(family, {}).get('teacher_evidence', [])
                    if re.search(pattern, e.get('quote', '').replace('\n', ''))]
        rules.append(dict(id=rid, title=title, family=family, enabled=bool(evidence),
                          checker='explicit_teacher_page_requirement', teacher_evidence=evidence,
                          policy='teacher_specific_not_universal',
                          scope='仅核查所附教师明确要求；封面段落与当前原件绑定的PDF物理页，不猜测缺失标准。',
                          automatic_error_judgment_enabled=False))
    return rules + load_user_page_rules()


def _normalized(text):
    return re.sub(r'\s+', '', text).lower()


def _check_chinese_abstract(paragraphs, rule, source_sha256, layout_result, styles=None):
    """Abstain unless the complete bounded abstract maps to a verified PDF."""
    row = dict(rule_id=rule['id'], title=rule['title'], status='partial',
               policy=rule['policy'], policy_evidence=rule['policy_evidence'])
    findings = []
    try:
        if rule.get('configuration_error'):
            raise ValueError('用户摘要配置需复核：' + rule['configuration_error'])
        if (not layout_result or not source_sha256
                or layout_result.get('source', {}).get('sha256') != source_sha256
                or not layout_result.get('source_unchanged')):
            raise ValueError('没有当前原件的实际分页证据')
        pdf = layout_result.get('pdf', {})
        path = Path(pdf.get('path', ''))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pdf.get('sha256'):
            raise ValueError('PDF文件缺失或哈希不符')
        visible = [p for p in paragraphs if not any(p.get(k) for k in
                   ('in_deleted', 'in_toc', 'in_textbox', 'in_table'))]
        starts = [i for i, p in enumerate(visible) if _normalized(p['text']) in {'摘要', '中文摘要'}]
        if len(starts) != 1:
            raise ValueError('没有唯一的正文中文摘要标题；目录、删除、文本框和表格标题不作为摘要起点')
        heading = visible[starts[0]]
        before = [p for p in visible[:starts[0]] if p['text'].strip()]
        title = before[-1] if before else None
        style_names = {} if styles is None else {
            s.get(W + 'styleId'): (s.find(W + 'name').get(W + 'val', '')
                                  if s.find(W + 'name') is not None else '')
            for s in styles.findall(W + 'style')}
        title_style = '' if title is None else _normalized(style_names.get(title.get('style_id'), title.get('style_id', '')) or '')
        if title is None or not (re.match(r'^(?:论文)?题目\s*[:：]', title['text'].strip())
                                 or title_style in {'title', '标题', '论文题目', '论文标题'}):
            raise ValueError('无法确认摘要前的论文题目范围；摘要页须连同论文题目、摘要标题及关键词核查')
        selected, ended, has_keywords = [], False, False
        for p in visible[starts[0] + 1:]:
            key = _normalized(p['text'])
            if key in {'abstract', '英文摘要', '目录', '致谢', '参考文献'} or p.get('heading_level') is not None:
                ended = True
                break
            if not key:
                continue
            selected.append(p)
            if re.match(r'^关键词\s*[:：]', key):
                ended = True
                has_keywords = True
                break
        if not selected or not ended or not has_keywords:
            raise ValueError('没有建立中文摘要完整范围及关键词结束边界')
        import fitz
        with fitz.open(path) as pages:
            texts = [_normalized(page.get_text()) for page in pages]
            heading_pages = [i + 1 for i, page in enumerate(pages)
                             if any(_normalized(line) == _normalized(heading['text'])
                                    for line in page.get_text().splitlines())]
        if len(heading_pages) != 1:
            raise ValueError('中文摘要标题未唯一匹配PDF独立文本行')
        # Use all paragraphs, including keywords, rather than short endpoint
        # fragments which can silently miss overflow or match repeated text.
        joined = ''.join(texts)
        offsets, offset = [], 0
        for text in texts:
            offsets.append((offset, offset + len(text)))
            offset += len(text)
        heading_offset = offsets[heading_pages[0] - 1][0]
        with fitz.open(path) as pages:
            for line in pages[heading_pages[0] - 1].get_text().splitlines():
                if _normalized(line) == _normalized(heading['text']):
                    break
                heading_offset += len(_normalized(line))
        title_needle = _normalized(title['text'])
        title_matches = [m.start() for m in re.finditer(re.escape(title_needle), joined)
                         if m.start() + len(title_needle) <= heading_offset]
        if not title_matches:
            raise ValueError('论文题目未在摘要标题前完整匹配实际PDF')
        title_offset = title_matches[-1]
        positions = []
        for p in selected:
            needle = _normalized(p['text'])
            matches = [m.start() for m in re.finditer(re.escape(needle), joined)]
            if len(matches) != 1:
                raise ValueError('中文摘要原文未完整唯一匹配实际PDF，无法可靠计算页数')
            positions.append((matches[0], matches[0] + len(needle)))
        if any(right[0] < left[1] for left, right in zip(positions, positions[1:])):
            raise ValueError('中文摘要原文在PDF中的顺序与原件不一致')
        body_first = next(i + 1 for i, (_, end) in enumerate(offsets) if end > positions[0][0])
        last = next(i + 1 for i, (_, end) in enumerate(offsets) if end >= positions[-1][1])
        first = next(i + 1 for i, (_, end) in enumerate(offsets) if end > title_offset)
        if heading_pages[0] > body_first:
            raise ValueError('中文摘要标题在PDF中位于摘要正文之后')
        if hashlib.sha256(path.read_bytes()).hexdigest() != pdf.get('sha256'):
            raise ValueError('核验期间PDF文件发生变化')
        page_evidence = dict(source_sha256=source_sha256, pdf=pdf,
                             first_physical_page=first, last_physical_page=last,
                             matched_paragraph_count=len(selected), includes_abstract_heading=True,
                             includes_thesis_title=True, thesis_title_paragraph_index=title['index'])
        row.update(status='checked', physical_pages=[first, last], source_page_evidence=page_evidence,
                   detail=f'当前原件论文题目、中文摘要标题及全部摘要原文（含关键词）匹配PDF物理页{first}–{last}；只核查一页上限，略少一页不判错，不设字数下限。')
        if last > first:
            p = selected[0]
            findings.append(dict(id='UP001-0001', rule_id='UP001', status='candidate',
                title='中文摘要跨越多页', text=p['text'], paragraph_index=p['index'],
                start=0, end=len(p['text']), location=p.get('location'), teacher_evidence=[],
                policy=rule['policy'], policy_evidence=rule['policy_evidence'],
                reason=f'用户已确认中文摘要不得超过一页；当前原件实际PDF范围为物理页{first}–{last}。',
                suggestion='将中文摘要及关键词排在一页内；不按虚构字数下限扩写，必要研究信息应保留。',
                source_page_evidence=page_evidence))
    except (ValueError, OSError, RuntimeError) as error:
        row['detail'] = '本项未核验：' + str(error) + '；不使用XML分页标记或文字长度猜测页数。'
    return findings, row


def check_page_rules(document, styles, paragraphs, rules, source_sha256, layout_result=None):
    from teacher_format_checks import Properties
    findings, coverage = [], []
    active = {r['id']: r for r in rules if r['id'] in {'NP042', 'NP043', 'UP001'}}
    nodes = list(document.iter(W+'p'))
    properties = Properties(styles)
    for rid, rule in active.items():
        row = dict(rule_id=rid, title=rule['title'], status='partial')
        if not rule.get('enabled'):
            row.update(status='not_applicable', detail='没有明确的同类教师参数要求，本项未启用。')
        elif rid == 'UP001':
            added, row = _check_chinese_abstract(paragraphs, rule, source_sha256, layout_result, styles)
            findings.extend(added)
        elif rid == 'NP042':
            cover = []
            for p in paragraphs:
                if p.get('heading_level') is not None or re.sub(r'\s+', '', p['text']).lower() in {'摘要', '中文摘要', 'abstract', '英文摘要', '目录'}:
                    break
                if not any(p.get(flag) for flag in ('in_table', 'in_textbox', 'in_deleted', 'in_toc')):
                    cover.append(p)
            dates = [p for p in cover if re.search(r'答辩日期|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日', p['text'])]
            unknown = 0
            for p in dates:
                if len(nodes) != len(paragraphs):
                    unknown += 1; continue
                node = nodes[p['index']]
                jc = properties.resolve(node, None, W+'jc', 'val')
                # Tabs, indentation and floating positioning can center text
                # independently of paragraph alignment; refuse those cases.
                if jc is None or '\t' in p['text'] or node.find(W+'pPr/'+W+'ind') is not None:
                    unknown += 1; continue
                if jc['value'] == 'center':
                    continue
                findings.append(dict(id=f'{rid}-{len(findings)+1:04d}', rule_id=rid, status='candidate',
                    title='封面日期段落未采用居中对齐', text=p['text'], paragraph_index=p['index'],
                    start=0, end=len(p['text']), location=p.get('location'), teacher_evidence=rule['teacher_evidence'],
                    reason=f'所附教师要求封面答辩日期居中；当前日期段落有效对齐为{jc["value"]}，来源{jc["source"]}。',
                    suggestion='确认此段为答辩日期后设置居中对齐；不修改正文日期或推断日期值错误。',
                    format_evidence=jc, policy=rule['policy']))
            row.update(status='checked' if dates and not unknown else 'partial',
                       detail=f'核查前置材料中{len(dates)}个明确日期段落；{unknown}个定位或继承证据不足；未找到日期不直接判断缺失。')
        else:
            try:
                if not layout_result or layout_result.get('source', {}).get('sha256') != source_sha256 or not layout_result.get('source_unchanged'):
                    raise ValueError('没有当前原件的实际分页证据')
                pdf = layout_result.get('pdf', {})
                path = Path(pdf.get('path', ''))
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pdf.get('sha256'):
                    raise ValueError('PDF文件缺失或哈希不符')
                selected = []
                inside = False
                for p in paragraphs:
                    key = re.sub(r'\s+', '', p['text']).lower()
                    if key in {'英文摘要', 'abstract'}:
                        inside = True; continue
                    if inside and (p.get('heading_level') is not None or key in {'目录', '摘要', '中文摘要', '致谢', '参考文献'}):
                        break
                    if inside and p['text'].strip() and not any(p.get(k) for k in ('in_deleted','in_toc','in_textbox')):
                        selected.append(p)
                        if re.match(r'^\s*(?:key\s*words|keywords)\s*[:：]', p['text'], re.I):
                            break
                if not selected:
                    raise ValueError('没有建立英文摘要真实范围')
                import fitz
                with fitz.open(path) as pages:
                    texts = [re.sub(r'\s+', '', page.get_text()).lower() for page in pages]
                endpoints = [re.sub(r'\s+', '', selected[0]['text']).lower()[:32],
                             re.sub(r'\s+', '', selected[-1]['text']).lower()[-32:]]
                matches = [[i+1 for i, text in enumerate(texts) if endpoint and endpoint in text] for endpoint in endpoints]
                if any(len(m) != 1 for m in matches) or matches[1][0] < matches[0][0]:
                    raise ValueError('英文摘要首末原文未唯一匹配实际PDF页，无法可靠计算页数')
                first, last = matches[0][0], matches[1][0]
                row.update(status='checked', detail=f'当前原件英文摘要首末原文唯一匹配PDF物理页{first}–{last}；核查教师一页要求。', physical_pages=[first,last])
                if last > first:
                    p = selected[0]
                    findings.append(dict(id=rid+'-0001', rule_id=rid, status='candidate', title='英文摘要跨越多页',
                        text=p['text'], paragraph_index=p['index'], start=0,end=len(p['text']),location=p.get('location'),
                        teacher_evidence=rule['teacher_evidence'], policy=rule['policy'],
                        reason=f'教师明确要求英文摘要在一页内；当前原件实际PDF范围为物理页{first}–{last}。',
                        suggestion='按此教师要求调整英文摘要篇幅及排版；不据此删减必要研究信息。',
                        source_page_evidence={'source_sha256':source_sha256,'pdf':pdf,'first_physical_page':first,'last_physical_page':last}))
            except (ValueError, OSError, RuntimeError) as error:
                row['detail'] = '本项未判定：'+str(error)+'；不使用XML分页标记或文字长度猜测页数。'
        coverage.append(row)
    return findings, coverage
