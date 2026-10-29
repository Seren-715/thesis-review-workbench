"""Additional teacher-bound methods with complete, source-ordered read scopes.

This module does not make network/Word/SVN requests or approve teacher rules.
The semantic queue must read every saved chunk before issuing a content
decision. Missing words, source language, and unspecified formatting thresholds
are never converted into automatic paper errors.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from content_review_context import CHUNK_CHARACTERS

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
SCOPE_VERSION = 'extended_teacher_complete_scope_v2_read_boundaries'
OUTSIDE = {'目录', '致谢', '谢辞', '参考文献', 'references', 'bibliography', '附录', '独创性声明', '原创性声明', '学术诚信声明'}
SPECIAL = OUTSIDE | {'摘要', '中文摘要', '英文摘要', 'abstract', '结论', '结束语', '总结与展望', '关键词', 'keywords'}
CAPTION = re.compile(r'^\s*([图表])\s*\d+\s*[-－‐‑–—]\s*\d+\s*\S')
CITATION = re.compile(r'\[\s*\d+(?:\s*[,，、\-–—]\s*\d+)*\s*\]')

# Each dimension refers to the bound feedback rather than an invented universal
# norm. The new five categories stay disabled until source evidence exists.
DEFINITIONS = (
    ('NP027', '英文参考文献及条目说明', 'references', 'bibliography',
     ('依据教师原话核对英文参考文献要求，文献语言不能仅从作者姓名判断',
      '核对参考文献条目可提取内容；文献真实性和外部来源不由本地文本证明')),
    ('NP028', '论述与论文实现说明一致性', 'implementation_claims', 'body',
     ('对照论文的目标、设计、实现和测试，核查功能或性能主张的文内支持',
      '区分未说明、文内矛盾和实际未实现；不得从缺少关键词推断实际未实现')),
    ('NP029', '架构说明与本系统的对应关系', 'architecture_fidelity', 'design',
     ('结合本系统角色、模块、部署及交互说明，核查架构叙述是否针对本项目',
      '通用B/S架构不当然错误；实际图像未审阅时不对图中节点或连线下结论')),
    ('NP030', '引用内容与所述观点的对应关系', 'citation_validity', 'citations',
     ('核查正文引文所支持的具体观点与参考文献题名和条目的文内对应',
      '题名不等于文献全文；不能据题名断言外部文献确实支持或不支持某结论')),
    ('NP031', '引用标记与观点的位置关系', 'citation_placement', 'citations',
     ('按教师原话核查引用是否附于所述观点而非仅附于作者或系统名字',
      '作者引导式引用可能合理；结合完整句段，禁止看到姓名后编号即判错')),
    ('NP032', '引用来源首次说明位置', 'citation_leading_position', 'citations',
     ('结合首次介绍来源与后续说明，核查教师要求的源头引用位置',
      '不要求所有引用一律置于段首；区分前文已有来源说明与确实缺少来源标记')),
    ('NP033', '原始资料来源说明', 'source_citation_missing', 'body',
     ('按教师原话核查非原创数据、图表、方法及原始资料的来源说明',
      '自主设计及原创结果不强制引用；文内缺乏说明与没有真实来源必须区分')),
    ('NP034', '数据图坐标与可视化解释', 'chart_axes', 'figures',
     ('按教师原话核查数据图横纵轴含义及图示结果的正文解释',
      '用例图、架构图、界面图不强制设置坐标轴；未审阅图像不判断图内轴标不存在')),
    ('NP035', '关键词与论文实际任务的对应', 'keyword_specificity', 'keywords',
     ('结合全部摘要、课题背景及结论，评估关键词是否准确表述本论文任务或技术',
      '不套用统一词数或抽象程度阈值；领域常用词不因简短或常见即判错')),
    ('NP036', '建模效果及结果解释', 'model_effect_explanation', 'results',
     ('按教师原话核查实际建模效果、指标与结果解释的文内支持',
      '不虚构指标或实验数字；系统设计型论文未使用建模时不强加建模实验')),
    ('NP037', '本项目提示词设计说明', 'prompt_specification', 'prompts',
     ('按教师原话核查最终提示词及任务之间提示词差异的实际说明',
      '提示词可位于代码、表格或附录；不得要求未使用大模型提示词的论文新增提示词')),
    ('NP038', '预测对象与目标定义', 'prediction_target', 'prediction',
     ('结合摘要、数据和方法，核查预测对象、目标变量及适用对象的实际定义',
      '非预测型论文不强加预测对象；预测变量与受益用户分别判断')),
    ('NP039', '系统模块及职责说明', 'module_inventory', 'design',
     ('结合总体设计、详细设计和实现，核查系统模块划分及各模块职责',
      '不强制固定模块数；研究型非系统论文不要求照搬功能模块清单')),
    ('NP040', '数据分析过程及结果说明', 'data_analysis_explanation', 'results',
     ('按教师原话核查数据分析操作、结果及其意义是否有可核对说明',
      '区分数据预处理与结果分析，不虚构未提供的实验或数据')),
)

STRUCTURAL_DEFINITIONS = (
    ('NP041', '数据表题名完整性', 'table_caption_name',
     '仅正文中可靠识别的原生数据表和附近题注；表格排版框、封面表格和图片表不套用。'),
)


def _key(text):
    return re.sub(r'\s+', '', re.sub(r'^\s*(?:第[一二三四五六七八九十百\d]+章|\d+(?:\.\d+)*[.、\s]*)\s*', '', text)).strip('：:').lower()


def _visible(p):
    return not any(p.get(flag) for flag in ('in_deleted', 'in_toc', 'in_textbox'))


def _frames(paragraphs):
    """Read boundaries include nominal numbered titles, never compliance proof.

    Mixed-style papers commonly leave chapter titles as ordinary paragraphs.
    An English-abstract/contents stack must not absorb every following chapter.
    Only short nominal headings are used; chapter-description prose, table cells
    and post-body numbered lists cannot reopen the body.
    """
    from paper_locations import inferred_heading
    stack, frames = [], []
    for p in paragraphs:
        if not _visible(p):
            continue
        text = str(p.get('text', ''))
        key = _key(text)
        level = p.get('heading_level')
        special = key in SPECIAL and not p.get('in_table')
        outside = {k for _, _, k in stack} & (OUTSIDE - {'目录', '独创性声明', '原创性声明', '学术诚信声明'})
        reading_title = re.sub(r'^(\d+(?:\.\d+)*)(?:[.．、])\s+', r'\1 ', text.strip())
        inferred = (level is None and not special and not p.get('in_table')
                    and not outside and inferred_heading(reading_title))
        heading = not p.get('in_table') and (type(level) is int or special or inferred)
        if heading:
            numbered = re.match(r'^\s*(\d+(?:\.\d+)*)(?:[.．、])?\s+', text)
            effective = (level if type(level) is int else
                         len(numbered.group(1).split('.')) if inferred and numbered else 1)
            # Even a styled 1.1 may be the first real body title when the first
            # chapter title was absent. Keep the reading range, with an honest
            # boundary marker, instead of inheriting an abstract/contents parent.
            if not special and (inferred or numbered or re.match(r'^\s*第.+章', text)):
                if any(k in {'目录', '英文摘要', 'abstract', '独创性声明', '原创性声明', '学术诚信声明'}
                       for _, _, k in stack):
                    stack = []
            stack = [(n, h, k) for n, h, k in stack if n < effective]
            stack.append((effective, p, key))
        frames.append((p, tuple((h['index'], k) for _, h, k in stack), heading))
    return frames


def _bound_evidence(source, family):
    evidence = source.get('teacher_evidence', [])
    if family == 'references':
        # A pasted bibliography entry is not itself an English-count norm.
        return [e for e in evidence if re.search(r'(?:英文|外文).{0,12}(?:文献|参考)|(?:文献|参考).{0,12}(?:英文|外文)', e.get('quote', ''))]
    return evidence


def load_extended_rules(library):
    families = {r['id']: r for r in library.get('rules', [])}
    result = []
    for rid, title, family, scope, dimensions in DEFINITIONS:
        source = families.get(family, {})
        evidence = deepcopy(_bound_evidence(source, family))
        result.append(dict(id=rid, title=title, family=family, checker='complete_section_semantics',
                           scope='按教师原话审阅全部适用原文及表格文字；实际章节标题确定范围；未识别对应章节时扩大到可见正文，原文不得截断为首段。',
                           scope_selector=scope, check_dimensions=list(dimensions), teacher_evidence=evidence,
                           enabled=bool(evidence), policy='teacher_specific_not_universal',
                           recurrence_status=source.get('recurrence_status', 'not_evaluated'),
                           supporting_students=sorted({e['student_name'] for e in evidence if e.get('student_name')}),
                           automatic_error_judgment_enabled=False))
    for rid, title, family, scope in STRUCTURAL_DEFINITIONS:
        source = families.get(family, {})
        evidence = deepcopy(source.get('teacher_evidence', []))
        result.append(dict(id=rid, title=title, family=family, checker='native_data_table_caption_name', scope=scope,
                           teacher_evidence=evidence, enabled=bool(evidence), policy='teacher_specific_not_universal',
                           recurrence_status=source.get('recurrence_status', 'not_evaluated'),
                           supporting_students=sorted({e['student_name'] for e in evidence if e.get('student_name')}),
                           automatic_error_judgment_enabled=False))
    return result


_PATTERNS = {
    'design': re.compile(r'设计|实现|架构|模块|部署|需求'),
    'figures': re.compile(r'实验|结果|数据|分析|评估|评价|测试|预测|实现'),
    'results': re.compile(r'数据|分析|模型|实验|结果|评估|评价|测试|结论|总结|摘要'),
    'prompts': re.compile(r'提示词|提示工程|大模型|语言模型|系统设计|实现|附录'),
    'prediction': re.compile(r'摘要|预测|数据|模型|方法|结论|结果'),
    'keywords': re.compile(r'^摘要$|^中文摘要$|绪论|引言|研究背景|结论|总结|关键词'),
}


def collect_extended_scope(paragraphs, selector, family, source_sha256):
    if selector not in {'bibliography', 'body', 'citations', *_PATTERNS}:
        raise ValueError('Unsupported extended teacher scope')
    frames = _frames(paragraphs)
    inferred_read_headings = [p['index'] for p, _, h in frames
                             if h and p.get('heading_level') is None and _key(p['text']) not in SPECIAL]
    body = [(p, path, h) for p, path, h in frames if path and not any(k in OUTSIDE for _, k in path)
            and not any(k in {'英文摘要', 'abstract'} for _, k in path)]
    appendix = [(p, path, h) for p, path, h in frames if any(k == '附录' for _, k in path)]
    bibliography = [(p, path, h) for p, path, h in frames if any(k in {'参考文献', 'references', 'bibliography'} for _, k in path)]
    fallback = False
    body_boundary_unresolved = False
    if not body and selector != 'bibliography':
        # An unstyled thesis is still readable. The model receives all visible
        # text, explicitly without a claimed chapter boundary, rather than a
        # single sentence or a request for the user to read the available text.
        from paper_locations import declaration_template
        body = [(p, path, h) for p, path, h in frames if p['text'].strip()
                and not any(k in OUTSIDE for _, k in path) and not declaration_template(p['text'])]
        body_boundary_unresolved = bool(body)
        fallback = bool(body)
    if selector == 'bibliography':
        chosen = bibliography
    elif selector == 'body':
        chosen = body
    elif selector == 'citations':
        # Numeric citation paragraphs, their full adjacent sentences and every
        # bibliography entry; no preview truncation and no external-literature
        # support claim. All citations remain source-ordered in a single scope.
        chosen_ids = {p['index'] for p, _, h in body if not h and CITATION.search(p['text'])}
        neighbors = set(chosen_ids)
        for pos, (p, path, _) in enumerate(body):
            if p['index'] not in chosen_ids:
                continue
            for j in (pos - 1, pos + 1):
                if 0 <= j < len(body) and body[j][1] == path:
                    neighbors.add(body[j][0]['index'])
        chosen = [row for row in body if row[0]['index'] in neighbors] + bibliography if chosen_ids else []
    else:
        regex = _PATTERNS[selector]
        population = body + appendix if selector == 'prompts' else body
        chosen = [row for row in population if any(regex.search(k) for _, k in row[1])]
        if selector == 'keywords':
            # Abstract keywords may be an ordinary paragraph following the
            # English abstract; include their exact line regardless of stack.
            chosen += [row for row in frames if re.match(r'^\s*(?:关\s*键\s*词|keywords?)\s*[:：]', row[0]['text'], re.I)]
        if not any(p['text'].strip() and not h for p, _, h in chosen) and body:
            chosen = body
            fallback = True
    by_index = {p['index']: (p, path, h) for p, path, h in chosen if p['text'].strip()}
    chosen = [by_index[i] for i in sorted(by_index)]
    selected = [p for p, _, _ in chosen]
    chunks = []
    for p, _, heading in chosen:
        text = p['text']
        for start in range(0, len(text), CHUNK_CHARACTERS):
            end = min(len(text), start + CHUNK_CHARACTERS)
            chunks.append(dict(id=f'{family}-p{p["index"]}-{start}-{end}', paragraph_index=p['index'], start=start,
                               end=end, text=text[start:end], location=p.get('location'),
                               role='heading' if heading else 'table_cell' if p.get('in_table') else 'body'))
    headings = [dict(paragraph_index=p['index'], text=p['text'], heading_level=p.get('heading_level'),
                     location=p.get('location'),
                     evidence_role=('read_boundary_only' if p['index'] in inferred_read_headings else 'explicit_heading'))
                for p, _, h in chosen if h]
    scope = dict(version=SCOPE_VERSION, scope_kind=family, selector=selector, source_sha256=source_sha256,
                 scope_sha256=hashlib.sha256('\n'.join(f'{p["index"]}:{p["text"]}' for p in selected).encode('utf-8')).hexdigest(),
                 paragraph_indices=[p['index'] for p in selected], total_paragraphs=len(selected),
                 total_characters=sum(len(p['text']) for p in selected), chunks=chunks, total_chunks=len(chunks),
                 complete=True, heading_indices=[h['paragraph_index'] for h in headings], headings=headings,
                 scope_fallback='complete_visible_document_body_boundary_unresolved' if body_boundary_unresolved else 'complete_visible_body' if fallback else None,
                 body_boundary_unresolved=body_boundary_unresolved,
                 inferred_read_heading_indices=inferred_read_headings,
                 heading_compliance_evidence=False,
                 coverage_basis='all_selected_visible_text_without_truncation',
                 external_sources_read=False, unread_object_types=['image_text', 'embedded_ole', 'headers_footers_footnotes'])
    return selected, scope


def check_extended_rules(document, paragraphs, rules, source_sha256):
    findings, coverage = [], []
    ids = {row[0] for row in DEFINITIONS}
    for rule in rules:
        rid = rule.get('id')
        if rid not in ids and rid != 'NP041':
            continue
        if not rule.get('enabled'):
            coverage.append(dict(rule_id=rid, title=rule['title'], status='not_applicable',
                                 detail='没有明确支持本项要求的已核验教师原话，未启用；不对论文新增强制要求。'))
            continue
        if rid == 'NP041':
            rows, row = _check_table_names(document, paragraphs, rule)
            findings.extend(rows)
            coverage.append(row)
            continue
        selected, scope = collect_extended_scope(paragraphs, rule['scope_selector'], rule['family'], source_sha256)
        narrative = [p for p in selected if p.get('heading_level') is None and _key(p['text']) not in SPECIAL]
        if not narrative:
            coverage.append(dict(rule_id=rid, title=rule['title'], status='partial', semantic_review_complete=False,
                                 detail='未取得适用原文范围；不据未识别到范围判断内容缺失，不新增重复人工核验卡片。'))
            continue
        p = narrative[0]
        findings.append(dict(id=rid+'-0001', rule_id=rid, title=rule['title']+'待完整范围审阅', status='candidate',
                             paragraph_index=p['index'], text=p['text'], start=0, end=min(len(p['text']), 1000),
                             location=p.get('location'), semantic_required=True, content_scope=scope,
                             check_dimensions=rule['check_dimensions'], section_complete=True,
                             text_scope='all_selected_text_complete', teacher_evidence=rule['teacher_evidence'],
                             policy=rule['policy'], context=[],
                             reason='已保留本项适用范围的完整可提取文字；必须完成后续段落读取才能形成语境判断。',
                             suggestion='逐项依据教师原话及逐字引文作出判断，已提供后文由智能体继续读取；论文外实现或未提供图像不作事实断言。'))
        coverage.append(dict(rule_id=rid, title=rule['title'], status='partial', semantic_review_complete=False,
                             source_scope_complete=True, total_paragraphs=scope['total_paragraphs'],
                             total_characters=scope['total_characters'], total_chunks=scope['total_chunks'],
                             detail=f'完整保存{scope["total_paragraphs"]}个原文段落、{scope["total_characters"]}字符；等待实际语境审阅。'))
    return findings, coverage


def _check_table_names(document, paragraphs, rule):
    from table_format_checks import _scope
    nodes = list(document.iter(W+'p'))
    if len(nodes) != len(paragraphs):
        raise ValueError('表格题名核查段落与当前文稿不匹配')
    parents = {child: parent for parent in document.iter() for child in parent}
    index_by_node = {node: i for i, node in enumerate(nodes)}
    findings, count = [], 0
    for number, table in enumerate(document.iter(W+'tbl'), 1):
        scope = _scope(table, {id(node): parent for node, parent in parents.items()})
        p_indices = [index_by_node[n] for n in table.iter(W+'p') if n in index_by_node]
        if not scope.get('applicable') or not p_indices:
            continue
        p = paragraphs[p_indices[0]]
        ancestors, ancestor = [], table
        while ancestor in parents:
            ancestor = parents[ancestor]
            ancestors.append(ancestor.tag)
        section = _key(p.get('location', {}).get('section', ''))
        if section in OUTSIDE | {'封面／前置材料', '摘要', '中文摘要', '英文摘要', 'abstract'} or any(tag in {W+'del', W+'moveFrom', W+'txbxContent', W+'tbl'} for tag in ancestors):
            continue
        count += 1
        captions = scope.get('captions', [])
        # _scope requires a numbered name; check an adjacent bare number as a
        # positive missing-name candidate. Missing adjacency alone is uncertain
        # because a distant/floating title can still belong to this table.
        parent = parents.get(table)
        sibling_nodes = list(parent) if parent is not None else []
        table_pos = sibling_nodes.index(table) if table in sibling_nodes else -1
        bare = None
        if table_pos >= 0:
            for pos in (table_pos-1, table_pos+1):
                if 0 <= pos < len(sibling_nodes) and sibling_nodes[pos].tag == W+'p':
                    node = sibling_nodes[pos]
                    i = index_by_node.get(node)
                    if i is not None and re.fullmatch(r'\s*表\s*(?:\d+\s*[-－‐‑–—]\s*)?\d+\s*[:：]?\s*', paragraphs[i]['text']):
                        bare = paragraphs[i]
        if captions and not bare:
            continue
        target = bare or p
        findings.append(dict(id=f'NP041-{len(findings)+1:04d}', rule_id=rule['id'],
                             title='数据表编号后未写明题名' if bare else '数据表附近未识别到可靠题名',
                             status='candidate' if bare else 'needs_review', paragraph_index=target['index'], text=target['text'],
                             start=0, end=len(target['text']), location=target.get('location'), table_index=number,
                             teacher_evidence=rule['teacher_evidence'], policy=rule['policy'], table_scope=scope,
                             reason='相邻题注只有表号，没有题名文字。' if bare else '识别到多行多列数据表，但邻近原生段落中未建立题名绑定；浮动题名及图片文字可能未覆盖。',
                             suggestion='依据该教师要求为此表补充说明内容的题名。' if bare else '核对本表是否有题名及其对象归属；若确实缺失，补充明确题名，不因未识别到邻接关系直接判错。'))
    return findings, dict(rule_id=rule['id'], title=rule['title'], status='partial' if count else 'not_applicable',
                          detail=f'核查{count}张可靠原生数据表的邻近题名；不套用排版框、封面表和图片表，浮动或远处题名不据此判定缺失。')
