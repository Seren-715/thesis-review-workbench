"""One P002 r201 original export, audited analysis copy, and offline clue replay."""
import argparse
from datetime import datetime
import json
import shutil
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from cross_student_rule_pilot import digest, save
from docx_comment_reader import extract_docx
from legacy_doc_provenance import validate_lineage
from process_p002_doc_pilot import convert_copy, verify_parser_comments, CONVERTER
from review_cross_student_pilot import prepare_review, scan_clues
from check_p003_initial_pilot import BATCH
from svn_export_pilot import run_svn

REPO_PATH = "/212003113_李晨波/论文/Linux实训教学辅助系统的设计与实现.doc"


def prepare(review_dir):
    fresh, _ = prepare_review(review_dir.resolve().parent)
    if json.loads((review_dir / "reviewed_rules.json").read_text(encoding="utf-8")) != fresh:
        raise ValueError("Reviewed rules differ from fresh source-bound review")
    manifest = json.loads((BATCH / "P002/manifest.json").read_text(encoding="utf-8"))
    log = BATCH / "P002/svn_log.xml"
    if digest(log) != manifest["history_sha256"]:
        raise ValueError("P002 saved history SHA differs")
    rows = [(int(entry.attrib["revision"]), entry.findtext("author"), node.attrib, entry.findtext("date"))
            for entry in ET.fromstring(log.read_bytes()).findall("logentry")
            for node in entry.findall("paths/path") if node.text == REPO_PATH]
    earliest = min(rows, key=lambda row: row[0])
    if earliest[:2] != (201, "licb") or earliest[2].get("action") != "A" or earliest[2].get("copyfrom-path"):
        raise ValueError("Expected earliest recorded P002 student add r201 without copyfrom")
    if not any(row[:2] == (204, "mayy") and row[2].get("action") == "M" for row in rows):
        raise ValueError("Expected teacher edit r204 is absent")
    parent = json.loads((BATCH / "summary.json").read_text(encoding="utf-8"))
    if parent["snapshot_revision"] != 502:
        raise ValueError("Unexpected saved snapshot")
    return {"sample_id": "P002", "revision": 201, "svn_author": "licb", "svn_commit_time": earliest[3],
            "repository_path": REPO_PATH, "source_url": parent["repository_root"] + quote(REPO_PATH, safe="/") + "@201",
            "history_sha256": digest(log), "reviewed_rules_sha256": digest(review_dir / "reviewed_rules.json"),
            "initial_definition": "earliest recorded student add of this path; not proof of first-ever local draft"}


def execute(review_dir, resume_dir=None):
    review_dir = review_dir.resolve()
    source = prepare(review_dir)
    resumed_original = None
    resume_manifest = None
    if resume_dir is not None:
        resume_dir = resume_dir.resolve()
        if resume_dir.parent != review_dir:
            raise ValueError("Resume batch must be a direct child of this reviewed directory")
        resume_manifest = json.loads((resume_dir / "manifest.json").read_text(encoding="utf-8"))
        if (resume_manifest["status"] != "failed_no_retry" or resume_manifest["source"] != source
                or resume_manifest.get("error") != "ZIP container is not a supported Word OOXML document"):
            raise ValueError("Not the verified r201 container-identification failure")
        meta = resume_manifest["original_export"]
        resumed_original = Path(meta["document_path"]).resolve()
        if resumed_original.parent != resume_dir or digest(resumed_original) != meta["sha256"]:
            raise ValueError("Previously exported original changed or escapes its batch")
    output = review_dir / ("P002_initial_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    original = output / "P002_r201_licb_original.doc"
    derived = output / "P002_r201_licb_analysis.docx"
    audit_path = output / "P002_r201_conversion.json"
    run = {"status": "prepared", "source": source, "svn_requests": 0, "llm_requests": 0,
           "conversion_helper_sha256": digest(CONVERTER), "independent_accuracy_test": False,
           "evaluation_role": "same_student_retrospective_development_replay", "visual_verification": "not_verified"}
    save(output / "manifest.json", run)
    code = 1
    try:
        run.update(status="export_started", svn_requests=0 if resumed_original else 1)
        save(output / "manifest.json", run)
        if resumed_original:
            print("复用已核验的 P002 r201 原件，不重新下载……", flush=True)
            shutil.copyfile(resumed_original, original)
            if digest(original) != resume_manifest["original_export"]["sha256"]:
                raise ValueError("Resume copy SHA mismatch")
            run.update(resumed_from=str(resume_dir), previous_manifest_sha256=digest(resume_dir / "manifest.json"))
            shutil.copyfile(resume_dir / "manifest.json", output / "previous_failed_manifest.json")
        else:
            print("导出 P002 r201 学生原件……", flush=True)
            run_svn("export", "--quiet", "-r", 201, source["source_url"], original.name, cwd=output)
        original_meta = {"document_path": str(original), "sha256": digest(original), "size_bytes": original.stat().st_size}
        run.update(status="exported_conversion_started", original_export=original_meta)
        save(output / "manifest.json", run)
        print("识别真实格式并核对转换保留情况……", flush=True)
        result = convert_copy(original, derived, audit_path)
        item = {"exported_path": str(derived), "sha256": digest(derived), "original_export": original_meta,
                "artifact_role": "analysis_copy_from_legacy_svn_doc",
                "conversion": {"document_path": str(audit_path), "sha256": digest(audit_path), "mode": result["audit"]["status"]}}
        source.update(document_path=str(derived), sha256=item["sha256"], **validate_lineage(output, item))
        extracted = extract_docx(derived, source)
        verify_parser_comments(result["audit"], extracted)
        if extracted["source_sha256"] != source["sha256"] or digest(derived) != source["sha256"]:
            raise ValueError("Derived source SHA changed")
        checks = scan_clues(extracted["body_paragraphs"])
        save(output / "checks.json", {"source": source, "checks": checks, "reader_warnings": extracted["warnings"],
                                     "independent_accuracy_test": False, "llm_requests": 0})
        run.update(status="exported_audited_clue_replay_completed", reader_warnings=extracted["warnings"], actual_format=result["actual_format"])
        (output / "report.md").write_text("# P002 最早记录学生稿回查\n\n"
            "r201是保存历史中该旧DOC路径最早的学生新增，不证明是首次本地草稿。原件与分析副本分别绑定哈希。\n\n"
            "同学生后续教师反馈构建规则后回查早期稿，属于开发检查，不是独立准确率测试。\n\n"
            f"我字线索：{checks['XR001']['hit_count']}；章节标题线索：{checks['XR002']['title_clue_count']}；续表待视觉核验。\n\n"
            "字符及标题线索不等于错误；正文范围、章级、分页、图片与排版尚需核验。\n\n"
            f"原DOC：[打开](<{original.as_posix()}>)\n\n分析DOCX：[打开](<{derived.as_posix()}>)\n", encoding="utf-8")
        print(f"P002 r201：我字线索 {checks['XR001']['hit_count']}；章节标题线索 {checks['XR002']['title_clue_count']}；续表待视觉核验")
        code = 0
    except Exception as error:
        run.update(status="failed_no_retry", error_type=type(error).__name__, error=str(error))
        print("停止原因：", run["error"])
    finally:
        save(output / "manifest.json", run)
    print("状态：", run["status"])
    print(f"本轮 SVN 请求：{run['svn_requests']}；LLM 请求：0。")
    print("结果目录：", output)
    print("来源记录：", output / "manifest.json")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--resume-dir", type=Path, help="Reuse the verified existing r201 export after container detection failure")
    args = parser.parse_args()
    return execute(args.review_dir, args.resume_dir)


if __name__ == "__main__":
    raise SystemExit(main())
