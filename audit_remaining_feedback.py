"""Reconcile the original 21 gaps against current, evidence-bound methods."""
import argparse
import json
from pathlib import Path
from copy import deepcopy
from atomic_json import write_json
from classification_scope_corrections import propose_scope_corrections


def audit(root, output, correct_scope=False):
    from teacher_rule_library import get_library, publish_classification
    from new_paper_checks import load_teacher_rules
    from rule_governance import build_review_package, write_package
    root=Path(root); output=Path(output); output.mkdir(parents=True,exist_ok=True)
    library=get_library(root)
    corrected,changes=propose_scope_corrections(library)
    if correct_scope and changes:
        write_json(output/'classification_scope_correction.json',dict(changes=changes,
            source_fingerprint=library['source_fingerprint'],human_rule_approval=False,
            rationale='完整锚点与字符偏移证明反馈指向图编号，保留修正前后分类及原始证据。'))
        updates={change['evidence_id'] for change in changes}
        payload=dict(source_fingerprint=library['source_fingerprint'],
                     comments=[c for c in corrected['comments'] if c.get('evidence_id') in updates],
                     classification_run={'origin':'source_anchor_scope_audit','requests':[]})
        library=publish_classification(root,payload)
    rules=load_teacher_rules(library)
    package=build_review_package(library,rules); write_package(output/'rule_review',package)
    original_path=root/'output/reaudit_remediation_20261011/rule_review_v2/rule_review_package.json'
    original=json.loads(original_path.read_text(encoding='utf8'))
    gaps=[r for r in original['rules'] if not r['checker_ids']]
    methods={r['id']:r for r in package['rules']}
    records=[]
    added={'blank_pages','image_page_bounds','figure_layout'}
    for row in gaps:
        current=methods.get(row['id'],{})
        ids=current.get('checker_ids',[])
        status='本轮新增有限范围方法' if row['id'] in added and ids else '已有检查方法' if ids else '已采集属性，缺少明确判定标准'
        records.append(dict(family=row['id'],title=row['title'],checker_ids=ids,status=status,
                            teacher_evidence=current.get('teacher_evidence',[])))
    audit_record=dict(original_gap_count=len(gaps),original_gap_linked=sum(bool(r['checker_ids']) for r in records),
        original_gap_unlinked=[r['family'] for r in records if not r['checker_ids']],
        current_family_count=len(package['rules']),current_linked=sum(bool(r['checker_ids']) for r in package['rules']),
        current_unlinked=[r['id'] for r in package['rules'] if not r['checker_ids']],
        registered_checks=len(rules),scope_corrections_applied=len(changes) if correct_scope else 0,
        feedback=records,requests={'llm':0,'svn':0,'word':0},
        limitation='关联检查方法不表示覆盖整类全部批注、已完成人工审定或已完成所有初稿审阅。')
    write_json(output/'remaining_feedback_audit.json',audit_record)
    lines=['# 历史待实现反馈逐项核查与补充','',
        '历史“23/44，21类待实现”表示21类没有直接关联检查方法，不是21类都毫无处理能力。历史审定包保持原样。','',
        f"原{len(gaps)}类中，当前{audit_record['original_gap_linked']}类已关联方法，{len(audit_record['original_gap_unlinked'])}类因缺少明确标准保留缺口。当前反馈类别{audit_record['current_family_count']}类，直接关联{audit_record['current_linked']}类，未关联{len(audit_record['current_unlinked'])}类；注册检查方法{len(rules)}项。",
        '', '| 历史待实现类别 | 当前方法 | 当前状态 |','| --- | --- | --- |']
    lines += [f"| {r['title']} | {', '.join(r['checker_ids']) or '原文与属性采集'} | {r['status']} |" for r in records]
    lines += ['', '新增图表对象定位 NP050 支持“表2”等单级编号及未绑定标识，保留候选题注与真实原文；未匹配不证明对象不存在。',
        '', '“编号错误要全部自查”原归为章节编号，现依完整锚点及字符偏移修正为图编号反馈。删除的是错误分类，没有删除批注；不能据此猜测正确图号。',
        '', '## 本轮方法的边界','',
        '- 空白页：仅核查当前原件对应的实际PDF完全空页；空行、局部留白及仅有页眉页脚的页面仍未覆盖。',
        '- 图像版心：仅核查媒体像素唯一绑定且页边距明确的实际图像；表格越界、水纹、矢量或透明图未冒充完成。',
        '- 图片排列：分别检查教师提出的同一行或叠放；缺少明确图号元数据、组合子图和重复媒体不直接判错。',
        '- 局部字体与排版、公式字体、代码展示格式、正文颜色：已采集完整原文及可追溯属性；教师没有提供目标参数，不新增逐对象待核验卡片。',
        '', '## 采用的实现方式','',
        '通过原件SHA、PDF哈希、媒体像素与显式对象元数据建立检查证据，并保留全文语境和样式继承来源。相比按图片尺寸、关键词或属性差异直接判断，这种方式更保守，能减少误报；代价是未可靠绑定或标准不明确的对象仍保留覆盖缺口。',
        '', '本轮新增代码和证据核查未调用模型、SVN或Word。教师规则仍待逐项审定，全部初稿审阅仍是独立的后续工作。']
    (output/'历史21类反馈逐项状态与实现说明.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    return audit_record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--correct-scope',action='store_true')
    args=parser.parse_args()
    data=audit(args.root,args.output,args.correct_scope)
    print(json.dumps({k:v for k,v in data.items() if k!='feedback'},ensure_ascii=False))
