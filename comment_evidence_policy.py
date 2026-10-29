"""Separate recoverable comment evidence from local unsupported structures."""

import re


ABSENT_COMMENTS = "word/comments.xml is absent; no standard comment bodies extracted"
LOCAL_OBJECT_WARNING = "unsupported body structure object; XML retained, text may be partial"
RETAINED_DURABLE_IDS = "unsupported optional comment metadata retained as XML: word/commentsIds.xml"


def check_supported_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        if warning in {ABSENT_COMMENTS, LOCAL_OBJECT_WARNING, RETAINED_DURABLE_IDS}:
            continue
        if re.fullmatch(r"comment \d+ anchor status: reference_only", warning):
            continue
        raise ValueError(f"Unreviewed DOCX evidence warning: {warning}")


def assess_comments(comments: list[dict]) -> dict:
    rows = []
    for record in comments:
        reasons = []
        if record["comment"]["empty_text"]:
            status = "empty_text_preserved"
            reasons.append("无文字批注保留，不猜测含义")
        else:
            if record["anchor"]["status"] != "complete":
                reasons.append("原文选区不完整；只保留已有位置，不猜测选区")
            if record["anchor"].get("unsupported_structures"):
                reasons.append("选区含尚未解释的旧对象或其他结构；需复核源文档及附件")
            status = "needs_review" if reasons else "eligible_for_text_analysis"
        rows.append({"evidence_id": record["evidence_id"], "revision": record["source"]["revision"],
                     "comment_id": record["comment"]["id"], "status": status, "reasons": reasons})
    return {"comment_count": len(rows),
            "eligible_text_count": sum(row["status"] == "eligible_for_text_analysis" for row in rows),
            "needs_review_count": sum(row["status"] == "needs_review" for row in rows),
            "empty_text_count": sum(row["status"] == "empty_text_preserved" for row in rows),
            "interpretation": "可进入文字分析不等于规则成立或批注内容已验证；视觉和语义仍须另行核验",
            "items": rows}
