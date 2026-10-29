"""Source-bound reference diagnostics. Local facts precede any semantic verdict."""
from collections import defaultdict
import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
VERSION = 'object_reference_candidates_v1'
NUMBER = r'\d{1,3}\s*[-‐‑–—－]\s*\d{1,3}'
CAPTION = re.compile(r'^\s*([图表])\s*(' + NUMBER + r')\s*(.*)$')
REFERENCE = re.compile(r'(?:参见|详见|根据|如|见|由)\s*([图表])\s*(' + NUMBER + r')(?=$|[\s，,。；;：:]|所示|可知|给出|中|显示)|公式\s*[（(]\s*(' + NUMBER + r')\s*[）)]')
OUTSIDE = {'目录', '致谢', '谢辞', '独创性声明', '原创性声明', '学术诚信声明', '参考文献', '附录'}


def number_key(raw):
    return '-'.join(str(int(n)) for n in re.split(r'[-‐‑–—－]', re.sub(r'\s+', '', raw)))


def eligible(p):
    return (not any(p.get(k) for k in ('in_deleted', 'in_toc', 'in_textbox', 'in_table'))
            and re.sub(r'\s+', '', str((p.get('location') or {}).get('section', ''))).strip('：:') not in OUTSIDE)


def check_object_references(document, paragraphs, rule, source_sha256=None):
    nodes = list(document.iter(W + 'p'))
    if len(nodes) != len(paragraphs):
        raise ValueError('引用诊断的原文索引不一致。')
    targets, caption_indices, unparsed, objects = [], set(), [], []
    for i, (p, node) in enumerate(zip(paragraphs, nodes)):
        if p.get('in_deleted') or p.get('in_toc'):
            continue
        image = node.find('.//' + W + 'drawing') is not None or node.find('.//' + W + 'pict') is not None
        if image and not p.get('in_textbox'):
            objects.append(p['index'])
        match = CAPTION.match(p.get('text', ''))
        if match and p.get('in_textbox'):
            unparsed.append({'paragraph_index': p['index'], 'text': p['text'][:180], 'cause': 'textbox_caption'})
        if not eligible(p):
            continue
        kind, raw, title = (match.group(1), match.group(2), match.group(3).strip(' ：:')) if match else (None, None, None)
        binding = None
        bound_indices = []
        if match and len(p['text']) <= 200:
            for j in range(max(0, i - 1), min(len(nodes), i + 2)):
                if paragraphs[j].get('in_deleted') or paragraphs[j].get('in_toc') or paragraphs[j].get('in_textbox'):
                    continue
                n = nodes[j]
                if kind == '图' and (n.find('.//' + W + 'drawing') is not None or n.find('.//' + W + 'pict') is not None):
                    binding = 'adjacent_image'
                    bound_indices.append(paragraphs[j]['index'])
                if kind == '表' and paragraphs[j].get('in_table'):
                    binding = 'adjacent_native_table'
                    bound_indices.append(paragraphs[j]['index'])
            style = bool(re.search(r'caption|题注', str(p.get('style_id', '')), re.I))
            if not binding and style:
                binding = 'caption_style_only'
            # A prose sentence beginning with a label is not itself a caption.
            if not binding:
                continue
        else:
            fm = re.search(r'[（(]\s*(' + NUMBER + r')\s*[）)]\s*$', p['text'])
            if fm and node.find('.//' + M + 'oMath') is not None and (node.find('.//' + M + 'oMathPara') is not None or '\t' in p['text']):
                kind, raw, title, binding = '公式', fm.group(1), p['text'][:120], 'native_omml_label'
                bound_indices = [p['index']]
            else:
                continue
        number = number_key(raw)
        targets.append({'id': f'{kind}-{number}-p{p["index"]}', 'kind': kind, 'number': number,
                        'label': kind + number, 'title': title, 'text': p['text'],
                        'paragraph_index': p['index'], 'location': p.get('location'),
                        'binding': binding, 'object_paragraph_indices': bound_indices})
        caption_indices.add(p['index'])
    mapping = defaultdict(list)
    for target in targets:
        mapping[(target['kind'], target['number'])].append(target)
    findings, reference_count, matched_count = [], 0, 0
    for p in paragraphs:
        if not eligible(p) or p['index'] in caption_indices or p.get('heading_level'):
            continue
        for m in REFERENCE.finditer(p['text']):
            reference_count += 1
            kind, number = (m.group(1), number_key(m.group(2))) if m.group(1) else ('公式', number_key(m.group(3)))
            exact = mapping[(kind, number)]
            if len(exact) == 1:
                matched_count += 1
                continue
            path = (p.get('location') or {}).get('heading_path', [])
            candidates = []
            for target in targets:
                distance = abs(target['paragraph_index'] - p['index'])
                same_scope = bool(path and path == (target.get('location') or {}).get('heading_path'))
                if target in exact:
                    relation, priority = 'duplicate_same_label', 0
                elif target['kind'] == kind and (distance <= 80 or same_scope):
                    relation, priority = 'same_kind_different_number', 1 if same_scope and distance <= 8 else 3
                elif target['kind'] != kind and target['number'] == number:
                    relation, priority = 'same_number_other_kind', 2
                else:
                    continue
                candidates.append(dict(target, distance=distance, same_heading_scope=same_scope,
                                       relation=relation, _priority=priority))
            candidates.sort(key=lambda c: (c['_priority'], c['distance'], c['id']))
            candidates = candidates[:6]
            for c in candidates:
                c.pop('_priority')
            near_objects = [index for index in objects if abs(index - p['index']) <= 3]
            nearby_caption = any(t['kind'] == kind and abs(t['paragraph_index'] - p['index']) <= 4 for t in targets)
            if exact:
                classification, title = 'duplicate_target', '引用编号对应多个题注，目标有歧义'
            elif any(c['same_heading_scope'] and c['distance'] <= 8 and c['kind'] == kind for c in candidates):
                classification, title = 'nearby_number_mismatch', '引用编号与同节附近题注不一致'
            elif any(c['relation'] == 'same_number_other_kind' for c in candidates):
                classification, title = 'possible_kind_mismatch', '引用未匹配同类目标，存在同号异类题注'
            elif near_objects and not nearby_caption:
                classification, title = 'unlabelled_nearby_object', '引用附近有对象但未识别到对应题注'
            else:
                classification, title = 'target_not_found', '引用目标待定位：未找到可靠对应题注'
            label = kind + number
            reason = f'原文引用{label}；同类型同编号题注{len(exact)}个，提供{len(candidates)}个有原文位置的候选。'
            if candidates:
                reason += '候选包括' + '、'.join(c['text'][:60] for c in candidates[:3]) + '。须结合所述内容判断，不能仅按数字改为另一类对象。'
            else:
                reason += '暂未定位替代目标；缺少题注不等于对象不存在。'
            diagnosis = {'version': VERSION, 'classification': classification, 'reference_label': label,
                         'reference_text': m.group(0), 'source_sha256': source_sha256,
                         'candidates': candidates, 'exact_target_count': len(exact),
                         'nearby_image_paragraph_indices': near_objects,
                         'unparsed_caption_count': len(unparsed),
                         'coverage_limits': ['图片内编号和非标准题注未完整识别', '文本框题注仅作为解析缺口记录'],
                         'object_absence_proven': False}
            findings.append({'id': f'NP010-{len(findings)+1:04d}', 'rule_id': 'NP010', 'title': title,
                             'status': 'candidate', 'paragraph_index': p['index'], 'text': p['text'],
                             'start': m.start(), 'end': m.end(), 'location': p.get('location'),
                             'reason': reason, 'suggestion': '比较引用处描述与候选题注：确定目标后修正引用类型或编号；没有可靠目标时检查对象或题注是否遗漏。',
                             'teacher_evidence': rule.get('teacher_evidence', []), 'policy': rule.get('policy'),
                             'object_kind': kind, 'object_number': list(map(int, number.split('-'))),
                             'semantic_required': True, 'text_scope': 'specific_object_reference',
                             'reference_diagnostic': diagnosis,
                             'check_dimensions': ['引用类型和编号', '引用处描述与目标内容', '候选对象或题注缺失及解析不足']})
    counts = {kind: sum(t['kind'] == kind for t in targets) for kind in ('图', '表', '公式')}
    coverage = {'rule_id': 'NP010', 'title': rule['title'], 'status': 'partial',
                'detail': f'可靠图题{counts["图"]}、表题{counts["表"]}、公式标签{counts["公式"]}；显式引用{reference_count}处，唯一匹配{matched_count}处，需诊断{len(findings)}处。对错号、图表类型差异、题注缺失和重复目标分别核查；未匹配不直接判对象不存在。',
                'reference_count': reference_count, 'matched_count': matched_count,
                'diagnostic_count': len(findings), 'target_counts': counts,
                'unparsed_captions': unparsed, 'reference_policy_version': VERSION}
    return findings, coverage


def attach_reference_pages(report):
    """Link unique literal captions to a verified, same-source Word PDF."""
    from hashlib import sha256
    from pathlib import Path
    pdf = Path(report.get('pdf_path') or '')
    if not pdf.is_file() or sha256(pdf.read_bytes()).hexdigest() != report.get('pdf_sha256'):
        return 0
    try:
        import fitz
        with fitz.open(pdf) as pages:
            texts = [re.sub(r'\s+', '', page.get_text()) for page in pages]
    except (ImportError, OSError, RuntimeError, ValueError):
        return 0
    bound = 0
    for f in report.get('findings', []):
        d = f.get('reference_diagnostic')
        if not d or d.get('source_sha256') != report.get('source_sha256'):
            continue
        for c in d.get('candidates', []):
            needle = re.sub(r'\s+', '', c['text'])
            matches = [i + 1 for i, text in enumerate(texts) if needle and needle in text]
            if len(matches) == 1:
                c.update(physical_page=matches[0], page_source_sha256=report['source_sha256'],
                         page_pdf_sha256=report['pdf_sha256'])
                bound += 1
    return bound
