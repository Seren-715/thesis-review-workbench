"""Collect bounded P002/P003 teacher-comment evidence; never call an LLM.

Each student has one fixed thesis path. Export the newest teacher version; only
if it parses successfully and has zero nonempty comments, try the second newest.
Existing working copies and earlier outputs are never modified.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from docx_comment_reader import extract_docx
from extract_comments_pilot import collect_batch, write_json, write_report
from svn_export_pilot import OUTPUT_ROOT, WORKCOPY, run_svn
from comment_evidence_policy import assess_comments, check_supported_warnings


STUDENTS = (("P002", "212003113_李晨波"), ("P003", "212003180_周运龙"))
SNAPSHOT = 502
TEACHER = "mayy"
MAX_VERSIONS = 2


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_candidates(log_bytes: bytes, thesis_path: str) -> list[dict]:
    """Pure selection, exact path and teacher, newest-first, at most two items."""
    history = ET.fromstring(log_bytes)
    if history.tag != "log":
        raise ValueError("Unexpected SVN log XML root")
    candidates = []
    seen = set()
    for entry in history.findall("logentry"):
        revision = int(entry.attrib["revision"])
        if not 1 <= revision <= SNAPSHOT:
            raise ValueError("Log entry is outside the fixed 502:1 history range")
        if entry.findtext("author") != TEACHER:
            continue
        for path in entry.findall("paths/path"):
            if (path.text != thesis_path or path.get("kind") != "file"
                    or path.get("action") not in {"A", "M", "R"}):
                continue
            if (revision, thesis_path) in seen:
                raise ValueError("Duplicate exact thesis path in one SVN revision")
            seen.add((revision, thesis_path))
            commit_time = entry.findtext("date")
            if not commit_time:
                raise ValueError("Teacher commit has no timestamp")
            candidates.append({
                "revision": revision, "author": TEACHER,
                "commit_time": commit_time, "repository_path": thesis_path,
                "action": path.get("action"),
                "copyfrom_path": path.get("copyfrom-path"),
                "copyfrom_revision": path.get("copyfrom-rev"),
            })
    return sorted(candidates, key=lambda item: item["revision"], reverse=True)[:MAX_VERSIONS]


def save_evidence(child: Path) -> tuple[Path, dict]:
    """Use the existing evidence collector; write a fresh child evidence folder."""
    evidence = collect_batch(child)
    check_supported_warnings([warning["detail"] for warning in evidence["warnings"]])
    output = child / ("comment_evidence_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    for collection in (evidence["assets"], evidence["xml_parts"]):
        for relative, content in collection.items():
            rel = Path(relative)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("Unsafe evidence-relative path")
            destination = output / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    write_json(output / "comments.json", {"schema_version": 1, "comments": evidence["comments"]})
    write_json(output / "document_text.json", {"documents": evidence["documents"]})
    quality = assess_comments(evidence["comments"])
    write_json(output / "comment_quality.json", quality)
    saved_summary = {**evidence["summary"], "warnings": evidence["warnings"]}
    saved_summary.update(eligible_text_count=quality["eligible_text_count"], needs_review_count=quality["needs_review_count"])
    write_json(output / "summary.json", saved_summary)
    write_report(output, evidence)
    return output, saved_summary


def collect_student(parent: Path, sample_id: str, student: str, repository_root: str) -> dict:
    """Collect one fixed student; all failure evidence stays in its own folder."""
    if (sample_id, student) not in STUDENTS:
        raise ValueError("Only the two fixed student/sample mappings are allowed")
    child = parent / sample_id
    child.mkdir(exist_ok=False)
    prefix = f"/{student}/论文/"
    thesis_path = prefix + f"{student}_毕业论文.docx"
    query_url = repository_root + "/" + quote(student, safe="") + f"@{SNAPSHOT}"
    manifest = {
        "sample_id": sample_id, "created_at": now_iso(), "snapshot_revision": SNAPSHOT,
        "teacher_author": TEACHER, "query_url": query_url,
        "revision_range": f"{SNAPSHOT}:1", "path_prefix": prefix,
        "exact_thesis_path": thesis_path, "max_files": MAX_VERSIONS,
        "candidate_limit": MAX_VERSIONS, "candidate_count": 0,
        "candidates": [], "files": [], "status": "prepared", "llm_requests": 0,
        "fallback_policy": "try_second_only_if_first_parses_with_zero_nonempty_comments",
    }
    report = {
        "sample_id": sample_id, "status": "prepared", "export_attempts": 0,
        "exported_count": 0, "comment_count": None,
        "nonempty_text_comments": None, "evidence_dir": None,
        "student_dir": str(child), "llm_requests": 0,
        "comment_author_counts": {},
        "author_interpretation": "教师SVN提交不证明全部文档批注由该教师亲自撰写；作者需后续核对。",
    }
    extracted_summary = None
    evidence_dir = None
    author_counts: Counter[str] = Counter()
    try:
        write_json(child / "manifest.json", manifest)
        raw_log = run_svn("log", "--xml", "--verbose", "-r", f"{SNAPSHOT}:1", query_url, cwd=WORKCOPY)
        (child / "svn_log.xml").write_bytes(raw_log)
        candidates = select_candidates(raw_log, thesis_path)
        manifest.update(candidates=candidates, candidate_count=len(candidates),
                        history_sha256=hashlib.sha256(raw_log).hexdigest())
        write_json(child / "manifest.json", manifest)
        if not candidates:
            report.update(status="no_teacher_versions", comment_count=0, nonempty_text_comments=0)
        else:
            for index, candidate in enumerate(candidates, start=1):
                filename = f"{sample_id}_r{candidate['revision']}_mayy_{index:02d}.docx"
                target = child / filename
                url = repository_root + quote(thesis_path, safe="/") + f"@{candidate['revision']}"
                item = {
                    **candidate, "source_url": url, "exported_path": str(target),
                    "status": "export_started", "comments_checked": False,
                }
                manifest["files"].append(item)
                report["export_attempts"] += 1
                write_json(child / "manifest.json", manifest)
                try:
                    run_svn("export", "--quiet", "-r", candidate["revision"], url, filename, cwd=child)
                    item.update(status="exported", size_bytes=target.stat().st_size, sha256=sha256(target))
                    report["exported_count"] += 1
                    write_json(child / "manifest.json", manifest)
                    source = {
                        "sample_id": sample_id, "revision": item["revision"],
                        "svn_author": item["author"], "svn_commit_time": item["commit_time"],
                        "repository_path": item["repository_path"], "source_url": url,
                        "document_path": str(target), "sha256": item["sha256"],
                    }
                    extracted = extract_docx(target, source)
                    check_supported_warnings(extracted["warnings"])
                    quality = assess_comments(extracted["comments"])
                    nonempty = sum(not record["comment"]["empty_text"] for record in extracted["comments"])
                    counts = Counter(record["comment"].get("author") or "[missing_author]"
                                     for record in extracted["comments"])
                    author_counts.update(counts)
                    item.update(comments_checked=True, extraction_status="parsed",
                                comment_count=len(extracted["comments"]), nonempty_text_comments=nonempty,
                                comment_author_counts=dict(counts), warnings=extracted["warnings"],
                                eligible_text_count=quality["eligible_text_count"], needs_review_count=quality["needs_review_count"])
                except Exception as error:
                    if item["status"] == "exported":
                        item["extraction_status"] = "failed"
                    else:
                        item["status"] = "failed"
                    item.update(error_type=type(error).__name__, error=str(error))
                    raise
                finally:
                    write_json(child / "manifest.json", manifest)
                if nonempty:
                    break
                # The only fallback is a successfully parsed, zero-text version.
            evidence_dir, extracted_summary = save_evidence(child)
            report.update(
                comment_count=extracted_summary["comment_count"],
                nonempty_text_comments=extracted_summary["nonempty_text_comments"],
                eligible_text_count=extracted_summary["eligible_text_count"],
                needs_review_count=extracted_summary["needs_review_count"],
                evidence_dir=str(evidence_dir),
                status=("collected" if extracted_summary["eligible_text_count"] else
                        "review_only" if extracted_summary["nonempty_text_comments"] else "no_nonempty_comments"),
            )
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        report["comment_author_counts"] = dict(author_counts)
        manifest.update(status=report["status"], finished_at=now_iso(), result=report)
        try:
            write_json(child / "manifest.json", manifest)
            # The batch collector ran before this final status write. Bind the
            # delivered evidence summary to the final manifest bytes as well.
            if extracted_summary is not None and evidence_dir is not None:
                extracted_summary["source_manifest_sha256"] = sha256(child / "manifest.json")
                extracted_summary["comment_author_counts"] = dict(author_counts)
                extracted_summary["author_interpretation"] = report["author_interpretation"]
                write_json(evidence_dir / "summary.json", extracted_summary)
        except Exception as error:
            # Preserve actual attempt/export counts even when persistence fails.
            report.update(status="failed", error_type=type(error).__name__, error=str(error))
    return report


def collect_students(output_root: Path = OUTPUT_ROOT) -> tuple[Path, dict]:
    """Run bounded SVN collection; call only when ready for real read-only exports."""
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / ("cross_student_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    summary = {
        "created_at": now_iso(), "status": "prepared", "snapshot_revision": SNAPSHOT,
        "students": [], "valid_student_count": 0, "export_attempts": 0,
        "exported_count": 0, "llm_requests": 0, "export_limit": 4,
        "interpretation": "仅两学生小批次批注采集；不代表全集、常见性或规则正确性已验证。",
    }
    try:
        raw_info = run_svn("info", "--xml", ".", cwd=WORKCOPY)
        (output / "svn_info.xml").write_bytes(raw_info)
        info = ET.fromstring(raw_info)
        entry = info.find("entry")
        if entry is None or int(entry.attrib["revision"]) != SNAPSHOT:
            raise ValueError("Working-copy base revision must be exactly 502")
        repository_root = (entry.findtext("repository/root") or "").rstrip("/")
        if not repository_root:
            raise ValueError("SVN info did not supply a repository root URL")
        summary["repository_root"] = repository_root
        for sample_id, student in STUDENTS:
            try:
                result = collect_student(output, sample_id, student, repository_root)
            except Exception as error:
                # Even local persistence failure for one student does not stop the other.
                result = {"sample_id": sample_id, "status": "failed",
                          "export_attempts": 0, "exported_count": 0,
                          "comment_count": None, "nonempty_text_comments": None,
                          "error_type": type(error).__name__, "error": str(error)}
            summary["students"].append(result)
        summary["valid_student_count"] = sum(row["status"] == "collected" for row in summary["students"])
        summary["export_attempts"] = sum(row["export_attempts"] for row in summary["students"])
        summary["exported_count"] = sum(row["exported_count"] for row in summary["students"])
        summary["status"] = "collected_two_students" if summary["valid_student_count"] == 2 else "incomplete"
    except Exception as error:
        summary.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        summary["finished_at"] = now_iso()
        write_json(output / "summary.json", summary)
    return output, summary


def main() -> int:
    output, summary = collect_students()
    for student in summary["students"]:
        count = student["comment_count"] if student["comment_count"] is not None else "未完成"
        print(f"{student['sample_id']}：导出 {student['exported_count']} 份；批注 {count}；状态 {student['status']}")
    print(f"有效学生：{summary['valid_student_count']}/2；本轮LLM请求：0")
    if summary.get("error"):
        print("任务未完成：", summary["error"])
    print("批次目录：", output)
    print("汇总清单：", output / "summary.json")
    return 0 if summary["status"] == "collected_two_students" else 1


if __name__ == "__main__":
    raise SystemExit(main())
