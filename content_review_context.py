"""Complete, source-ordered content scopes for bounded semantic reading.

This module performs no quality judgments and no I/O. Display previews are
separate from the lossless evidence chunks an agent can continue reading.
"""
from __future__ import annotations

import hashlib
import re

CONTENT_SCOPE_VERSION = 'complete_content_scope_v2_read_boundaries'
CHUNK_CHARACTERS = 1000
_SPECIAL = {'摘要', '中文摘要', '英文摘要', 'abstract', '结论', '结论与建议', '结论与讨论', '结束语',
            '总结与展望', '工作总结', '总结', '参考文献', '致谢', '谢辞',
            '附录', '目录', '独创性声明', '原创性声明', '学术诚信声明'}
_OUTSIDE = {'目录', '致谢', '谢辞', '参考文献', '附录', '独创性声明',
            '原创性声明', '学术诚信声明', '英文摘要', 'abstract'}
_IMPLEMENTATION = re.compile(r'(?:系统(?:的)?|功能(?:的)?|模块(?:的)?|具体)实现|详细设计与实现|实现过程')
_ORGANIZATION = re.compile(r'论文(?:的)?(?:结构|组织|安排)|(?:章节|结构|组织)(?:安排|结构)|本文结构')
_EXTENDED = {
    'data_provenance': re.compile(r'数据(?:的)?(?:来源|获取|收集|准备|说明|预处理|分析)|数据集'),
    'database_explanation': re.compile(r'数据库|数据(?:库)?表(?:的)?设计|数据结构|实体关系|[Ee][Rr]图'),
    'model_comparison': re.compile(r'模型(?:的)?(?:选择|对比|比较|优化|评估|评价)|优化模型|(?:实验|预测|训练)(?:结果|效果)|结果分析|性能(?:比较|评估)'),
}


def _title(text):
    return re.sub(r'\s+', '', re.sub(r'^\s*(?:第[一二三四五六七八九十百\d]+章|\d+(?:\.\d+)*[.、\s]*)\s*', '', text)).strip('：:').lower()


def _visible(p):
    return not any(p.get(flag) for flag in ('in_deleted', 'in_toc', 'in_textbox'))


def _structure_sentence(text):
    """Recognize chapter-outline prose, not ordinary implementation steps."""
    text = text.strip()
    return bool(re.match(r'^(?:第[一二三四五六七八九十百\d]+章|最后(?:是|为)?).*论文.*(?:总结|结构|安排)', text)
                or re.match(r'^第[一二三四五六七八九十百\d]+章[，,:：\s].*(?:介绍|阐述|描述|讨论)', text))


def _matches(keys, family):
    if any(k in _OUTSIDE for k in keys):
        return False
    if family == 'abstract':
        return any(k in {'摘要', '中文摘要'} for k in keys)
    if family == 'conclusion':
        return any(re.fullmatch(r'(?:结论(?:与(?:展望|建议|讨论))?|结束语|总结与展望|工作总结)', k) for k in keys) or bool(keys and keys[0] == '总结')
    if family in _EXTENDED:
        return any(_EXTENDED[family].search(k) for k in keys) and not any(_ORGANIZATION.search(k) for k in keys)
    return any(_IMPLEMENTATION.search(k) for k in keys) and not any(_ORGANIZATION.search(k) for k in keys)


def collect_content_scope(paragraphs, family, source_sha256=None):
    """Return selected paragraphs and a lossless chunk manifest.

    Short numbered nominal titles provide reading boundaries even when Word
    styles are missing. They remain separate from chapter-format compliance
    evidence. Chapter-outline prose cannot create an implementation range.
    """
    if family not in {'abstract', 'conclusion', 'implementation', *_EXTENDED}:
        raise ValueError('Unsupported content scope family')
    from paper_locations import inferred_heading
    stack, selected, headings, excluded, inferred_indices = [], [], [], [], []
    abstract_closed = False
    for p in paragraphs:
        if not _visible(p):
            continue
        text = str(p.get('text', ''))
        key = _title(text)
        level = p.get('heading_level')
        special = key in _SPECIAL and not p.get('in_table')
        postbody = {title for _, _, title in stack} & {'致谢', '谢辞', '参考文献', '附录'}
        # A terminal numbering separator ("3.1. 数据集介绍") is a reading
        # boundary too; retain the original spelling in every evidence chunk.
        reading_title = re.sub(r'^(\d+(?:\.\d+)*)(?:[.．、])\s+', r'\1 ', text.strip())
        inferred = (level is None and not special and not p.get('in_table') and not postbody
                    and inferred_heading(reading_title))
        is_heading = not p.get('in_table') and (level is not None or special or inferred)
        if is_heading:
            numbered = re.match(r'^\s*(\d+(?:\.\d+)*)(?:[.．、])?\s+', text)
            effective_level = (int(level) if level is not None else
                               len(numbered.group(1).split('.')) if inferred and numbered else 1)
            if inferred:
                inferred_indices.append(p['index'])
            if not special and (inferred or numbered or re.match(r'^\s*第.+章', text)):
                if any(title in {'摘要', '中文摘要', '目录', '英文摘要', 'abstract',
                                 '独创性声明', '原创性声明', '学术诚信声明'} for _, _, title in stack):
                    stack = []
            stack = [(n, row, title) for n, row, title in stack if n < effective_level]
            stack.append((effective_level, p, key))
            if family == 'abstract' and key in {'摘要', '中文摘要'}:
                abstract_closed = False
            if _matches([title for _, _, title in stack], family):
                headings.append({'paragraph_index': p['index'], 'text': p['text'],
                                 'heading_level': p.get('heading_level'), 'location': p.get('location'),
                                 'evidence_role': 'read_boundary_only' if inferred else 'explicit_heading'})
            continue
        if not text.strip():
            continue
        # A standalone keyword paragraph ends Chinese abstract body. The
        # following English title must not inherit the last Chinese range.
        # Embedded keywords later within prose are not treated as this marker.
        if (family == 'abstract' and not abstract_closed
                and _matches([title for _, _, title in stack], family)
                and re.match(r'^\s*(?:关\s*键\s*词|keywords?)\s*[:：]', text, re.I)):
            excluded.append(p['index'])
            abstract_closed = True
            continue
        if family == 'abstract' and abstract_closed:
            continue
        keys = [title for _, _, title in stack]
        if not _matches(keys, family):
            continue
        if family == 'implementation' and (any(_ORGANIZATION.search(k) for k in keys) or _structure_sentence(text)):
            excluded.append(p['index'])
            continue
        # Other rule ranges do not treat table-cell text as narrative prose.
        if p.get('in_table') and family not in {'implementation', *_EXTENDED}:
            continue
        selected.append(p)
        for _, heading, _ in stack:
            if not any(h['paragraph_index'] == heading['index'] for h in headings):
                headings.append({'paragraph_index': heading['index'], 'text': heading['text'],
                                 'heading_level': heading.get('heading_level'), 'location': heading.get('location'),
                                 'evidence_role': 'read_boundary_only' if heading['index'] in inferred_indices else 'explicit_heading'})

    chunks = []
    for p in selected:
        text = p['text']
        for start in range(0, len(text), CHUNK_CHARACTERS):
            end = min(len(text), start + CHUNK_CHARACTERS)
            chunks.append({'id': f'{family}-p{p["index"]}-{start}-{end}',
                           'paragraph_index': p['index'], 'start': start, 'end': end,
                           'text': text[start:end], 'location': p.get('location'),
                           'role': 'table_cell' if p.get('in_table') else 'body'})
    indices = [p['index'] for p in selected]
    digest = hashlib.sha256('\n'.join(f'{p["index"]}:{p["text"]}' for p in selected).encode('utf-8')).hexdigest()
    scope = {'version': CONTENT_SCOPE_VERSION, 'scope_kind': family,
             'source_sha256': source_sha256, 'scope_sha256': digest,
             'paragraph_indices': indices, 'total_paragraphs': len(selected),
             'total_characters': sum(len(p['text']) for p in selected),
             'heading_indices': [h['paragraph_index'] for h in headings], 'headings': headings,
             'chunks': chunks, 'total_chunks': len(chunks), 'complete': True,
             'excluded_paragraph_indices': excluded,
             'inferred_read_heading_indices': inferred_indices,
             'heading_compliance_evidence': False,
             'coverage_basis': 'visible_body_and_native_table_text' if family in {'implementation', *_EXTENDED} else 'visible_body_text',
             'unread_object_types': ['image_text', 'embedded_ole', 'headers_footers_footnotes']}
    return selected, scope
