"""Teacher-evidence-backed bounded local policy checks, without paid requests.

Candidates are clues. XML ordering is not a rendered page, and three sampled
paragraphs cannot establish the language quality of an entire thesis.
"""
from __future__ import annotations

import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
OUTSIDE = {'目录', '致谢', '谢辞', '参考文献', '附录', '独创性声明', '原创性声明', '学术诚信声明', '封面／前置材料', '摘要', '中文摘要', '英文摘要', 'abstract'}
LIST = re.compile(r'^\s*(?:[（(](\d{1,3})[）)]|[（(]([一二三四五六七八九十]+)[）)]|([①②③④⑤⑥⑦⑧⑨⑩]))(?=\s*\S)')
CAPTION = re.compile(r'^\s*[图表]\s*\d+\s*[-－—–]\s*\d+')


def _eligible(p):
    section = re.sub(r'\s+', '', str(p.get('location', {}).get('section', ''))).strip(':：').lower()
    return not any(p.get(k) for k in ('in_toc', 'in_table', 'in_textbox', 'in_deleted')) and section not in OUTSIDE


def _chinese_number(text):
    digits = {c: i for i, c in enumerate('零一二三四五六七八九')}
    if text in digits:
        return digits[text]
    if text == '十':
        return 10
    if re.fullmatch(r'[一二三四五六七八九]?十[一二三四五六七八九]?', text):
        lo, _, hi = text.partition('十')
        return digits.get(lo, 1)*10 + digits.get(hi, 0)
    return None


def load_policy_rules(library):
    families = {r['id']: r for r in library.get('rules', [])}
    definitions = [
        ('NP014', '局部列表编号重复与逆序', 'list_numbering', 'local_list_numbering', '仅同一章节内相邻的显式（1）、（一）或①列表项；中间有普通说明段或标题即重新划分，不要求全文连续编号。'),
        ('NP015', '跨页原生表格重复表头', 'native_table_header_repeat', 'native_table_header_repeat', '仅与当前文稿SHA一致的真实Word跨页表，核查OOXML首行重复表头设置；人工复制表头、合并表头及实际显示仍需视觉核验。'),
        ('NP016', '正文语句通顺与明显错字', 'language_fluency', 'body_language_semantics', '仅抽样最多3个已定位的正文文字段落，各800字；仅形成附逐字原文的语义审阅请求，不以模型推测替代原文、不据抽样宣称全篇已改完。'),
        ('NP017', '图片附近的实现说明', 'image_exposition', 'figure_exposition_semantics', '仅最多3个图片对象附近可提取的文字与图题；不读取图片内文字，不推断论文外的真实实现情况。'),
    ]
    result = []
    for rid, title, family, checker, scope in definitions:
        source = families.get(family, {})
        evidence = source.get('teacher_evidence', [])
        if family == 'language_fluency':
            evidence = [e for e in evidence if '不通顺' in e.get('quote', '')]
        if family == 'native_table_header_repeat':
            evidence = [e for e in evidence if '重复表头' in e.get('quote', '')]
        result.append({'id': rid, 'title': title, 'family': family, 'checker': checker, 'scope': scope, 'enabled': bool(evidence),
                       'teacher_evidence': evidence, 'policy': 'teacher_specific_not_universal', 'recurrence_status': source.get('recurrence_status', 'not_evaluated'),
                       'supporting_students': sorted({e.get('student_name') for e in evidence if e.get('student_name')}), 'automatic_error_judgment_enabled': False})
    return result


def check_policy_rules(document, paragraphs, rules, layout_result=None):
    nodes = list(document.iter(W+'p'))
    if len(nodes) != len(paragraphs):
        raise ValueError('教师策略检查的段落索引与当前DOCX不一致')
    by_id = {r['id']: r for r in rules if r.get('id') in {'NP014','NP015','NP016','NP017'}}
    findings, coverage = [], []

    def add(rid, p, title, reason, suggestion, start=None, end=None, status='candidate', **extras):
        row = {'id': f'{rid}-{1+sum(f["rule_id"]==rid for f in findings):04d}', 'rule_id': rid, 'title': title, 'status': status,
               'text': p['text'] if p else '', 'paragraph_index': p['index'] if p else None, 'location': p.get('location') if p else None,
               'start': start, 'end': end, 'reason': reason, 'suggestion': suggestion, 'teacher_evidence': by_id[rid]['teacher_evidence'], 'policy': by_id[rid]['policy']}
        row.update(extras)
        findings.append(row)

    def cover(rid, status, detail, **extras):
        coverage.append({'rule_id':rid,'title':by_id[rid]['title'],'status':status,'detail':detail,**extras})

    for rid, rule in by_id.items():
        if not rule.get('enabled'):
            cover(rid,'disabled_no_teacher_evidence','没有明确且经来源核验的同类教师反馈，本稿不启用该检查。')

    if by_id.get('NP014',{}).get('enabled'):
        groups, active, previous_scope, explicit_count = [], [], None, 0
        for p in paragraphs:
            if not p['text'].strip():
                continue
            m = LIST.match(p['text']) if _eligible(p) and not p.get('heading_level') else None
            if m:
                style = 'arabic' if m.group(1) else ('chinese' if m.group(2) else 'circled')
                value = int(m.group(1)) if m.group(1) else (_chinese_number(m.group(2)) if m.group(2) else '①②③④⑤⑥⑦⑧⑨⑩'.index(m.group(3))+1)
                scope = (style, tuple(p.get('location',{}).get('heading_path',[])), p.get('location',{}).get('section'))
                if value is not None:
                    explicit_count += 1
                    if active and scope != previous_scope:
                        groups.append(active)
                        active=[]
                    active.append((p,m,value))
                    previous_scope=scope
                    continue
            if active:
                groups.append(active)
                active=[]
            previous_scope=None
        if active:
            groups.append(active)
        for group in groups:
            if len(group)<2:
                continue
            seen={}
            previous=None
            for p,m,value in group:
                if value in seen:
                    add('NP014',p,'相邻列表编号重复',f'同一局部显式列表的编号{m.group(0).strip()}已经出现过。','核对该项是否与前项属于同一列表，再调整局部编号；不从全文累计编号。',m.start(),m.end(),related_paragraph_indices=[seen[value]['index']])
                elif previous is not None and value<previous[2]:
                    add('NP014',p,'相邻列表编号逆序',f'同一局部列表前项编号{previous[2]}，本项编号{value}。','核对是否为独立列表开始或编号误写；编号跳号本身未判错。',m.start(),m.end(),related_paragraph_indices=[previous[0]['index']])
                seen[value]=p
                previous=(p,m,value)
        cover('NP014','checked' if explicit_count else 'not_applicable',f'读取{explicit_count}个显式列表项，划分{len(groups)}个局部组；仅检查组内重复/逆序，Word自动编号、跨说明段列表和跳号未判错。')

    if by_id.get('NP015',{}).get('enabled'):
        tables=list(document.iter(W+'tbl'))
        layout=layout_result or {}
        digest=layout.get('source',{}).get('sha256')
        verified=bool(digest and layout.get('_verified_source_sha256')==digest and layout.get('source_unchanged') is True and layout.get('status')=='native_table_layout_collected_visual_pending')
        rows=layout.get('table_results',[])
        verified=verified and len(rows)==len(tables) and {r.get('index') for r in rows}==set(range(1,len(tables)+1))
        if not verified:
            cover('NP015','needs_review','尚无调用方核验为当前文稿SHA且表格身份完整对应的实际Word分页，不能由XML猜测哪些表跨页。')
        else:
            index_of={id(node):p for node,p in zip(nodes,paragraphs)}
            cross=0
            for row in rows:
                first,last=row.get('first_physical_page'),row.get('last_physical_page')
                if type(first) is not int or type(last) is not int or first<1 or last<first:
                    cover('NP015','needs_review','Word表格页码无效，本项停止判定。')
                    break
                if first==last:
                    continue
                cross+=1
                table=tables[row['index']-1]
                table_rows=table.findall(W+'tr')
                header=table_rows[0].find(W+'trPr/'+W+'tblHeader') if table_rows else None
                repeated=header is not None and header.attrib.get(W+'val','true').lower() in {'true','1','on','yes'}
                if not repeated:
                    first_p=next(iter(table.iter(W+'p')),None)
                    p=index_of.get(id(first_p)) if first_p is not None else None
                    add('NP015',p,'跨页表首行未开启重复表头设置',f'该表实际位于物理页{first}–{last}；首行未找到开启的tblHeader设置。未检查人工复制表头或复杂表头的实际显示。','查看续页，确认是否需要将连续的表头行设置为每页重复；已有人工续表表头时不重复修改。',status='candidate',table_index=row['index'],physical_pages=[first,last],source_page_evidence={'source_sha256':digest,'first_physical_page':first,'last_physical_page':last,'pdf':layout.get('pdf')})
            else:
                cover('NP015','partial' if cross else 'not_applicable',f'核查{len(tables)}张当前SHA对应的原生表格，其中{cross}张实际跨页；仅读取首行重复表头设置，实际表头显示需视觉核验。')

    if by_id.get('NP016',{}).get('enabled'):
        body=[p for p in paragraphs if _eligible(p) and not p.get('heading_level') and len(p['text'].strip())>=35 and not CAPTION.match(p['text'])]
        chosen=[body[i] for i in sorted({0,len(body)//2,len(body)-1})] if body else []
        for p in chosen:
            end=min(len(p['text']),800)
            add('NP016',p,'正文文字片段待语句审阅','已抽取正文原文供语义审阅，抽样本身不证明存在错字或语病。','仅指出可以用逐字原文支持的具体语病或明显错字；专业术语与事实不在本项中猜测纠正。',0,end,semantic_required=True,check_dimensions=['原句语法是否完整通顺','是否有明确可核验的错别字；不纠正专业名词或猜测事实'],text_scope='sampled_body_paragraphs_not_whole_paper',section_complete=False,context=[])
        cover('NP016','partial' if chosen else 'not_applicable',f'从{len(body)}个可识别正文段落中抽样{len(chosen)}个片段，每段最多800字；摘要、致谢、图表内字、脚注及其他正文未在本项检查，不宣称全篇语病已扫描。',sampled_paragraph_count=len(chosen),eligible_paragraph_count=len(body))

    if by_id.get('NP017',{}).get('enabled'):
        images=[i for i,node in enumerate(nodes) if node.find('.//'+W+'drawing') is not None or node.find('.//'+W+'pict') is not None]
        considered=0
        for i in images:
            if considered>=3:
                break
            nearby=[p for p in paragraphs[max(0,i-2):i+3] if _eligible(p) and p['text'].strip() and not p.get('heading_level')]
            if not nearby:
                continue
            main=next((p for p in nearby if not CAPTION.match(p['text']) and len(p['text'])>=35),nearby[0])
            end=min(len(main['text']),700)
            context=[]
            remaining=1800-end
            for p in nearby:
                if p['index']==main['index'] or len(context)>=3 or remaining<=0:
                    continue
                text=p['text'][:min(450,remaining)]
                context.append({'paragraph_index':p['index'],'text':text,'location':p.get('location')})
                remaining-=len(text)
            add('NP017',main,'图片附近文字说明待语义审阅','已提取图片对象附近的文字与图题；模型未读取图片内容，不从片段推断整个实现章节缺少说明。','按教师图文交替说明要求核对当前可见文字是否说明流程与效果；缺失结论应限于给出的片段。',0,end,semantic_required=True,check_dimensions=['当前给出的图文附近片段是否含具体说明','仅评价文字叙述，不推测图内内容或整章是否缺失'],context=context,section_complete=False,text_scope='figure_adjacent_text_excerpt',image_paragraph_index=paragraphs[i]['index'])
            considered+=1
        cover('NP017','partial' if considered else 'not_applicable',f'从{len(images)}个可识别图片对象中提取{considered}处邻近文字供语义审阅；最多3处，不覆盖全部图片，图片内容未送模型。')
    return findings,coverage
