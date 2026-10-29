"""Verify original SVN bytes and the separately audited analysis copy."""
from pathlib import Path
import hashlib
import json


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_cleanup_only_failure(audit):
    """Permit text recovery only for the specific reviewed COM cleanup defect."""
    from process_p002_doc_pilot import normalize_word_text
    if (audit.get("status") != "failed" or audit.get("errors") != []
            or len(audit.get("cleanup_errors", [])) != 1
            or audit["cleanup_errors"][0] != '参数“1”应为 System.Management.Automation.PSReference。请使用 [ref]。'
            or not all(audit.get(key) is True for key in
                       ("source_sha256_unchanged", "comment_fidelity_verified",
                        "comment_metadata_sequence_verified", "body_text_hash_equal"))):
        raise ValueError("Not the reviewed cleanup-only failure; do not recover automatically")
    before, after = audit["comments_before"], audit["comments_after"]
    if len(before) != audit["comments_before_count"] or len(after) != audit["comments_after_count"] or len(before) != len(after):
        raise ValueError("Comment sequence counts differ")
    for left, right in zip(before, after):
        for key in ("word_index", "author", "initials", "date"):
            if left[key] != right[key]:
                raise ValueError("Recorded comment metadata/order differs")
        for key in ("range_text", "scope_text"):
            if normalize_word_text(left[key]) != normalize_word_text(right[key]):
                raise ValueError("Recorded comment text/selected text differs")
    if (audit["source"]["sha256"] != audit["source_after"]["sha256"]
            or audit["original_body_text_utf8_sha256"] != audit["derived_body_text_utf8_sha256"]):
        raise ValueError("Recorded source/body hashes differ")


def validate_lineage(batch, item):
    if item.get("artifact_role") != "analysis_copy_from_legacy_svn_doc":
        raise ValueError("Unknown derived artifact role")
    original = item["original_export"]
    conversion = item["conversion"]
    for metadata in (original, conversion):
        path = Path(metadata["document_path"]).resolve()
        if not path.is_relative_to(batch.resolve()) or file_hash(path) != metadata["sha256"]:
            raise ValueError("Legacy source or conversion audit hash/path mismatch")
    audit = json.loads(Path(conversion["document_path"]).read_text(encoding="utf-8-sig"))
    accepted = {"comment_text_fidelity_verified_visual_pending", "zip_container_analysis_copy",
                "recovered_verified_text_cleanup_pending"}
    if audit["status"] not in accepted or not audit["source_sha256_unchanged"]:
        raise ValueError("Conversion fidelity not accepted")
    derived_path = Path(item["exported_path"]).resolve()
    if (audit["source"]["sha256"] != original["sha256"]
            or Path(audit["source"]["path"]).resolve() != Path(original["document_path"]).resolve()
            or audit["derived"]["sha256"] != item["sha256"]
            or Path(audit["derived"]["path"]).resolve() != derived_path
            or file_hash(derived_path) != item["sha256"]):
        raise ValueError("Original/derived audit binding differs from manifest")
    if audit["status"] == "zip_container_analysis_copy":
        if original["sha256"] != item["sha256"]:
            raise ValueError("ZIP analysis copy must preserve exact source bytes")
    elif audit["status"] == "recovered_verified_text_cleanup_pending":
        supporting_path = Path(audit["supporting_audit"]["document_path"]).resolve()
        if not supporting_path.is_relative_to(batch.resolve()) or file_hash(supporting_path) != audit["supporting_audit"]["sha256"]:
            raise ValueError("Supporting failed Word audit hash mismatch")
        supporting = json.loads(supporting_path.read_text(encoding="utf-8-sig"))
        verify_cleanup_only_failure(supporting)
        if (supporting["source"]["sha256"] != original["sha256"]
                or supporting["derived"]["sha256"] != item["sha256"]):
            raise ValueError("Supporting Word audit does not bind these exact original/derived bytes")
    elif not (audit["comment_fidelity_verified"] and audit["comment_metadata_sequence_verified"]
              and audit["body_text_hash_equal"]):
        raise ValueError("Word comment sequence/body fidelity was not verified")
    return {"artifact_role": item["artifact_role"], "original_export": original,
            "conversion": conversion, "visual_verification": "not_verified",
            "word_cleanup_status": audit.get("word_cleanup_status", "completed")}
