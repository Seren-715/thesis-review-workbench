"""Materialize source-bound Codex context decisions; no deployed-model calls.

The keyed decisions below are a recorded review of these fixed versions,
not a general semantic classifier or an independent evaluation gold standard.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from cross_student_rule_pilot import digest, save
from docx_comment_reader import extract_docx
from triage_initial_first_person_pilot import collect_triage

PINNED = {
    'P001': (194, '1def8d253017061149444173cfd84e9355d0042f12e8877073934284e93954c1'),
    'P002': (201, '25c4f714bf2d8827ea9a88294064cc65c648e304d8b9f9e234c00ba6389102ed'),
    'P003': (246, '945f50a423f4fb7d4ec6fbb7a049d02923e458d9dfb3a7d6b879b78d2a957660'),
}
# A = author/research-process narrative; E = inclusive explanatory first person.
DECISIONS = {
 'P001': [
 (116,100,'E','“帮助我们评估”以包容性第一人称说明评价指标；既有用户复核结论保留。'),
 (141,78,'E','前一句以[10]结束并介绍赵岩等研究，命中在随后独立的“我们通过使用”句中；未见直接引文或代码。'),
 (194,123,'A','“我的预测需求”以第一人称领属指作者自己的预测需求。'),
 (194,132,'A','“于是我在其他指标相同的前提下”叙述作者进行模型对比的过程。'),
 (199,77,'A','“我在lstm层后面皆采用”叙述作者采用的网络结构。'),
 (210,116,'A','“我们这里批次大小设为64”叙述本研究选定的批次参数。'),
 (267,91,'A','“接下来，我将把”叙述作者后续训练步骤。'),
 (281,107,'E','“帮助我们识别”说明BRAR指标的作用，属于包容性第一人称。'),
 (281,140,'A','“接下来我将算出”叙述作者拟进行的特征计算。'),
 (295,25,'A','“于是我选择增加”叙述作者调整训练参数。'),
 (306,6,'A','“我在lstm层后添加”叙述作者改进网络结构。')],
 'P002': [(680,13,'A','“我很好地了解了”是结论正文中的作者学习体会；既有用户复核结论保留。')],
 'P003': [
 (124,67,'E','“我们可以快速获取和处理”说明接口能力，属于包容性第一人称；既有用户复核结论保留。'),
 (126,103,'E','“需要我们在研究和实践中”泛指研究参与者；末尾[3]是文献标号，不能据此把整句判为直接引文。'),
 (127,54,'E','“我们可以充分利用”说明技术应用，属于包容性第一人称。'),
 (140,119,'E','“我们可以期待”以第一人称表达对领域发展的展望。'),
 (240,15,'E','“比如…我们可以获得”引入假设性计算示例，属于说明性第一人称。'),
 (242,34,'E','“现在我们用一半的钱”叙述假设性投资示例；不是实际作者投资行为的证据。'),
 (247,13,'E','“我们可以求出”说明协方差矩阵计算方法。'),
 (254,9,'E','“我们就可以进行规划求解”说明计算步骤。'),
 (254,39,'A','“我们使用国债收益率来代替”说明该计算中采用的无风险利率替代方法。'),
 (255,0,'A','“我们以函数…作为程序的入口”描述本系统实现，命中在自然语言而非函数名内。'),
 (256,111,'A','“我们进行组合投资分析的股票列表”说明该程序中列表的用途，命中在自然语言内。'),
 (256,145,'A','“因此我们需要按照”说明本程序的数据对齐步骤，后随代码不改变该词的自然语言身份。'),
 (256,214,'A','“接着我们只取周线的数据”叙述本程序数据取样步骤，命中不在代码字符串内。'),
 (256,396,'A','“我们取的收益率数据是7天”说明本计算采用的数据间隔，不在随后利率代码内。'),
 (533,15,'E','“我们首先需要考量”引入系统评价角度，属于说明性第一人称。'),
 (537,64,'E','“我们可以侧重”说明金融评价角度，属于说明性第一人称。'),
 (546,16,'A','“我们成功构建了”总结作者项目成果。'),
 (546,77,'A','“我们对系统的运作”总结作者实践认识。')],
}
USER_CONFIRMED = {('P001',116,100), ('P002',680,13), ('P003',124,67)}


def review(root, worksheet):
    original = worksheet.read_text(encoding='utf-8')
    rules_path = root / 'reviewed_rules.json'
    rule = next(r for r in json.loads(rules_path.read_text(encoding='utf-8'))['rules'] if r['rule_id']=='XR001')
    teacher = []
    for reference in rule['sources']:
        source = reference['source']
        if digest(Path(source['document_path'])) != source['sha256']:
            raise ValueError('Teacher source changed')
        extracted = extract_docx(Path(source['document_path']), source)
        comment = next(c for c in extracted['comments'] if c['comment']['id']==reference['comment_id'])
        if reference['teacher_quote'] not in comment['comment']['text'] or reference['original_quote'] not in comment['anchor']['text']:
            raise ValueError('Teacher quote or anchor differs from source')
        teacher.append({'evidence_id':reference['evidence_id'], 'source':source,
                        'comment':comment['comment'], 'anchor':comment['anchor']})
    documents = []
    updated = original
    changed = 0
    for document in collect_triage(root):
        source = document['source']; sample=source['sample_id']
        if (source['revision'],source['sha256']) != PINNED[sample]:
            raise ValueError('This context review applies only to pinned initial versions')
        candidates = [r for r in document['triage']['items'] if r['status']=='retained_for_context_review']
        planned = DECISIONS[sample]
        if [(r['paragraph_index'],r['offset']) for r in candidates] != [(p,o) for p,o,_,_ in planned]:
            raise ValueError('Candidate positions differ from reviewed set')
        reviewed = []
        start = updated.index('## '+sample+' r')
        end = updated.find('\n## ',start+4)
        if end < 0: end=len(updated)
        section = updated[start:end]
        for number,(candidate,decision) in enumerate(zip(candidates,planned),1):
            paragraph,offset,kind,reason=decision
            full=candidate['full_paragraph_text']
            if full[offset]!='我': raise ValueError('Exact character offset mismatch')
            origin='existing_user_confirmation' if (sample,paragraph,offset) in USER_CONFIRMED else 'codex_context_review'
            word='我们' if full[offset:offset+2]=='我们' else ('我的' if full[offset:offset+2]=='我的' else '我')
            # Repeated offsets remain separate occurrences; no global replace.
            heading=f'### 候选{number} · XML段落{paragraph} · 字符偏移{offset}'
            match=re.search(re.escape(heading)+r'\n(.*?)(?=\n### |\Z)',section,re.S)
            if match is None: raise ValueError('Worksheet candidate missing')
            block=match.group(0)
            if origin=='existing_user_confirmation':
                if '复核结论：符合教师要求的待改表述' not in block:
                    raise ValueError('Existing user confirmation changed')
            else:
                if block.count('复核结论：待核验')!=1: raise ValueError('Pending review already edited')
                replacement=block.replace('复核结论：待核验','复核结论：符合本轮教师规则的待改表述（Codex语境复核，非新增用户确认）。')
                replacement=replacement.replace('依据/备注：','依据/备注：'+reason+' 按本轮正文范围及XR001教师文字要求判定，不推定通用学术规范。',1)
                section=section[:match.start()]+replacement+section[match.end():]
                changed+=1
            reviewed.append(candidate | {'candidate_id':f'{sample}:r{source["revision"]}:p{paragraph}:o{offset}',
                          'matched_word':word,'pronoun_kind':'author_narrative' if kind=='A' else 'inclusive_expository',
                          'review_origin':origin,'verdict':'change_candidate_under_pilot_teacher_policy',
                          'reason':reason,'teacher_rule_id':'XR001','automated_error_judgment_enabled':False,
                          'policy_basis':'same_student_teacher_feedback' if sample in ('P001','P003') else 'cross_student_pilot_policy_and_existing_user_scope_confirmation'})
        updated=updated[:start]+section+updated[end:]
        documents.append({'source':source,'reviewed_items':reviewed,'raw_character_hits':document['triage']['raw_character_hits'],
                          'excluded_count':document['triage']['excluded_count'],
                          'excluded_items':[r for r in document['triage']['items'] if r['status']!='retained_for_context_review'],
                          'reviewed_occurrence_count':len(reviewed),
                          'affected_paragraph_count':len({r['paragraph_index'] for r in reviewed})})
    if changed!=27: raise ValueError('Exactly 27 new context reviews required')
    return documents,teacher,updated


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-dir',required=True,type=Path)
    args=parser.parse_args(); root=args.review_dir.resolve()
    worksheet=Path(r'D:\svn_agent\deliverables\三学生初稿人工核查表.md')
    worksheet_hash=digest(worksheet)
    documents,teacher,updated=review(root,worksheet)
    output=root/('first_person_context_review_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(exist_ok=False)
    data={'status':'source_bound_codex_context_review','rule_id':'XR001','documents':documents,
          'teacher_evidence':teacher,'existing_user_confirmations':3,'new_codex_context_reviews':27,
          'reviewed_occurrences':30,'affected_paragraphs':sum(d['affected_paragraph_count'] for d in documents),
          'independent_accuracy_test':False,'automated_error_judgment_enabled':False,
          'original_worksheet':{'path':str(worksheet),'sha256':worksheet_hash},'script_sha256':digest(Path(__file__)),
          'project_api_requests':0,'svn_requests':0,'word_launches':0,
          'limits':['Codex-assisted semantic review, not 27 new human confirmations or independent ground truth.',
                    'P002 applies the cross-student pilot policy; no P002 direct first-person teacher comment is asserted.',
                    'Original source revisions, prior reports and worksheet preserved. No deployed general classifier.',
                    'Occurrences within one paragraph are not independent errors; document.xml extracted text only.']}
    save(output/'review.json',data)
    header='# 第一人称语境复核记录\n\n以下27项由Codex结合原段落及教师批注复核，不冒充用户确认。原有3项用户判定原样保留。本文为原核查表的补充副本，后半部分旧分页记录属于历史状态，新分页结论见第二版验收报告。\n\n'
    (output/'核查表_语境复核副本.md').write_text(header+updated,encoding='utf-8')
    lines=['# 第一人称语境复核结果','','按XR001本轮教师要求：已复核30次命中；原有用户确认3次，本次Codex语境复核27次。均为待改表述，不代表30个独立错误或普遍学术禁令。','',
           '| 学生 | 版本 | 复核命中 | 涉及段落 | 原有用户确认 | 新增Codex复核 |','|---|---:|---:|---:|---:|---:|']
    for d in documents:
        old=sum(r['review_origin']=='existing_user_confirmation' for r in d['reviewed_items'])
        lines.append(f'| {d["source"]["sample_id"]} | {d["source"]["revision"]} | {d["reviewed_occurrence_count"]} | {d["affected_paragraph_count"]} | {old} | {d["reviewed_occurrence_count"]-old} |')
    lines+=['','P001/P003有同学生教师要求支持；P002沿用跨学生试验规则并保留既有用户判定，不声称找到P002教师原批注。','说明性“我们”与作者自述分别记录；句末文献标号不自动代表直接引文；自然语言解释旁出现代码不等于命中代码。','',
            f'[逐项复核及理由](<{(output/"核查表_语境复核副本.md").as_posix()}>)','',
            '未修改论文、原核查表或既有智能体报告；不启用自动错误判决，不计算独立准确率。未新增项目API/SVN/Word请求。']
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    if digest(worksheet)!=worksheet_hash: raise ValueError('Original worksheet changed during review')
    print('已复核30次命中：保留用户判定3；新增Codex语境复核27。')
    print('涉及段落：',data['affected_paragraphs'])
    print('结果目录：',output)


if __name__=='__main__': main()
