"""Source-bound institutional checks. OOXML evidence is not rendered pagination.

The caller must have passed the DOCX input gate. No Word, SVN or LLM calls.
Reference evidence deliberately remains separate from historical teacher evidence.
"""
from pathlib import Path
import hashlib
import json
import posixpath
import re
import zipfile

from teacher_format_checks import Properties, W

ROOT = Path(__file__).resolve().parent
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PINNED = {
    'institution_norm': ('3adbda05e722dff839f0df5d0fee4507f4a3c4e0f3ac1a1f08b099438296e28f',
                         '49584ce10205179e51ed81d777c23b16097553f593ff46e38d3ab0437b4a8362'),
    'checklist': ('ca49ecce867314e9eb2059582c74f241d5c2890223fd347ce5766163e057a3bd',
                  '2546647c88d0b3829243372f2c147b15f17b444b827a7f0192d1a494f7048603'),
}
TYPES = {
    'engineering_design': (10, 2), '工程设计类': (10, 2),
    'experimental_research': (15, 2), '试验研究类': (15, 2),
    'theoretical_research': (15, 2), '理论探讨类（理科）': (15, 2),
    'software': (10, 2), '计算机软件类': (10, 2),
    'humanities': (15, 1), '经济、管理及文科类': (15, 1),
    'civil_engineering': (10, None), '土建类': (10, None),
}


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_structure_rules(library=None, *, root=ROOT):
    """Verify pinned originals/copies/readers, then bind clauses by paragraph anchor."""
    root = Path(root).resolve()
    catalog = json.loads((root / 'data/reference_standards/catalog.json').read_text(encoding='utf8'))
    sources = {s['id']: s for s in catalog['sources']}
    texts = {}
    for sid, hashes in PINNED.items():
        source = sources[sid]
        if (source['source_sha256'], source['reader_sha256']) != hashes:
            raise ValueError('参考来源登记哈希与独立核验基线不符: ' + sid)
        for key, sha in [('original_path', hashes[0]), ('source_copy_path', hashes[0]), ('reader_path', hashes[1])]:
            path = Path(source[key])
            if key != 'original_path' and not path.resolve().is_relative_to(root / 'data/reference_standards'):
                raise ValueError('参考副本路径越界')
            if _hash(path) != sha:
                raise ValueError('参考原件或读取副本变化: ' + sid)
        texts[sid] = Path(source['reader_path']).read_text(encoding='utf8')

    def evidence(sid, number):
        match = re.search(r'^## 提取段落 ' + str(number) + r'\s*\n(.*?)(?=\n## |\Z)', texts[sid], re.M | re.S)
        if not match or not match.group(1).strip():
            raise ValueError('参考条文锚点缺失')
        source = sources[sid]
        return dict(source_id=sid, source_title=source['title'], source_path=source['original_path'],
                    source_sha256=source['source_sha256'], reader_path=source['reader_path'],
                    reader_sha256=source['reader_sha256'], location=f'提取段落 {number}', quote=match.group(1).strip())

    definitions = [
        ('UP002', '正文奇偶页眉与摘要无页眉', [('institution_norm', 160), ('institution_norm', 161), ('institution_norm', 162), ('checklist', 125)]),
        ('UP003', '论文组成顺序', [('institution_norm', 30)]),
        ('UP004', '按论文类型核查参考文献数量', [('institution_norm', n) for n in range(24, 29)]),
    ]
    # Reader paragraph numbers are part of the immutable reader hash.
    rules = []
    for rid, title, anchors in definitions:
        ev = [evidence(sid, n) for sid, n in anchors]
        # Retain only real historical quotations whose objects match this check.
        pattern = {'UP002': r'页眉', 'UP003': r'(?:封面|独创性声明|摘要|目录|参考文献|谢辞|致谢|附录).{0,15}(?:顺序|之后|之前|放在)',
                   'UP004': r'(?:参考文献|外文文献).{0,15}(?:至少|不少于|不低于|篇数|数量)'}[rid]
        teacher = [e for r in (library or {}).get('rules', []) for e in r.get('teacher_evidence', [])
                   if re.search(pattern, e.get('quote', ''))]
        family = {'UP002': 'page_header', 'UP003': 'document_order', 'UP004': 'references'}[rid]
        rules.append(dict(id=rid, title=title, family=family, enabled=True, reference_evidence=ev, teacher_evidence=teacher,
                          policy='user_authorized_project_reference', automatic_error_judgment_enabled=False,
                          checker='reference_structure_ooxml_v1', scope=title))
    return rules


def _key(text):
    return re.sub(r'\s+', '', text).lower().replace('（', '(').replace('）', ')').strip(':：')


def _component(row):
    if any(row.get(flag) for flag in ('in_toc', 'in_deleted', 'in_textbox', 'in_table')):
        return None
    text = _key(row['text'])
    # Explicit front matter labels are reliable objects; numbered main chapters
    # additionally require a genuine heading level from the input reader.
    names = {'独创性声明': 'declaration', '毕业设计(论文)独创性声明': 'declaration',
             '原创性声明': 'declaration', '摘要': 'chinese_abstract', '中文摘要': 'chinese_abstract',
             'abstract': 'english_abstract', '英文摘要': 'english_abstract', '目录': 'toc',
             '参考文献': 'references', '谢辞': 'acknowledgments', '致谢': 'acknowledgments',
             '封面': 'cover', '毕业设计(论文)封面': 'cover'}
    if text in names:
        return names[text]
    if re.fullmatch(r'附录(?:[a-z0-9一二三四五六七八九十]+)?(?:[:：].*)?', text):
        return 'appendix'
    if row.get('heading_level') == 1 and (re.match(r'^(?:第[一二三四五六七八九十\d]+章|[1-9]\d*[.、]?)', text)
                                          or text in {'绪论', '引言', '结论'}):
        return 'body'
    return None


def check_structure_rules(document, styles, paragraphs, rules, source_sha256, *, source_path=None,
                          paper_type=None, expected_title=None, reference_languages=None):
    """Return findings/coverage with conservative structure, header and count clues.

    paper_type is a user-confirmed TYPES key; never inferred from prose. Optional
    reference_languages maps numbered reference IDs to 'foreign' or 'chinese'
    after human confirmation. Civil engineering has no guessed foreign minimum.
    """
    from new_paper_checks import _xml, _visible_text, _hidden_run_resolver
    by_id = {r['id']: r for r in rules if r['id'] in {'UP002', 'UP003', 'UP004'}}
    nodes = list(document.iter(W + 'p'))
    if len(nodes) != len(paragraphs):
        raise ValueError('参考检查段落索引与正文不一致')
    paper_type = paper_type or None
    if paper_type is not None and paper_type not in TYPES:
        raise ValueError('论文类型未列入明确规范类型')
    if reference_languages is not None and (not isinstance(reference_languages, dict) or
            any(str(k).isdigit() is False or v not in {'foreign', 'chinese'} for k, v in reference_languages.items())):
        raise ValueError('参考文献语言确认映射无效')
    findings, coverage = [], []
    def add(rid, reason, row=None, status='candidate', **extra):
        findings.append(dict(id=f'{rid}-{1 + sum(f["rule_id"] == rid for f in findings):04d}',
                             rule_id=rid, title=by_id[rid]['title'], status=status,
                             paragraph_index=row['index'] if row else None, text=row['text'] if row else '',
                             start=None, end=None, reason=reason,
                             teacher_evidence=by_id[rid].get('teacher_evidence', []),
                             suggestion='依据已绑定规范核对并调整；待复核项需补充对象或实际分页证据。',
                             reference_evidence=by_id[rid]['reference_evidence'], **extra))
    active = lambda rid: rid in by_id and by_id[rid].get('enabled') is True
    components = [(p, _component(p)) for p in paragraphs if _component(p)]
    ranks = {name: i for i, name in enumerate(['cover', 'declaration', 'chinese_abstract', 'english_abstract',
                                             'toc', 'body', 'references', 'acknowledgments', 'appendix'])}
    if active('UP003'):
        previous = None
        seen = set()
        for row, name in components:
            if previous is not None and ranks[name] < ranks[previous[1]]:
                add('UP003', f'可靠组成对象顺序倒置：{previous[0]["text"]} 之后出现 {row["text"]}。', row,
                    preceding_paragraph_index=previous[0]['index'])
            if name in seen and name not in {'body', 'appendix'}:
                add('UP003', '组成对象出现重复标题，需核对是否为实际章节或其他前置材料。', row, 'needs_review')
            seen.add(name)
            previous = (row, name)
        missing = [name for name in ranks if name not in seen and name != 'appendix']
        if missing:
            add('UP003', '尚未可靠识别部分组成对象；不能仅据标题缺失认定实际材料缺失。', status='needs_review',
                unrecognized_components=missing)
        coverage.append(dict(rule_id='UP003', title=by_id['UP003']['title'], status='partial' if missing else 'checked',
                             detail='仅比较可靠组成对象/章级标题；排除目录引用。附录为必要时提供。',
                             recognized_components=[dict(component=n, paragraph_index=p['index']) for p, n in components]))
    if active('UP004'):
        ref_heads = [p for p, n in components if n == 'references']
        entries, unresolved = {}, []
        if len(ref_heads) == 1:
            start = ref_heads[0]['index']
            end = next((p['index'] for p, n in components if p['index'] > start), len(paragraphs))
            for row, node in zip(paragraphs[start+1:end], nodes[start+1:end]):
                if not row['text'].strip() or any(row.get(f) for f in ('in_toc', 'in_deleted', 'in_textbox')):
                    continue
                match = re.match(r'^\s*[\[［]\s*(\d+)\s*[\]］]\s*\S', row['text'])
                number = str(int(match.group(1))) if match else None
                multiple = len(re.findall(r'[\[［]\s*\d+\s*[\]］]', row['text'])) > 1
                if match and number not in entries and not multiple:
                    entries[number] = row
                else:
                    unresolved.append(row['index'])
        count = len(entries)
        if paper_type is None:
            add('UP004', '论文类型尚未确认，不从论文内容或通用检查表猜测10/15篇阈值。', status='needs_review')
        elif len(ref_heads) != 1 or unresolved:
            add('UP004', '参考文献边界、自动编号、续段或重复编号未完整确认；当前可识别条目数不是总数。',
                status='needs_review', recognized_reference_count=count, unresolved_paragraph_indexes=unresolved)
        else:
            minimum, foreign_min = TYPES[paper_type]
            if count < minimum:
                add('UP004', f'已确认类型 {paper_type} 要求参考文献至少{minimum}篇；可靠编号条目仅{count}篇。',
                    ref_heads[0], recognized_reference_count=count, required_minimum=minimum)
            languages = {str(k): v for k, v in (reference_languages or {}).items()}
            if foreign_min is None:
                add('UP004', '土建类外文篇数由所在系确定；本次不猜测外文阈值。', status='needs_review')
            elif not set(entries).issubset(languages):
                add('UP004', '外文文献身份未全部确认；英文字符、作者姓名或发表地点不等于外文身份。', status='needs_review',
                    required_foreign_minimum=foreign_min)
            elif sum(languages[k] == 'foreign' for k in entries) < foreign_min:
                add('UP004', f'已确认的外文文献少于{foreign_min}篇。', ref_heads[0], required_foreign_minimum=foreign_min)
        pending = any(f['rule_id'] == 'UP004' and f['status'] == 'needs_review' for f in findings)
        coverage.append(dict(rule_id='UP004', title=by_id['UP004']['title'], status='partial' if pending else 'checked',
                             detail='类型使用显式确认；总数仅统计完整编号条目，外文身份需确认。',
                             paper_type=paper_type, recognized_reference_count=count))
    if active('UP002'):
        if source_path is None:
            add('UP002', '未取得绑定原稿的页眉部件；不能仅从正文XML认定页眉正确。', status='needs_review')
        else:
            if _hash(source_path) != source_sha256:
                raise ValueError('页眉读取原稿哈希不匹配')
            with zipfile.ZipFile(source_path) as z:
                rels = _xml(z.read('word/_rels/document.xml.rels')) if 'word/_rels/document.xml.rels' in z.namelist() else []
                targets = {}
                for rel in rels:
                    if rel.get('TargetMode') != 'External' and rel.get('Type', '').endswith('/header'):
                        target = rel.get('Target', '')
                        part = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'word/' + target)
                        if part.startswith('word/') and '..' not in part.split('/'):
                            targets[rel.get('Id')] = part
                start, spans = 0, []
                for row, node in zip(paragraphs, nodes):
                    sect = node.find(W+'pPr/'+W+'sectPr')
                    if sect is not None:
                        spans.append((start, row['index'] + 1, sect)); start = row['index'] + 1
                final = document.find(W+'body/'+W+'sectPr')
                if final is not None:
                    spans.append((start, len(paragraphs), final))
                props = Properties(styles)
                for si, (begin, end, sect) in enumerate(spans):
                    labels = [n for p, n in components if begin <= p['index'] < end]
                    main = bool(labels and labels[0] == 'body' and all(n == 'body' for n in labels))
                    abstract = bool(labels and labels[0] in {'chinese_abstract', 'english_abstract'} and
                                    all(n in {'chinese_abstract', 'english_abstract'} for n in labels))
                    for href in sect.findall(W+'headerReference'):
                        part = targets.get(href.get(R+'id'))
                        kind = href.get(W+'type')
                        if not part or part not in z.namelist():
                            add('UP002', '页眉引用无法解析，需核对部件。', status='needs_review', section_index=si)
                            continue
                        header = _xml(z.read(part))
                        hidden = _hidden_run_resolver(styles)
                        hps = list(header.iter(W+'p'))
                        text = ''.join(_visible_text(p, hidden, (p.find(W+'pPr/'+W+'pStyle').get(W+'val')
                                       if p.find(W+'pPr/'+W+'pStyle') is not None else None)) for p in hps)
                        extra = dict(section_index=si, header_part=part, header_type=kind, header_text=text)
                        if abstract and text.strip():
                            add('UP002', '摘要所属节直接定义非空页眉；规范要求摘要无页眉，需核对实际页显示。', **extra)
                        if not main or kind == 'first':
                            continue
                        if kind == 'even' and _key(text) != _key('福州大学至诚学院本科生毕业设计(论文)'):
                            add('UP002', '正文节直接定义的偶数页眉内容与规范不符。', **extra)
                        if kind == 'default' and expected_title and _key(text) != _key(expected_title):
                            add('UP002', '正文节默认页眉内容与显式确认的论文题目不符；需核对奇偶页启用及实际显示。', **extra)
                        for p in hps:
                            if not _visible_text(p, hidden).strip():
                                continue
                            jc = props.resolve(p, None, W+'jc', 'val')
                            if jc and jc['value'] != 'center':
                                add('UP002', '正文页眉段落明确设置为非居中。', **extra)
                            for run in p.iter(W+'r'):
                                if not _visible_text(run, hidden).strip():
                                    continue
                                size = props.resolve(p, run, W+'sz', 'val')
                                font = props.resolve(p, run, W+'rFonts', 'eastAsia')
                                if size and size['value'] != '21':
                                    add('UP002', '正文页眉可见文字明确字号不是五号(10.5磅)。', **extra)
                                if font and font['value'] != '宋体':
                                    add('UP002', '正文页眉可见文字明确中文字体不是宋体。', **extra)
            if _hash(source_path) != source_sha256:
                raise ValueError('页眉读取过程中原稿变化')
            add('UP002', '已核查直接引用页眉部件；跨节继承、奇偶页和首页启用、实际页归属仍需渲染核验。', status='needs_review')
        coverage.append(dict(rule_id='UP002', title=by_id['UP002']['title'], status='partial',
                             detail='检查直接页眉内容及可解析样式；不将节定义、空引用或继承猜测当作有效渲染。'))
    for rid, rule in by_id.items():
        if not rule.get('enabled'):
            coverage.append(dict(rule_id=rid, title=rule['title'], status='reference_verification_required',
                detail='参考来源未通过核验；本项暂停，其他检查继续。'))
    return findings, coverage
