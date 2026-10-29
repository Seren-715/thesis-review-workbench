"""Bounded P002 historical DOC export/conversion; no LLM requests or retries."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from collect_two_students_pilot import save_evidence, sha256
from comment_evidence_policy import assess_comments, check_supported_warnings
from docx_comment_reader import extract_docx
from extract_comments_pilot import write_json
from recover_cross_student_pilot import inspect_recovery
from svn_export_pilot import run_svn

CONVERTER = Path(__file__).with_name("convert_legacy_doc.ps1")
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


def detect_format(path: Path) -> str:
    # Legacy OLE documents can contain an appended ZIP (e.g. drs/downrev.xml).
    # zipfile accepts such hybrid files; the leading container signature wins.
    with path.open("rb") as stream:
        if stream.read(8) == OLE_MAGIC:
            return "legacy_ole_candidate"
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(archive.namelist()):
                raise ValueError("ZIP container is not a supported Word OOXML document")
            # Parsing document XML also rejects corrupt XML before accepting the copy.
            import xml.etree.ElementTree as ET
            root = ET.fromstring(archive.read("word/document.xml"))
            if root.tag != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document":
                raise ValueError("Unexpected OOXML document root")
        return "word_ooxml_zip"
    raise ValueError("Unsupported actual document format; no conversion attempted")


def normalize_word_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    return text[:-1] if text.endswith("\n") else text


def convert_copy(original: Path, derived: Path, audit_path: Path) -> dict:
    if derived.exists() or audit_path.exists():
        raise ValueError("Derived file/audit already exists; refusing overwrite")
    source_hash = sha256(original)
    actual_format = detect_format(original)
    if actual_format == "word_ooxml_zip":
        shutil.copyfile(original, derived)
        audit = {"schema_version": 1, "status": "zip_container_analysis_copy",
                 "source": {"path": str(original), "sha256": source_hash, "size_bytes": original.stat().st_size},
                 "derived": {"path": str(derived), "sha256": sha256(derived), "size_bytes": derived.stat().st_size},
                 "source_sha256_unchanged": sha256(original) == source_hash,
                 "visual_verification": "not_verified", "llm_requests": 0,
                 "explanation": "Actual source is OOXML ZIP; analysis DOCX is a byte-identical copy, not Word conversion."}
        write_json(audit_path, audit)
    else:
        if not CONVERTER.is_file() or not POWERSHELL.is_file():
            raise ValueError("Word conversion helper or Windows PowerShell missing")
        result = subprocess.run([str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
                                 "-File", str(CONVERTER), "-InputPath", str(original),
                                 "-OutputPath", str(derived), "-AuditPath", str(audit_path)],
                                capture_output=True, timeout=180)
        if not audit_path.exists():
            raise RuntimeError(f"Word conversion exited {result.returncode} without an audit; stop and inspect")
        audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
        if result.returncode != 0 or audit["status"] != "comment_text_fidelity_verified_visual_pending":
            raise ValueError("Word conversion/preservation audit failed; retained copy is not eligible")
    if (sha256(original) != source_hash or not audit["source_sha256_unchanged"]
            or audit["source"]["sha256"] != source_hash
            or audit["derived"]["sha256"] != sha256(derived)):
        raise ValueError("Source/derived hashes do not match conversion audit")
    return {"actual_format": actual_format, "audit": audit}


def verify_parser_comments(audit: dict, extracted: dict) -> None:
    if audit["status"] == "zip_container_analysis_copy":
        return
    rows = audit["comments_after"]
    if len(extracted["comments"]) != audit["comments_after_count"] or len(rows) != audit["comments_after_count"]:
        raise ValueError("Word and XML parser comment counts differ; manual review required")
    # Word supplies a terminal paragraph marker; XML joins paragraph texts with LF.
    # Internal/leading/trailing whitespace remains significant in both policies.
    expected = Counter((str(row["author"]), normalize_word_text(row["range_text"])) for row in rows)
    actual = Counter((record["comment"]["author"] or "", record["comment"]["text"])
                     for record in extracted["comments"])
    if expected != actual:
        raise ValueError("Word and XML full comment text/author differ; manual review required")


def prepare_context(recovery_dir: Path) -> tuple[Path, dict, dict]:
    recovery_dir = recovery_dir.resolve()
    recovery = json.loads((recovery_dir / "recovery_summary.json").read_text(encoding="utf-8-sig"))
    batch = Path(recovery["source_batch"]).resolve()
    if recovery_dir.parent != batch:
        raise ValueError("Recovery folder is outside the declared original batch")
    verified = inspect_recovery(batch)
    pending = json.loads((recovery_dir / "pending_P002.json").read_text(encoding="utf-8-sig"))
    if pending != verified["P002"] or recovery["P002"] != verified["P002"]:
        raise ValueError("P002 pending candidates differ from freshly checked saved history")
    if [item["revision"] for item in pending["candidates"]] != [283, 204]:
        raise ValueError("Expected the reviewed two P002 historical revisions")
    return batch, pending, recovery


def process(recovery_dir: Path) -> Path:
    batch, pending, _ = prepare_context(recovery_dir)
    output = batch / "P002" / ("legacy_doc_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    manifest = {"sample_id": "P002", "snapshot_revision": 502, "files": [], "original_exports": [],
                "history_sha256": pending["history_sha256"], "source_recovery_dir": str(recovery_dir.resolve()),
                "teacher_author": "mayy", "max_files": 2, "llm_requests": 0,
                "conversion_helper_sha256": sha256(CONVERTER), "status": "started"}
    report = {"status": "started", "svn_requests": 0, "llm_requests": 0, "versions": [],
              "evidence_dir": None, "visual_verification": "not_verified"}
    write_json(output / "manifest.json", manifest)
    try:
        for candidate in pending["candidates"]:
            revision = candidate["revision"]
            original = output / f"P002_r{revision}_mayy_original.doc"
            derived = output / f"P002_r{revision}_mayy_analysis.docx"
            audit_path = output / f"P002_r{revision}_conversion.json"
            version = {"revision": revision, "status": "export_started", "original_path": str(original)}
            report["versions"].append(version)
            manifest["original_exports"].append({**candidate, "document_path": str(original), "status": "export_started"})
            write_json(output / "manifest.json", manifest)
            write_json(output / "run.json", report)
            print(f"导出 P002 教师历史原件 r{revision}……", flush=True)
            report["svn_requests"] += 1
            run_svn("export", "--quiet", "-r", revision, candidate["source_url"], original.name, cwd=output)
            original_meta = {"document_path": str(original), "sha256": sha256(original),
                             "size_bytes": original.stat().st_size}
            manifest["original_exports"][-1].update(**original_meta, status="exported")
            write_json(output / "manifest.json", manifest)
            print("识别文件格式并核对转换前后批注……", flush=True)
            result = convert_copy(original, derived, audit_path)
            item = {**candidate, "status": "exported", "exported_path": str(derived), "sha256": sha256(derived),
                    "size_bytes": derived.stat().st_size, "artifact_role": "analysis_copy_from_legacy_svn_doc",
                    "original_export": original_meta,
                    "conversion": {"document_path": str(audit_path), "sha256": sha256(audit_path),
                                   "mode": result["audit"]["status"]}}
            extracted = extract_docx(derived, {"sample_id": "P002", "revision": revision, "sha256": item["sha256"]})
            verify_parser_comments(result["audit"], extracted)
            check_supported_warnings(extracted["warnings"])
            quality = assess_comments(extracted["comments"])
            nonempty = sum(not row["comment"]["empty_text"] for row in extracted["comments"])
            item.update(comment_count=len(extracted["comments"]), nonempty_text_comments=nonempty)
            manifest["files"].append(item)
            version.update(status="parsed_text_audit_visual_pending", actual_format=result["actual_format"],
                           quality=quality, nonempty_text_comments=nonempty,
                           comment_author_counts=dict(Counter(row["comment"]["author"] or "[missing_author]"
                                                              for row in extracted["comments"])),
                           conversion_audit=str(audit_path), conversion_status=result["audit"]["status"])
            write_json(output / "manifest.json", manifest)
            write_json(output / "run.json", report)
            print(f"r{revision}：批注 {quality['comment_count']}；可文字分析 {quality['eligible_text_count']}；"
                  f"待复核 {quality['needs_review_count']}；空文字 {quality['empty_text_count']}", flush=True)
            if nonempty:
                break
        manifest["status"] = "conversion_and_parser_checked_visual_pending"
        write_json(output / "manifest.json", manifest)
        evidence_dir, summary = save_evidence(output)
        report.update(status="text_evidence_saved_visual_pending", evidence_dir=str(evidence_dir), summary=summary)
    except Exception as error:
        report.update(status="failed_no_retry", error_type=type(error).__name__, error=str(error))
        manifest["status"] = "failed_no_retry"
        if report["versions"]:
            report["versions"][-1].update(status="failed_no_retry", error=str(error))
        write_json(output / "manifest.json", manifest)
    finally:
        write_json(output / "run.json", report)
        lines = ["# P002 旧 DOC 小批次处理", "", f"状态：{report['status']}", "",
                 "原始 SVN DOC 与分析 DOCX 分开保存并分别绑定哈希。转换只核验文字、批注与文字选区；视觉尚未核验。",
                 "此批数据不用于推断独立准确率或启用常见问题规则。", "",
                 "```json", json.dumps(report, ensure_ascii=False, indent=2), "```", ""]
        (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("状态：", report["status"])
    print(f"本轮 SVN 请求：{report['svn_requests']}；LLM 请求：0。")
    if report.get("error"):
        print("停止原因：", report["error"])
    if report["evidence_dir"]:
        print("批注证据：", report["evidence_dir"])
    print("处理报告：", output / "report.md")
    print("运行记录：", output / "run.json")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-dir", required=True, type=Path)
    args = parser.parse_args()
    output = process(args.recovery_dir)
    status = json.loads((output / "run.json").read_text(encoding="utf-8"))["status"]
    if status == "failed_no_retry":
        sys.exit(1)


if __name__ == "__main__":
    main()
