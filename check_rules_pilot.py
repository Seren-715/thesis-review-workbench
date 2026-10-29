"""Reviewed two-rule DOCX replay, no API calls and no document modifications."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from docx_comment_reader import extract_docx
from pilot_rule_analysis import build_payload, validate_response


CONCLUSION = re.compile(r"^(?:第[一二三四五六七八九十\d]+章\s*|\d+[.、\s]+)?(?:结论|结论与建议|结束语)$")
BOUNDARY = re.compile(r"^(?:参考文献|谢辞|致谢|附录(?:[A-Z一二三四五六七八九十\d]*)?)$")
LABEL = re.compile(r"公式\s*[（(]\s*(\d+)\s*[-－–]\s*(\d+)\s*[）)]\s*$")
IMAGE_LABEL = re.compile(r"[（(]\s*公式\s*(\d+)\s*[-－–]\s*(\d+)\s*[）)]\s*$")


def read_check_document(path: Path, source: dict) -> dict:
    """Add visible formula/image presence without interpreting image pixels."""
    extracted = extract_docx(path, source)
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    m = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    with ZipFile(path) as package:
        body = ET.fromstring(package.read("word/document.xml")).find(w + "body")
    nodes = list(body.iter(w + "p"))
    if len(nodes) != len(extracted["body_paragraphs"]):
        raise ValueError("Formula media paragraph order differs from the text reader")
    for paragraph, node in zip(extracted["body_paragraphs"], nodes):
        image_ids, math_text = [], []
        def visit(element: ET.Element, inside_math: bool = False) -> None:
            if element is not node and element.tag in {w + "p", w + "del", w + "moveFrom"}:
                return
            inside_math = inside_math or element.tag == m + "oMath"
            if element.tag == a + "blip":
                image_ids.append(element.get(r + "embed") or element.get(r + "link"))
            if inside_math and element.tag == m + "t" and element.text:
                math_text.append(element.text)
            for child in element:
                visit(child, inside_math)
        visit(node)
        paragraph["image_relationship_ids"] = image_ids
        paragraph["image_count"] = len(image_ids)
        paragraph["contains_nonempty_math"] = bool("".join(math_text).strip())
    if file_hash(path) != extracted["source_sha256"]:
        raise ValueError("Source changed while reading formula/image presence")
    return extracted


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def eligible(paragraph: dict) -> bool:
    return not any(paragraph.get(key, False) for key in ("in_table", "in_textbox", "in_deleted"))


def check_first_person(paragraphs: list[dict]) -> dict:
    # Do not strip tabs before matching: a TOC entry with a page number is not a heading.
    headings = [p for p in paragraphs if eligible(p) and "\t" not in p["text"]
                and CONCLUSION.fullmatch(p["text"].strip())]
    result = {"rule_id": "R001", "status": "cannot_check", "hits": [], "hit_count": None,
              "scope": "正文结论章节；每个我字仅计一次，命中为待复核线索"}
    if len(headings) != 1:
        result["reason"] = "未找到唯一的正文结论标题，需要人工指定范围"
        return result
    heading = headings[0]
    ends = [p for p in paragraphs if p["index"] > heading["index"] and eligible(p)
            and "\t" not in p["text"] and BOUNDARY.fullmatch(p["text"].strip())]
    if not ends:
        result["reason"] = "未找到明确的结论结束边界，停止此项检查"
        return result
    end = ends[0]
    selected = [p for p in paragraphs if heading["index"] < p["index"] < end["index"] and eligible(p)]
    if not any(p["text"].strip() for p in selected):
        result["reason"] = "结论范围无可读正文"
        return result
    hits = []
    for paragraph in selected:
        for match in re.finditer("我", paragraph["text"]):
            pos = match.start()
            hits.append({"paragraph_index": paragraph["index"], "offset": pos,
                         "context": paragraph["text"][max(0, pos - 12):pos + 13]})
    result.update(status="checked", heading_paragraph=heading["index"],
                  end_heading_paragraph=end["index"], checked_paragraph_indices=[p["index"] for p in selected],
                  hit_count=len(hits), hits=hits)
    return result


def check_formula_labels(paragraphs: list[dict]) -> dict:
    labels, orphan_labels, uncertain_labels, image_labels, unbound_labels = [], [], [], [], []
    for paragraph in paragraphs:
        if not eligible(paragraph):
            continue
        text = paragraph["text"]
        match = LABEL.search(text) or IMAGE_LABEL.search(text)
        if not match:
            continue
        prefix = text[:match.start()]
        record = {"paragraph_index": paragraph["index"], "label": f"{int(match[1])}-{int(match[2])}",
                  "chapter": int(match[1]), "sequence": int(match[2]), "offset": match.start(),
                  "context": text, "contains_math": paragraph["contains_math"]}
        record["image_relationship_ids"] = paragraph.get("image_relationship_ids", [])
        nonempty_math = paragraph.get("contains_nonempty_math", paragraph["contains_math"])
        if not prefix.strip() and not nonempty_math and paragraph.get("image_count", 0):
            record["status"] = "image_label_needs_visual_review"
            image_labels.append(record)
        elif IMAGE_LABEL.search(text) and not prefix.strip() and not nonempty_math:
            record["status"] = "unbound_label_needs_layout_review"
            unbound_labels.append(record)
        elif not prefix.strip() and not nonempty_math:
            orphan_labels.append(record)
        elif nonempty_math and prefix.strip() and prefix.endswith("\t") and LABEL.search(text):
            # The pilot accepts a trailing, tab-separated label with OMML in the same paragraph.
            labels.append(record)
        else:
            uncertain_labels.append(record)
    groups = defaultdict(list)
    for label in labels:
        groups[label["label"]].append(label)
    duplicate_groups = [{"label": name, "occurrences": occurrences}
                        for name, occurrences in groups.items() if len(occurrences) > 1]
    text_groups = defaultdict(list)
    for label in labels + image_labels + unbound_labels:
        text_groups[label["label"]].append(label)
    text_duplicate_groups = [{"label": name, "occurrences": occurrences}
                             for name, occurrences in text_groups.items() if len(occurrences) > 1]
    previous, sequence_flags = {}, []
    for label in labels:
        prev = previous.get(label["chapter"])
        if prev is not None and label["sequence"] != prev["sequence"] + 1:
            sequence_flags.append({"previous_paragraph": prev["paragraph_index"],
                                   "previous_label": prev["label"], "paragraph_index": label["paragraph_index"],
                                   "label": label["label"], "status": "needs_review"})
        previous[label["chapter"]] = label
    status = "partial" if image_labels or unbound_labels or uncertain_labels else ("checked" if labels else "cannot_check")
    return {"rule_id": "R002", "status": status,
            "scope": "OMML后制表符分隔的公式（章-序号）；图片旁（公式章-序号）只检查保存的编号文字",
            "formula_label_count": len(labels), "labels": labels, "duplicate_group_count": len(duplicate_groups) if labels else None,
            "duplicate_groups": duplicate_groups, "sequence_flags": sequence_flags,
            "orphan_label_count": len(orphan_labels), "orphan_labels": orphan_labels,
            "uncertain_label_count": len(uncertain_labels), "uncertain_labels": uncertain_labels,
            "image_label_count": len(image_labels), "image_labels": image_labels,
            "unbound_label_count": len(unbound_labels), "unbound_labels": unbound_labels,
            "text_label_count": len(labels) + len(image_labels) + len(unbound_labels),
            "text_duplicate_group_count": len(text_duplicate_groups) if text_groups else None,
            "text_duplicate_groups": text_duplicate_groups,
            "interpretation": "检查DOCX保存的编号文本，字段结果未重算；孤立编号不计作独立公式；重复/非递增为疑点，需核查实际公式、版面与编号规范"}


def reviewed_checklist(payload: dict, verified: list[dict]) -> dict:
    records = payload["source_mapping"]
    ids = {(item["ref"], item["category"]): item["candidate_id"] for item in verified}
    return {
        "activation_scope": "P001_local_pilot_only", "commonality": "unverified",
        "independent_student_count": 1, "review_type": "assistant_source_based_review_not_teacher_approval",
        "rules": [
            {"rule_id": "R001", "candidate_ids": [ids[("CMT001", "第一人称表述")]], "category": "第一人称表述",
             "scope": "正文结论，原教师批注覆盖r300 XML段落549与550；新版本重新定位章节",
             "check_method": "按我字检索一次，输出段落/偏移/语境，不自动断言错误",
             "evidence_ids": [records["CMT001"]["evidence_id"]], "status": "pilot_enabled"},
            {"rule_id": "R002", "candidate_ids": [ids[(ref, "公式编号")] for ref in ("CMT002", "CMT003")], "category": "公式编号",
             "scope": "同段OMML后制表符标签；图片旁括号内公式编号为文字线索，图片关联待视觉核查",
             "check_method": "比较规范化编号的重复和相邻顺序；孤立编号单列；图片与跨段编号不自动认定独立公式",
             "evidence_ids": [records[ref]["evidence_id"] for ref in ("CMT002", "CMT003")],
             "status": "pilot_enabled", "evidence_dependence": "same_student_related_comments"},
        ],
        "deferred": [
            {"candidate_id": ids[("CMT002", "公式字体")], "reason": "字体需要视觉与模板依据；公式字体无需与正文字体一致", "status": "needs_review"},
            {"candidate_id": ids[("CMT003", "删除对象不明")], "reason": "r247段119只有编号没有OMML；仍不能据此自动删除，需要版面和教师意图确认", "status": "needs_review"},
        ],
        "corrections": ["C001范围补全549、550段", "C002不采用字体与正文一致这一无依据标准",
                        "C003/C005合并同一规则族，C005不将孤立标签视为独立公式"],
    }


def collect_checks(analysis_dir: Path, current_docx: Path) -> dict:
    analysis_dir = analysis_dir.resolve()
    evidence_dir = analysis_dir.parent
    payload = build_payload(evidence_dir)
    raw = json.loads((analysis_dir / "response.json").read_text(encoding="utf-8-sig"))
    run = json.loads((analysis_dir / "run.json").read_text(encoding="utf-8-sig"))
    candidates = json.loads((analysis_dir / "candidate_rules.json").read_text(encoding="utf-8-sig"))
    if run.get("status") != "validated_candidates" or raw["choices"][0]["finish_reason"] != "stop":
        raise ValueError("Only a complete, validated classification run can be reviewed")
    verified = validate_response(raw["choices"][0]["message"]["content"], payload)
    if verified != candidates["items"]:
        raise ValueError("Saved candidate records differ from the source-bound response")
    sources = {}
    for record in payload["source_mapping"].values():
        sources[record["source"]["sha256"]] = record["source"]
    current_docx = current_docx.resolve()
    if current_docx.suffix.lower() != ".docx":
        raise ValueError("Current comparison file must be a DOCX")
    digest = file_hash(current_docx)
    sources[digest] = {"sample_id": "P001", "revision": None, "version_role": "local_current_comparison_not_initial_draft",
                       "document_path": str(current_docx), "sha256": digest}
    results = []
    for source in sources.values():
        source = dict(source)
        source.setdefault("version_role", "teacher_reviewed_historical_not_initial_draft")
        extracted = read_check_document(Path(source["document_path"]), source)
        allowed_notice = "word/comments.xml is absent; no standard comment bodies extracted"
        if any(warning != allowed_notice for warning in extracted["warnings"]):
            raise ValueError("DOCX reader reports unsupported/incomplete evidence; inspect extraction first")
        results.append({"source": source, "reader_notices": extracted["warnings"],
                        "rules": [check_first_person(extracted["body_paragraphs"]),
                                                   check_formula_labels(extracted["body_paragraphs"])]})
    return {"checklist": reviewed_checklist(payload, verified), "documents": results, "llm_requests": 0,
            "validation_role": "historical_and_current_replay_not_initial_draft_or_independent_accuracy_test",
            "classification_usage": run["usage"],
            "input_hashes": {name: file_hash(analysis_dir / name) for name in ("response.json", "run.json", "candidate_rules.json")}}


def main() -> int:
    parser = argparse.ArgumentParser(description="本地验证两个修订后的试验规则，不调用LLM")
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--current-docx", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = collect_checks(args.analysis_dir, args.current_docx)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("来源或输入核验失败，未生成检查结果：", exc)
        return 2
    output = args.analysis_dir.resolve() / ("rule_check_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    save_json(output / "reviewed_checklist.json", result["checklist"])
    save_json(output / "checks.json", result)
    lines = ["# 两个试验规则的本地回放", "", "位置是从1开始的XML段落索引，不是Word页码。",
             "仅回放同一学生的教师批注版本与当前稿，不是初稿检验或独立准确率测试。",
             "未检出仅表示此工具在限定格式和范围内未检出；公式、图片、字体和分页不由本轮判定。", "",
             "| 版本 | 我字线索 | 公式检查状态 | OMML标签 | 重复编号组 | 孤立编号 | 图片编号线索 | 未绑定编号 |",
             "|---|---:|---|---:|---:|---:|---:|---:|"]
    details = []
    for doc in result["documents"]:
        first, formula = doc["rules"]
        name = "当前稿" if doc["source"]["revision"] is None else f"r{doc['source']['revision']}"
        print(f"{name}：我字线索 {first['hit_count']}；公式状态 {formula['status']}；OMML标签 {formula['formula_label_count']}；重复组 {formula['duplicate_group_count']}；孤立编号 {formula['orphan_label_count']}")
        lines.append(f"| {name} | {first['hit_count']} | {formula['status']} | {formula['formula_label_count']} | {formula['duplicate_group_count']} | {formula['orphan_label_count']} | {formula['image_label_count']} | {formula['unbound_label_count']} |")
        details.extend(["", f"## {name} 位置证据", "", "```json", json.dumps(doc["rules"], ensure_ascii=False, indent=2), "```", ""])
    lines.extend(details)
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("本轮 LLM 请求：0；2条规则仅用于本样本试验，2项仍待人工核验。")
    print("可读报告：", output / "report.md")
    print("检查结果：", output / "checks.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
