"""Recover already-exported P003 evidence and audit P002 historical names."""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from collect_two_students_pilot import save_evidence, sha256
from comment_evidence_policy import assess_comments, check_supported_warnings
from extract_comments_pilot import collect_batch, write_json


def inspect_recovery(batch: Path) -> dict:
    batch = batch.resolve()
    overall = json.loads((batch / "summary.json").read_text(encoding="utf-8-sig"))
    if overall["snapshot_revision"] != 502:
        raise ValueError("Expected the reviewed snapshot502 cross-student batch")
    evidence = collect_batch(batch / "P003")
    if evidence["summary"]["sample_id"] != "P003":
        raise ValueError("Unexpected evidence sample")
    check_supported_warnings([warning["detail"] for warning in evidence["warnings"]])
    quality = assess_comments(evidence["comments"])
    authors = Counter(record["comment"].get("author") or "[missing_author]" for record in evidence["comments"])
    p002_manifest = json.loads((batch / "P002/manifest.json").read_text(encoding="utf-8-sig"))
    log_path = batch / "P002/svn_log.xml"
    if sha256(log_path) != p002_manifest["history_sha256"]:
        raise ValueError("P002 saved log differs from its manifest hash")
    prefix = p002_manifest["path_prefix"]
    known_old = prefix + "Linux实训教学辅助系统的设计与实现.doc"
    candidates = []
    for entry in ET.fromstring(log_path.read_bytes()).findall("logentry"):
        if entry.findtext("author") != "mayy":
            continue
        for path in entry.findall("paths/path"):
            if path.text != known_old or path.get("kind") != "file" or path.get("action") not in {"A", "M", "R"}:
                continue
            revision = int(entry.attrib["revision"])
            if not 1 <= revision <= 502:
                raise ValueError("P002 historical version outside the snapshot")
            candidates.append({"revision": revision, "author": "mayy", "repository_path": path.text,
                               "commit_time": entry.findtext("date"), "commit_message": entry.findtext("msg") or "",
                               "action": path.get("action"),
                               "source_url": overall["repository_root"] + quote(path.text, safe="/") + f"@{revision}",
                               "status": "not_exported_legacy_format_pending"})
    candidates.sort(key=lambda item: item["revision"], reverse=True)
    return {"source_batch": str(batch), "P003": {"sample_id": "P003", "quality": quality,
             "comment_author_counts": dict(authors), "warnings": evidence["warnings"],
             "source_manifest_sha256": evidence["summary"]["source_manifest_sha256"]},
            "P002": {"sample_id": "P002", "status": "historical_doc_candidates_found_not_exported",
             "candidates": candidates, "history_sha256": sha256(log_path),
             "explanation": "旧DOC路径与当前DOCX名称不同；新增无copyfrom不能证明内容连续。尚未导出、确认真实格式或读取批注。"},
            "llm_requests": 0, "svn_requests": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复已下载P003批注，记录P002旧DOC候选；不访问网络")
    parser.add_argument("--batch", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = inspect_recovery(args.batch)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("恢复前核验失败：", exc)
        return 2
    batch = args.batch.resolve()
    output = batch / ("recovery_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    # Original manifest and exported source bytes are preserved; save a new extraction only.
    evidence_dir, summary = save_evidence(batch / "P003")
    summary.update(comment_author_counts=result["P003"]["comment_author_counts"],
                   extraction_interpretation="特殊批注已隔离，原警告和原始失败清单保留，未将全批次改称成功")
    write_json(evidence_dir / "summary.json", summary)
    result["P003"].update(status="recovered_with_review_queue", evidence_dir=str(evidence_dir))
    write_json(output / "recovery_summary.json", result)
    write_json(output / "pending_P002.json", result["P002"])
    quality = result["P003"]["quality"]
    lines = ["# 跨学生小批次采集恢复", "",
             f"P003：批注{quality['comment_count']}条，可进入文字分析{quality['eligible_text_count']}条，待复核{quality['needs_review_count']}条，空文字{quality['empty_text_count']}条。",
             "旧公式对象批注保留源DOCX、XML与预览图，不解析为完整公式；点式批注保留位置，不能猜选区。",
             "可进入文字分析只表示来源和选区满足当前条件，不代表批注观点、规则或常见性已验证。", "",
             f"P003新证据目录：{evidence_dir}", "",
             f"P002：日志发现{len(result['P002']['candidates'])}份旧DOC教师提交候选，尚未导出和读取批注。",
             "不能把当前精确DOCX路径未匹配解释为老师没有批注；旧DOC不能仅改后缀冒充DOCX。", "",
             "原导出字节、原manifest和失败summary均保留；本轮SVN和LLM请求均为0。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"P003已恢复：批注 {quality['comment_count']}；可进入文字分析 {quality['eligible_text_count']}；待复核 {quality['needs_review_count']}；空文字 {quality['empty_text_count']}")
    print(f"P002：找到旧DOC候选 {len(result['P002']['candidates'])} 份，仍待处理，未冒充成功。")
    print("本轮 SVN 请求：0；LLM 请求：0。")
    print("P003证据目录：", evidence_dir)
    print("恢复报告：", output / "report.md")
    print("恢复清单：", output / "recovery_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
