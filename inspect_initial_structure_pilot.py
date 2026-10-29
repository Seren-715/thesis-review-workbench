"""Read inherited Word outline levels in three verified initial samples; no API."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from cross_student_rule_pilot import digest, save
from docx_comment_reader import extract_docx
from recheck_initial_pilot import collect_recheck
from review_cross_student_pilot import INITIAL, prepare_review
from legacy_doc_provenance import validate_lineage

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def inspect_document(source):
    path = Path(source["document_path"])
    if digest(path) != source["sha256"]:
        raise ValueError("Initial document hash differs")
    extracted = extract_docx(path, source)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        styles_root = ET.fromstring(archive.read("word/styles.xml"))
    styles = {style.get(W + "styleId"): style for style in styles_root.findall(W + "style")}
    default_id = next((sid for sid, node in styles.items() if node.get(W + "type") == "paragraph" and node.get(W + "default") == "1"), None)
    def inherited_level(sid, seen=None):
        seen = set() if seen is None else seen
        if sid is None:
            return None, []
        if sid in seen:
            raise ValueError("Cyclic paragraph style inheritance")
        seen.add(sid)
        style = styles.get(sid)
        if style is None:
            return None, [{"style_id": sid, "missing": True}]
        name = style.find(W + "name")
        link = {"style_id": sid, "style_name": name.get(W + "val") if name is not None else None}
        outline = style.find(W + "pPr/" + W + "outlineLvl")
        if outline is not None:
            return int(outline.get(W + "val")), [link]
        based = style.find(W + "basedOn")
        level, chain = inherited_level(based.get(W + "val") if based is not None else None, seen)
        return level, [link] + chain
    nodes = list(root.find(W + "body").iter(W + "p"))
    if len(nodes) != len(extracted["body_paragraphs"]):
        raise ValueError("Paragraph order differs from source text reader")
    headings, active_chapter, chapter_count = [], None, 0
    for node, paragraph in zip(nodes, extracted["body_paragraphs"]):
        if paragraph["in_deleted"] or paragraph["in_table"] or paragraph["in_textbox"]:
            continue
        text = paragraph["text"].strip()
        if not text or "\t" in text:
            continue
        properties = node.find(W + "pPr")
        style = properties.find(W + "pStyle") if properties is not None else None
        sid = style.get(W + "val") if style is not None else default_id
        direct = properties.find(W + "outlineLvl") if properties is not None else None
        level, chain = inherited_level(sid)
        origin = "inherited_style_outline"
        if direct is not None:
            level, origin = int(direct.get(W + "val")), "direct_paragraph_outline"
        if level is None or level >= 9:
            continue
        if level == 0:
            chapter_count += 1
            active_chapter = {"paragraph_index": paragraph["index"], "text": text}
        headings.append({"paragraph_index": paragraph["index"], "text": text, "outline_level": level,
                         "style_chain": chain, "level_origin": origin, "parent_chapter": active_chapter,
                         "interpretation": "stored_Word_outline_structure_not_visual_layout_validation"})
    relevant = [h for h in headings if "需求分析" in h["text"] or "系统设计" in h["text"] or "系统总体设计" in h["text"]]
    if digest(path) != source["sha256"]:
        raise ValueError("Initial changed during structural inspection")
    return {"source": source, "status": "outline_structure_extracted" if chapter_count else "cannot_establish_chapter_structure",
            "chapter_level_count": chapter_count, "relevant_headings": relevant, "all_outline_headings": headings,
            "confirmed_error_count": None, "scope_applicability": "requires teacher-policy and thesis-type review",
            "limitations": "Reflects saved outline levels only; manually styled titles without outline metadata may be missed; numbering, fields and visual correctness are not validated."}


def collect(review_dir):
    fresh, _ = prepare_review(review_dir.parent)
    if json.loads((review_dir / "reviewed_rules.json").read_text(encoding="utf-8")) != fresh:
        raise ValueError("Reviewed checklist changed")
    sources = [collect_recheck(INITIAL)["source"]]
    for sample, name, status in (
        ("P002", "P002_initial_20261007_122556_603906", "exported_audited_clue_replay_completed"),
        ("P003", "P003_initial_20261007_122122_606899", "exported_clue_replay_completed")):
        folder = review_dir / name
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        checks = json.loads((folder / "checks.json").read_text(encoding="utf-8"))
        source = manifest["source"]
        if manifest["status"] != status or source != checks["source"] or source["sample_id"] != sample:
            raise ValueError("Initial manifest/check source mismatch")
        if Path(source["document_path"]).resolve().parent != folder.resolve():
            raise ValueError("Initial document escapes its output directory")
        if sample == "P002":
            validate_lineage(folder, {**source, "exported_path": source["document_path"]})
        sources.append(source)
    return [inspect_document(source) for source in sources]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", required=True, type=Path)
    review_dir = parser.parse_args().review_dir.resolve()
    results = collect(review_dir)
    output = review_dir / ("initial_structure_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    save(output / "structure.json", {"documents": results, "svn_requests": 0, "llm_requests": 0,
                                      "independent_accuracy_test": False})
    lines = ["# 初稿章节结构核验", "", "以Word保存的大纲层级区分章标题、下一层标题和正文。仍需确认教师要求的适用范围，不能作为通用论文规范。",
             "层级0表示最高大纲层级，层级1表示下一层；无大纲的手动标题可能漏检。未验证编号、分页或续表。", ""]
    for result in results:
        source = result["source"]
        lines += [f"## {source['sample_id']} r{source['revision']}", "", f"状态：{result['status']}", ""]
        for heading in result["relevant_headings"]:
            parent = heading["parent_chapter"]
            lines += [f"- 段落{heading['paragraph_index']}，大纲层级{heading['outline_level']}：{heading['text']}",
                      f"  所属最高层标题：{parent['text'] if parent else '未确定'}", ""]
        print(f"{source['sample_id']} r{source['revision']}：相关大纲标题 {len(result['relevant_headings'])}；状态 {result['status']}")
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("本轮 SVN 请求：0；LLM 请求：0；未启动 Word。")
    print("结构报告：", output / "report.md")
    print("结构证据：", output / "structure.json")


if __name__ == "__main__":
    main()
