"""Scope-bound three-line table checks over effective Word border properties.

This checker supports unmerged rectangular native tables with ordinary table
styles and row/cell overrides.  It does not guess conditional-style conflicts,
merged geometry, theme colours, or the rendered appearance.  A proved visible
vertical line is a structural revision suggestion, not a human-confirmed error.

Word's `none` cell border falls back to style/row/table borders, whereas `nil`
suppresses a conflicting cell edge. See Microsoft's implementation notes:
https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/7c791c5d-44c3-4fe8-abdd-8f136761bb93
"""
from __future__ import annotations

import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
CHECKER = 'native_table_borders_v2'
CAPTION = re.compile(r'^\s*表\s*(?:\d+\s*[-－‐‑–—]\s*)?\d+\s*\S+')
HEADER = re.compile(r'^(?:字段(?:名|名称|含义)?|数据类型|序号|指标|参数|样本|类别|类型|方法|模型|评分项目|测试项目|实验组|对照组|项目名称|日期|时间|名称|单位)$')
OUTSIDE = {'目录','致谢','谢辞','参考文献','独创性声明','原创性声明','学术诚信声明','摘要','中文摘要','英文摘要','abstract','封面／前置材料','附录'}
SOURCES = [
    'https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/7c791c5d-44c3-4fe8-abdd-8f136761bb93',
    'https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.tableborders?view=openxml-3.0.1',
]
LINE_VALUES = {'single','thick','double','dotted','dashed','dotDash','dotDotDash',
               'triple','thinThickSmallGap','thickThinSmallGap','thinThickThinSmallGap',
               'thinThickMediumGap','thickThinMediumGap','thinThickThinMediumGap',
               'thinThickLargeGap','thickThinLargeGap','thinThickThinLargeGap',
               'wave','doubleWave','dashSmallGap','dashDotStroked','threeDEmboss',
               'threeDEngrave','outset','inset'}


def _text(node):
    result = []
    def visit(n):
        if n.tag in {W+'del', W+'moveFrom', W+'txbxContent'}:
            return
        if n.tag == W+'t':
            result.append(n.text or '')
        for child in n:
            visit(child)
    visit(node)
    return ''.join(result).strip()


def _edges(root, source):
    if root is None:
        return {}
    result = {}
    # Each hierarchy layer overwrites the same physical side. Do not allow a
    # start/end from an older style to survive a newer direct left/right override.
    ordered = [n for n in root if n.tag not in {W+'start', W+'end'}] + [n for n in root if n.tag in {W+'start', W+'end'}]
    for n in ordered:
        name = n.tag[len(W):]
        physical = {'start': 'left', 'end': 'right'}.get(name, name)
        result[physical] = {'value': n.get(W+'val'), 'size': n.get(W+'sz'),
                            'color': n.get(W+'color'), 'theme_color': n.get(W+'themeColor'),
                            'source': source, 'xml_edge': name}
    return result


def _border(value):
    """Interpret explicit border, with provenance; None denotes implicit none."""
    value = dict(value or {'value': 'none', 'source': 'implicit_no_border'})
    kind = value.get('value')
    if kind in {'nil', 'none'}:
        return dict(value, visible=False)
    if not kind:
        return dict(value, visible=None, unresolved='border_value_missing')
    if kind not in LINE_VALUES:
        return dict(value, visible=None, unresolved='border_style_unsupported')
    # OOXML border width is in eighths of a point. Missing width is not a
    # fabricated zero; ordinary border values still state a non-empty line.
    if value.get('size') is not None:
        try:
            size = float(value['size'])
        except (ValueError, TypeError):
            return dict(value, visible=None, unresolved='border_width_invalid')
        if size <= 0:
            return dict(value, visible=None, unresolved='border_width_invalid')
    if (value.get('color') or '').upper() == 'FFFFFF' or value.get('theme_color'):
        return dict(value, visible=None, unresolved='colour_requires_rendering')
    return dict(value, visible=True)


def _style_chain(table, styles):
    registry = {s.get(W+'styleId'): s for s in styles.findall(W+'style')} if styles is not None else {}
    ref = table.find(W+'tblPr/'+W+'tblStyle')
    sid = ref.get(W+'val') if ref is not None else next((k for k, s in registry.items()
        if s.get(W+'type') == 'table' and s.get(W+'default') in {'1','true','on'}), None)
    result, seen, warnings = [], set(), []
    while sid:
        if sid in seen:
            warnings.append('table_style_cycle:'+sid); break
        seen.add(sid)
        node = registry.get(sid)
        if node is None:
            warnings.append('table_style_missing:'+sid); break
        result.append((sid, node))
        base = node.find(W+'basedOn')
        sid = base.get(W+'val') if base is not None else None
    return list(reversed(result)), warnings


def _physical_edge(edges, side):
    # Logical start/end take precedence over left/right in left-to-right tables.
    # Bidirectional tables are explicitly unsupported below.
    if side in {'left','right'}:
        return edges.get('start' if side == 'left' else 'end') or edges.get(side)
    return edges.get(side)


def _table_side(edges, side, row, col, nrows, ncols):
    edge = side
    if side == 'left' and col > 0 or side == 'right' and col < ncols-1:
        edge = 'insideV'
    elif side == 'top' and row > 0 or side == 'bottom' and row < nrows-1:
        edge = 'insideH'
    return _physical_edge(edges, edge)


def resolve_table_borders(table, styles):
    """Return an auditable edge layout, or explicit unsupported reasons."""
    rows = [r.findall(W+'tc') for r in table.findall(W+'tr')]
    reasons = []
    if not rows or not rows[0] or len({len(r) for r in rows}) != 1:
        reasons.append('非矩形或空表格，需要按合并单元格网格解析')
    if sum(map(len, rows)) > 20000:
        reasons.append('单元格数量超过本地结构核查上限')
    if table.find('.//'+W+'gridSpan') is not None or table.find('.//'+W+'vMerge') is not None or table.find('.//'+W+'hMerge') is not None:
        reasons.append('表格包含合并单元格，当前边框核查未解析合并后的有效边界')
    if table.find('.//'+W+'gridBefore') is not None or table.find('.//'+W+'gridAfter') is not None:
        reasons.append('表格行存在省略网格，当前有效边界未解析')
    if any(n is not table for n in table.iter(W+'tbl')):
        reasons.append('包含嵌套表格，外层与内层边框不能合并判断')
    if table.find(W+'tblPr/'+W+'bidiVisual') is not None:
        reasons.append('从右向左表格的逻辑边框位置尚未解析')
    chain, style_warnings = _style_chain(table, styles)
    reasons.extend(style_warnings)
    table_edges, style_cell_edges, spacing = {}, {}, None
    for sid, node in chain:
        table_edges.update(_edges(node.find(W+'tblPr/'+W+'tblBorders'), 'table_style:'+sid))
        style_cell_edges.update(_edges(node.find(W+'tcPr/'+W+'tcBorders'), 'table_cell_style:'+sid))
        for condition in node.findall(W+'tblStylePr'):
            if condition.get(W+'type') == 'wholeTable':
                table_edges.update(_edges(condition.find(W+'tblPr/'+W+'tblBorders'), 'whole_table_style:'+sid))
                style_cell_edges.update(_edges(condition.find(W+'tcPr/'+W+'tcBorders'), 'whole_table_cell_style:'+sid))
            elif condition.find('.//'+W+'tblBorders') is not None or condition.find('.//'+W+'tcBorders') is not None:
                reasons.append('样式'+sid+'含区域条件边框'+str(condition.get(W+'type'))+'；需核验tblLook/cnfStyle优先级')
        candidate = node.find(W+'tblPr/'+W+'tblCellSpacing')
        if candidate is not None:
            spacing = candidate
    table_edges.update(_edges(table.find(W+'tblPr/'+W+'tblBorders'), 'table_direct'))
    direct_spacing = table.find(W+'tblPr/'+W+'tblCellSpacing')
    if direct_spacing is not None:
        spacing = direct_spacing
    spacings = ([spacing] if spacing is not None else []) + list(table.findall(W+'tr/'+W+'tblPrEx/'+W+'tblCellSpacing'))
    for s in spacings:
        if s.get(W+'type') not in {None, 'dxa'} or s.get(W+'w') not in {'0', None}:
            reasons.append('非零或未知单元格间距，分离边框模型尚未解析')
    if style_cell_edges.get('insideV') or style_cell_edges.get('insideH'):
        reasons.append('单元格样式包含内部边框，需要解析区域边界')
    if reasons:
        return {'status': 'unresolved', 'unresolved_reasons': list(dict.fromkeys(reasons)),
                'table_edges': table_edges, 'style_chain': [sid for sid, _ in chain],
                'references': SOURCES}
    nrows, ncols = len(rows), len(rows[0])
    cells = []
    for ri, (row, tr) in enumerate(zip(rows, table.findall(W+'tr'))):
        row_edges = _edges(tr.find(W+'tblPrEx/'+W+'tblBorders'), 'row_exception:'+str(ri))
        inherited = dict(table_edges); inherited.update(row_edges)
        for ci, cell in enumerate(row):
            direct = _edges(cell.find(W+'tcPr/'+W+'tcBorders'), f'cell_direct:{ri}:{ci}')
            if direct.get('insideH') or direct.get('insideV'):
                reasons.append('单元格含内部边框，当前区域优先级未解析')
            sides = {}
            for side in ('top','bottom','left','right'):
                choice = _physical_edge(direct, side)
                # In Word, cell `none` follows the inherited hierarchy. A
                # table-level `none` is an explicit style override instead.
                if choice is None or choice.get('value') == 'none':
                    choice = _physical_edge(style_cell_edges, side)
                if choice is None or choice.get('value') == 'none':
                    choice = _table_side(inherited, side, ri, ci, nrows, ncols)
                sides[side] = _border(choice)
            cells.append({'row': ri, 'column': ci, 'borders': sides})
    lookup = {(c['row'], c['column']): c['borders'] for c in cells}
    segments = []
    def segment(axis, boundary, position, first, second=None):
        pairs = [first] + ([second] if second is not None else [])
        if any(b.get('value') == 'nil' for b in pairs):
            visible = False  # nil suppresses the opposing edge with zero spacing.
        elif any(b['visible'] is True for b in pairs):
            visible = True
        elif any(b['visible'] is None for b in pairs):
            visible = None
        else:
            visible = False
        segments.append({'axis': axis, 'boundary': boundary, 'position': position,
                         'visible': visible, 'borders': pairs})
    for ri in range(nrows):
        for boundary in range(ncols+1):
            if boundary == 0:
                segment('vertical', boundary, ri, lookup[ri,0]['left'])
            elif boundary == ncols:
                segment('vertical', boundary, ri, lookup[ri,ncols-1]['right'])
            else:
                segment('vertical', boundary, ri, lookup[ri,boundary-1]['right'], lookup[ri,boundary]['left'])
    for boundary in range(nrows+1):
        for ci in range(ncols):
            if boundary == 0:
                segment('horizontal', boundary, ci, lookup[0,ci]['top'])
            elif boundary == nrows:
                segment('horizontal', boundary, ci, lookup[nrows-1,ci]['bottom'])
            else:
                segment('horizontal', boundary, ci, lookup[boundary-1,ci]['bottom'], lookup[boundary,ci]['top'])
    reasons.extend(s['borders'][0].get('unresolved', '有效边框的可见性未确定') for s in segments if s['visible'] is None)
    return {'status': 'resolved' if not reasons else 'unresolved',
            'row_count': nrows, 'column_count': ncols, 'style_chain': [sid for sid, _ in chain],
            'table_edges': table_edges, 'segments': segments, 'unresolved_reasons': list(dict.fromkeys(reasons)),
            'references': SOURCES}


def _scope(table, parents):
    rows = [r.findall(W+'tc') for r in table.findall(W+'tr')]
    shape = [len(r) for r in rows]
    first_row = [_text(c) for c in rows[0]] if rows else []
    parent = parents.get(id(table))
    captions = []
    if parent is not None:
        siblings = list(parent); index = siblings.index(table)
        # Only immediate non-empty neighboring paragraphs; no walking past
        # another table or a prose paragraph to steal an unrelated caption.
        for step in (-1, 1):
            if step == 1 and captions:
                break  # A bound preceding caption has priority.
            j, empty = index+step, 0
            while 0 <= j < len(siblings) and empty < 3:
                node = siblings[j]
                if node.tag != W+'p': break
                text = _text(node)
                if text:
                    if CAPTION.match(text):
                        caption_for_next_table = False
                        if step == 1:
                            # A caption between two consecutive tables belongs
                            # plausibly to the next table; never steal it to
                            # establish the first table's data-table scope.
                            probe = j+1
                            while probe < len(siblings) and siblings[probe].tag == W+'p' and not _text(siblings[probe]):
                                probe += 1
                            caption_for_next_table = probe < len(siblings) and siblings[probe].tag == W+'tbl'
                        if not caption_for_next_table:
                            captions.append(text)
                    break
                empty += 1; j += step
    evidence = {'row_count': len(rows), 'column_counts': shape, 'header_text': first_row,
                'captions': captions}
    if shape == [1]:
        return dict(evidence, applicable=False, category='content_box',
                    reason='该原生对象只有一个单元格，是文本/提示词展示框，未形成数据或比较表的行列结构；三线表规则不套用。')
    if len(rows) < 2 or not shape or min(shape) < 2:
        return dict(evidence, applicable=False, category='layout_or_unestablished',
                    reason='未形成至少两行两列的资料比较结构；此对象的三线表适用范围未成立。')
    if captions:
        return dict(evidence, applicable=True, category='captioned_data_or_comparison',
                    reason='相邻编号表题与至少两行两列的资料/比较内容可靠绑定。')
    if len({len(r) for r in rows}) == 1 and any(HEADER.fullmatch(re.sub(r'\s+', '', c)) for c in first_row) and sum(bool(c) for c in first_row) >= 2:
        return dict(evidence, applicable=True, category='structured_data_table',
                    reason='矩形多行多列表的表头包含字段、指标或资料类型等明确列名称。')
    return dict(evidence, applicable=False, category='scope_unresolved',
                reason='有行列结构，但没有可靠题注或资料表头支持；未将排版网格擅自按三线表处理。')


def check_native_tables(document, styles, paragraphs, rule, three_line_tables=False):
    """Return findings plus rich coverage, without network/Word/source writes."""
    if type(three_line_tables) is not bool:
        raise ValueError('three_line_tables 必须为布尔值；未明确采用规则时不判断表格错误。')
    nodes = list(document.iter(W+'p'))
    by_node = {id(n): p for n, p in zip(nodes, paragraphs)}
    parents = {id(c): p for p in document.iter() for c in p}
    findings, excluded, checked = [], [], []
    for index, table in enumerate(document.iter(W+'tbl')):
        table_paragraphs = [by_node[id(p)] for p in table.iter(W+'p') if id(p) in by_node]
        first = next((p for p in table_paragraphs if p.get('text', '').strip()), table_paragraphs[0] if table_paragraphs else None)
        if first is None: continue
        base = {'table_index': index, 'paragraph_index': first['index'], 'location': first.get('location'),
                'text': first.get('text', '')}
        scope = _scope(table, parents)
        section = re.sub(r'\s+', '', (first.get('location') or {}).get('section', '')).lower().strip('：:')
        ancestor = parents.get(id(table)); inside_excluded = False
        while ancestor is not None:
            if ancestor.tag in {W+'del', W+'moveFrom', W+'txbxContent', W+'tbl'}: inside_excluded = True; break
            ancestor = parents.get(id(ancestor))
        if section in OUTSIDE or inside_excluded:
            scope.update(applicable=False, category='outside_scope', reason='对象位于封面、致谢等正文规则排除范围，或嵌套/删除对象中。')
        if not scope['applicable']:
            excluded.append(dict(base, scope_evidence=scope, reason=scope['reason']))
            continue
        borders = resolve_table_borders(table, styles)
        if not three_line_tables:
            # Object identity and border facts do not establish that this new
            # paper adopts a three-line-table format. Preserve them for audit,
            # but create neither revisions nor one manual task per table.
            checked.append(dict(base, scope_evidence=scope, format_evidence=borders,
                                decision='not_judged_policy',
                                reason='未明确采用三线表要求；仅保留表格范围与有效边框属性，不判断错误或合格。'))
            continue
        reasons, decision = [], None
        if borders['status'] == 'resolved':
            visible_vertical = [s for s in borders['segments'] if s['axis'] == 'vertical' and s['visible']]
            horizontal = {b: [s for s in borders['segments'] if s['axis'] == 'horizontal' and s['boundary'] == b]
                          for b in range(borders['row_count']+1)}
            extra_lines = [b for b, ss in horizontal.items() if b not in {0,1,borders['row_count']} and any(s['visible'] for s in ss)]
            missing_lines = [b for b in {0,1,borders['row_count']} if not all(s['visible'] for s in horizontal[b])]
            if visible_vertical:
                reasons.append(f'有效边框中存在{len(visible_vertical)}段纵向或侧边线')
            if extra_lines:
                reasons.append('除表顶、第一行下方和表底以外还有横线（行边界'+','.join(map(str,extra_lines))+'）')
            # A vertical line is an unambiguous counterexample to three-line
            # structure. Do not label multi-row header separators as missing
            # without resolving their semantics, however.
            if visible_vertical:
                decision = 'suggested_revision'
            elif extra_lines:
                reasons.append('需核对这些横线是否属于多层表头，未仅凭附加横线自动判错')
            elif not missing_lines:
                decision = 'not_issue'
                reasons.append('有效结构只有表顶、第一行表头分隔、表底三条完整横线，未见纵向或侧边线')
            else:
                reasons.append('无明显纵向线，但三条完整横线或表头分隔位置尚未成立；需核对表头语义及版面')
        else:
            reasons.extend(borders['unresolved_reasons'])
        reason = '；'.join(reasons)+'。'
        checked.append(dict(base, scope_evidence=scope, decision=decision or 'needs_review', reason=reason))
        title = '数据表边框与三线表结构不符' if decision == 'suggested_revision' else '数据表边框符合三线表结构' if decision == 'not_issue' else '数据表有效边框尚不能完整解析'
        suggestion = '按本教师的三线表要求，调整已确认的纵向/侧边线；表头横线按实际层级核对，保留表顶、表头分隔及表底横线。本项只判断边框结构，不判定表格数据或其他排版合格。' if decision == 'suggested_revision' else '' if decision == 'not_issue' else '核对该表实际边框或表头结构；具体未完成原因已列出，不根据未知属性判断合格或错误。'
        finding = dict(base, id=f'NP022-{len(findings)+1:04d}', rule_id='NP022', title=title,
                       status='candidate' if decision else 'needs_review', start=None, end=None,
                       reason=reason, suggestion=suggestion, teacher_evidence=rule['teacher_evidence'],
                       scope_evidence=scope, format_evidence=borders)
        if decision:
            finding['local_review'] = {'decision': decision, 'validated_structure': True,
                                      'checker': CHECKER, 'reason': reason, 'suggestion': suggestion,
                                      'proof_scope': '仅原生数据/比较表的有效边框结构'}
        findings.append(finding)
    counts = {key: sum(r['decision'] == key for r in checked) for key in ('suggested_revision','not_issue','needs_review','not_judged_policy')}
    if not three_line_tables:
        return [], {'rule_id':'NP022','title':rule['title'],'status':'not_applicable_policy',
                    'detail':f'本稿未明确采用三线表要求，因此不按边框格式判断错误或合格。保留{len(checked)}张资料表的题注、结构与边框属性；排除内容框/非资料表或范围未成立对象{len(excluded)}张。原教师反馈来自其他历史样本，不能直接推广为本稿规范。',
                    'requires_policy':'three_line_tables','policy_not_applicable':True,
                    'three_line_tables':False,'checked_count':0,'observed_count':len(checked),
                    'excluded_count':len(excluded),'counts':counts,'checked_objects':checked,
                    'excluded_objects':excluded,'checker':CHECKER,'references':SOURCES}
    coverage = {'rule_id': 'NP022', 'title': rule['title'],
                'status': 'partial' if counts['needs_review'] else 'checked' if checked else 'not_applicable',
                'detail': f'可靠数据/比较表{len(checked)}张：结构建议修改{counts["suggested_revision"]}张，边框结构符合{counts["not_issue"]}张，具体原因待核验{counts["needs_review"]}张；排除内容框/非资料表或范围未成立对象{len(excluded)}张。',
                'checked_count': len(checked), 'excluded_count': len(excluded), 'counts': counts,
                'excluded_objects': excluded, 'checked_objects': checked,
                'checker': CHECKER, 'references': SOURCES}
    coverage.update(requires_policy='three_line_tables',policy_not_applicable=False,three_line_tables=True)
    return findings, coverage
