"""Propose source-anchored corrections; publication uses verified overlay receipts."""
from copy import deepcopy
import re


def propose_scope_corrections(library):
    result=deepcopy(library); changes=[]
    for c in result.get('comments',[]):
        if 'headings' not in c.get('rule_ids',[]) or '编号' not in c.get('text',''):
            continue
        if re.search(r'章节|标题', c['text']):
            continue
        anchor=c.get('anchor',{}); raw=anchor.get('text','')
        if anchor.get('status')!='complete' or not re.fullmatch(r'\d+[-－]\d+',raw):
            continue
        selected=set(anchor.get('paragraph_indices',[]))
        starts=anchor.get('markers',{}).get('starts',[])
        valid=False
        for p in anchor.get('context_paragraphs',[]):
            if p.get('index') not in selected or any(p.get(k) for k in ('in_deleted','in_textbox')):
                continue
            for marker in starts:
                offset=marker.get('paragraph_offset')
                if marker.get('paragraph_index')!=p['index'] or type(offset) is not int:
                    continue
                text=p.get('text','')
                valid |= (text[offset:offset+len(raw)]==raw
                          and bool(re.search(r'图\s*$',text[:offset])))
        if not valid:
            continue
        previous={k:deepcopy(c.get(k)) for k in ('rule_ids','status','classification_quote','classification_reason')}
        c['rule_ids']=[x for x in c['rule_ids'] if x!='headings']
        if 'figure_numbering' not in c['rule_ids']: c['rule_ids'].append('figure_numbering')
        c.update(status='semantic_candidate',classification_quote=c['text'],
                 classification_reason='原件完整批注锚点及字符偏移指向“图”后的编号；修正为图编号反馈，不据此推定章节编号标准或图号应如何修改。')
        changes.append(dict(comment_id=c['comment_id'],evidence_id=c.get('evidence_id'),
                            source_sha256=c['source_sha256'],source_path=c['source_path'],
                            before=previous,after={k:c.get(k) for k in previous},anchor=anchor))
    return result,changes
