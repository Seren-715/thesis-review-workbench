"""Export one logged earliest P003 student submission and replay clue tools."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from cross_student_rule_pilot import digest, save
from docx_comment_reader import extract_docx
from review_cross_student_pilot import prepare_review, scan_clues
from svn_export_pilot import run_svn

BATCH = Path(r"D:\svn_agent\data\svn_exports\cross_student_20261007_113217_741043")
REPO_PATH = "/212003180_周运龙/论文/212003180_周运龙_毕业论文.docx"


def prepare(review_dir):
    review_dir = review_dir.resolve()
    fresh, _ = prepare_review(review_dir.parent)
    saved = json.loads((review_dir / "reviewed_rules.json").read_text(encoding="utf-8"))
    if saved != fresh:
        raise ValueError("Reviewed rules differ from freshly verified semantic review")
    manifest = json.loads((BATCH / "P003/manifest.json").read_text(encoding="utf-8"))
    log = BATCH / "P003/svn_log.xml"
    if digest(log) != manifest["history_sha256"]:
        raise ValueError("Saved P003 history SHA mismatch")
    changes = []
    for entry in ET.fromstring(log.read_bytes()).findall("logentry"):
        for node in entry.findall("paths/path"):
            if node.text == REPO_PATH:
                changes.append((int(entry.attrib["revision"]), entry.findtext("author"), node.attrib, entry.findtext("date")))
    earliest = min(changes, key=lambda row: row[0])
    if earliest[:2] != (246, "zhouyl") or earliest[2].get("action") != "A" or earliest[2].get("copyfrom-path"):
        raise ValueError("Expected reviewed earliest recorded student add r246 without copyfrom")
    if not any(row[:2] == (249, "mayy") and row[2].get("action") == "M" for row in changes):
        raise ValueError("Teacher r249 edit missing from saved history")
    parent = json.loads((BATCH / "summary.json").read_text(encoding="utf-8"))
    if parent["snapshot_revision"] != 502:
        raise ValueError("Unexpected source snapshot")
    return {"sample_id": "P003", "revision": 246, "svn_author": "zhouyl", "svn_commit_time": earliest[3],
            "repository_path": REPO_PATH, "source_url": parent["repository_root"] + quote(REPO_PATH, safe="/") + "@246",
            "saved_history_sha256": digest(log), "reviewed_rules_sha256": digest(review_dir / "reviewed_rules.json"),
            "initial_definition": "earliest recorded student addition of this exact path in saved 502:1 history; not proof of first-ever draft"}


def execute(review_dir):
    source = prepare(review_dir)
    output = review_dir.resolve() / ("P003_initial_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    target = output / "P003_r246_zhouyl_initial.docx"
    run = {"status": "prepared", "source": source, "svn_requests": 0, "llm_requests": 0,
           "evaluation_role": "same_student_retrospective_development_replay", "independent_accuracy_test": False}
    save(output / "manifest.json", run)
    code = 1
    try:
        run.update(status="export_started", svn_requests=1)
        save(output / "manifest.json", run)
        run_svn("export", "--quiet", "-r", 246, source["source_url"], target.name, cwd=output)
        source.update(document_path=str(target), sha256=digest(target), size_bytes=target.stat().st_size)
        run["status"] = "exported_check_started"
        save(output / "manifest.json", run)
        extracted = extract_docx(target, source)
        if extracted["source_sha256"] != source["sha256"] or digest(target) != source["sha256"]:
            raise ValueError("Initial document SHA changed")
        checks = scan_clues(extracted["body_paragraphs"])
        save(output / "checks.json", {"source": source, "checks": checks, "reader_warnings": extracted["warnings"],
                                     "independent_accuracy_test": False, "llm_requests": 0})
        run.update(status="exported_clue_replay_completed", reader_warnings=extracted["warnings"])
        text = ("# P003 最早记录的学生稿回查\n\n"
                "r246是已保存日志中该路径最早的学生新增，不证明不存在更早的本地稿。教师r249修改同一路径。\n\n"
                "同学生回顾性开发检查，规则来源包含后续教师反馈，不是独立准确率测试。\n\n"
                f"我字线索：{checks['XR001']['hit_count']}；章节标题线索：{checks['XR002']['title_clue_count']}。\n\n"
                "字符扫描含声明、引用、致谢等；标题关键词不证明章级。续表待视觉核验，未判定不是0错误。\n\n"
                f"原稿：[打开文档](<{target.as_posix()}>)\n\n"
                f"完整检查结果：[查看JSON](<{(output / 'checks.json').as_posix()}>)\n")
        (output / "report.md").write_text(text, encoding="utf-8")
        print(f"P003 r246：我字线索 {checks['XR001']['hit_count']}；章节标题线索 {checks['XR002']['title_clue_count']}；续表待视觉核验")
        code = 0
    except Exception as error:
        run.update(status="failed_no_retry", error_type=type(error).__name__, error=str(error))
        print("停止原因：", run["error"])
    finally:
        save(output / "manifest.json", run)
    print("状态：", run["status"])
    print(f"本轮 SVN 请求：{run['svn_requests']}；LLM 请求：0；未启动 Word。")
    print("结果目录：", output)
    print("来源记录：", output / "manifest.json")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, required=True)
    args = parser.parse_args()
    return execute(args.review_dir)


if __name__ == "__main__":
    raise SystemExit(main())
