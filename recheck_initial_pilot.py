"""Recheck the existing exported initial DOCX; no SVN or model requests."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from check_rules_pilot import check_first_person, check_formula_labels, file_hash, read_check_document, save_json


def collect_recheck(initial_dir: Path) -> dict:
    initial_dir = initial_dir.resolve()
    manifest = json.loads((initial_dir / "manifest.json").read_text(encoding="utf-8-sig"))
    previous = json.loads((initial_dir / "checks.json").read_text(encoding="utf-8-sig"))
    source = manifest["source"]
    if manifest.get("status") != "exported_and_checked" or source != previous["source"]:
        raise ValueError("Initial export manifest and previous check source do not agree")
    if source["revision"] != 194 or source["svn_author"] != "huangfp" or source["sample_id"] != "P001":
        raise ValueError("This recheck is limited to the verified P001 r194 initial draft")
    docx = Path(source["document_path"]).resolve()
    if not docx.is_relative_to(initial_dir) or file_hash(docx) != source["sha256"]:
        raise ValueError("Initial DOCX path or SHA differs from the saved export")
    extracted = read_check_document(docx, source)
    allowed_notice = "word/comments.xml is absent; no standard comment bodies extracted"
    if any(warning != allowed_notice for warning in extracted["warnings"]):
        raise ValueError("Unsupported initial DOCX structure; inspect extraction first")
    return {"source": source,
            "rules": [check_first_person(extracted["body_paragraphs"]), check_formula_labels(extracted["body_paragraphs"])],
            "reader_notices": extracted["warnings"], "llm_requests": 0, "svn_requests": 0,
            "checker_version": "image-label-coverage-v2", "validation_role": manifest["validation_role"],
            "source_artifact_hashes": {name: file_hash(initial_dir / name) for name in ("manifest.json", "checks.json", "reviewed_checklist.json")},
            "checker_code_hashes": {name: file_hash(Path(__file__).parent / name) for name in ("check_rules_pilot.py", "docx_comment_reader.py", "recheck_initial_pilot.py")}}


def main() -> int:
    parser = argparse.ArgumentParser(description="复查已有r194初稿的公式图片编号覆盖情况，不导出、不调用LLM")
    parser.add_argument("--initial-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = collect_recheck(args.initial_dir)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("输入核验失败：", exc)
        return 2
    output = args.initial_dir.resolve() / ("recheck_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    save_json(output / "checks.json", result)
    first, formula = result["rules"]
    lines = ["# P001 r194 初稿检查覆盖修正", "",
             "本轮检查已有初稿，不请求SVN或LLM；原始导出和旧检查结果保留。",
             "同学生后续批注规则回查初稿，不是独立准确率测试。段落索引为XML索引，不是Word页码。", "",
             f"- R001状态：{first['status']}；我字线索：{first['hit_count']}",
             f"- R002状态：{formula['status']}",
             f"- 已确认同段OMML格式编号：{formula['formula_label_count']}；重复组：{formula['duplicate_group_count']}",
             f"- 同段图片编号线索：{formula['image_label_count']}；未绑定图片的编号：{formula['unbound_label_count']}",
             f"- 共识别编号文字：{formula['text_label_count']}；这些文字重复组：{formula['text_duplicate_group_count']}", "",
             "图片旁标签只是线索，本轮未识别图片内公式或判定图片包含几个公式。跨段编号不自动绑定图片。",
             "partial表示部分检查；cannot_check表示未能检查，不能理解为合格。None表示未判定，不是零缺陷。",
             "未检出编号文字重复不代表公式全部正确、顺序合规或整篇论文合格。", "",
             "## 定位与语境", "", "```json", json.dumps(result["rules"], ensure_ascii=False, indent=2), "```", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"R001：{first['status']}；我字线索 {first['hit_count']}")
    print(f"R002：{formula['status']}；已确认OMML编号 {formula['formula_label_count']}；图片编号线索 {formula['image_label_count']}；未绑定编号 {formula['unbound_label_count']}")
    print(f"已保存编号文字 {formula['text_label_count']}；文字重复组 {formula['text_duplicate_group_count']}（不代表公式已全部检查）")
    print("本轮 SVN 请求：0；LLM 请求：0。")
    print("可读报告：", output / "report.md")
    print("检查结果：", output / "checks.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
