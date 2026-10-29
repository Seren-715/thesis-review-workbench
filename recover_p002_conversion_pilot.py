"""Recover audited P002 text from the known COM cleanup failure, offline."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil

from collect_two_students_pilot import save_evidence, sha256
from comment_evidence_policy import assess_comments, check_supported_warnings
from docx_comment_reader import extract_docx
from extract_comments_pilot import collect_batch, write_json
from legacy_doc_provenance import verify_cleanup_only_failure
from process_p002_doc_pilot import prepare_context, verify_parser_comments


def inspect_failed_conversion(failed_dir):
    failed_dir = failed_dir.resolve()
    manifest_path = failed_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    batch, pending, _ = prepare_context(Path(manifest["source_recovery_dir"]))
    if failed_dir.parent != batch / "P002" or manifest["sample_id"] != "P002" or manifest["status"] != "failed_no_retry":
        raise ValueError("Not the declared failed P002 conversion batch")
    if len(manifest["original_exports"]) != 1 or manifest["files"]:
        raise ValueError("Expected only one exported original with no enabled derived files")
    original_meta = manifest["original_exports"][0]
    candidate = pending["candidates"][0]
    if original_meta["revision"] != 283 or any(original_meta[key] != candidate[key] for key in
            ("author", "source_url", "repository_path", "commit_time")):
        raise ValueError("Original metadata differs from reviewed teacher history")
    audit_path = failed_dir / "P002_r283_conversion.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
    verify_cleanup_only_failure(audit)
    original = Path(audit["source"]["path"]).resolve()
    derived = Path(audit["derived"]["path"]).resolve()
    if (original.parent != failed_dir or derived.parent != failed_dir
            or original != Path(original_meta["document_path"]).resolve()
            or sha256(original) != original_meta["sha256"] or sha256(original) != audit["source"]["sha256"]
            or sha256(derived) != audit["derived"]["sha256"]):
        raise ValueError("Original/derived files changed or escape the failed batch")
    extracted = extract_docx(derived, {"sample_id": "P002", "revision": 283})
    verify_parser_comments(audit, extracted)
    check_supported_warnings(extracted["warnings"])
    return {"manifest_path": manifest_path, "audit_path": audit_path, "audit": audit,
            "original": original, "derived": derived, "candidate": candidate,
            "quality": assess_comments(extracted["comments"]), "warnings": extracted["warnings"]}


def recover(failed_dir):
    checked = inspect_failed_conversion(failed_dir)
    output = failed_dir.resolve() / ("text_recovery_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    original, derived = output / checked["original"].name, output / checked["derived"].name
    supporting = output / "original_failed_conversion_audit.json"
    old_manifest = output / "original_failed_manifest.json"
    for source, destination in ((checked["original"], original), (checked["derived"], derived),
                                (checked["audit_path"], supporting), (checked["manifest_path"], old_manifest)):
        shutil.copyfile(source, destination)
        if sha256(source) != sha256(destination):
            raise ValueError("Recovery copy hash differs; stop")
    audit_path = output / "text_recovery_audit.json"
    audit = {"schema_version": 1, "status": "recovered_verified_text_cleanup_pending",
             "source": {"path": str(original), "sha256": sha256(original)},
             "derived": {"path": str(derived), "sha256": sha256(derived)},
             "source_sha256_unchanged": True,
             "supporting_audit": {"document_path": str(supporting), "sha256": sha256(supporting)},
             "original_failed_manifest": {"document_path": str(old_manifest), "sha256": sha256(old_manifest)},
             "source_failed_dir": str(failed_dir.resolve()), "word_cleanup_status": "not_verified",
             "visual_verification": "not_verified", "svn_requests": 0, "llm_requests": 0,
             "explanation": "New offline recovery decision based on unchanged original/derived bytes and failed audit; not a new Word conversion or a successful Word shutdown."}
    write_json(audit_path, audit)
    item = {**checked["candidate"], "status": "exported", "exported_path": str(derived),
            "sha256": sha256(derived), "artifact_role": "analysis_copy_from_legacy_svn_doc",
            "original_export": {"document_path": str(original), "sha256": sha256(original)},
            "conversion": {"document_path": str(audit_path), "sha256": sha256(audit_path), "mode": audit["status"]}}
    write_json(output / "manifest.json", {"sample_id": "P002", "snapshot_revision": 502,
               "status": "text_recovered_word_cleanup_and_visual_pending", "files": [item],
               "source_failed_dir": str(failed_dir.resolve()), "svn_requests": 0, "llm_requests": 0})
    # Re-read the copied originals and audit chain before saving any text evidence.
    evidence = collect_batch(output)
    verify_parser_comments(checked["audit"], {"comments": evidence["comments"]})
    evidence_dir, summary = save_evidence(output)
    report = {"status": "text_recovered_word_cleanup_and_visual_pending", "quality": checked["quality"],
              "evidence_dir": str(evidence_dir), "summary": summary,
              "word_cleanup_status": "not_verified", "visual_verification": "not_verified",
              "svn_requests": 0, "llm_requests": 0}
    write_json(output / "recovery_summary.json", report)
    (output / "report.md").write_text("# P002 文字证据恢复\n\n"
        "Word 转换的6条批注、选区、顺序和正文文本已通过原审计核对；原失败是COM清理引用参数错误。\n\n"
        "旧failed记录保持不变，本轮未重新下载或启动Word。Word退出状态未确认，版面、图片及公式尚未视觉核验。\n\n"
        f"批注证据：[打开报告](<{(evidence_dir / 'report.md').as_posix()}>)\n\n"
        f"原失败审计：[查看原样副本](<{supporting.as_posix()}>)\n", encoding="utf-8")
    quality = checked["quality"]
    print(f"P002文字证据已恢复：批注 {quality['comment_count']}；可文字分析 {quality['eligible_text_count']}；"
          f"待复核 {quality['needs_review_count']}；空文字 {quality['empty_text_count']}")
    print("Word退出状态：未确认；视觉核验：未完成。")
    print("本轮 SVN 请求：0；LLM 请求：0；未启动 Word。")
    print("批注证据：", evidence_dir)
    print("恢复报告：", output / "report.md")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-dir", type=Path, required=True)
    args = parser.parse_args()
    recover(args.failed_dir)


if __name__ == "__main__":
    main()
