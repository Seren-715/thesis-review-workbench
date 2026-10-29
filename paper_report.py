"""Render human-readable reports without exposing XML counters as positions."""

def result_group(finding):
    if finding.get('review_blocked'):
        return 'pending'
    v=finding.get('vision_review',{})
    semantic=finding.get('semantic_review',{})
    local=finding.get('local_review',{})
    semantic_valid = semantic.get('validated_quote') and (not finding.get('content_scope') or semantic.get('validated_scope'))
    decision=v.get('decision') if v.get('validated_image') else semantic.get('decision') if semantic_valid else local.get('decision') if local.get('validated_structure') else None
    if finding.get('status')=='confirmed_issue' or decision=='suggested_revision':
        return 'changes'
    if decision=='not_issue':
        return 'compliant'
    if finding.get('rule_id')=='NP007' and finding.get('figure_evidence',{}).get('reviewable') and not v.get('validated_image'):
        return 'unreviewed'
    semantic=finding.get('semantic_review',{})
    if finding.get('status')=='candidate' and (finding.get('semantic_required') or finding.get('rule_id') in {'NP001','NP002'}) and not semantic_valid:
        return 'unreviewed'
    return 'pending'


def render_report(report, state=None):
    state = state or report.get('run') or {}
    lines = ['# 新论文核查报告', '', f"学生：{report['student_name']}；文件：{report['file_name']}",
             f"来源 SHA256：{report['source_sha256']}", '',
             f"本轮模型请求：{state.get('dispatched_requests', state.get('request_attempts', 0))}；已知 token：{state.get('known_total_tokens', 0)}；用量未知请求：{state.get('unknown_cost_attempts', 0)}。", '',
             '教师反馈规则与补充结构检查分开保留。模型建议不等于人工确认，未命中不证明整篇合格。', '']
    completion=state.get('completion')
    if completion:
        lines += ['自动审阅状态：'+completion['status'], '处理说明：'+completion['reason'],
                  f"可处理对象 {completion['reviewable']}；已审阅 {completion['reviewed']}；不能自动判定 {completion['blocked']}；预算停止待审 {completion['budget_stopped']}。", '']
    ordered=[]
    for group,title in [('changes','建议修改'),('compliant','符合当前规则'),('unreviewed','Agent 尚未审阅'),('pending','需人工核验')]:
        selected=[f for f in report.get('findings',[]) if result_group(f)==group]
        ordered.append((None, title+f'（{len(selected)} 项）'))
        ordered.extend((f,None) for f in selected)
    for f,heading in ordered:
        if heading:
            lines += ['## '+heading,'']
            continue
        location = (f.get('location') or {}).get('label')
        if not location:
            location = '物理页 ' + '–'.join(map(str, f['physical_pages'])) if f.get('physical_pages') else '对象/范围待复核'
        lines += [f"### {f['title']} · {location}", '', f"状态：{f['status']}", '', f.get('text', ''), '']
        # Merged first-person hits already carry their count in `reason`; the
        # per-hit offsets stay in report.json instead of exposing XML counters.
        lines += [f['reason'], '']
        if f.get('headings'):
            lines += ['涉及标题：' + '；'.join(
                f"“{row['title']}”（{row['heading_level']} 级标题）" for row in f['headings']), '']
        if f.get('review_blocked'):
            lines += ['无法自动审阅：'+f['review_blocked']['reason'], '']
        local=f.get('local_review',{})
        if local.get('validated_structure'):
            labels={'suggested_revision':'结构检查建议修改','not_issue':'结构检查符合当前规则'}
            lines += [labels.get(local['decision'],local['decision']), '']
            if local['reason'] != f['reason']:
                lines += [local['reason'], '']
            if local['decision']!='not_issue' and local.get('suggestion'):
                lines += [local['suggestion'], '']
        if result_group(f) != 'compliant' and f.get('suggestion') and not f.get('content_scope') and not f.get('vision_review') and not local.get('validated_structure'):
            lines += [f['suggestion'], '']
        if f.get('semantic_review',{}).get('validated_quote'):
            row = f['semantic_review']
            labels={'suggested_revision':'建议修改，待确认','not_issue':'符合当前规则','needs_review':'待复核'}
            lines += [f"Agent：{labels.get(row['decision'],row['decision'])}；{row['reason']}", '对应原文：'+row.get('quote', ''), '']
            if row['decision'] != 'not_issue' and row.get('suggestion'):
                lines += [row['suggestion'], '']
        if f.get('adaptive_context'):
            extra=f['adaptive_context']
            lines += ['Agent 已根据先前的上下文缺口，追加读取同源章节原文后重新判断；补读本身不算发现错误。','']
            for paragraph in extra.get('paragraphs',[]):
                label=(paragraph.get('location') or {}).get('label','同章节段落')
                lines += ['补读位置：'+label,'补读原文：'+paragraph.get('text',''),'']
        if f.get('reference_diagnostic'):
            diagnosis = f['reference_diagnostic']
            lines += ['原引用：'+diagnosis['reference_label'], '']
            for target in diagnosis.get('candidates', []):
                chosen = f.get('semantic_review', {}).get('target_id') == target['id']
                lines += [('选定目标：' if chosen else '候选目标：')+target['text']+'；'+(target.get('location') or {}).get('label', '原文题注'), '']
        elif f.get('content_scope'):
            scope = f['content_scope']
            progress = f.get('content_read_progress', {})
            chunks = scope.get('chunks', [])
            read_ids = set(progress.get('read_chunk_ids', []))
            read_paragraphs = {c['paragraph_index'] for c in chunks if c['id'] in read_ids}
            lines += [f"内容范围：已提取 {scope['total_paragraphs']} 段、{scope['total_characters']} 字；实际阅读 {len(read_ids)}/{scope['total_chunks']} 个片段，涉及 {len(read_paragraphs)} 段。", '']
            row = f.get('semantic_review', {})
            if row.get('validated_scope'):
                lines += ['已读完该检查对应范围并逐项综合；不代表已评价全文所有内容。', '']
                labels = {'satisfied':'符合当前要求', 'revision':'建议修改', 'uncertain':'证据不足', 'not_applicable':'不适用'}
                for dimension in row.get('dimension_checks', []):
                    lines += ['- '+dimension['dimension']+'：'+labels[dimension['decision']]+'；'+dimension['reason'], '']
                    for evidence in dimension.get('evidence', []):
                        lines += ['  原文位置：'+(evidence.get('location') or {}).get('label','对应章节'),
                                  '  原文：'+evidence['quote'], '']
            else:
                lines += ['该范围仍未完成综合判断；已读取记录可复用续审。', '']
        elif f.get('semantic_required'):
            lines += ['语境覆盖：'+('识别的章节范围完整' if f.get('section_complete') else '仅章节片段，不能据此断言整章缺失'), '']
        if f.get('vision_review',{}).get('validated_image'):
            v=f['vision_review'];e=f.get('figure_evidence',{})
            lines += ['Agent 图像判断：'+v['decision']+'；'+v['reason'],'图型：'+v['image_kind'],*['- '+s for s in v['observations']], '读出的图内文字：'+'、'.join(v['visible_text']), '图片证据SHA256：'+str(e.get('preview_sha256')), '']
            if v.get('suggestion'):lines += ['建议：'+v['suggestion'],'']
    excluded = report.get('excluded_clues', [])
    if excluded:
        lines += ['## 正文规则未判错的“我”字记录', '',
                  f"共记录 {len(excluded)} 条；可提取文字中“我”字共 {report.get('raw_first_person_count', len(excluded))} 条。致谢、声明及概念用语按当前正文规则排除。", '']
        for clue in excluded:
            location = (clue.get('location') or {}).get('label', '范围未确认')
            lines += [f"### {location}", '', clue.get('text', ''), '', f"处理说明：{clue.get('reason', '')}", '']
    lines += ['## 检查覆盖', ''] + [f"- {r['title']}：{r['status']}；{r['detail']}" for r in report.get('coverage', [])]
    for coverage in report.get('coverage', []):
        if not coverage.get('excluded_objects'):
            continue
        lines += ['', '### '+coverage['title']+'：未套用本项规则的对象', '']
        for obj in coverage['excluded_objects']:
            location=(obj.get('location') or {}).get('label', '原生表格对象')
            lines += [f"- {location}；表格序号 {obj['table_index']}：{obj['reason']}", obj.get('text',''), '']
    lines += ['', '## 检查项与教师依据', '']
    for rule in report.get('rules', []):
        lines += [f"### {rule['id']} · {rule['title']}", '', '适用范围：' + rule.get('scope', ''), '']
        if rule.get('recurrence_status'):
            labels = {'common_candidate':'至少三名学生重复的常见问题候选，尚待教师审定', 'cross_student_candidate': '两名学生同类反馈候选，适用范围仍需确认', 'single_student_feedback': '单名学生反馈，尚未证明常见', 'not_teacher_evidence': '无直接教师依据，补充结构核查'}
            lines += ['重复支持：' + labels.get(rule['recurrence_status'], rule['recurrence_status']), '']
        if rule.get('policy') == 'supplemental_structural_check':
            lines += ['来源性质：补充结构检查，不冒充教师已确认规范。', '']
        for evidence in rule.get('teacher_evidence', []):
            lines += [f"- {evidence.get('student_name', '教师样本')} r{evidence.get('revision', '?')} 批注{evidence.get('comment_id', '?')}：{evidence.get('quote', evidence.get('comment_text', ''))}"]
    diagnostic_rows = [r for r in report.get('format_diagnostics', {}).get('coverage', [])
                       if r.get('status') == 'standard_not_specified']
    if diagnostic_rows:
        lines += ['', '## 尚需明确判定标准的格式反馈', '',
                  '已采集原文及样式属性，但缺少明确目标参数；不计入修改建议或逐对象人工核验数量。', '']
        for item in diagnostic_rows:
            lines += ['- ' + item.get('title', item['family']) + '：' + item['detail']]
    reference_rows = [r for r in report.get('format_diagnostics', {}).get('coverage', [])
                      if r.get('status') == 'reference_standard_available']
    if reference_rows:
        standards = report['format_diagnostics'].get('reference_standards', {})
        sources = {s['id']: s for s in standards.get('sources', [])}
        lines += ['', '## 已找到参考标准的格式反馈', '', standards.get('applicability', ''), '',
                  '以下为来源明确的参考要求，需对应本稿适用范围和实际对象；本节不自动增加修改建议。', '']
        selected = {rid for item in reference_rows for rid in item.get('reference_requirement_ids', [])}
        for item in standards.get('requirements', []):
            if item['id'] not in selected:
                continue
            source = sources.get(item['source_id'], {})
            lines += [f"### {source.get('title', '参考资料')} · {item.get('location', '')}", '',
                      item.get('quote', ''), '', item.get('interpretation', ''), '']
    lines += ['', '## 限制', ''] + ['- ' + item for item in report.get('limitations', [])]
    return '\n'.join(lines) + '\n'
