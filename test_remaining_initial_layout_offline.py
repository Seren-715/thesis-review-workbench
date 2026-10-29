"""Offline source, coverage and refusal checks; never starts Word/SVN/API."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import check_remaining_initial_layout_pilot as pilot

REVIEW = Path(r"D:\svn_agent\data\svn_exports\cross_student_rules_20261007_121139_462691\reviewed_pilot_20261007_121835_788699")


def audit_for(plan, pdf):
    source = plan["source"]
    pdf.write_bytes(b"offline fake PDF; not visual evidence")
    tables = []
    for i, original in enumerate(plan["native_tables"]):
        start, end = (10, 11) if i == 0 else (12, 12)
        tables.append({"index": original["index"], "nesting_level": original["nesting_level"],
                       "text": original["text"], "preceding_text": "offline mock caption",
                       "first_physical_page": start, "last_physical_page": end, "cross_page": start != end})
    pages = [] if not tables else [{"physical_page": p, "text": "mock body, no marker",
                                   "continuation_marker_matches": []} for p in (10, 11, 12)]
    return {"status": "native_table_layout_collected_visual_pending", "source_unchanged": True,
            "source": {"path": source["document_path"], "sha256": source["sha256"]},
            "source_after": {"path": source["document_path"], "sha256": source["sha256"]},
            "errors": [], "cleanup_errors": [], "layout_changed_during_export": False, "export_item": 7,
            "pdf": {"path": str(pdf), "sha256": pilot.digest(pdf)}, "tables_before": tables,
            "tables_after": copy.deepcopy(tables), "page_texts": pages,
            "table_results": [{"index": t["index"], "first_physical_page": t["first_physical_page"],
                              "last_physical_page": t["last_physical_page"], "visual_verified": False,
                              "continuation_marker_page_clues": [],
                              "result": "cross_page_no_continuation_text_clue" if t["cross_page"]
                              else "not_applicable_single_page"} for t in tables]}


def main():
    plans = pilot.prepare(REVIEW)
    assert [p["native_table_count"] for p in plans] == [0, 11]
    assert plans[0]["drawing_count"] == 23
    with tempfile.TemporaryDirectory(dir=r"D:\svn_agent\data") as temp:
        folder = Path(temp)
        for i, plan in enumerate(plans):
            pdf = folder / f"mock{i}.pdf"
            audit = audit_for(plan, pdf)
            assert len(pilot.validate(audit, plan, pdf)) == plan["native_table_count"]
            if plan["native_table_count"]:
                for mode in ("missing_table", "missing_page", "wrong_identity", "false_visual"):
                    bad = copy.deepcopy(audit)
                    if mode == "missing_table":
                        bad["table_results"].pop()
                    elif mode == "missing_page":
                        bad["page_texts"].pop(1)
                    elif mode == "wrong_identity":
                        bad["tables_before"][0]["text"] = "other table"
                    else:
                        bad["table_results"][0]["visual_verified"] = True
                    try:
                        pilot.validate(bad, plan, pdf)
                        raise AssertionError("Invalid evidence admitted: " + mode)
                    except ValueError:
                        pass
                marker = copy.deepcopy(audit)
                marker["page_texts"][1]["text"] = "续 表"
                marker["page_texts"][1]["continuation_marker_matches"] = [{"offset": 0, "text": "续 表"}]
                marker["table_results"][0]["result"] = "cross_page_marker_needs_table_association_review"
                marker["table_results"][0]["continuation_marker_page_clues"] = [11]
                pilot.validate(marker, plan, pdf)
        def fake_run(args, **kwargs):
            source_path = args[args.index("-InputPath") + 1]
            plan = next(p for p in plans if p["source"]["document_path"] == source_path)
            pdf = Path(args[args.index("-PdfPath") + 1])
            audit = audit_for(plan, pdf)
            Path(args[args.index("-AuditPath") + 1]).write_text(json.dumps(audit), encoding="utf-8")
            return SimpleNamespace(returncode=0)
        with patch.object(pilot, "prepare", return_value=plans), patch.object(pilot.subprocess, "run", side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()):
            assert pilot.execute(folder) == 0
        outputs = list(folder.glob("remaining_initial_layout_*"))
        assert len(outputs) == 1
        run = json.loads((outputs[0] / "run.json").read_text(encoding="utf-8"))
        assert run["results"][0]["status"] == "no_native_tables_images_visual_pending"
        assert run["visual_verified"] is False and run["results"][1]["cross_page_count"] == 1
        with patch.object(pilot, "prepare", return_value=plans), patch.object(pilot.subprocess, "run", return_value=SimpleNamespace(returncode=1)), contextlib.redirect_stdout(io.StringIO()):
            assert pilot.execute(folder) == 1
    print("PASS actual inventory P001 native0/drawings23, P002 native11; offline mocked collection/marker/coverage/identity/failure checks. No Word/SVN/API.")


if __name__ == "__main__":
    main()
