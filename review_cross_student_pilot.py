"""Source-bound offline semantic review and clue replay; no API/SVN/Word."""
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re

from cross_student_rule_pilot import build_payload, validate, digest, save
from docx_comment_reader import extract_docx
from recheck_initial_pilot import collect_recheck

EXPECTED_SHA = "b1ab04599bef52a7e2d2c745d7f1fd3a5d80913c45984807b69ad286d52a2b99"
INITIAL = Path(r"D:\svn_agent\data\svn_exports\P001_20261007_103650_661507\comment_evidence_20261007_104915_363894\rule_analysis_20261007_110415_531971\initial_check_20261007_111448_335299")


def prepare_review(analysis):
    if digest(analysis / "candidate_rules.json") != EXPECTED_SHA:
        raise ValueError("This review is only for the inspected candidate batch; changed candidates need a new review")
    request = json.loads((analysis / "request.json").read_text(encoding="utf-8"))
    p002 = Path(next(row["evidence_dir"] for row in request["integrity"] if row["sample_id"] == "P002"))
    payload = build_payload(p002)
    if payload != request:
        raise ValueError("Stored request differs from revalidated source payload")
    response = json.loads((analysis / "response.json").read_text(encoding="utf-8"))
    groups = validate(response["choices"][0]["message"]["content"], payload)
    saved = json.loads((analysis / "candidate_rules.json").read_text(encoding="utf-8"))["groups"]
    if groups != saved:
        raise ValueError("Stored candidates differ from original response validation")
    def citation(sample, revision, comment_id, teacher_quote, original_quote):
        record = next(record for record in payload["source_mapping"].values()
                      if record["source"]["sample_id"] == sample and record["source"]["revision"] == revision
                      and record["comment"]["id"] == comment_id)
        if teacher_quote not in record["comment"]["text"] or original_quote not in record["anchor"]["text"]:
            raise ValueError("Review quotation is not supported by complete original evidence")
        return {"evidence_id": record["evidence_id"], "source": record["source"], "comment_id": comment_id,
                "teacher_quote": teacher_quote, "original_quote": original_quote}
    first = deepcopy(groups[0]["sources"])
    first[0] = citation("P001", 300, "0", "不要出现“我”字。", "我们会在今后的学习中")
    rules = [
        {"rule_id": "XR001", "model_candidate": "X001", "issue": "我字与第一人称表述线索",
         "scope": "遵循该教师要求的论文；本工具仅扫描document.xml可提取可见文字",
         "check_mode": "deterministic_clue_then_manual_context_review", "pilot_tool": "scan_visible_wo",
         "decision": "narrowed", "sources": first,
         "reason": "删除未被教师证据支持的其他代词扩展；字符命中包括我国、引用与代码等，必须复核语境。"},
        {"rule_id": "XR002", "model_candidate": "X002", "issue": "需求分析与系统设计分章要求",
         "scope": "遵循该教师要求的系统设计类论文；非普遍论文规范",
         "check_mode": "heading_clues_then_manual_structure_review", "pilot_tool": "collect_chapter_title_clues",
         "decision": "scope_limited", "sources": deepcopy(groups[1]["sources"]),
         "reason": "教师反馈同类成立；关键词和目录不能证明真实章级，自动工具仅提供位置线索。"},
        {"rule_id": "XR003", "model_candidate": None, "issue": "跨页表格续表标识",
         "scope": "遵循该教师要求且实际跨页的表格", "check_mode": "visual", "pilot_tool": None,
         "decision": "added_during_offline_evidence_review", "sources": [
             citation("P001", 247, "14", "跨页要使用续表", "表3-1 "),
             citation("P003", 249, "15", "表格跨页需要写续表", "表4-2 单只股票当日交易数据格式")],
         "reason": "补充模型遗漏的明确同类反馈；未渲染不能判断跨页和续表位置，保留视觉待核验。"},
    ]
    for rule in rules:
        rule.update(activation_status="not_activated_for_automatic_error_judgment",
                    supporting_students=sorted({source["source"]["sample_id"] for source in rule["sources"]}),
                    same_category_feedback_reviewed=True, universal_validity_verified=False)
    return {"rules": rules, "rejected": [{"candidate_id": "X003", "reason":
            "公式编号、图编号、表标题位置是不同问题；P003的‘在哪里？’不支持编号或标题位置结论，跨学生同类证据不足。"}],
            "review_origin": "local_source_semantic_review", "coverage": "partial/capped",
            "analysis_dir": str(analysis), "original_candidates_sha256": EXPECTED_SHA,
            "llm_requests": 0, "svn_requests": 0}, payload


def scan_clues(paragraphs):
    hits, title_clues = [], []
    for paragraph in paragraphs:
        if paragraph.get("in_deleted"):
            continue
        text = paragraph["text"]
        for match in re.finditer("我", text):
            hits.append({"paragraph_index": paragraph["index"], "offset": match.start(),
                         "context": text[max(0, match.start() - 20):match.end() + 20],
                         "interpretation": "character_clue_not_confirmed_error"})
        if len(text.strip()) <= 80 and "\t" not in text and any(word in text for word in ("需求分析", "系统设计", "系统总体设计")):
            title_clues.append({"paragraph_index": paragraph["index"], "text": text,
                                "in_table": paragraph.get("in_table"), "in_textbox": paragraph.get("in_textbox"),
                                "chapter_level_verified": False})
    return {"XR001": {"status": "partial_visible_text_clues", "hit_count": len(hits), "hits": hits,
                       "limitations": "扫描范围含前置内容、参考文献、致谢等；未自动区分正文、代码、引用、词义，未覆盖页眉脚注或图片文字。"},
            "XR002": {"status": "needs_structure_review", "title_clue_count": len(title_clues),
                      "title_clues": title_clues, "confirmed_error_count": None,
                      "limitations": "短文本关键词只供定位，可能是目录或正文，不判断真实章级或是否已正确分章。"},
            "XR003": {"status": "needs_visual_review", "confirmed_error_count": None,
                      "limitations": "未渲染分页，未核验续表标识；不是0处缺陷。"}}


def collect_replay(payload):
    documents = {}
    for record in payload["source_mapping"].values():
        source = record["source"]
        documents[(source["sample_id"], source["revision"])] = source
    # Verify the existing initial export using its original source manifest/checks.
    initial = collect_recheck(INITIAL)
    # Different pipelines use a single canonical source object; inspect its key explicitly.
    source = initial["source"]
    documents[(source["sample_id"], source["revision"])] = source
    results = []
    for _, source in sorted(documents.items()):
        extracted = extract_docx(Path(source["document_path"]), source)
        if extracted["source_sha256"] != source["sha256"]:
            raise ValueError("Replay document hash changed")
        results.append({"source": source, "checks": scan_clues(extracted["body_paragraphs"]),
                        "evaluation_role": "initial_retrospective_development_replay" if source["revision"] == 194 else "teacher_version_development_replay"})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    args = parser.parse_args()
    analysis = args.analysis_dir.resolve()
    review, payload = prepare_review(analysis)
    results = collect_replay(payload)
    output = analysis / ("reviewed_pilot_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    save(output / "reviewed_rules.json", review)
    save(output / "checks.json", {"documents": results, "independent_accuracy_test": False,
                                 "llm_requests": 0, "svn_requests": 0})
    lines = ["# 跨学生候选语义复核与线索回查", "", "X001收窄、X002限定范围、X003拒绝；补充明确同类的续表反馈XR003。",
             "两项仅提供文字/章节位置线索，续表待视觉核验。未启用自动错误判定。",
             "既有教师版本和P001初稿均为开发回查，不是独立准确率测试。P002 Word清理未确认、各样本视觉未验。", ""]
    for rule in review["rules"]:
        lines += [f"## {rule['rule_id']}：{rule['issue']}", "", rule["scope"], rule["reason"], ""]
        for citation in rule["sources"]:
            lines += [f"- {citation['source']['sample_id']} r{citation['source']['revision']} 批注{citation['comment_id']}：{citation['teacher_quote']}", ""]
    for result in results:
        source, checks = result["source"], result["checks"]
        label = f"{source['sample_id']} r{source['revision']}"
        message = f"{label}：我字线索 {checks['XR001']['hit_count']}；章节标题线索 {checks['XR002']['title_clue_count']}；续表待视觉核验"
        print(message)
        lines += [message, ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("本轮 SVN 请求：0；LLM 请求：0；未启动 Word。")
    print("复核报告：", output / "report.md")
    print("检查结果：", output / "checks.json")


if __name__ == "__main__":
    main()
