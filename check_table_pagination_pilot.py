"""Collect Word layout evidence for P003 table4-1 in r246/r249; no LLM/SVN."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess

from cross_student_rule_pilot import digest, save
from extract_comments_pilot import collect_batch
from inspect_initial_structure_pilot import collect

HELPER = Path(__file__).with_name("inspect_word_table_pages.ps1")
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
CAPTION = "表4-1 所有股票历史数据表结构"
TEACHER_BATCH = Path(r"D:\svn_agent\data\svn_exports\cross_student_20261007_113217_741043\P003")


def prepare_sources(review_dir):
    initial = next(row["source"] for row in collect(review_dir) if row["source"]["sample_id"] == "P003")
    teacher_evidence = collect_batch(TEACHER_BATCH)
    teacher = next(row["source"] for row in teacher_evidence["documents"] if row["source"]["revision"] == 249)
    if initial["revision"] != 246 or teacher["sample_id"] != "P003":
        raise ValueError("Unexpected versions in table pagination comparison")
    anchor = next(row for row in teacher_evidence["comments"] if row["source"]["revision"] == 249 and row["comment"]["id"] == "15")
    return [initial, teacher], {"evidence_id": anchor["evidence_id"], "teacher_quote": anchor["comment"]["text"],
                              "original_anchor": anchor["anchor"]["text"], "observed_table": "表4-1",
                              "association": "user-confirmed observed location; original teacher anchor is preserved, not rebound"}


def validate_audit(audit, source, pdf):
    if audit.get("status") != "layout_evidence_collected_visual_pending" or not audit.get("source_unchanged"):
        raise ValueError("Word layout collection failed; inspect the audit, no retry")
    if audit.get("errors") or audit.get("cleanup_errors"):
        raise ValueError("Word audit has errors")
    if audit.get("layout_changed_during_export") is not False or not audit.get("layout_after_export"):
        raise ValueError("Missing or inconsistent post-export pagination evidence")
    after = audit["layout_after_export"]
    if (after["first_physical_page"] != audit["table"]["first_physical_page"]
            or after["last_physical_page"] != audit["table"]["last_physical_page"]
            or after["table_text"] != audit["table"]["text"]):
        raise ValueError("Table changed during PDF export")
    if (audit["source"]["sha256"] != source["sha256"] or audit["source_after"]["sha256"] != source["sha256"]
            or Path(audit["source"]["path"]).resolve() != Path(source["document_path"]).resolve()
            or digest(Path(source["document_path"])) != source["sha256"]):
        raise ValueError("Original source changed or audit belongs to a different file")
    if Path(audit["pdf"]["path"]).resolve() != pdf.resolve() or digest(pdf) != audit["pdf"]["sha256"]:
        raise ValueError("PDF hash/path differs from audit")
    table = audit["table"]
    first, last = table["first_physical_page"], table["last_physical_page"]
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= first + 10:
        raise ValueError("Invalid physical page range")
    if type(table["cross_page"]) is not bool or table["cross_page"] != (first != last):
        raise ValueError("Page span and cross-page result disagree")
    pages = audit["page_texts"]
    if [page["physical_page"] for page in pages] != list(range(first, last + 1)):
        raise ValueError("Missing or unordered page text evidence")
    expected = "not_applicable_single_page" if first == last else (
        "cross_page_marker_present_needs_table_association_review" if table["continuation_marker_page_clues"]
        else "cross_page_no_continuation_text_clue")
    if table["result"] != expected:
        raise ValueError("Inconsistent page/marker summary")
    if audit["caption"] != CAPTION or len(audit["caption_matches"]) != 1:
        raise ValueError("Table caption is not uniquely bound")
    return table


def execute(review_dir, include_markup=False):
    sources, anchor = prepare_sources(review_dir)
    helper_hash = digest(HELPER)
    output = review_dir / ("table_pagination_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    run = {"status": "prepared", "source_versions": sources, "teacher_anchor": anchor,
           "helper_sha256": helper_hash, "word_launch_attempts": 0, "svn_requests": 0, "llm_requests": 0,
           "attempts": [], "results": [], "visual_verified": False, "independent_accuracy_test": False,
           "export_item": 7 if include_markup else 0,
           "export_mode": "document_with_markup" if include_markup else "document_content_without_markup",
           "comparison": "earlier student r246 vs later teacher r249; not a post-review repair comparison"}
    save(output / "run.json", run)
    code = 1
    try:
        for source in sources:
            revision = source["revision"]
            audit_path, pdf = output / f"r{revision}_layout.json", output / f"r{revision}_document.pdf"
            attempt = {"revision": revision, "status": "started", "audit_path": str(audit_path)}
            run["attempts"].append(attempt)
            run["word_launch_attempts"] += 1
            save(output / "run.json", run)
            print(f"使用 Word 读取 r{revision} 表4-1分页并导出页面证据……", flush=True)
            result = subprocess.run([str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPER),
                                     "-InputPath", source["document_path"], "-AuditPath", str(audit_path),
                                     "-PdfPath", str(pdf), "-Caption", CAPTION, "-ExportItem", str(run["export_item"])], capture_output=True, timeout=180)
            if not audit_path.exists():
                raise ValueError(f"Word helper exit {result.returncode} without audit; stop")
            audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
            if result.returncode != 0:
                raise ValueError(f"Word r{revision} failed at {audit.get('stage')}: {audit.get('errors')}; cleanup={audit.get('cleanup_errors')}")
            if audit.get("export_item") != run["export_item"]:
                raise ValueError("Export mode does not match the requested mode")
            table = validate_audit(audit, source, pdf)
            if digest(HELPER) != helper_hash:
                raise ValueError("Word helper changed during collection")
            attempt["status"] = "completed"
            row = {"source": source, "table": table, "pdf_path": str(pdf), "pdf_sha256": digest(pdf),
                   "audit_path": str(audit_path), "audit_sha256": digest(audit_path), "visual_verified": False,
                   "user_expectation": "single_page" if revision == 246 else "cross_page_without_continuation_label",
                   "matches_user_page_span": table["cross_page"] == (revision == 249)}
            run["results"].append(row)
            save(output / "run.json", run)
            print(f"r{revision}：物理页 {table['first_physical_page']}–{table['last_physical_page']}；{table['result']}", flush=True)
        current, _ = prepare_sources(review_dir)
        if current != sources:
            raise ValueError("Sources changed before finalization")
        run["status"] = "layout_evidence_collected_visual_pending"
        lines = ["# 表4-1 Word分页证据", "", "两版本是早期学生稿r246和教师稿r249，不是后续修复验证。",
                 f"导出模式：{run['export_mode']}。Word物理页从文件第一页计数，与印刷页码可能不同。",
                 "本机字体、打印机、修订及字段显示可能影响布局。PDF与Word页号相同仍需核对表格身份，不能自动验收。",
                 "后续页全文未检出续表只是文字线索；图片标识、页眉、其他表格归属或PDF分页差异必须视觉复核。", ""]
        for row in run["results"]:
            table, revision = row["table"], row["source"]["revision"]
            lines += [f"## r{revision}", "", f"物理页：{table['first_physical_page']}–{table['last_physical_page']}；结果：{table['result']}",
                      f"导出前Word页跨度与人工预期一致：{row['matches_user_page_span']}（不代表PDF已核验）",
                      f"页面证据：[打开PDF](<{Path(row['pdf_path']).as_posix()}>)", ""]
        (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
        code = 0
    except Exception as error:
        run.update(status="failed_no_retry", error_type=type(error).__name__, error=str(error))
        if run["attempts"] and run["attempts"][-1]["status"] == "started":
            run["attempts"][-1]["status"] = "failed"
        print("停止原因：", run["error"])
    finally:
        save(output / "run.json", run)
    print("状态：", run["status"])
    print("本轮 SVN 请求：0；LLM 请求：0。")
    if code == 0:
        print("可读报告：", output / "report.md")
    print("运行记录：", output / "run.json")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--include-markup", action="store_true", help="Diagnostic comparison: export PDF with comments/revision markup")
    args = parser.parse_args()
    return execute(args.review_dir.resolve(), args.include_markup)


if __name__ == "__main__":
    raise SystemExit(main())
