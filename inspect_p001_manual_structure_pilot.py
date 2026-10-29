"""Locate P001 r194 manual title candidates without establishing chapter compliance.

Only OOXML is read. This does not render Word, evaluate numbering/fields, infer
thesis type, or adjudicate the requirements/design chapter rule.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from cross_student_rule_pilot import digest, save
from docx_comment_reader import TEXT_POLICY, extract_docx
from inspect_initial_structure_pilot import collect

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
EXPECTED_SHA = "1def8d253017061149444173cfd84e9355d0042f12e8877073934284e93954c1"
CHAPTER = re.compile(r"^第([0-9一二三四五六七八九十百]{1,3})章[ \u3000]+([^\n\r\t]{1,60})$")
SECTION = re.compile(r"^([0-9]{1,2}(?:\.[0-9]{1,2}){1,3})\.?[ \u3000]+([^\n\r\t]{1,60})$")
TOC_STYLE = re.compile(r"^(?:toc[ _]*[1-9]?|目录[ _]*[1-9]?)$", re.I)
LIMITS = (
    "Candidate text numbering and saved formatting are evidence, not confirmed chapter structure. "
    "No rendering, OCR, field evaluation, numbering evaluation, thesis-type decision or rule applicability decision. "
    "No candidate keyword match is not proof of missing required chapters. Paragraph indices are XML order, not pages."
)


def _attrs(element):
    return {key.rsplit("}", 1)[-1]: value for key, value in element.attrib.items()}


def _properties(element):
    if element is None:
        return None
    return [{"tag": child.tag.rsplit("}", 1)[-1], "attributes": _attrs(child),
             "children": _properties(child) if list(child) else []} for child in element]


def _style_chain(node, styles, default_id):
    pp = node.find(W + "pPr")
    style = pp.find(W + "pStyle") if pp is not None else None
    sid = style.get(W + "val") if style is not None else default_id
    chain, seen = [], set()
    while sid is not None:
        if sid in seen:
            raise ValueError("Cyclic paragraph style inheritance")
        seen.add(sid)
        definition = styles.get(sid)
        if definition is None:
            chain.append({"style_id": sid, "missing": True})
            break
        name = definition.find(W + "name")
        chain.append({"style_id": sid, "style_name": name.get(W + "val") if name is not None else None,
                      "pPr": _properties(definition.find(W + "pPr")),
                      "rPr": _properties(definition.find(W + "rPr"))})
        based = definition.find(W + "basedOn")
        sid = based.get(W + "val") if based is not None else None
    return chain


def _toc_paragraphs(root, nodes, parents, chains):
    """Identify explicit TOC metadata, plus a uniquely bounded directory section.

    Unbounded plain '目录' is only an ambiguity marker; it never excludes all
    following body paragraphs. A trailing tab alone is not a TOC page number.
    """
    reasons = {index: [] for index in range(1, len(nodes) + 1)}
    positions = {id(node): index for index, node in enumerate(nodes, 1)}
    stack = []

    def walk(node, paragraph_index=None):
        if node.tag == W + "p":
            paragraph_index = positions[id(node)]
        if node.tag == W + "fldChar":
            kind = node.get(W + "fldCharType")
            if kind == "begin":
                stack.append({"toc": False, "instruction": ""})
            elif kind == "end" and stack:
                if paragraph_index and any(frame["toc"] for frame in stack):
                    reasons[paragraph_index].append("toc_complex_field")
                stack.pop()
        elif node.tag == W + "instrText" and stack:
            stack[-1]["instruction"] += node.text or ""
            if re.match(r"^\s*TOC(?:\s|$)", stack[-1]["instruction"], re.I):
                stack[-1]["toc"] = True
        if paragraph_index and any(frame["toc"] for frame in stack):
            reasons[paragraph_index].append("toc_complex_field")
        for child in node:
            walk(child, paragraph_index)

    walk(root.find(W + "body"))
    for index, node in enumerate(nodes, 1):
        if any(TOC_STYLE.fullmatch(item.get("style_name") or "") for item in chains[index]):
            reasons[index].append("toc_paragraph_style")
        current = node
        while current is not None:
            if current.tag == W + "fldSimple" and re.match(r"^\s*TOC(?:\s|$)", current.get(W + "instr", ""), re.I):
                reasons[index].append("toc_simple_field")
            if current.tag == W + "sdt":
                galleries = list(current.iter(W + "docPartGallery"))
                if any((g.get(W + "val") or "").lower() in ("table of contents", "目录") for g in galleries):
                    reasons[index].append("toc_content_control")
            current = parents.get(current)
        for link in node.iter(W + "hyperlink"):
            if (link.get(W + "anchor") or "").startswith("_Toc"):
                reasons[index].append("toc_destination_hyperlink")
    return reasons


def analyze_parts(parts, paragraphs, saved_outline_headings):
    root = ET.fromstring(parts["word/document.xml"])
    body = root.find(W + "body")
    if body is None:
        raise ValueError("Missing body")
    nodes = list(body.iter(W + "p"))
    if len(nodes) != len(paragraphs) or [p["index"] for p in paragraphs] != list(range(1, len(nodes) + 1)):
        raise ValueError("Reader/XML paragraph mapping mismatch")
    parents = {child: parent for parent in root.iter() for child in parent}
    styles_root = ET.fromstring(parts["word/styles.xml"])
    styles = {s.get(W + "styleId"): s for s in styles_root.findall(W + "style")}
    default_id = next((sid for sid, s in styles.items() if s.get(W + "type") == "paragraph" and s.get(W + "default") == "1"), None)
    chains = {index: _style_chain(node, styles, default_id) for index, node in enumerate(nodes, 1)}
    toc = _toc_paragraphs(root, nodes, parents, chains)
    directory_markers = [p["index"] for p in paragraphs if re.sub(r"\s", "", p["text"]) == "目录"
                         and not any(p[key] for key in ("in_deleted", "in_table", "in_textbox"))]
    directory_ranges = []
    if len(directory_markers) == 1:
        start = directory_markers[0]
        # A saved section boundary is an explicit local delimiter, not a visual
        # assumption that every subsequent chapter-like line belongs to a TOC.
        end = next((index for index in range(start, len(nodes) + 1)
                    if nodes[index - 1].find(W + "pPr/" + W + "sectPr") is not None), None)
        region_is_supported = end is not None and all(
            not p["text"].strip() or toc[p["index"]]
            or re.fullmatch(r"[^\n]+\t\s*(?:[0-9]+|[ivxlcdmIVXLCDM]+)\s*", p["text"])
            for p in paragraphs[start:end]
        )
        if region_is_supported:
            for index in range(start, end + 1):
                toc[index].append("directory_heading_to_saved_section_boundary")
            directory_ranges.append({"start_paragraph_index": start, "end_paragraph_index": end,
                                     "basis": "unique_directory_heading_and_next_saved_section_boundary",
                                     "nonempty_intervening_indices": [p["index"] for p in paragraphs[start:end]
                                                                      if p["text"].strip()],
                                     "interpretation": "local_directory_region_not_a_confirmed_TOC_generation_result"})
    saved = {h["paragraph_index"]: h for h in saved_outline_headings}
    candidates, excluded = [], []
    for node, paragraph in zip(nodes, paragraphs):
        index, raw = paragraph["index"], paragraph["text"]
        text = raw.strip()
        matching_text = text.split("\t", 1)[0].strip() if "\t" in text else text
        match = CHAPTER.fullmatch(matching_text)
        kind = "literal_chapter_number" if match else "literal_section_number"
        if not match:
            match = SECTION.fullmatch(matching_text)
        if not match and index not in saved:
            continue
        record = {"paragraph_index": index, "original_text": raw, "normalized_candidate_text": text,
                  "evidence_locator": f"word/document.xml::body_descendant_p[{index}]"}
        rejection = list(dict.fromkeys(toc[index]))
        rejection += [key for key in ("in_deleted", "in_table", "in_textbox") if paragraph[key]]
        # Internal tabs may separate a title from a cached/manual TOC page.
        # Trailing tabs (e.g. actual P001 3.1/5.1/5.2) are not exclusions.
        if "\t" in text:
            rejection.append("internal_tab_ambiguous_numbered_line")
        if rejection:
            excluded.append(record | {"exclusion_reasons": rejection})
            continue
        pp = node.find(W + "pPr")
        num = pp.find(W + "numPr") if pp is not None else None
        num_id = num.find(W + "numId") if num is not None else None
        nval = num_id.get(W + "val") if num_id is not None else None
        typography = []
        for run in node.iter(W + "r"):
            if any(ancestor.tag in (W + "del", W + "moveFrom") for ancestor in _ancestors(run, parents)):
                continue
            run_text = "".join(t.text or "" for t in run.iter(W + "t"))
            if run_text:
                typography.append({"text": run_text, "rPr": _properties(run.find(W + "rPr"))})
        candidates.append(record | {
            "kind": kind if match else "saved_outline_only",
            "literal_number": match.group(1) if match else None,
            "title_text": match.group(2) if match else text,
            "text_numbering_depth_hint": (1 if kind == "literal_chapter_number" else len(match.group(1).split("."))) if match else None,
            "candidate_status": "requires_manual_review",
            "saved_outline_evidence": saved.get(index),
            "saved_paragraph_properties": _properties(pp), "style_chain": chains[index],
            "run_typography": typography,
            "numbering": {"direct_num_id": nval,
                          "interpretation": "disabled_num_id_zero" if nval == "0" else "not_evaluated",
                          "numbering_part_present": "word/numbering.xml" in parts},
            "paragraph_xml": ET.tostring(node, encoding="unicode"),
        })
    for candidate in candidates:
        index = candidate["paragraph_index"]
        candidate["context"] = {"before": [{"paragraph_index": p["index"], "text": p["text"]}
                                            for p in paragraphs[max(0, index - 3):index - 1]],
                                "after": [{"paragraph_index": p["index"], "text": p["text"]}
                                           for p in paragraphs[index:index + 2]]}
    return {"candidates": candidates, "excluded_numbered_or_outline_lines": excluded,
            "directory_evidence": {"heading_indices": directory_markers, "bounded_regions": directory_ranges,
                                   "explicit_toc_metadata_indices": [index for index, why in toc.items()
                                                                     if any(reason != "directory_heading_to_saved_section_boundary" for reason in why)],
                                   "unbounded_directory_marker": bool(directory_markers and not directory_ranges)},
            "part_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in parts.items()},
            "confirmed_error_count": None, "confirmed_chapter_count": None,
            "scope_applicability": "scope_review", "status": "manual_heading_candidates_scope_review", "limits": LIMITS}


def _ancestors(node, parents):
    current = parents.get(node)
    while current is not None:
        yield current
        current = parents.get(current)


def collect_manual_structure(review_dir):
    rows = [row for row in collect(Path(review_dir)) if row["source"]["sample_id"] == "P001"]
    if len(rows) != 1:
        raise ValueError("Expected exactly one source-verified P001")
    original = rows[0]
    source = original["source"]
    if type(source["revision"]) is not int or source["revision"] != 194 or source["sha256"] != EXPECTED_SHA:
        raise ValueError("This manual-structure pilot is pinned to P001 r194")
    path = Path(source["document_path"])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != source["sha256"]:
        raise ValueError("Initial source changed")
    extracted = extract_docx(path, source)
    with ZipFile(BytesIO(data)) as package:
        if len(package.namelist()) != len(set(package.namelist())):
            raise ValueError("Duplicate ZIP parts")
        parts = {name: package.read(name) for name in ("word/document.xml", "word/styles.xml", "word/numbering.xml")
                 if name in package.namelist()}
    result = analyze_parts(parts, extracted["body_paragraphs"], original["all_outline_headings"])
    if digest(path) != source["sha256"]:
        raise ValueError("Initial changed during inspection")
    result.update(source=source, original_inspector_status=original["status"],
                  original_saved_outline_headings=original["all_outline_headings"],
                  source_unchanged=True, xml_text_policy=TEXT_POLICY,
                  relevant_candidates=[c for c in result["candidates"] if any(term in c["title_text"] for term in ("需求分析", "系统设计", "系统总体设计"))],
                  scope_review_evidence=[{"paragraph_index": p["index"], "text": p["text"]}
                                         for p in extracted["body_paragraphs"] if p["index"] in (10, 43, 44)],
                  scope_review_note="Title/abstract discuss a price-prediction model; this is evidence for applicability review, not an automated non-system-thesis verdict.",
                  svn_requests=0, word_launches=0, llm_requests=0, independent_accuracy_test=False)
    return result, parts


def self_test():
    def inspect(texts, wrappers=None, prefix=""):
        wrappers = wrappers or {}
        body = prefix + "".join(wrappers.get(i, f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>") for i, text in enumerate(texts, 1))
        raw = f'<w:document xmlns:w="{W[1:-1]}"><w:body>{body}</w:body></w:document>'.encode()
        rows = [{"index": i, "text": text, "in_deleted": False, "in_table": False, "in_textbox": False}
                for i, text in enumerate(texts, 1)]
        parts = {"word/document.xml": raw, "word/styles.xml": f'<w:styles xmlns:w="{W[1:-1]}"/>'.encode()}
        return analyze_parts(parts, rows, [])
    result = inspect(["3.1. 数据集介绍\t", "5.1 研究总结\t", "1.1 标题\t1", "1.23 普通句子。"])
    assert [c["paragraph_index"] for c in result["candidates"]][:2] == [1, 2]
    assert result["confirmed_chapter_count"] is None and result["confirmed_error_count"] is None
    assert result["excluded_numbered_or_outline_lines"][0]["paragraph_index"] == 3
    result = inspect(["第1章 绪论", "第1章 绪论"], {1: '<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:instrText> TOC \\o "1-3" </w:instrText><w:fldChar w:fldCharType="separate"/><w:t>第1章 绪论</w:t><w:fldChar w:fldCharType="end"/></w:r></w:p>'})
    assert [c["paragraph_index"] for c in result["candidates"]] == [2]
    result = inspect(["目录", "第1章 绪论\t1", "", "第1章 绪论"], {3: '<w:p><w:pPr><w:sectPr/></w:pPr></w:p>'})
    assert [c["paragraph_index"] for c in result["candidates"]] == [4]
    assert result["excluded_numbered_or_outline_lines"][0]["paragraph_index"] == 2
    result = inspect(["目录", "第1章 绪论"])
    assert result["directory_evidence"]["unbounded_directory_marker"] is True
    assert len(result["candidates"]) == 1
    print("离线边界自检通过：末尾tab保留、内部tab疑点不确认为标题、TOC字段/节断目录分离、无界目录不吞正文、无错误/章数判定。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.review_dir is None:
        parser.error("--review-dir is required unless --self-test")
    review_dir = args.review_dir.resolve()
    result, parts = collect_manual_structure(review_dir)
    output = review_dir / ("P001_manual_structure_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    for name, data in parts.items():
        destination = output / "source_parts" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    result["source_part_exports"] = [{"part": name, "path": str(output / "source_parts" / name),
                                      "sha256": result["part_sha256"][name]} for name in parts]
    result["script_sha256"] = digest(Path(__file__))
    save(output / "structure_candidates.json", result)
    counts = Counter(candidate["kind"] for candidate in result["candidates"])
    lines = ["# P001 r194 手动标题证据补查", "", "状态：scope_review；所有手动标题仍是待人工核验候选。",
             f"原大纲检查：{result['original_inspector_status']}；本轮未修改该结论。",
             f"源文件：[P001 r194](<{Path(result['source']['document_path']).as_posix()}>)",
             f"源SHA256：{result['source']['sha256']}", "",
             "读取OOXML正文、样式和字面编号，保留原XML附件；未渲染Word、评价目录页码或计算自动编号。",
             "段落号按XML顺序，不是页码。末尾tab不等于目录；显式目录元数据、表内/文本框/删除内容及内部tab歧义不进入标题候选。", "",
             f"候选共{len(result['candidates'])}项，字面章号{counts['literal_chapter_number']}项；这不是已确认章数。",
             f"需求分析/系统设计字样的标题候选：{len(result['relevant_candidates'])}；不能据此认定缺章或不适用。", "",
             "| XML段落 | 原文字面标题 | 来源线索 | 已保存大纲 |", "|---|---|---|---|"]
    for candidate in result["candidates"]:
        stored = candidate["saved_outline_evidence"]
        outline = str(stored["outline_level"]) if stored else "无"
        text = candidate["normalized_candidate_text"].replace("|", "\\|")
        lines.append(f"| {candidate['paragraph_index']} | {text} | {candidate['kind']} | {outline} |")
    lines += ["", "目录边界证据：", json.dumps(result["directory_evidence"], ensure_ascii=False), "",
              "题目/摘要讨论股价预测模型，供检查项适用范围复核；程序不据此判断系统类/非系统类，不自动判拆章规则通过或失败。", "",
              "原DOCX与原检查器未修改。本轮 SVN/Word/LLM 调用均为0；不包含视觉核验或独立准确率评价。"]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"P001 r194：标题候选{len(result['candidates'])}；字面章号候选{counts['literal_chapter_number']}；scope_review。")
    print("报告：", output / "report.md")
    print("定位证据：", output / "structure_candidates.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
