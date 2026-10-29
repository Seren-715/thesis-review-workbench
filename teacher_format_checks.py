"""Teacher-evidence-enabled OOXML formatting clues, without layout guessing."""
from collections import defaultdict
import re

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M='{http://schemas.openxmlformats.org/officeDocument/2006/math}'
OUTSIDE={'目录','致谢','谢辞','参考文献','独创性声明','原创性声明','学术诚信声明','摘要','中文摘要','英文摘要','abstract','封面／前置材料','附录'}
CODE=re.compile(r'^\s*(?:def\s+\w+\s*\(|class\s+\w+|(?:from\s+[\w.]+\s+import\b|import\s+\w)|public\s+(?:static\s+)?[\w<>\[\]]+\s+\w+\s*\(|@[A-Za-z]\w*(?:\(|$)|for\s+\w+\s+in\b|if\s+[^\n]+[:{])',re.M)
CAPTION=re.compile(r'^\s*([图表])\s*(\d+\s*[-－‐‑–—]\s*\d+)\s*(\S.*)$')


def load_format_rules(library):
    families={r['id']:r for r in library.get('rules',[])}
    defs=[('NP018','代码五号字号','code_font_size',r'代码.{0,8}(?:五号|5号)','仅可可靠识别的代码段或代码样式；五号按10.5pt核查，不作用于标题、正文、图题。'),
          ('NP019','正文固定20磅行距','line_spacing',r'固定值20磅','仅可靠正文文字段落；继承链未取得完整行距属性时待复核，不套用于摘要、标题、图题、表格、代码。'),
          ('NP020','代码段后零间距','paragraph_spacing',r'段后.{0,8}0行','本教师原批注锚定代码，因此仅可靠代码段核查；不泛化全部段落。'),
          ('NP021','公式对象采用公式格式','native_formula',r'公式.{0,12}(?:公式的格式|公式格式|格式要求)','仅图片/OLE明确绑定公式括号编号或公式命名题注；有图片不等于公式。原生OMML不判错。'),
          ('NP022','原生数据表三线表结构','native_table',r'三线表','仅本稿明确采用三线表要求时启用结构判断；有可靠题注或资料表头的多行多列数据/比较表才进入核查。单格提示词、内容展示框和排版网格不套用。复杂条件样式、合并网格等未完整解析时明确说明原因。'),
          ('NP023','图题紧跟图对象顺序','figure_page_binding',r'图序和图名必须紧跟着图','仅有可靠图题和同章附近图对象时核查XML相邻顺序；同页情况不能由XML判断。')]
    result=[]
    for rid,title,family,pattern,scope in defs:
        source=families.get(family,{})
        evidence=[e for e in source.get('teacher_evidence',[]) if re.search(pattern,e.get('quote',''))]
        if rid=='NP020':
            evidence=[e for e in evidence if CODE.search(str(e.get('anchor',{}).get('text','')))]
        result.append({'id':rid,'title':title,'family':family,'checker':family,'scope':scope,'teacher_evidence':evidence,
                       'enabled':bool(evidence),'policy':'teacher_specific_not_universal',
                       'recurrence_status':source.get('recurrence_status','not_evaluated') if evidence else 'not_teacher_evidence',
                       'supporting_students':sorted({e['student_name'] for e in evidence if e.get('student_name')}),
                       'automatic_error_judgment_enabled':False})
        if rid=='NP022':
            result[-1].update(requires_policy='three_line_tables',
                             policy_scope='教师历史三线表反馈不等于新稿格式要求；只有上传者明确采用三线表要求时进行结构判断。')
    return result


class Properties:
    def __init__(self,styles):
        self.styles={} if styles is None else {s.get(W+'styleId'):s for s in styles.findall(W+'style')}
        self.defaults=styles.find(W+'docDefaults') if styles is not None else None
        self.default_paragraph=next((sid for sid,s in self.styles.items() if s.get(W+'type')=='paragraph' and s.get(W+'default')=='1'),None)

    def chain(self,sid):
        seen=set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid);node=self.styles[sid];yield sid,node
            base=node.find(W+'basedOn');sid=base.get(W+'val') if base is not None else None

    def resolve(self,p,run,path,attr):
        def lookup(root,route,source):
            if root is None:return None
            node=root.find(route)
            if node is not None and node.get(W+attr) is not None:return {'value':node.get(W+attr),'source':source}
        if run is not None:
            direct=lookup(run,W+'rPr/'+path,'run_direct')
            if direct:return direct
            rstyle=run.find(W+'rPr/'+W+'rStyle')
            if rstyle is not None:
                for sid,node in self.chain(rstyle.get(W+'val')):
                    val=lookup(node,W+'rPr/'+path,'character_style:'+sid)
                    if val:return val
        direct=lookup(p,(W+'rPr/' if run is not None else W+'pPr/')+path,'paragraph_direct') if run is None else None
        if direct:return direct
        pstyle=p.find(W+'pPr/'+W+'pStyle');sid=pstyle.get(W+'val') if pstyle is not None else self.default_paragraph
        for sid,node in self.chain(sid):
            val=lookup(node,(W+'rPr/' if run is not None else W+'pPr/')+path,'paragraph_style:'+sid)
            if val:return val
        return lookup(self.defaults,(W+'rPrDefault/'+W+'rPr/' if run is not None else W+'pPrDefault/'+W+'pPr/')+path,'doc_defaults')


def check_format_rules(document,styles,paragraphs,rules,three_line_tables=False):
    if type(three_line_tables) is not bool:
        raise ValueError('three_line_tables 必须为布尔值；不能以字符串或数字确认格式要求。')
    by_id={r['id']:r for r in rules};nodes=list(document.iter(W+'p'))
    if len(nodes)!=len(paragraphs):raise ValueError('格式检查段落索引与原件不同')
    properties=Properties(styles);findings=[];coverage=[];counts=defaultdict(int);unknown=defaultdict(int)
    def add(rid,p,title,reason,suggestion,status='candidate',**extra):
        findings.append(dict(id=f'{rid}-{1+sum(f["rule_id"]==rid for f in findings):04d}',rule_id=rid,title=title,status=status,
                             paragraph_index=p['index'],text=p['text'],start=None,end=None,location=p.get('location'),reason=reason,
                             suggestion=suggestion,teacher_evidence=by_id[rid]['teacher_evidence'],**extra))
    def active(rid):return rid in by_id and by_id[rid].get('enabled',bool(by_id[rid].get('teacher_evidence')))
    def eligible(p):
        key=re.sub(r'\s+','',p.get('location',{}).get('section','')).lower().strip('：:')
        return key not in OUTSIDE and not any(p.get(flag) for flag in ('in_deleted','in_toc','in_textbox','in_table')) and not p.get('heading_level')
    def code(p):return bool(CODE.search(p['text']) or re.search(r'code|代码',str(p.get('style_id','')),re.I))
    for p,node in zip(paragraphs,nodes):
        if not eligible(p) or not p['text'].strip():continue
        iscode=code(p)
        if active('NP018') and iscode:
            counts['NP018']+=1;sizes=[];unresolved=0
            for run in node.iter(W+'r'):
                visible=''.join(t.text or '' for t in run.iter(W+'t'))
                if not visible.strip():continue
                value=properties.resolve(node,run,W+'sz','val')
                if value is None:unresolved+=1;continue
                try:pt=float(value['value'])/2
                except ValueError:unresolved+=1;continue
                if pt!=10.5:sizes.append(dict(text=visible,points=pt,**value))
            if sizes:add('NP018',p,'代码文字字号与五号要求不同','可靠代码段可见文字中解析到非10.5pt字号；按本教师代码要求核对。','确认适用这位教师的代码格式要求后，将相关代码文字设为五号。',format_evidence=sizes)
            elif unresolved:unknown['NP018']+=1;add('NP018',p,'代码字号继承未完整解析','代码范围已识别，但部分运行文字字号未解析到明确值。','在Word中核对字号及样式继承；不按未知值判错。',status='needs_review')
        if active('NP019') and not iscode and len(p['text'].strip())>=15 and not CAPTION.match(p['text']) and node.find('.//'+W+'drawing') is None and node.find('.//'+W+'pict') is None and node.find('.//'+M+'oMath') is None:
            counts['NP019']+=1
            line=properties.resolve(node,None,W+'spacing','line');rule=properties.resolve(node,None,W+'spacing','lineRule')
            if line and rule:
                if line['value']!='400' or rule['value']!='exact':
                    add('NP019',p,'正文行距与固定20磅不同','解析到显式/继承行距'+str(line['value'])+'、规则'+str(rule['value'])+'；该教师正文批注要求固定值20磅。','核对本稿适用的格式要求，再设置固定值20磅（OOXML line=400、lineRule=exact）。',format_evidence={'line':line,'lineRule':rule})
            else:unknown['NP019']+=1
        if active('NP020') and iscode:
            counts['NP020']+=1;after=properties.resolve(node,None,W+'spacing','after');after_lines=properties.resolve(node,None,W+'spacing','afterLines');auto=properties.resolve(node,None,W+'spacing','afterAutospacing')
            explicit=[v for v in (after,after_lines) if v]
            if auto and auto['value'] in {'1','true','on'}:unknown['NP020']+=1
            elif explicit and any(re.fullmatch(r'\d+(?:\.\d+)?', v['value']) is None for v in explicit):unknown['NP020']+=1
            elif explicit and any(float(v['value'])>0 for v in explicit):
                add('NP020',p,'代码段显式段后间距非零','教师“段后应该是0行”的原批注锚定代码；当前可靠代码段解析到非零段后间距。','核对代码块格式，把段后间距设为0；此项不作用于普通正文。',format_evidence={'after':after,'afterLines':after_lines,'afterAutospacing':auto})
            elif not explicit:unknown['NP020']+=1
        if active('NP021'):
            image=node.find('.//'+W+'drawing') is not None or node.find('.//'+W+'pict') is not None
            ole=node.find('.//'+W+'object') is not None
            numbered=bool(re.fullmatch(r'\s*(?:公式\s*)?[（(]\s*\d+\s*(?:[-－]\s*\d+)?\s*[）)]\s*',p['text']))
            named=bool(re.fullmatch(r'\s*图\s*\d+[-－]\d+\s+[^。\n]{0,40}公式\s*',p['text']))
            if (image or ole) and numbered and node.find('.//'+M+'oMath') is None:
                counts['NP021']+=1;add('NP021',p,'带公式编号的图片或旧对象需核对公式格式','对象旁有独立公式编号，但没有原生OMML公式结构；未激活或识别对象语义。','查看该对象是否确为数学公式；适用本教师要求时，用Word公式编辑器重建。',status='needs_review',object_evidence={'image':image,'ole':ole,'omml':False})
            elif named:
                nearby=nodes[max(0,p['index']-1):p['index']+2]
                if any(n.find('.//'+W+'drawing') is not None or n.find('.//'+W+'pict') is not None for n in nearby):
                    counts['NP021']+=1;add('NP021',p,'命名为公式的图对象需核对','明确题注含公式名称且附近有图片，不能仅凭题注判断数学内容。','查看图片确为公式后，依据教师要求调整为公式格式。',status='needs_review')
    if active('NP022'):
        from table_format_checks import check_native_tables
        table_findings,table_coverage=check_native_tables(document,styles,paragraphs,by_id['NP022'],three_line_tables=three_line_tables)
        findings.extend(table_findings);coverage.append(table_coverage)
    if active('NP023'):
        for index,p in enumerate(paragraphs):
            if not eligible(p):continue
            caption=CAPTION.match(p['text'])
            if not caption or caption.group(1)!='图' or len(p['text'])>140:continue
            nearby=[j for j in range(max(0,index-5),min(len(nodes),index+4)) if nodes[j].find('.//'+W+'drawing') is not None or nodes[j].find('.//'+W+'pict') is not None]
            nearby=[j for j in nearby if paragraphs[j].get('location',{}).get('chapter')==p.get('location',{}).get('chapter')]
            if not nearby:continue
            counts['NP023']+=1;closest=min(nearby,key=lambda j:abs(index-j))
            intervening=[paragraphs[j] for j in range(min(index,closest)+1,max(index,closest)) if paragraphs[j]['text'].strip()]
            if closest>index or intervening:
                add('NP023',p,'图题与附近图对象XML顺序需核对','图题出现在附近图对象之前，或中间隔着非空段落；仅为XML内容顺序，不代表图序跨页。','查看原稿确认题注确属该图，并按教师要求紧跟图对象；同页要求仍需Word实际分页。',status='needs_review',related_paragraph_indices=[closest],format_evidence={'caption_index':index,'nearby_image_index':closest,'intervening_paragraphs':[r['index'] for r in intervening]})
    for rid in ('NP018','NP019','NP020','NP021','NP022','NP023'):
        if rid not in by_id:continue
        if rid=='NP022' and active(rid):continue
        if not active(rid):status='not_applicable';detail='没有已核验且明确支持该格式标准的教师原话，本项未启用。'
        else:
            status='partial' if counts[rid] else 'not_applicable'
            detail=f'已检查可靠范围/对象{counts[rid]}项；继承或视觉待核对{unknown[rid]}项。仅使用列出的教师格式要求，不将未知属性默认为合格。'
        coverage.append({'rule_id':rid,'title':by_id[rid]['title'],'status':status,'detail':detail})
    return findings,coverage
