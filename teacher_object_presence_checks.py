"""Teacher-bound object presence diagnostics, including unchaptered labels.

An unmatched label establishes a lookup gap, never that the object is absent.
No chapter numbering convention is inferred from a general numbering comment.
"""
import re
from object_reference_checks import W, eligible, number_key

NUMBER = r'\d{1,3}(?:\s*[-‐‑–—－]\s*\d{1,3})?'
CAPTION = re.compile(r'^\s*([图表])\s*('+NUMBER+r')(?!\d)\s*(.*)$')
REFERENCE = re.compile(r'(?:参见|详见|如|见)\s*([图表])\s*('+NUMBER+r')(?=$|[\s，,。；;：:]|所示|可知|中)')


def load_object_presence_rules(library):
    family = next((r for r in library.get('rules', []) if r['id']=='object_references'), {})
    evidence = [e for e in family.get('teacher_evidence', [])
                if re.search(r'在哪里|缺少|不存在|未找到', e.get('quote',''))
                and re.match(r'[图表]\s*\d', e.get('anchor',{}).get('text',''))]
    return [dict(id='NP050',title='图表标识与实际对象的对应',family='object_references',
                 enabled=bool(evidence),teacher_evidence=evidence,
                 checker='teacher_object_presence_diagnostic',policy='teacher_specific_not_universal',
                 automatic_error_judgment_enabled=False,
                 scope='正文显式图表引用及有题注特征的标识；含表2等单级编号。未绑定不证明对象不存在。')]


def check_object_presence(document, paragraphs, rules, source_sha256):
    rule = next((r for r in rules if r['id']=='NP050'), None)
    if rule is None:
        return [], []
    coverage = dict(rule_id='NP050', title=rule['title'], status='not_applicable')
    if not rule.get('enabled'):
        coverage['detail']='没有所附教师明确提出的图表对象定位反馈，本项未启用。'
        return [], [coverage]
    nodes = list(document.iter(W+'p'))
    if len(nodes)!=len(paragraphs):
        raise ValueError('对象定位的原文索引不一致')
    inventory, references = [], []
    for i, p in enumerate(paragraphs):
        if not eligible(p) or p.get('heading_level') is not None:
            continue
        text = p['text']; m=CAPTION.match(text)
        # Avoid "表2说明了..." prose being invented into a missing caption.
        caption_cue = bool(m and len(text)<=200 and m.group(3).strip()
                           and (re.match(r'^\s*[图表]\s*'+NUMBER+r'\s+\S', text)
                                or re.search(r'图源|表源|来源\s*[:：]', text)
                                or re.search(r'caption|题注', str(p.get('style_id','')), re.I)))
        binding=[]
        if m:
            for direction in (-1,1):
                for distance in (0,1,2):
                    j=i+direction*distance
                    if not 0<=j<len(nodes): break
                    other=paragraphs[j]
                    if any(other.get(k) for k in ('in_deleted','in_textbox','in_toc')): break
                    image = any(nodes[j].find('.//'+W+tag) is not None for tag in ('drawing','pict'))
                    if image or (m.group(1)=='表' and other.get('in_table')):
                        binding.append(other['index']); break
                    if j!=i and other['text'].strip(): break
            if caption_cue or binding:
                inventory.append(dict(id=f'{m.group(1)}-{number_key(m.group(2))}-p{p["index"]}',kind=m.group(1),number=number_key(m.group(2)),
                    label=m.group(1)+number_key(m.group(2)),text=text,paragraph_index=p['index'],
                    location=p.get('location'),object_paragraph_indices=sorted(set(binding)),
                    binding='nearby_native_object' if binding else 'label_only'))
                if not binding:
                    references.append((p,m.start(),m.end(),m.group(1),number_key(m.group(2)),True))
                continue
        for m in REFERENCE.finditer(text):
            if '-' in number_key(m.group(2)):
                # Chapter-qualified references already have NP010 diagnostics.
                continue
            references.append((p,m.start(),m.end(),m.group(1),number_key(m.group(2)),False))
    findings=[]; matched=0
    for p,start,end,kind,number,label_only in references:
        exact=[t for t in inventory if t['kind']==kind and t['number']==number and t['object_paragraph_indices']]
        if len(exact)==1 and not label_only:
            matched+=1; continue
        candidates=[t for t in inventory if t['paragraph_index']!=p['index']
                    and t['object_paragraph_indices'] and (t['number']==number or t['kind']==kind)]
        candidates=sorted(candidates,key=lambda t:(t['number']!=number,abs(t['paragraph_index']-p['index'])))[:6]
        candidates=[dict(t,distance=abs(t['paragraph_index']-p['index']),
            same_heading_scope=bool(p.get('location',{}).get('heading_path') and p.get('location',{}).get('heading_path')==(t.get('location') or {}).get('heading_path')),
            relation='duplicate_same_label' if t in exact and len(exact)>1 else 'same_label' if t in exact else 'same_number_other_kind' if t['number']==number else 'same_kind_different_number') for t in candidates]
        label=kind+number
        findings.append(dict(id=f'NP050-{len(findings)+1:04d}',rule_id='NP050',status='candidate',
            title='图表标识尚未绑定可靠对象' if label_only else '图表引用目标需核对',
            paragraph_index=p['index'],text=p['text'],start=start,end=end,location=p.get('location'),
            teacher_evidence=rule['teacher_evidence'],policy=rule['policy'],semantic_required=True,
            text_scope='specific_object_reference',object_kind=kind,object_number=list(map(int,number.split('-'))),
            reason=f'原文标识{label}；可靠同类型同编号对象{len(exact)}个。保留附近实际对象及题注供上下文核查；不据未匹配断言对象不存在。',
            suggestion='结合完整句段和候选对象判断标识是引用还是题名，再核对类型、编号或对象是否遗漏；有合理对应时无需修改。',
            check_dimensions=['标识性质','引用类型与编号','对象及题注的实际对应'],
            reference_diagnostic=dict(version='teacher_object_presence_v1',classification='unbound_label' if label_only else 'target_lookup_gap',
                reference_label=label,reference_text=p['text'][start:end],source_sha256=source_sha256,
                candidates=candidates,exact_target_count=len(exact),object_absence_proven=False,
                coverage_limits=['图片内编号、文本框及远离对象的题名未完整识别'])))
    coverage.update(status='partial',detail=f'图表标识{len(inventory)}处，引用唯一匹配{matched}处，待语境核查{len(findings)}处；支持单级与章节编号，不将查找缺口直接判错。',
                    object_inventory=inventory,diagnostic_count=len(findings))
    return findings,[coverage]
