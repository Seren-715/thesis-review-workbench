"""Source-bound current-paper identity; historical feedback never supplies it."""
from __future__ import annotations

import hashlib
import json
import re

VERSION = 'current_paper_identity_and_teacher_cases_v1'
APPLICATION_POLICY = (
    'teacher_quotes保留历史原话及示例，不代表本稿题目、必选功能或必选技术。'
    '通用写作维度与明确规范仍须核查；历史个案只有在当前题目、实际系统对象、'
    '具体功能或研究任务的原文共同证明适用时才用于该项判断，并在reason说明适用依据。'
    '题目关键词相同或技术名称出现均不足以证明适用。'
    '本稿与历史示例主题不同时不得要求改成示例主题；缺少本项目方法说明可指出缺口，'
    '不得据此强制采用示例的AKShare、Vue、Flask或其他特定技术。'
)


def _eligible(p):
    return (isinstance(p, dict) and type(p.get('index')) is int
            and isinstance(p.get('text'), str) and p['text'].strip()
            and not any(p.get(k) for k in ('in_toc', 'in_deleted', 'in_textbox')))


def _evidence(p, start=0, end=None):
    text = p['text']
    end = len(text) if end is None else end
    return {'paragraph_index': p['index'], 'quote': text[start:end],
            'start': start, 'end': end}


def _normalized(text):
    return re.sub(r'\s+', '', text)


def current_paper_context(report, provided_paragraph_indices=()):
    """Extract literal identity anchors from this report's current-source rows.

    The caller has already parsed the hash-verified source. No historical
    teacher quote or filename is used as a fallback topic. Missing identity is
    explicit. Cover names/student IDs are never included in model packets.
    """
    rows = [p for p in report.get('paragraphs', []) if _eligible(p)]
    provided = set(provided_paragraph_indices)
    abstract_heading = next((p for p in rows if _normalized(p['text']) in {'摘要', '中文摘要'}
                             and not p.get('in_table')), None)
    title, title_evidence, title_status = None, [], 'not_identified'
    labeled = next((p for p in rows if re.match(r'^\s*(?:论文)?题\s*目\s*[:：]', p['text'])
                    and (abstract_heading is None or p['index'] < abstract_heading['index'])), None)
    if labeled:
        match = re.match(r'^\s*(?:论文)?题\s*目\s*[:：]\s*', labeled['text'])
        first = labeled['text'][match.end():]
        parts = [_evidence(labeled, match.end())] if first.strip() else []
        subsequent = rows[rows.index(labeled)+1:]
        for p in subsequent[:2]:
            if (p.get('in_table') or p.get('heading_level') is not None
                    or re.match(r'^\s*(?:姓\s*名|学\s*号|系\s*别|专\s*业|年\s*级|指导|日期|独创|摘要)', p['text'])
                    or re.search(r'[:：]', p['text']) or len(p['text'].strip()) > 120):
                break
            # A wrapped continuation must sit before the next cover label.
            parts.append(_evidence(p))
        if parts:
            title = _normalized(''.join(x['quote'] for x in parts))
            title_evidence, title_status = parts, 'explicit_current_cover_title'
    if abstract_heading:
        before = [p for p in rows if p['index'] < abstract_heading['index'] and not p.get('in_table')]
        immediate = before[-1] if before else None
        if immediate and 5 <= len(immediate['text'].strip()) <= 120 and not re.search(
                r'[:：]|作者签名|教师签名|年\s*月\s*日|独创性声明|授权', immediate['text']):
            candidate = _normalized(immediate['text'])
            if title and candidate == title:
                title_evidence.append(_evidence(immediate))
                title_status = 'cover_and_pre_abstract_title_agree'
            elif title is None:
                # Positional evidence is a candidate, never a teacher-derived
                # declared title or a keyword-based topic classification.
                title, title_evidence = candidate, [_evidence(immediate)]
                title_status = 'candidate_from_pre_abstract_position'
    abstract = []
    for p in rows:
        section = _normalized(p.get('location', {}).get('section') or '')
        if section not in {'摘要', '中文摘要'} or p.get('in_table'):
            continue
        if _normalized(p['text']) in {'摘要', '中文摘要'} or re.match(r'^\s*关键词\s*[:：]', p['text']):
            continue
        abstract.append(p)
    full = [_evidence(p) for p in abstract]
    additional = [e for e in full if e['paragraph_index'] not in provided]
    context = {'version': VERSION, 'source_sha256': report.get('source_sha256'),
        'title': {'status': title_status, 'text': title, 'evidence': title_evidence},
        'abstract': {'status': 'identified' if full else 'not_identified',
            'paragraph_indices': [e['paragraph_index'] for e in full],
            'provided_in_main_evidence': [e['paragraph_index'] for e in full if e['paragraph_index'] in provided],
            'additional_source_evidence': additional},
        'system_object_basis': 'current_title_and_current_abstract_original_text_only',
        'teacher_case_application_policy': APPLICATION_POLICY}
    # Hash excludes which paragraphs were already sent in the main packet.
    canonical = dict(context, abstract={'status': context['abstract']['status'], 'evidence': full})
    context['context_sha256'] = hashlib.sha256(json.dumps(canonical, ensure_ascii=False,
                                                         sort_keys=True).encode()).hexdigest()
    return context


def validate_context_evidence(context, report):
    """Verify identity/abstract quotes against current paragraph offsets."""
    if context.get('source_sha256') != report.get('source_sha256'):
        raise ValueError('论文身份上下文来源哈希不一致。')
    by_index = {p['index']: p for p in report.get('paragraphs', []) if _eligible(p)}
    anchors = context.get('title', {}).get('evidence', []) + context.get('abstract', {}).get('additional_source_evidence', [])
    for anchor in anchors:
        p = by_index.get(anchor.get('paragraph_index'))
        start, end = anchor.get('start'), anchor.get('end')
        if (not p or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(p['text']) or p['text'][start:end] != anchor.get('quote')):
            raise ValueError('论文身份上下文没有逐字当前来源依据。')
    return True
