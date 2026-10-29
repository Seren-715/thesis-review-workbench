"""Read verified paper evidence for the local UI; never calls Word/SVN/LLM.

Offsets are Unicode code-point indices. Assets are opaque allowlisted IDs;
absolute paths are private server metadata, never part of the workspace JSON.
"""
import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

from assemble_pilot_delivery import collect_verified, CHAPTERS, SCOPED, ALIGNED
from paper_pipeline import ROOT, REVIEW, ACCEPTED, read
from reviewed_rule_catalog import BATCH
from cross_student_rule_pilot import digest
from verified_context_review_pilot import load_verified_context_review
from verified_initial_review_pilot import load_verified_pagination, RECEIPT_RELATIVE
from verified_table_pilot import load_verified_pagination as load_table_pdf, RECEIPT_RELATIVE as TABLE_RECEIPT
from docx_comment_reader import extract_docx

STUDENT_NAMES = {'P001': '黄发平', 'P002': '李晨波', 'P003': '周运龙'}
INITIAL_REVISIONS = {'P001': 194, 'P002': 201, 'P003': 246}


class Assets:
    def __init__(self):
        self.rows = {}

    def add(self, path, sha=None, kind=None, label=None):
        path = Path(path).resolve()
        current = digest(path)
        if sha and current != sha:
            raise ValueError('Asset changed: ' + str(path))
        suffix = path.suffix.lower()
        kind = kind or ('image' if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.gif'} else
                        'pdf' if suffix == '.pdf' else 'document' if suffix in {'.doc', '.docx'} else 'report')
        asset_id = hashlib.sha256((str(path) + ':' + current).encode()).hexdigest()[:24]
        self.rows[asset_id] = {'path': str(path), 'sha256': current, 'kind': kind, 'label': label or path.name}
        return asset_id

    def source(self, source):
        path = Path(source['document_path'])
        sid = source['sample_id']
        name = STUDENT_NAMES[sid]
        revision = source['revision']
        role = 'initial' if INITIAL_REVISIONS[sid] == revision else 'teacher'
        return {'file_name': path.name, 'sha256': source['sha256'],
                'revision': revision, 'student_name': name, 'role': role,
                'display_name': f'{name} · {"初稿" if role == "initial" else "教师批改稿"} · r{revision}',
                'asset_id': self.add(path, source['sha256'])}

    def artifact(self, path, label=None, sha=None, page=None):
        ident = self.add(path, sha, label=label)
        result = {'id': ident, 'label': label or Path(path).name, 'kind': self.rows[ident]['kind']}
        if page is not None:
            result['page'] = page
        return result


def _finding(ident, sid, rule, family, title, status, origin, source, assets,
             text='', highlights=None, location=None, reason='', teacher=None,
             artifacts=None, coverage='', suggestion=None):
    return {'id': ident, 'student_id': sid, 'rule_id': rule, 'family': family,
            'title': title, 'status': status, 'review_origin': origin,
            'excerpt': {'text': text, 'highlights': highlights or []},
            'location': {'revision': source['revision'], **(location or {})},
            'reason': reason, 'suggestion': suggestion, 'teacher_evidence': teacher or [],
            'artifacts': artifacts or [], 'coverage_note': coverage,
            'source': assets.source(source)}


async def build_workspace_async():
    verified = await collect_verified()
    assets = Assets()
    context = load_verified_context_review(REVIEW)['review']
    layout = load_verified_pagination(REVIEW, REVIEW / RECEIPT_RELATIVE)
    table_layout = load_table_pdf(REVIEW, REVIEW / TABLE_RECEIPT)
    supplementary_run = next(r for r in layout['receipt']['artifacts'] if r['role'] == 'word_batch_run')
    table_run = next(r for r in table_layout['receipt']['artifacts'] if r['role'] == 'original_run')
    pdf_results = read(supplementary_run['path'])['results'] + read(table_run['path'])['results']
    # These files were already tied to the Word export/source hash by the pinned
    # readers above. Do not create new PDFs or transfer one across revisions.
    pdf_by_source = {r['source']['sha256']: r for r in pdf_results}
    rules = verified['cross_student_rules']['rules']
    catalog = verified['scoped_catalog']
    # Re-extract all teacher files represented by the accepted three-student
    # collection, including a revision with no selected rule-supporting comment.
    evidence_dirs = [BATCH/'extract/P001',
        ROOT/'data/svn_exports/cross_student_20261007_113217_741043/P002/legacy_doc_20261007_114629_164953/text_recovery_20261007_115029_248525/comment_evidence_20261007_115029_416450',
        ROOT/'data/svn_exports/cross_student_20261007_113217_741043/P003/comment_evidence_20261007_113843_679890']
    comments = {}
    for folder in evidence_dirs:
        saved = read(folder/'comments.json')['comments']
        sources = {row['source']['sha256']: row['source'] for row in saved}
        for source in sources.values():
            if digest(Path(source['document_path'])) != source['sha256']:
                raise ValueError('Teacher document changed')
            fresh = extract_docx(Path(source['document_path']), source)['comments']
            for row in fresh:
                eid = row['evidence_id']
                matching = next(c for c in saved if c['evidence_id'] == eid)
                if row['comment'] != matching['comment'] or row['anchor'] != matching['anchor']:
                    raise ValueError('Teacher comment extraction differs: ' + eid)
                src = assets.source(source)
                linked = []
                for media in row['comment'].get('media', []) + row['anchor'].get('media', []):
                    if media.get('export_path') and (folder/media['export_path']).is_file():
                        linked.append(assets.artifact(folder/media['export_path'], '批注/选区图片', media.get('sha256')))
                comments[eid] = {'id': eid, 'student_id': source['sample_id'],
                    'source': {'revision': source['revision'], 'author': source.get('svn_author'), **src},
                    'comment': {k: row['comment'].get(k, '') for k in ('id', 'text', 'author')},
                    'anchor_text': row['anchor']['text'], 'paragraph_indices': row['anchor']['paragraph_indices'],
                    'artifacts': linked, 'rule_ids': []}
    for rule in rules + catalog['rules']:
        for ref in rule['sources']:
            if ref['evidence_id'] not in comments:
                raise ValueError('Missing teacher support ' + ref['evidence_id'])
            target = comments[ref['evidence_id']]['rule_ids']
            if rule['rule_id'] not in target:
                target.append(rule['rule_id'])

    def supports(rule_id, sid=None, relationship='policy_support'):
        rule = next(r for r in rules + catalog['rules'] if r['rule_id'] == rule_id)
        refs = [r for r in rule['sources'] if sid is None or r['source']['sample_id'] == sid]
        return [{k: v for k, v in comments[r['evidence_id']].items()
                 if k in {'id', 'source', 'comment', 'anchor_text'}} | {'relationship': relationship}
                for r in refs]

    findings, students = [], []
    names = STUDENT_NAMES
    source_map = {}
    for document in context['documents']:
        source = document['source']; sid = source['sample_id']; source_map[sid] = source
        reviews = document['reviewed_items']
        for item in reviews:
            text = item['full_paragraph_text']; start = item['offset']; end = start + len(item['matched_word'])
            if text[start:end] != item['matched_word']:
                raise ValueError('Invalid Unicode highlight')
            origin = 'user' if item['review_origin'] == 'existing_user_confirmation' else 'codex'
            findings.append(_finding(item['candidate_id'], sid, 'XR001', 'first_person', '第一人称表述待改',
                'reviewed_change', origin, source, assets, text, [{'start': start, 'end': end}],
                {'paragraph_index': item['paragraph_index'], 'offset': start}, item['reason'],
                supports('XR001', sid) if sid != 'P002' else supports('XR001'),
                coverage=('跨学生教师政策试验；初稿命中位置不是教师批注锚点。' if sid == 'P002' else
                          '不同版本的教师政策支持；初稿命中位置不是教师批注锚点。'),
                suggestion='按该教师要求复核并改为客观表述；不自动删除或改写。'))
        students.append({'id': sid, 'name': names[sid], 'revision': source['revision'],
            'source': assets.source(source), 'counts': {'first_person_occurrences': len(reviews),
            'user_confirmations': sum(r['review_origin'] == 'existing_user_confirmation' for r in reviews),
            'codex_reviews': sum(r['review_origin'] == 'codex_context_review' for r in reviews),
            'chapter_issues': int(sid in {'P002', 'P003'}), 'table_issues': 2 if sid == 'P002' else 0},
            'coverage_note': '开发样本回查，非独立准确率测试；未检查范围不能算通过。'})
    for item in verified['chapter_manual_reviews']:
        source = item['source']; sid = source['sample_id']
        findings.append(_finding(sid+':chapter', sid, 'XR002', 'chapter', '需求分析与系统设计分章',
            'confirmed_issue', 'user', source, assets, '\n'.join(h['text'] for h in item['headings']),
            location={'paragraph_index': item['headings'][0]['paragraph_index']}, reason=item['human_verdict'],
            teacher=supports('XR002', sid), coverage='仅适用于该教师要求的系统设计类论文。',
            artifacts=[assets.artifact(CHAPTERS, '章节人工确认')]))
    p2 = layout['visual_reviews'][1]
    for item in p2['violations']:
        pages = item['physical_pages']
        images = [assets.artifact(r['path'], '物理页 '+str(r['physical_page']), r['sha256'], r['physical_page'])
                  for r in p2['rendered_page_artifacts'] if r['physical_page'] in pages]
        images.append(assets.artifact(p2['pdf_path'], '初稿带批注页面PDF', p2['pdf_sha256'], pages[0]))
        findings.append(_finding('P002:table:'+str(item['table_index']), 'P002', 'XR003', 'table',
            item['original_caption']+'：跨页缺续表', 'confirmed_issue', 'visual', p2['source'], assets,
            item['original_caption'], location={'physical_pages': pages},
            reason=('表格从物理页14跨至15：第14页以Path行结束，第15页从Icon行继续，含Description、Pid、Page_path、Sort_num，未见续表标识。'
                    if item['table_index'] == 3 else
                    '表格从物理页16跨至17：第16页以Score行结束，第17页继续Flag行，未见续表标识。保留原表题exam，不替换为正文描述的student_paper。'),
            teacher=supports('XR003'), artifacts=images,
            coverage='实际物理页；限定已验收带批注呈现模式。教师锚点仅为政策支持。'))
    formula_rule = catalog['rules'][1]
    formula = verified['scoped_checks']['rules'][1]['result']['formula_label_evidence']
    findings.append(_finding('P001:formula', 'P001', 'PR002', 'formula', '公式编号：部分覆盖',
        'partial', 'program', catalog['initial_source'], assets,
        '\n'.join(r['context'] for r in formula['image_labels']+formula['unbound_labels']),
        reason='已确认OMML标签0；图片标签4；未绑定1。文字重复组0不能证明全部公式正确。',
        teacher=supports('PR002'), artifacts=[assets.artifact(SCOPED/'report.md', '公式范围检查报告')],
        coverage='图片内容及标签绑定仍需视觉核验。'))
    families = {'PR003': 'figure', 'PR004': 'list', 'PR005': 'image', 'PR006': 'caption'}
    for rule in verified['object_alignment']['rules']:
        rid = rule['rule_id']
        for item in rule['items']:
            status = 'trigger_absent' if item['ref'] == 'C009' else 'aligned_pending' if item['ref'] == 'C007' else 'pending'
            candidates = item.get('content_candidates', [])
            paragraph = candidates[0]['paragraph'] if status != 'pending' and candidates else None
            evidence = item['teacher_evidence']
            teacher = [{k: v for k, v in comments[evidence['evidence_id']].items()
                        if k in {'id', 'source', 'comment', 'anchor_text'}} |
                       {'relationship': 'content_alignment' if paragraph else 'teacher_anchor_unresolved'}]
            attachments = [assets.artifact(ALIGNED/'report.md', '对象对齐报告')]
            for image in item.get('teacher_adjacent_images', []):
                if 'part' not in image:
                    continue
                target = ALIGNED/'teacher_target_media'/(item['ref']+'_'+Path(image['part']).name)
                attachment = assets.artifact(target,
                    '教师 r'+str(evidence['source']['revision'])+' '+item['ref']+' 图片（初稿对应未确认）',
                    image['sha256'])
                attachments.append(attachment)
                comment_assets = comments[evidence['evidence_id']]['artifacts']
                if attachment not in comment_assets:
                    comment_assets.append(attachment)
            findings.append(_finding('P001:'+rid+':'+item['ref'], 'P001', rid, families[rid],
                next(r['title'] for r in catalog['rules'] if r['rule_id'] == rid)+' · '+item['ref'],
                status, 'program', catalog['initial_source'], assets,
                paragraph['text'] if paragraph else '',
                location={'paragraph_index': paragraph['index']} if paragraph else {},
                reason=('已对齐内容，但初稿未出现教师选区的裸编号2；不等于整项通过。' if status == 'trigger_absent' else
                        '内容已对齐，正确编号及层次仍待确认。' if status == 'aligned_pending' else
                        '尚未确认初稿中的对应对象，不能转移教师稿的错误结论。'),
                teacher=teacher, artifacts=attachments,
                coverage='未对应不证明对象不存在；未判定为错误或通过。'))
    runs = []
    for ident, title, path, tokens, calls, mode in [
        ('cross', '三学生批注归纳', REVIEW.parent/'run.json', 4026, 1, 'historical_model'),
        ('agent', '三学生受控智能体', REVIEW/ACCEPTED/'run.json', 3020, 2, 'historical_model'),
        ('pipeline', 'P001四阶段链路', BATCH/'run.json', 2124, 1, 'historical_model'),
        ('scope', '局部范围检查', SCOPED/'run.json', 0, 0, 'local'),
        ('alignment', '对象对齐', ALIGNED/'run.json', 0, 0, 'local')]:
        run = read(path)
        runs.append({'id': ident, 'title': title, 'mode': mode, 'calls': calls, 'tokens': tokens,
            'status': run['status'], 'note': '所列单轮历史用量，非项目累计成本；刷新不重新调用模型。',
            'asset_id': assets.add(path)})
    # A document registry lets every original-file button open a reader while
    # keeping the original bytes/hash and the visible student identity separate.
    document_sources = {s['source']['asset_id']: (s['id'], s['source']) for s in students}
    document_sources.update({f['source']['asset_id']: (f['student_id'], f['source']) for f in findings})
    document_sources.update({c['source']['asset_id']: (c['student_id'], c['source'])
                             for c in comments.values()})
    documents = []
    for asset_id, (sid, src) in document_sources.items():
        original = assets.rows[asset_id]
        if Path(original['path']).suffix.lower() != '.docx':
            raise ValueError('Browser reader requires verified OOXML originals')
        direct_comments = [dict(c['comment'], anchor_text=c['anchor_text'])
                           for c in comments.values() if c['source']['asset_id'] == asset_id]
        pdf_assets = []
        if src['sha256'] in pdf_by_source:
            pdf = pdf_by_source[src['sha256']]
            if pdf['source']['revision'] != src['revision'] or pdf['source']['sample_id'] != sid:
                raise ValueError('PDF does not belong to this student/version')
            pdf_assets = [assets.artifact(pdf['pdf_path'], src['display_name']+' · 已保存排版PDF', pdf['pdf_sha256'])]
            assets.rows[pdf_assets[0]['id']]['display_file_name'] = src['display_name'].replace(' · ', '_')+'_排版证据.pdf'
        original['display_file_name'] = src['display_name'].replace(' · ', '_') + '.docx'
        documents.append({**src, 'student_id': sid, 'student_name': names[sid],
                          'comments': direct_comments, 'pdf_assets': pdf_assets})
    workspace = {'schema_version': 1, 'title': 'SVN论文审阅工作台',
        'generated_at': datetime.now().astimezone().isoformat(), 'students': students,
        'summary': {'first_person_occurrences': 30, 'first_person_paragraphs': 23,
        'user_confirmations': 3, 'codex_reviews': 27, 'chapter_issues': 2, 'table_issues': 2,
        'unresolved_object_targets': 4, 'scoped_conditions': 6},
        'findings': findings, 'teacher_comments': list(comments.values()), 'documents': documents,
        'runs': runs, 'actions': [],
        'warnings': ['小批次开发回查，不是整仓库完成或独立准确率测试。',
                     'P003 r249教师稿表4-1跨页问题不计入r246初稿；r246更早，不能当作后续修复。',
                     '用户确认、Codex辅助复核、自动线索及待核验分别呈现；论文原件不改写。']}
    return workspace, assets.rows


def build_workspace():
    from web_backend.portable_startup import historical_workspace_present, empty_workspace
    from portable_rules import packaged_mode
    if packaged_mode(ROOT):
        from web_backend.portable_startup import packaged_workspace
        return packaged_workspace(ROOT)
    if not historical_workspace_present(ROOT):
        return empty_workspace()
    # Partial/mutated historical evidence must fail its normal pinned checks.
    data, assets = asyncio.run(build_workspace_async())
    data['startup'] = {'mode': 'verified_history', 'status': 'ready',
                       'checks_enabled': True,
                       'message': '历史工作区证据已按既有来源与哈希验证；检查仍受规则适用范围限制。',
                       'requirements': []}
    return data, assets
