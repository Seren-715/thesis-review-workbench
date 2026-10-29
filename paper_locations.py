"""Readable locations; XML indexes remain provenance identifiers only."""
import re

SPECIAL = {'致谢', '谢辞', '参考文献', '摘要', '中文摘要', '英文摘要', 'abstract',
           '独创性声明', '原创性声明', '毕业论文原创性声明', '学术诚信声明', '目录', '附录'}


def declaration_template(text):
    """Recognize an explicit declaration paragraph, not a quoted field name."""
    compact = re.sub(r'\s+', '', str(text)).strip()
    if compact.startswith('本毕业设计（论文）是我个人在导师指导下完成的'):
        return True
    return bool(re.match(r'^本人(?:郑重)?声明[：:，,]?(?:本人)?(?:所呈交|所提交|本(?:学位|毕业|论文)|该(?:论文|学位)|此次提交)', compact))


def inferred_heading(text):
    """Infer short nominal headings, never chapter descriptions in an outline."""
    if len(text) >= 100:
        return False
    match = re.match(r'^(?:第[一二三四五六七八九十百\d]+章|\d+(?:\.\d+)*\s+)\s*(.*)$', text)
    if not match:
        return False
    tail = match.group(1).strip()
    # A real styled heading is handled separately. Plain prose listing what each
    # chapter does must leave the current real heading's scope intact.
    if re.search(r'[。！？；;]|^(?:是|为|对|将|则|主要|首先|其次|最后)|(?:本章|本文|本论文|主要介绍|介绍了|阐述了|进行总结|内容包括|内容为)', tail):
        return False
    return bool(tail) and len(tail) <= 45


def clean_title(text):
    return re.sub(r'\s+', '', re.sub(r'^\s*(?:第[一二三四五六七八九十百\d]+章|\d+(?:\.\d+)*[.、\s]*)\s*', '', text)).strip('：:').lower()


def annotate_paragraphs(paragraphs):
    """Use real headings or explicit range titles, never invent a chapter."""
    stack, section, count = [], '封面／前置材料', 0
    for p in paragraphs:
        text = str(p.get('text', '')).strip()
        key = clean_title(text)
        eligible = text and not any(p.get(flag) for flag in ('in_deleted', 'in_toc', 'in_textbox', 'in_table'))
        level = p.get('heading_level')
        special = eligible and key in SPECIAL
        # Unstyled numbered headings provide navigation only, not NP002 evidence.
        inferred = eligible and level is None and inferred_heading(text)
        is_heading = eligible and (level is not None or special or inferred)
        if is_heading:
            if special:
                stack = [(1, text)]
            else:
                navigation_level = level or (len(re.match(r'^(\d+(?:\.\d+)*)', text).group(1).split('.')) if re.match(r'^\d+', text) else 1)
                stack = [(n, label) for n, label in stack if n < navigation_level]
                stack.append((navigation_level, text))
            section = text
            count = 0
        elif eligible:
            count += 1
        path = [label for _, label in stack]
        chapter = next((label for n, label in stack if n == 1), None)
        range_name = section
        if p.get('in_toc'):
            range_name, path, chapter = '目录', ['目录'], None
        elif p.get('in_textbox'):
            range_name = '文本框（范围未确认）'
        elif declaration_template(text):
            range_name, path, chapter = '独创性声明', ['独创性声明'], None
        label = ' / '.join(path) if path else range_name
        if p.get('in_table'):
            label += ' · 表格内'
        elif is_heading:
            label += ' · 标题'
        elif eligible and range_name == section:
            label += f' · 第{count}个文字段落'
        p['location'] = {'section': range_name, 'chapter': chapter, 'heading_path': path,
                         'paragraph_in_section': count if eligible and not is_heading else None,
                         'label': label, 'heading_inferred': bool(inferred)}
    return paragraphs


def excluded_disposition(clue):
    reason = clue.get('reason', '')
    if any(word in reason for word in ('致谢', '谢辞', '声明', '参考文献', '目录')):
        return 'outside_body_scope'
    if '概念' in reason or '国家称谓' in reason:
        return 'lexical_exclusion'
    return 'unresolved_scope'


def enrich_report_locations(report):
    paragraphs = annotate_paragraphs(report.get('paragraphs', []))
    mapping = {p['index']: p['location'] for p in paragraphs}
    for collection in ('findings', 'excluded_clues', 'headings'):
        for item in report.get(collection, []):
            index = item.get('paragraph_index', item.get('index'))
            if index in mapping:
                item['location'] = dict(mapping[index])
            if collection == 'excluded_clues':
                item['disposition'] = excluded_disposition(item)
    return report
