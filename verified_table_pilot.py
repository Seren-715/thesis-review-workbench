"""Read the pinned, visually reviewed P003 pagination receipt; no Word/SVN/LLM."""
import json
from pathlib import Path
from cross_student_rule_pilot import digest
from inspect_initial_structure_pilot import collect

RECEIPT_SHA256 = "1fed3ab81cd1b0682934c08b0e739e4f9d8da86299d85011bcdad654dcb01543"
RECEIPT_RELATIVE = Path("table_pagination_20261007_143020_151426/visual_review/verification.json")


def load_verified_pagination(review_dir, verification_path):
    review_dir, verification_path = Path(review_dir).resolve(), Path(verification_path).resolve()
    if verification_path != (review_dir / RECEIPT_RELATIVE).resolve() or digest(verification_path) != RECEIPT_SHA256:
        raise ValueError("Only the pinned reviewed pagination receipt is eligible")
    receipt = json.loads(verification_path.read_text(encoding="utf-8"))
    if receipt["status"] != "verified_p003_table4_1_markup_pilot" or type(receipt["export_item"]) is not int or receipt["export_item"] != 7:
        raise ValueError("Visual acceptance or export mode is missing")
    artifacts = receipt["artifacts"]
    paths = [Path(row["path"]).resolve() for row in artifacts]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate evidence artifacts")
    for row, path in zip(artifacts, paths):
        if digest(path) != row["sha256"]:
            raise ValueError("Pagination evidence artifact changed: " + str(path))
    runs = [row for row in artifacts if row["role"] == "original_run"]
    if len(runs) != 1:
        raise ValueError("Expected one original pagination run")
    run = json.loads(Path(runs[0]["path"]).read_text(encoding="utf-8"))
    if run["export_item"] != 7 or run["status"] != "layout_evidence_collected_visual_pending":
        raise ValueError("Unexpected underlying Word export")
    sources = {row["source"]["revision"]: row["source"] for row in run["results"]}
    if len(run["results"]) != 2 or set(sources) != {246, 249}:
        raise ValueError("Expected earlier r246 and teacher r249 only")
    listed = {Path(row["path"]).resolve(): row for row in artifacts}
    for row in run["results"]:
        source = row["source"]
        for path, sha, role in [(source["document_path"], source["sha256"], "source"),
                                (row["pdf_path"], row["pdf_sha256"], "pdf"),
                                (row["audit_path"], row["audit_sha256"], "word_audit")]:
            evidence = listed.get(Path(path).resolve())
            if evidence is None or evidence["sha256"] != sha or evidence["role"] != role:
                raise ValueError("Run artifact is not covered by visual receipt")
        audit = json.loads(Path(row["audit_path"]).read_text(encoding="utf-8-sig"))
        if audit["export_item"] != 7 or audit["source_unchanged"] is not True or audit["errors"] or audit["cleanup_errors"]:
            raise ValueError("Word source/cleanup/export audit failed")
    initial = collect(review_dir)
    rows = []
    for row in initial:
        source = row["source"]
        status = "not_checked"
        if source["sample_id"] == "P003":
            if any(source[key] != sources[246][key] for key in ("sample_id", "revision", "sha256")):
                raise ValueError("P003 initial differs from visually reviewed r246")
            status = "not_applicable_single_page"
        rows.append({key: source[key] for key in ("sample_id", "revision", "sha256")} | {"table_status": status})
    teacher = {"sample_id": "P003", "revision": 249, "table_title": receipt["table_title"],
               "physical_pages": [23, 24], "verdict": "confirmed_missing_continuation_label_in_markup_layout", "export_item": 7}
    return {"items": rows, "teacher_reference": teacher, "verification_path": str(verification_path),
            "verification_sha256": RECEIPT_SHA256, "receipt": receipt,
            "limits": "P001/P002 tables not checked; P003 only table4-1 in markup layout. Teacher r249 is not an initial paper. No later repair or accuracy claim."}
