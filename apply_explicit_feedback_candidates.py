"""Publish source-verified, narrowly reviewed classification candidates.

These are requirement interpretations, not teacher approval or paper truth.
Every update retains the previous state and literal evidence in its receipt.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from teacher_rule_library import get_library,publish_classification
from atomic_json import write_json

REQUIREMENTS=(
 ('这个放后面，这里是技术介绍',['section_content_placement'],'教师要求将项目实施内容移出当前技术介绍位置；仅保留局部章节适用范围。'),
 ('这里要描述你用的模型和技术',['method_specification'],'教师要求说明本项目实际选用的模型和技术，不是统一格式要求。'),
 ('这里要加一个方案概述。',['solution_overview'],'原话明确要求整体方案及数据到预测推荐的流程；示意图按建议保留。'),
 ('结果呢？需要做一个展示。',['result_demonstration'],'原话明确要求本项目结果展示；词向量或相似度是举例，不强制所有论文照搬。'),
 ('你的预测对象是谁呢？',['prediction_target','solution_overview'],'原话要求说明预测目标并增加方案概述，适用于本稿监督训练任务。'),
 ('系统分为几个模块，每个模块有什么功能。',['module_inventory'],'原话明确要求本系统模块组成与功能，不预设统一模块数量。'),
 ('这里应该介绍你对数据的分析',['data_analysis_explanation'],'原话要求对本项目实际数据作具体分析，区别于词云价值的抽象介绍。'),
)


def apply(output,root=Path(__file__).resolve().parent):
    library=get_library(root); updates=[]; receipt=[]
    for source in library['comments']:
        if source.get('status')!='semantic_review_required':continue
        match=next((r for r in REQUIREMENTS if r[0] in source['text']),None)
        if not match:continue
        path=Path(source['source_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=source['source_sha256']:
            raise ValueError('来源已变化，停止候选分类发布')
        updated=deepcopy(source)
        updated.update(status='semantic_candidate',rule_ids=match[1],
                       classification_quote=match[0],classification_reason=match[2])
        receipt.append(dict(id=source['id'],source_sha256=source['source_sha256'],comment_id=source['comment_id'],
            before={k:source.get(k) for k in ['status','rule_ids','classification_quote','classification_reason']},
            after={k:updated[k] for k in ['status','rule_ids','classification_quote','classification_reason']},
            source_path=source['source_path'],anchor=source['anchor'],human_approved=False))
        updates.append(updated)
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    write_json(output/'explicit_candidate_receipt.json',dict(origin='codex_source_grounded_requirement_review',
        source_fingerprint=library['source_fingerprint'],updates=receipt,requests={'llm':0,'svn':0,'word':0},
        limitation='分类候选仅关联实际教师要求；不自动完成规则可靠性审定或当前初稿检查。'))
    result=publish_classification(root,dict(source_fingerprint=library['source_fingerprint'],comments=updates,
        classification_run=dict(origin='codex_source_grounded_requirement_review',request_attempts=0,known_total_tokens=0,
                                reviewed_comment_ids=[c['id'] for c in updates]))) if updates else library
    return result


if __name__=='__main__':
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(apply(args.output)['summary'],ensure_ascii=False))
