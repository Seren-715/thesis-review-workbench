"""Local text checks of a hash-bound, read-only original DOC Word snapshot.

This does not rehabilitate a lossy conversion or simulate DOCX/Word pagination.
Original raw Word text, including control/object markers, stays in the snapshot.
"""
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from expanded_paper_checks import check_expanded_rules
from new_paper_checks import _quoted, digest
from paper_locations import annotate_paragraphs, enrich_report_locations

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def check_original_doc_text_snapshot(snapshot_path, conversion_audit_path, rules, student_name):
    snapshot_path, audit_path = Path(snapshot_path), Path(conversion_audit_path)
    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8-sig'))
    audit = json.loads(audit_path.read_text(encoding='utf-8-sig'))
    if snapshot.get('status') != 'read_only_snapshot_collected' or snapshot.get('errors') or snapshot.get('cleanup_errors'):
        raise ValueError('Original Word snapshot was not collected or cleaned up safely')
    if snapshot.get('source_sha256_unchanged') is not True or snapshot.get('derived_sha256_unchanged') is not True:
        raise ValueError('Snapshot source preservation gates failed')
    source = snapshot.get('source', {})
    path = Path(source.get('path', ''))
    if source.get('read_only') is not True or digest(path) != source.get('sha256') or source.get('sha256') != audit.get('source', {}).get('sha256'):
        raise ValueError('Snapshot is not bound to the current original DOC bytes')
    raw = source.get('raw_body_text', '')
    body_sha = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    if body_sha != source.get('body_utf8_sha256') or body_sha != audit.get('original_body_text_utf8_sha256'):
        raise ValueError('Original Word body snapshot no longer matches preserved source audit')
    original_rows = source.get('paragraphs', [])
    if ''.join(p['raw_text'] for p in original_rows) != raw or any(p['index'] != index for index, p in enumerate(original_rows)):
        raise ValueError('Snapshot paragraphs do not exactly reconstruct original body text')
    rows, document = [], ET.Element(W + 'document')
    body = ET.SubElement(document, W + 'body')
    # This in-memory tree indexes snapshot text for structural helpers only.
    # It is never written as a DOCX or claimed to be original XML evidence.
    for original in original_rows:
        text = original['raw_text']
        if text.endswith('\r'):
            text = text[:-1]
        text = text.replace('\r', '\n')
        rows.append({'index': original['index'], 'text': text, 'heading_level': original.get('heading_level'),
                     'style_id': original.get('style_name', ''), 'in_table': original.get('in_table', False),
                     'in_textbox': False, 'in_deleted': False, 'in_toc': original.get('style_name', '').lower().startswith(('toc', '目录')),
                     'word_range_start': original['range_start'], 'word_range_end': original['range_end'],
                     'text_source': 'original_doc_read_only_word_snapshot'})
        paragraph = ET.SubElement(body, W + 'p')
        ET.SubElement(ET.SubElement(paragraph, W + 'r'), W + 't').text = text
    annotate_paragraphs(rows)
    findings, excluded, coverage = [], [], []
    by_id = {r['id']: r for r in rules}
    if 'NP001' in by_id:
        count = 0
        for p in rows:
            outside = p.get('location', {}).get('section', '').strip() in {'谢辞', '致谢', '参考文献', '目录', '独创性声明', '原创性声明', '学术诚信声明'}
            for match in re.finditer('我', p['text']):
                count += 1
                index = match.start()
                reason = ('位于已识别的致谢、文末材料或声明范围，移出正文语境核查' if outside else
                          '目录文字移出正文语境核查' if p['in_toc'] else
                          '国家称谓或自我概念，不作为作者第一人称线索' if p['text'][index:index+2] == '我国' or p['text'][max(0,index-1):index+1] == '自我' else None)
                common = {'paragraph_index': p['index'], 'text': p['text'], 'start': index, 'end': index+1, 'location': p['location']}
                if reason:
                    excluded.append(dict(common, reason=reason))
                else:
                    findings.append(dict(common, id=f'NP001-{1+sum(f["rule_id"]=="NP001" for f in findings):04d}', rule_id='NP001',
                                         title='原DOC文字中的第一人称语境线索', status='candidate',
                                         reason='原件Word只读正文中检出“我”；需辨别作者自述与引用、功能名称等语境。',
                                         suggestion='仅确属作者自述并适用本教师正文要求时修改。', quoted_context=_quoted(p['text'],index),
                                         teacher_evidence=by_id['NP001'].get('teacher_evidence', [])))
        coverage.append({'rule_id':'NP001','title':by_id['NP001']['title'],'status':'partial',
                         'detail':f'原DOC正文快照“我”字{count}处，范围或词义排除{len(excluded)}处；未扫描图片内字、脚注、页眉页脚，原件控件和字段身份未全部覆盖。'})
    supported_ids = {'NP008', 'NP009', 'NP011', 'NP012', 'NP013'}
    more, more_coverage = check_expanded_rules(document, rows, [r for r in rules if r['id'] in supported_ids])
    findings.extend(more);coverage.extend(more_coverage)
    # Snapshot-text fallback intentionally cannot certify object semantics.
    for rid, rule in by_id.items():
        if rid == 'NP001' or rid in supported_ids:
            continue
        coverage.append({'rule_id':rid,'title':rule['title'],'status':'needs_review',
                         'detail':'源DOC正文文本已保存；本项目需要原生对象、DOCX格式或实际分页证据，原转换正文保真门槛未通过，未使用失真副本做该判断。'})
    report = {'schema_version':3,'status':'original_doc_text_checked_layout_pending','student_name':student_name,
              'file_name':path.name,'source_sha256':source['sha256'],'source_role':'original_legacy_doc_read_only_snapshot',
              'rules':rules,'paragraphs':rows,'headings':[p for p in rows if p.get('heading_level')],
              'findings':findings,'excluded_clues':excluded,'coverage':coverage,
              'conversion_eligible':False,'word_snapshot':{'document_path':str(snapshot_path),'sha256':digest(snapshot_path),
                                                       'original_body_utf8_sha256':body_sha,'conversion_audit_sha256':digest(audit_path)},
              'summary':{'candidate_count':sum(f['status']=='candidate' for f in findings),'confirmed_count':0,'pending_count':0},
              'limitations':['只读原DOC正文快照核查，不是通过DOCX转换。','控制字符逐字保留，不按删除或空白归一化放行原转换。',
                             '尚未完成原件图表、公式对象、图片及实际分页检查。','语义候选不等于已经确认的问题。'],
              'requests':{'llm':0,'svn':0,'word':0,'word_snapshot_requests_previously_recorded':snapshot.get('word_launches',0)}}
    if digest(path) != source['sha256']:
        raise ValueError('Original DOC changed during snapshot text checks')
    return enrich_report_locations(report)
