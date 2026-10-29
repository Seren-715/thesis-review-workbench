"""Collect P001/P002 native-table layouts and PDF evidence; no LLM/SVN."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from cross_student_rule_pilot import digest, save
from inspect_initial_structure_pilot import collect

HELPER = Path(__file__).with_name("inspect_word_all_tables.ps1")
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def normalized(text):
    return re.sub(r"[\s\x07]", "", text)


def inventory(source):
    path = Path(source["document_path"])
    if digest(path) != source["sha256"]:
        raise ValueError("Source changed before inventory")
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(W + "body")
    parents = {child: node for node in body.iter() for child in node}
    native = []
    for index, table in enumerate(body.iter(W + "tbl"), 1):
        parent, nesting = parents.get(table), 1
        while parent is not None:
            nesting += parent.tag == W + "tbl"
            if parent.tag in (W + "txbxContent", W + "del", W + "moveFrom"):
                raise ValueError("Deleted/textbox tables require separate visual handling")
            parent = parents.get(parent)
        native.append({"index": index, "nesting_level": nesting,
                       "text": "".join(node.text or "" for node in table.iter(W + "t")),
                       "row_count": len(table.findall(W + "tr"))})
    return {"source": source, "native_tables": native, "native_table_count": len(native),
            "drawing_count": len(list(body.iter(W + "drawing"))),
            "legacy_picture_count": len(list(body.iter(W + "pict"))),
            "object_count": len(list(body.iter(W + "object"))),
            "limits": "XML inventory is not visual verification; pictures may contain tables."}


def prepare(review_dir):
    rows = [inventory(row["source"]) for row in collect(review_dir)
            if row["source"]["sample_id"] in ("P001", "P002")]
    if [r["source"]["sample_id"] for r in rows] != ["P001", "P002"]:
        raise ValueError("Expected exactly P001/P002 initial sources")
    return rows


def validate(audit, plan, pdf):
    source = plan["source"]
    if audit.get("status") != "native_table_layout_collected_visual_pending" or audit.get("source_unchanged") is not True:
        raise ValueError("Word layout collection failed")
    if audit.get("errors") or audit.get("cleanup_errors") or audit.get("layout_changed_during_export") is not False:
        raise ValueError("Word cleanup or layout audit failed")
    if type(audit.get("export_item")) is not int or audit["export_item"] != 7:
        raise ValueError("Expected explicit markup PDF")
    for key in ("source", "source_after"):
        if (Path(audit[key]["path"]).resolve() != Path(source["document_path"]).resolve()
                or audit[key]["sha256"] != source["sha256"]):
            raise ValueError("Word audit source differs from verified manifest")
    if digest(Path(source["document_path"])) != source["sha256"]:
        raise ValueError("Source changed during collection")
    if Path(audit["pdf"]["path"]).resolve() != pdf.resolve() or digest(pdf) != audit["pdf"]["sha256"]:
        raise ValueError("PDF hash/path mismatch")
    before, after, results = audit["tables_before"], audit["tables_after"], audit["table_results"]
    if any(len(rows) != plan["native_table_count"] for rows in (before, after, results)):
        raise ValueError("Word/XML/post-export table coverage differs")
    pages = audit["page_texts"]
    page_map = {page["physical_page"]: page for page in pages}
    if len(page_map) != len(pages):
        raise ValueError("Duplicate page evidence")
    needed = set()
    for original, first, last, result in zip(plan["native_tables"], before, after, results):
        if original["index"] != first["index"] or first["index"] != last["index"] or result["index"] != first["index"]:
            raise ValueError("Table indices mismatch")
        if original["nesting_level"] != first["nesting_level"] or normalized(original["text"]) != normalized(first["text"]):
            raise ValueError("Word table identity differs from original XML")
        if any(first[key] != last[key] for key in
               ("index", "nesting_level", "text", "preceding_text", "first_physical_page", "last_physical_page")):
            raise ValueError("Table changed during PDF export")
        start, end = first["first_physical_page"], first["last_physical_page"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= start + 20:
            raise ValueError("Invalid table page span")
        if type(first["cross_page"]) is not bool or first["cross_page"] != (end > start):
            raise ValueError("Cross-page flag inconsistent")
        if result["first_physical_page"] != start or result["last_physical_page"] != end or result["visual_verified"] is not False:
            raise ValueError("Result span or visual claim invalid")
        needed.update(range(start, end + 1))
        marker_pages = []
        for page in range(start, end + 1):
            evidence = page_map.get(page)
            if evidence is None:
                raise ValueError("Missing page evidence")
            matches = [{"offset": m.start(), "text": m.group()} for m in re.finditer(r"续\s*表", evidence["text"])]
            if matches != evidence["continuation_marker_matches"]:
                raise ValueError("Continuation text evidence inconsistent")
            if page > start and matches:
                marker_pages.append(page)
        expected = "not_applicable_single_page" if start == end else (
            "cross_page_marker_needs_table_association_review" if marker_pages else "cross_page_no_continuation_text_clue")
        if result["result"] != expected or result["continuation_marker_page_clues"] != marker_pages:
            raise ValueError("Table clue does not match page evidence")
    if set(page_map) != needed:
        raise ValueError("Page coverage differs from tables")
    return results


def execute(review_dir):
    plans = prepare(review_dir)
    helper_hash = digest(HELPER)
    output = review_dir / ("remaining_initial_layout_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    run = {"status": "prepared", "plans": plans, "helper_sha256": helper_hash, "results": [], "attempts": [],
           "word_launch_attempts": 0, "svn_requests": 0, "llm_requests": 0, "export_item": 7,
           "visual_verified": False, "independent_accuracy_test": False}
    save(output / "run.json", run)
    code = 1
    try:
        for plan in plans:
            source = plan["source"]
            sample = source["sample_id"]
            pdf, audit_path = output / (sample + "_markup.pdf"), output / (sample + "_layout.json")
            attempt = {"sample_id": sample, "status": "started", "audit_path": str(audit_path)}
            run["attempts"].append(attempt)
            run["word_launch_attempts"] += 1
            save(output / "run.json", run)
            print(f"{sample}：核验全部{plan['native_table_count']}张原生表格分页，导出带批注页面证据……", flush=True)
            process = subprocess.run([str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPER),
                                      "-InputPath", source["document_path"], "-AuditPath", str(audit_path), "-PdfPath", str(pdf)],
                                     capture_output=True, timeout=180)
            if not audit_path.exists():
                raise ValueError(f"{sample} Word exit{process.returncode}, no audit; stop without retry")
            audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
            if process.returncode != 0:
                raise ValueError(f"{sample} failed at {audit.get('stage')}: {audit.get('errors')}; cleanup={audit.get('cleanup_errors')}")
            tables = validate(audit, plan, pdf)
            if digest(HELPER) != helper_hash:
                raise ValueError("Helper changed during run")
            attempt["status"] = "completed"
            cross = sum(row["first_physical_page"] != row["last_physical_page"] for row in tables)
            status = "native_table_layout_collected_visual_pending" if tables else "no_native_tables_images_visual_pending"
            run["results"].append({"source": source, "status": status, "tables": tables, "cross_page_count": cross,
                                   "pdf_path": str(pdf), "pdf_sha256": digest(pdf), "audit_path": str(audit_path),
                                   "audit_sha256": digest(audit_path), "visual_verified": False})
            save(output / "run.json", run)
            print(f"{sample}：原生表格{len(tables)}；跨页{cross}；图片对象仍需视觉核验", flush=True)
        if prepare(review_dir) != plans:
            raise ValueError("Initial provenance/inventory changed before finalization")
        run["status"] = "collected_visual_pending"
        lines = ["# 剩余初稿分页证据", "", "只读检查P001/P002，保留原件及P002原DOC到分析DOCX的转换证据链。",
                 "原生表格数为0不代表图片没有表格；跨页缺文字只是线索。PDF物理页、标题描述和表顺序需渲染核验。", ""]
        for row in run["results"]:
            sample = row["source"]["sample_id"]
            lines += [f"## {sample}", "", f"状态：{row['status']}",
                      f"[页面证据](<{Path(row['pdf_path']).as_posix()}>)", ""]
            for table in row["tables"]:
                lines += [f"- 表序号{table['index']}：物理{table['first_physical_page']}–{table['last_physical_page']}页；{table['result']}"]
            lines += [""]
        (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
        code = 0
    except Exception as error:
        run.update(status="failed_no_retry", error_type=type(error).__name__, error=str(error))
        if run["attempts"] and run["attempts"][-1]["status"] == "started":
            run["attempts"][-1]["status"] = "failed"
        print("停止原因：", str(error))
    finally:
        save(output / "run.json", run)
    print("状态：", run["status"])
    print("本轮SVN请求：0；LLM请求：0。")
    if code == 0:
        print("可读报告：", output / "report.md")
    print("运行记录：", output / "run.json")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, required=True)
    return execute(parser.parse_args().review_dir.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
