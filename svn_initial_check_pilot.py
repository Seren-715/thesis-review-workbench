"""Export one log-identified initial draft and run two offline pilot checks."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from check_rules_pilot import collect_checks, check_first_person, check_formula_labels, file_hash, read_check_document, save_json
from svn_export_pilot import STUDENT, run_svn


def select_initial(batch: Path) -> dict:
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("sample_id") != "P001" or manifest.get("teacher_author") != "mayy":
        raise ValueError("This pilot requires the verified P001/mayy export batch")
    if manifest.get("snapshot_revision") != 502 or manifest.get("path_prefix") != f"/{STUDENT}/论文/":
        raise ValueError("This pilot requires the reviewed P001 path and snapshot 502")
    thesis_paths = {item["repository_path"] for item in manifest["files"] if item.get("status") == "exported"}
    if len(thesis_paths) != 1:
        raise ValueError("Expected one historical thesis path, not multiple unrelated documents")
    thesis_path = next(iter(thesis_paths))
    if not thesis_path.startswith(manifest["path_prefix"]) or not thesis_path.endswith(".docx"):
        raise ValueError("Historical thesis is outside the expected DOCX directory")
    entries = ET.fromstring((batch / "svn_log.xml").read_bytes())
    matches = []
    for entry in entries.findall("logentry"):
        for path in entry.findall("paths/path"):
            if path.text != thesis_path or path.get("kind") != "file" or path.get("action") not in {"A", "M", "R"}:
                continue
            matches.append({"revision": int(entry.attrib["revision"]), "svn_author": entry.findtext("author"),
                            "commit_time": entry.findtext("date"), "commit_message": entry.findtext("msg") or "",
                            "action": path.get("action"), "copyfrom_path": path.get("copyfrom-path"),
                            "copyfrom_revision": path.get("copyfrom-rev"), "repository_path": thesis_path})
    matches.sort(key=lambda item: item["revision"])
    for item in manifest["files"]:
        if item.get("status") != "exported":
            continue
        corresponding = [entry for entry in matches if entry["revision"] == item["revision"]]
        if item["author"] != "mayy" or len(corresponding) != 1 or corresponding[0]["svn_author"] != item["author"] or corresponding[0]["commit_time"] != item["commit_time"]:
            raise ValueError("Saved log and teacher export manifest do not agree")
    students = [item for item in matches if item["svn_author"] == "huangfp"]
    teachers = [item for item in matches if item["svn_author"] == "mayy"]
    if not students or not teachers:
        raise ValueError("Missing student initial submission or teacher review evidence")
    selected = students[0]
    if selected["action"] != "A" or selected["copyfrom_path"] or selected["copyfrom_revision"]:
        raise ValueError("Earliest student file was copied/replaced; cannot label it an initial draft automatically")
    if selected["commit_message"].strip() != "初稿" or selected["revision"] >= teachers[0]["revision"]:
        raise ValueError("Earliest student version lacks an explicit pre-review initial-draft record")
    # The independently examined P001 lineage is r194 -> teacher r206, not a general lineage resolver.
    if selected["revision"] != 194 or teachers[0]["revision"] != 206:
        raise ValueError("Saved history differs from the reviewed P001 lineage")
    teacher_file = next(item for item in manifest["files"] if item.get("status") == "exported")
    encoded_path = quote(thesis_path, safe="/")
    base_url = teacher_file["source_url"].rsplit("@", 1)[0]
    if not base_url.endswith(encoded_path):
        raise ValueError("Export URL does not match the historical thesis path")
    repository_root = base_url[:-len(encoded_path)]
    for item in manifest["files"]:
        if item.get("status") == "exported" and item["source_url"] != f"{repository_root}{encoded_path}@{item['revision']}":
            raise ValueError("Teacher export URL peg does not agree with its revision/path")
    snapshot = manifest["snapshot_revision"]
    student_name = thesis_path.split("/")[1]
    if manifest["query_url"] != repository_root + "/" + quote(student_name, safe="") + f"@{snapshot}" or manifest["revision_range"] != f"{snapshot}:1":
        raise ValueError("Log query range/URL does not match this student's source")
    return {**selected, "sample_id": "P001", "source_url": f"{repository_root}{encoded_path}@{selected['revision']}",
            "version_role": "svn_initial_draft_based_on_explicit_log",
            "first_teacher_revision": teachers[0]["revision"],
            "selection_basis": "学生新增无复制来源，提交说明初稿，早于同文件教师首改；限此已核验日志中的P001",
            "history_sha256": file_hash(batch / "svn_log.xml"),
            "export_manifest_sha256": file_hash(batch / "manifest.json")}


def perform_run(analysis_dir: Path, current_docx: Path) -> tuple[Path, dict]:
    # Verify the reviewed rule provenance using the same checks as the successful replay.
    reviewed = collect_checks(analysis_dir, current_docx)
    selected = select_initial(analysis_dir.parent.parent)
    code_hashes = {name: file_hash(Path(__file__).parent / name) for name in (
        "check_rules_pilot.py", "docx_comment_reader.py", "pilot_rule_analysis.py",
        "svn_initial_check_pilot.py", "svn_export_pilot.py")}
    output = analysis_dir / ("initial_check_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    filename = f"P001_r{selected['revision']}_huangfp_initial.docx"
    target = output / filename
    source = {**selected, "document_path": str(target)}
    run = {"status": "prepared", "llm_requests": 0, "source": source,
           "validation_role": "same_student_retrospective_initial_draft_check_not_independent_accuracy_test",
           "rule_provenance": reviewed["input_hashes"], "export_attempts": 0}
    run["checker_code_hashes"] = code_hashes
    try:
        save_json(output / "manifest.json", run)
        save_json(output / "reviewed_checklist.json", reviewed["checklist"])
        run.update(status="export_started", export_attempts=1)
        save_json(output / "manifest.json", run)
        run_svn("export", "--quiet", "-r", selected["revision"], selected["source_url"], filename, cwd=output)
        source.update(sha256=file_hash(target), size_bytes=target.stat().st_size)
        extracted = read_check_document(target, source)
        allowed_notice = "word/comments.xml is absent; no standard comment bodies extracted"
        if any(warning != allowed_notice for warning in extracted["warnings"]):
            raise ValueError("Initial draft has unsupported structures; preserve export and review extraction")
        checks = [check_first_person(extracted["body_paragraphs"]), check_formula_labels(extracted["body_paragraphs"])]
        result = {"source": source, "rules": checks, "comment_count": len(extracted["comments"]),
                  "reader_notices": extracted["warnings"], "llm_requests": 0,
                  "validation_role": run["validation_role"]}
        save_json(output / "checks.json", result)
        save_json(output / "document_text.json", {"source": source, "paragraphs": extracted["body_paragraphs"]})
        first, formula = checks
        lines = ["# P001 SVN初稿的两规则回查", "",
                 f"来源：学生 {source['svn_author']} 在 r{source['revision']} 新增论文，说明“{source['commit_message']}”；教师首改 r{source['first_teacher_revision']}。",
                 "这是已保存日志支持的初稿身份；不能证明不存在更早的线下草稿。",
                 "使用该学生后续批注提炼的规则回查，属于回顾性实验，不是独立准确率或泛化测试。",
                 "段落索引不是Word页码。检查保存的编号文本，未重算字段，未判断字体/分页/公式语义。", "",
                 f"- 我字线索：{first['hit_count']}（状态 {first['status']}）",
                 f"- 公式检查状态：{formula['status']}（partial代表图片/版面关联未全部核验）",
                 f"- 已识别公式标签：{formula['formula_label_count']}",
                 f"- 重复编号组：{formula['duplicate_group_count']}",
                 f"- 孤立编号：{formula['orphan_label_count']}",
                 f"- 不确定标签：{formula['uncertain_label_count']}", "",
                 f"- 图片旁编号线索：{formula['image_label_count']}；未绑定编号：{formula['unbound_label_count']}",
                 "逐项位置与上下文见 checks.json；检查项来源见 reviewed_checklist.json。", ""]
        (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
        run.update(status="exported_and_checked", source_sha256_verified=True)
    except Exception as exc:
        run.update(status="failed_no_retry", error_type=type(exc).__name__, error=str(exc))
        raise RuntimeError(f"任务停止，来源/状态记录保存在 {output / 'manifest.json'}") from exc
    finally:
        run["finished_at"] = datetime.now().astimezone().isoformat()
        save_json(output / "manifest.json", run)
    return output, result


def main() -> int:
    parser = argparse.ArgumentParser(description="仅导出P001已核实r194初稿，并执行两条本地试验规则")
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--current-docx", required=True, type=Path)
    args = parser.parse_args()
    try:
        output, result = perform_run(args.analysis_dir.resolve(), args.current_docx.resolve())
    except Exception as exc:
        print("任务未完成：", exc)
        return 1
    first, formula = result["rules"]
    print(f"初稿已导出：r{result['source']['revision']} / {result['source']['svn_author']} / {result['source']['commit_message']}")
    print(f"我字线索：{first['hit_count']}；公式标签：{formula['formula_label_count']}；重复组：{formula['duplicate_group_count']}；孤立编号：{formula['orphan_label_count']}")
    print(f"公式检查状态：{formula['status']}；图片编号线索：{formula['image_label_count']}；未绑定编号：{formula['unbound_label_count']}")
    print("本轮 LLM 请求：0；同学生初稿回查，不是独立准确率测试。")
    print("初稿文件：", result["source"]["document_path"])
    print("可读报告：", output / "report.md")
    print("来源清单：", output / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
