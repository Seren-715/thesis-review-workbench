"""Export source-bound DOCX comment evidence without modifying the papers."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from docx_comment_reader import extract_docx


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_batch(batch: Path) -> dict:
    """Read and validate a batch in memory. This function writes no files."""
    batch = batch.resolve()
    manifest_path = batch / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    comments, documents, warnings = [], [], []
    assets, xml_parts = {}, {}

    for item in manifest["files"]:
        if item.get("status") != "exported":
            warnings.append({"file": item.get("exported_path"), "reason": "not_exported"})
            continue

        path = Path(item["exported_path"])
        if not path.is_absolute():
            path = batch / path
        path = path.resolve()
        if not path.is_relative_to(batch):
            raise ValueError(f"Manifest file is outside this batch: {path}")
        if path.suffix.lower() != ".docx":
            raise ValueError(f"This pilot only supports DOCX: {path.name}")

        source = {
            "sample_id": manifest["sample_id"],
            "revision": item["revision"],
            "svn_author": item["author"],
            "svn_commit_time": item.get("commit_time"),
            "repository_path": item["repository_path"],
            "source_url": item["source_url"],
            "document_path": str(path),
            "sha256": item["sha256"],
        }
        if item.get("artifact_role"):
            from legacy_doc_provenance import validate_lineage
            source.update(validate_lineage(batch, item))
        extracted = extract_docx(path, source)
        if extracted["source_sha256"] != item["sha256"] or sha256(path) != item["sha256"]:
            raise ValueError(f"Source hash changed: {path.name}")

        comments.extend(extracted["comments"])
        documents.append({
            "source": source,
            "comment_count": len(extracted["comments"]),
            "paragraphs": extracted["body_paragraphs"],
        })
        for warning in extracted["warnings"]:
            warnings.append({"file": path.name, "detail": warning})
        for destination, addition in ((assets, extracted["assets"]), (xml_parts, extracted["xml_parts"])):
            for key, content in addition.items():
                if key in destination and destination[key] != content:
                    raise ValueError(f"Conflicting evidence file: {key}")
                destination[key] = content

    if not documents:
        raise ValueError("No successfully exported DOCX files in this batch.")
    if manifest_bytes != manifest_path.read_bytes():
        raise ValueError("The source manifest changed during extraction.")

    summary = {
        "sample_id": manifest["sample_id"],
        "document_count": len(documents),
        "comment_count": len(comments),
        "nonempty_text_comments": sum(not c["comment"]["empty_text"] for c in comments),
        "empty_text_comments": sum(c["comment"]["empty_text"] for c in comments),
        "complete_anchors": sum(c["anchor"]["status"] == "complete" for c in comments),
        "math_anchor_count": sum(c["anchor"]["contains_math"] for c in comments),
        "formula_count": sum(len(c["anchor"]["formulas"]) for c in comments),
        "images_in_comments": sum(len(c["comment"]["media"]) for c in comments),
        "images_in_anchors": sum(len(c["anchor"]["media"]) for c in comments),
        "source_hashes_verified": True,
        "visual_evidence_reviewed": False,
        "warning_count": len(warnings),
        "source_manifest_path": str(manifest_path),
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "snapshot_revision": manifest.get("snapshot_revision"),
        "created_at": datetime.now().astimezone().isoformat(),
        "llm_requests": 0,
    }
    return {
        "summary": summary, "comments": comments, "documents": documents,
        "warnings": warnings, "assets": assets, "xml_parts": xml_parts,
    }


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def evidence_link(output: Path, relative: str) -> str:
    return f"[查看文件](<{(output / relative).as_posix()}>)"


def write_report(output: Path, evidence: dict) -> None:
    summary = evidence["summary"]
    lines = [
        f"# {summary['sample_id']} 自动批注提取报告", "",
        f"文件 {summary['document_count']} 份；批注 {summary['comment_count']} 条；"
        f"有文字 {summary['nonempty_text_comments']} 条；空文字 {summary['empty_text_comments']} 条。", "",
        "定位采用 document.xml 中从 1 开始的段落索引，包含表格内段落，并非 Word 页码。",
        "选区文本排除删除与移出修订；公式文本为线性提取，原始公式 XML 同时保留。",
        "图片已保存，图像含义和公式呈现尚未做视觉核验。", "",
    ]
    for record in evidence["comments"]:
        source, comment, anchor = record["source"], record["comment"], record["anchor"]
        lines.extend([
            f"## r{source['revision']} · 批注 {comment['id']}", "",
            f"批注作者：{comment['author']}；SVN 提交者：{source['svn_author']}。",
            f"批注时间：{comment['date']}；锚点状态：{anchor['status']}。",
            f"{'分析 DOCX 副本' if source.get('original_export') else '原始 DOCX'}：[打开文档](<{Path(source['document_path']).as_posix()}>)",
            f"段落索引：{', '.join(map(str, anchor['paragraph_indices'])) or '无'}。", "",
            "### 批注文字", "",
            "````text", comment["text"] if not comment["empty_text"] else "[该批注没有文字，已原样保留]", "````", "",
            "### 原文选区", "",
            "````text", anchor["text"] or "[选区没有可提取的可见文字，参见附件及源 XML]", "````", "",
        ])
        if source.get("original_export"):
            lines.extend([
                f"SVN 原始 DOC：[打开原件](<{Path(source['original_export']['document_path']).as_posix()}>)",
                f"转换审计：[查看记录](<{Path(source['conversion']['document_path']).as_posix()}>)",
                "转换后的文字和批注核对不代表排版、图片或公式已经视觉验证。", "",
            ])
            if source.get("word_cleanup_status") == "not_verified":
                lines.extend(["Word 清理/退出状态未确认；本批文字证据来自原失败审计的单独恢复。", ""])
        for location, attachments in (("批注图片", comment["media"]), ("原文图片", anchor["media"])):
            for attachment in attachments:
                relative = attachment.get("export_path")
                if relative:
                    lines.extend([f"{location}：{evidence_link(output, relative)}", ""])
                else:
                    lines.extend([f"{location}：未获取的关系 {attachment.get('relationship_id', '')}", ""])
        for formula in anchor["formulas"]:
            lines.extend([f"原始公式 XML：{evidence_link(output, formula['export_path'])}", ""])
    if evidence["warnings"]:
        lines.extend(["## 提取警告", "", "````json", json.dumps(evidence["warnings"], ensure_ascii=False, indent=2), "````", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True, help="SVN export batch containing manifest.json")
    args = parser.parse_args()
    evidence = collect_batch(args.batch)

    output = args.batch.resolve() / ("comment_evidence_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    for collection in (evidence["assets"], evidence["xml_parts"]):
        for relative, content in collection.items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Invalid output-relative evidence path: {relative}")
            destination = output / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

    write_json(output / "comments.json", {"schema_version": 1, "comments": evidence["comments"]})
    write_json(output / "document_text.json", {"documents": evidence["documents"]})
    write_json(output / "summary.json", {**evidence["summary"], "warnings": evidence["warnings"]})
    write_report(output, evidence)

    summary = evidence["summary"]
    print(f"已处理 DOCX：{summary['document_count']} 份")
    print(f"批注：{summary['comment_count']} 条；有文字：{summary['nonempty_text_comments']}；空文字：{summary['empty_text_comments']}")
    print(f"完整锚点：{summary['complete_anchors']}/{summary['comment_count']}")
    print(f"公式选区：{summary['math_anchor_count']} 处；公式 XML：{summary['formula_count']} 个")
    print(f"批注图片：{summary['images_in_comments']} 个；选区内图片：{summary['images_in_anchors']} 个")
    print(f"警告：{summary['warning_count']} 条")
    print("来源哈希已核验；本轮 LLM 请求为 0。")
    print("证据目录：", output)
    print("可读报告：", output / "report.md")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print("提取未完成：", error)
        raise SystemExit(1)
