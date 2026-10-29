"""Separate explicit non-body/non-pronoun clues from retained context candidates."""
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
import re

from cross_student_rule_pilot import digest, save
from docx_comment_reader import extract_docx
from inspect_initial_structure_pilot import collect

SECTION_TITLES = {"参考文献": "references", "谢辞": "acknowledgements", "致谢": "acknowledgements"}


def triage(paragraphs):
    headings = []
    for paragraph in paragraphs:
        text = paragraph["text"].strip()
        if (text in SECTION_TITLES and not paragraph["in_table"] and not paragraph["in_textbox"]
                and not paragraph["in_deleted"] and "\t" not in paragraph["text"]):
            headings.append({"paragraph_index": paragraph["index"], "text": text, "kind": SECTION_TITLES[text]})
    # Conservative boundary use: exactly one references heading and at most one
    # acknowledgement heading after it. Ambiguity keeps all hits for review.
    references = [row for row in headings if row["kind"] == "references"]
    acknowledgements = [row for row in headings if row["kind"] == "acknowledgements"]
    boundaries_verified = (len(references) == 1 and len(acknowledgements) <= 1
                           and (not acknowledgements or acknowledgements[0]["paragraph_index"] > references[0]["paragraph_index"]))
    rows = []
    for paragraph in paragraphs:
        if paragraph["in_deleted"]:
            continue
        text, index = paragraph["text"], paragraph["index"]
        for match in re.finditer("我", text):
            reason, status = "正文范围和语境仍需复核，不自动判定错误", "retained_for_context_review"
            template = "本毕业设计（论文）是我个人在导师指导下完成的"
            template_offsets = {occurrence.start() + template.index("我") for occurrence in re.finditer(re.escape(template), text)}
            if text[match.start():match.start() + 2] == "我国":
                status, reason = "excluded_lexical_country_reference", "按本轮试验排除国家称谓；不判断教师原要求是否允许"
            elif match.start() in template_offsets:
                status, reason = "excluded_declaration_template", "明确声明模板句中的该次命中移出本轮正文候选；不判断教师原要求是否允许"
            elif boundaries_verified:
                if acknowledgements and index > acknowledgements[0]["paragraph_index"]:
                    status, reason = "excluded_acknowledgements_scope", "位于已定位谢辞/致谢之后；从本轮正文检查范围排除，并原样保留"
                elif index > references[0]["paragraph_index"]:
                    status, reason = "excluded_references_scope", "位于已定位参考文献范围，移出本轮正文候选；不判断教师原要求是否允许"
            rows.append({"paragraph_index": index, "offset": match.start(), "status": status, "reason": reason,
                         "context": text[max(0, match.start() - 35):match.end() + 35],
                         "full_paragraph_text": text, "confirmed_error": None})
    counts = Counter(row["status"] for row in rows)
    return {"raw_character_hits": len(rows), "retained_context_candidates": counts["retained_for_context_review"],
            "excluded_count": len(rows) - counts["retained_for_context_review"], "status_counts": dict(counts),
            "section_boundaries": headings, "section_boundary_policy_applied": boundaries_verified,
            "items": rows, "confirmed_error_count": None,
            "limits": "Exclusions define this pilot's正文 scope; teacher policy may require a different scope. Retained items include generic/expository first-person, quotes and unclassified text. Boundary text is not visual proof; appendices after terminal sections may need separate review. Headers, footnotes and image text are not covered."}


def collect_triage(review_dir):
    results = []
    for structure in collect(review_dir):
        source = structure["source"]
        extracted = extract_docx(Path(source["document_path"]), source)
        if extracted["source_sha256"] != source["sha256"] or digest(Path(source["document_path"])) != source["sha256"]:
            raise ValueError("Initial source changed during first-person triage")
        results.append({"source": source, "triage": triage(extracted["body_paragraphs"]),
                        "reader_warnings": extracted["warnings"], "evaluation_role": "retrospective_development_replay"})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", required=True, type=Path)
    review_dir = parser.parse_args().review_dir.resolve()
    results = collect_triage(review_dir)
    output = review_dir / ("initial_first_person_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    save(output / "triage.json", {"documents": results, "svn_requests": 0, "llm_requests": 0,
                                 "independent_accuracy_test": False, "automated_error_judgment_enabled": False})
    lines = ["# 初稿我字线索分流", "", "原始命中保留；按本轮正文范围排除我国、独创性声明、已定位的参考文献和谢辞。",
             "剩余项仍待语境核验，不等于确认错误数。范围是本地试验约定，不改写教师原批注。", ""]
    for result in results:
        source, data = result["source"], result["triage"]
        label = f"{source['sample_id']} r{source['revision']}"
        message = f"{label}：原始我字 {data['raw_character_hits']}；范围/词义排除 {data['excluded_count']}；保留语境候选 {data['retained_context_candidates']}"
        print(message)
        lines += [f"## {label}", "", message, "", f"源文档：[打开](<{Path(source['document_path']).as_posix()}>)", ""]
        for row in data["items"]:
            if row["status"] == "retained_for_context_review":
                lines += [f"- 段落{row['paragraph_index']}，偏移{row['offset']}：{row['context']}", ""]
    lines += ["完整排除项及理由见triage.json；未自动更改论文或认定合格。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("本轮 SVN 请求：0；LLM 请求：0；未启动 Word。")
    print("语境核查报告：", output / "report.md")
    print("完整线索记录：", output / "triage.json")


if __name__ == "__main__":
    main()
