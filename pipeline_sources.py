"""Bounded source stages for paper_pipeline; no API or Word integration.

Injected ``invoke(argv, cwd=Path(...))`` must return bytes or a CompletedProcess.
The default invokes SVN without a shell. Every attempt is recorded before dispatch.
These helpers create additive stage outputs; their caller owns pipeline resume.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from docx_comment_reader import extract_docx
from extract_comments_pilot import collect_batch, write_report
from inspect_initial_structure_pilot import inspect_document
from triage_initial_first_person_pilot import triage


class PipelineSourceError(RuntimeError):
    def __init__(self, status: str, state_path: Path):
        super().__init__(f"{status}; stage ledger: {state_path}")
        self.status = status
        self.state_path = str(state_path)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _artifact(path, role):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _digest(path), "size_bytes": path.stat().st_size, "role": role}


def _verify_artifacts(artifacts):
    for item in artifacts:
        if _digest(item["path"]) != item["sha256"]:
            raise ValueError("Pinned source/stage artifact changed")


def _positive(value, field, maximum=None):
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {field}")


def validate_config(config):
    """Return validated public configuration; credentials never belong here."""
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    if set(config) != {"workcopy", "svn_executable", "snapshot_revision", "teacher_author", "max_teacher_files", "samples"}:
        raise ValueError("Unexpected configuration fields; keep credentials outside config")
    result = deepcopy(config)
    for name in ("workcopy", "svn_executable", "teacher_author"):
        if not isinstance(result.get(name), str) or not result[name].strip() or "\x00" in result[name]:
            raise ValueError(f"Invalid {name}")
    for name in ("workcopy", "svn_executable"):
        if not Path(result[name]).is_absolute():
            raise ValueError(f"{name} must be absolute")
    _positive(result.get("snapshot_revision"), "snapshot_revision")
    _positive(result.get("max_teacher_files"), "max_teacher_files", 2)
    samples = result.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 3:
        raise ValueError("samples must contain one to three items")
    seen, folders = set(), set()
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("Sample must be an object")
        if set(sample) != {"sample_id", "student_folder", "initial_revision", "initial_document_path"}:
            raise ValueError("Unexpected sample configuration fields")
        sid = sample.get("sample_id")
        if (not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", sid)
                or sid.upper() in reserved or sid.casefold() in seen):
            raise ValueError("Unsafe/duplicate sample_id")
        seen.add(sid.casefold())
        folder = sample.get("student_folder")
        if (not isinstance(folder, str) or not folder or folder in {".", ".."}
                or any(c in folder for c in '/\\:\x00<>"|?*') or folder.endswith((".", " "))
                or folder.casefold() in folders):
            raise ValueError("student_folder must be a unique single directory name")
        folders.add(folder.casefold())
        _positive(sample.get("initial_revision"), "initial_revision")
        if sample["initial_revision"] > result["snapshot_revision"]:
            raise ValueError("Configured student version exceeds snapshot")
        path = sample.get("initial_document_path")
        if (not isinstance(path, str) or not path.startswith(f"/{folder}/论文/")
                or "\\" in path or "\x00" in path or any(x in {"", ".", ".."} for x in path.split("/")[1:])
                or PurePosixPath(path).suffix.lower() not in {".doc", ".docx"}):
            raise ValueError("initial_document_path must be a thesis file under the student's repository directory")
    return result


def _stage(output, name):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / (name + "_state.json")
    if state_path.exists():
        raise ValueError("Stage output already exists; caller must verify/reuse or choose a new attempt directory")
    state = {"schema_version": 1, "stage": name, "status": "prepared",
             "created_at": datetime.now().astimezone().isoformat(), "samples": [],
             "svn_attempts": [], "api_requests": 0, "word_launches": 0, "automatic_retries": 0}
    _save(state_path, state)
    return output, state_path, state


def _default_invoke(argv, *, cwd):
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, timeout=60, shell=False)


def export_samples(config, output, invoke=None):
    """Export teacher candidates and explicitly configured student versions.

    Teacher SVN author is a selection fact, not proof of comment authorship.
    Failures preserve originals/manifests and raise PipelineSourceError; no retry.
    """
    config = validate_config(config)
    output, ledger_path, state = _stage(output, "export")
    invocation = invoke or _default_invoke
    artifacts, results = [], []

    def svn(operation, arguments, cwd, sample_id=None):
        attempt = {"number": len(state["svn_attempts"]) + 1, "operation": operation,
                   "sample_id": sample_id, "status": "started", "outcome": "unknown"}
        state["svn_attempts"].append(attempt)
        state["status"] = "running"
        _save(ledger_path, state)
        try:
            response = invocation([config["svn_executable"], *map(str, arguments), "--non-interactive"], cwd=Path(cwd))
            if isinstance(response, bytes):
                data = response
            else:
                if response.returncode != 0:
                    raise RuntimeError("SVN exited unsuccessfully")
                data = response.stdout
                if isinstance(data, str):
                    data = data.encode("utf-8")
            if not isinstance(data, bytes):
                raise TypeError("invoke must return bytes or CompletedProcess")
            attempt.update(status="completed", outcome="returned")
            _save(ledger_path, state)
            return data
        except Exception as exc:
            attempt.update(status="failed", error_type=type(exc).__name__)
            _save(ledger_path, state)
            raise

    try:
        raw_info = svn("info", ["info", "--xml", "."], config["workcopy"])
        info_path = output / "svn_info.xml"
        info_path.write_bytes(raw_info)
        artifacts.append(_artifact(info_path, "repository_info"))
        entry = ET.fromstring(raw_info).find("entry")
        if entry is None or int(entry.get("revision", "0")) < config["snapshot_revision"]:
            raise ValueError("Working-copy info does not cover the requested snapshot")
        root = entry.findtext("repository/root")
        if not root or urlsplit(root).username is not None or urlsplit(root).password is not None:
            raise ValueError("Repository root is missing or contains credential information")
        root = root.rstrip("/")
        state["repository_root"] = root
        state["snapshot_revision"] = config["snapshot_revision"]
        for configured in config["samples"]:
            sid, folder = configured["sample_id"], configured["student_folder"]
            sample_dir = output / sid
            sample_dir.mkdir(exist_ok=False)
            batch = sample_dir / "teacher_exports"
            batch.mkdir()
            snapshot = config["snapshot_revision"]
            query_url = root + "/" + quote(folder, safe="") + f"@{snapshot}"
            raw_log = svn("log", ["log", "--xml", "--verbose", "-r", f"{snapshot}:1", query_url], config["workcopy"], sid)
            log_path = batch / "svn_log.xml"
            log_path.write_bytes(raw_log)
            artifacts.append(_artifact(log_path, "student_history"))
            prefix = f"/{folder}/论文/"
            candidates, initial_matches, seen = [], [], set()
            for commit in ET.fromstring(raw_log).findall("logentry"):
                revision, author = int(commit.attrib["revision"]), commit.findtext("author")
                if not 1 <= revision <= snapshot:
                    raise ValueError("Log revision is outside the requested snapshot")
                for changed in commit.findall("paths/path"):
                    path = changed.text or ""
                    if changed.get("kind") != "file" or changed.get("action") not in {"A", "M", "R"}:
                        continue
                    common = {"revision": revision, "author": author, "commit_time": commit.findtext("date"),
                              "commit_message": commit.findtext("msg") or "", "repository_path": path,
                              "action": changed.get("action"), "copyfrom_path": changed.get("copyfrom-path"),
                              "copyfrom_revision": changed.get("copyfrom-rev")}
                    if revision == configured["initial_revision"] and path == configured["initial_document_path"]:
                        initial_matches.append(common)
                    key = (revision, path)
                    if (author == config["teacher_author"] and path.startswith(prefix)
                            and PurePosixPath(path).suffix.lower() in {".doc", ".docx"} and key not in seen):
                        seen.add(key)
                        candidates.append(common)
            candidates.sort(key=lambda row: (-row["revision"], row["repository_path"]))
            selected = candidates[:config["max_teacher_files"]]
            files = []
            for index, row in enumerate(selected, 1):
                suffix = PurePosixPath(row["repository_path"]).suffix.lower()
                destination = batch / f"{sid}_teacher_r{row['revision']}_{index:02d}{suffix}"
                files.append({**row, "source_url": root + quote(row["repository_path"], safe="/") + f"@{row['revision']}",
                              "exported_path": str(destination), "status": "not_attempted", "comments_checked": False})
            manifest = {"sample_id": sid, "created_at": datetime.now().astimezone().isoformat(),
                        "query_url": query_url, "snapshot_revision": snapshot, "revision_range": f"{snapshot}:1",
                        "teacher_author": config["teacher_author"], "path_prefix": prefix,
                        "max_files": config["max_teacher_files"], "candidate_count": len(candidates),
                        "candidates": candidates, "files": files, "svn_log_sha256": _digest(log_path)}
            manifest_path = batch / "manifest.json"
            _save(manifest_path, manifest)
            state_sample = {"sample_id": sid, "teacher_batch": str(batch), "status": "history_saved"}
            state["samples"].append(state_sample)
            _save(ledger_path, state)
            if not selected:
                state["status"] = "teacher_versions_not_found"
                raise ValueError("No teacher thesis candidates in bounded history")
            if (len(initial_matches) != 1 or not initial_matches[0]["author"]
                    or initial_matches[0]["author"] == config["teacher_author"]):
                state["status"] = "configured_student_version_unverified"
                raise ValueError("Configured student version lacks a unique non-teacher file change in saved history")
            for row in files:
                try:
                    svn("export_teacher", ["export", "--quiet", "-r", row["revision"], row["source_url"], row["exported_path"]], batch, sid)
                    path = Path(row["exported_path"])
                    row.update(status="exported", sha256=_digest(path), size_bytes=path.stat().st_size)
                    artifacts.append(_artifact(path, "teacher_original"))
                    _save(manifest_path, manifest)
                except Exception as exc:
                    row.update(status="failed", error_type=type(exc).__name__)
                    _save(manifest_path, manifest)
                    raise
            artifacts.append(_artifact(manifest_path, "teacher_manifest"))
            selected_initial = initial_matches[0]
            initial_path = sample_dir / f"{sid}_student_r{selected_initial['revision']}{PurePosixPath(selected_initial['repository_path']).suffix.lower()}"
            initial_url = root + quote(selected_initial["repository_path"], safe="/") + f"@{selected_initial['revision']}"
            state_sample.update(status="student_export_started", initial_document_path=str(initial_path))
            _save(ledger_path, state)
            svn("export_student", ["export", "--quiet", "-r", selected_initial["revision"], initial_url, str(initial_path)], sample_dir, sid)
            initial_source = {"sample_id": sid, "revision": selected_initial["revision"],
                              "svn_author": selected_initial["author"], "svn_commit_time": selected_initial["commit_time"],
                              "repository_path": selected_initial["repository_path"], "source_url": initial_url,
                              "document_path": str(initial_path), "sha256": _digest(initial_path),
                              "size_bytes": initial_path.stat().st_size, "version_role": "configured_student_version",
                              "action": selected_initial["action"], "copyfrom_path": selected_initial["copyfrom_path"],
                              "copyfrom_revision": selected_initial["copyfrom_revision"],
                              "selection_basis": "Explicit configured revision/path; non-teacher change verified in saved log; earliest/initial status not established.",
                              "history_sha256": _digest(log_path), "snapshot_revision": snapshot}
            source_path = sample_dir / "initial_source.json"
            _save(source_path, initial_source)
            artifacts.extend([_artifact(initial_path, "configured_student_original"), _artifact(source_path, "configured_student_identity")])
            state_sample.update(status="exported", initial_source=initial_source)
            results.append({"sample_id": sid, "teacher_batch": str(batch), "initial_source": initial_source,
                            "teacher_manifest_sha256": _digest(manifest_path), "history_sha256": _digest(log_path)})
            _save(ledger_path, state)
        state.update(status="exported", artifacts=artifacts)
        _save(ledger_path, state)
        return {"schema_version": 1, "status": "exported", "samples": results,
                "artifacts": [*artifacts, _artifact(ledger_path, "export_ledger")],
                "state_path": str(ledger_path), "svn_request_attempts": len(state["svn_attempts"])}
    except Exception as exc:
        if state["status"] in {"running", "prepared"}:
            state["status"] = "failed_no_retry"
        state["error_type"] = type(exc).__name__
        state["finished_at"] = datetime.now().astimezone().isoformat()
        _save(ledger_path, state)
        raise PipelineSourceError(state["status"], ledger_path) from None


def _write_evidence(directory, evidence):
    directory.mkdir(exist_ok=False)
    for collection in (evidence["assets"], evidence["xml_parts"]):
        for relative, content in collection.items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe evidence output-relative path")
            destination = directory / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    _save(directory / "comments.json", {"schema_version": 1, "comments": evidence["comments"]})
    _save(directory / "document_text.json", {"documents": evidence["documents"]})
    _save(directory / "summary.json", {**evidence["summary"], "warnings": evidence["warnings"]})
    write_report(directory, evidence)
    return [_artifact(path, "teacher_comment_evidence") for path in sorted(directory.rglob("*")) if path.is_file()]


def extract_samples(exported, output):
    """Preserve all extracted evidence; DOC requires a separate reviewed conversion."""
    output, ledger_path, state = _stage(output, "extract")
    artifacts, results = [], []
    try:
        if exported.get("status") != "exported" or not exported.get("samples"):
            raise ValueError("Export stage is not complete")
        _verify_artifacts(exported["artifacts"])
        # Preflight every selected source; a DOC is never silently skipped.
        for sample in exported["samples"]:
            batch = Path(sample["teacher_batch"])
            manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8-sig"))
            if manifest["sample_id"] != sample["sample_id"] or _digest(batch / "manifest.json") != sample["teacher_manifest_sha256"]:
                raise ValueError("Teacher manifest identity changed")
            if not manifest["files"] or any(row.get("status") != "exported" for row in manifest["files"]):
                raise ValueError("Selected teacher exports are incomplete")
            paths = [Path(row["exported_path"]) for row in manifest["files"]] + [Path(sample["initial_source"]["document_path"])]
            unsupported = [str(path) for path in paths if path.suffix.lower() != ".docx"]
            if unsupported:
                state.update(status="needs_conversion", blocked_files=unsupported)
                raise ValueError("Original DOC retained; reviewed conversion is required")
        for sample in exported["samples"]:
            evidence = collect_batch(Path(sample["teacher_batch"]))
            directory = output / sample["sample_id"]
            additions = _write_evidence(directory, evidence)
            artifacts.extend(additions)
            row = {"sample_id": sample["sample_id"], "teacher_batch": sample["teacher_batch"], "evidence_dir": str(directory),
                   "sources": [doc["source"] for doc in evidence["documents"]],
                   "summary": evidence["summary"], "warnings": evidence["warnings"],
                   "source_manifest_sha256": sample["teacher_manifest_sha256"],
                   "input_artifacts": deepcopy(exported["artifacts"])}
            results.append(row)
            state["samples"].append({"sample_id": row["sample_id"], "status": "extracted", "evidence_dir": str(directory)})
            _save(ledger_path, state)
        _verify_artifacts(exported["artifacts"])
        state.update(status="extracted", artifacts=artifacts)
        _save(ledger_path, state)
        return {"schema_version": 1, "status": "extracted", "samples": results,
                "artifacts": [*artifacts, _artifact(ledger_path, "extract_ledger")],
                "input_artifacts": deepcopy(exported["artifacts"]), "state_path": str(ledger_path)}
    except Exception as exc:
        if state["status"] != "needs_conversion":
            state["status"] = "failed_no_retry"
        state["error_type"] = type(exc).__name__
        _save(ledger_path, state)
        raise PipelineSourceError(state["status"], ledger_path) from None


def check_samples(extracted, exported, output):
    """Offline textual/outline/native-table clues, never candidate-rule execution."""
    output, ledger_path, state = _stage(output, "check")
    artifacts, results = [], []
    try:
        if extracted.get("status") != "extracted" or exported.get("status") != "exported":
            raise ValueError("Source stages are incomplete")
        _verify_artifacts(exported["artifacts"])
        _verify_artifacts(extracted["artifacts"])
        expected = [sample["sample_id"] for sample in exported["samples"]]
        if [sample["sample_id"] for sample in extracted["samples"]] != expected:
            raise ValueError("Extracted and exported sample sets differ")
        for sample, evidence in zip(exported["samples"], extracted["samples"]):
            source = deepcopy(sample["initial_source"])
            path = Path(source["document_path"])
            if path.suffix.lower() != ".docx":
                state["status"] = "needs_conversion"
                raise ValueError("Configured student DOC requires reviewed conversion")
            read = extract_docx(path, source)
            with ZipFile(path) as package:
                root = ET.fromstring(package.read("word/document.xml"))
                has_styles = "word/styles.xml" in package.namelist()
            w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            body = root.find(w + "body")
            if body is None:
                raise ValueError("Missing DOCX body")
            count = sum(1 for _ in body.iter(w + "tbl"))
            structure = inspect_document(source) if has_styles else {
                "source": source, "status": "cannot_establish_chapter_structure", "all_outline_headings": [],
                "relevant_headings": [], "confirmed_error_count": None, "limitations": "word/styles.xml absent; no saved-outline structure inferred."}
            row = {"sample_id": sample["sample_id"], "source": source,
                   "evaluation_role": "retrospective_development_clues", "teacher_evidence_dir": evidence["evidence_dir"],
                   "first_person_clues": triage(read["body_paragraphs"]), "saved_outline_clues": structure,
                   "native_xml_table_count": count, "native_xml_table_count_policy": "All saved body w:tbl nodes, including nested or revision content; not rendered Word tables.",
                   "table_status": "needs_visual_review" if count else "native_tables_absent_image_tables_not_assessed",
                   "reader_warnings": read["warnings"], "rule_activation_status": "not_activated",
                   "confirmed_error_count": None, "review_required": True,
                   "limits": "Generic local clue policies only; no candidate rules executed, no semantic judgment, no teacher applicability established. Saved outline is not visual chapter validation. Image tables, page breaks, headers and footnotes are not checked. Teacher-derived checks on the same students are not independent accuracy tests."}
            if _digest(path) != source["sha256"]:
                raise ValueError("Configured student source changed during clue checks")
            directory = output / sample["sample_id"]
            directory.mkdir(exist_ok=False)
            result_path = directory / "checks.json"
            _save(result_path, row)
            artifacts.append(_artifact(result_path, "configured_student_clues"))
            results.append(row)
            state["samples"].append({"sample_id": sample["sample_id"], "status": "clues_completed_review_required"})
            _save(ledger_path, state)
        _verify_artifacts(exported["artifacts"])
        _verify_artifacts(extracted["artifacts"])
        state.update(status="clues_completed_review_required", artifacts=artifacts)
        _save(ledger_path, state)
        return {"schema_version": 1, "status": "clues_completed_review_required", "samples": results,
                "artifacts": [*artifacts, _artifact(ledger_path, "check_ledger")], "state_path": str(ledger_path),
                "review_required": True, "activation_status": "not_activated", "confirmed_error_count": None}
    except Exception as exc:
        if state["status"] != "needs_conversion":
            state["status"] = "failed_no_retry"
        state["error_type"] = type(exc).__name__
        _save(ledger_path, state)
        raise PipelineSourceError(state["status"], ledger_path) from None
