"""Read standard OOXML comments without modifying documents or writing results.

Only ``extract_docx`` is public.  The caller owns saving the returned XML and
binary evidence.  Paragraph indices are one-based XML-order indices, including
table paragraphs; they are not Word page numbers.  This is a bounded OOXML
reader, not a Word renderer, revision accepter, field evaluator, or OCR engine.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import posixpath
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import ZipFile


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
V = "{urn:schemas-microsoft-com:vml}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
MAX_PART_BYTES = 64 * 1024 * 1024
TEXT_POLICY = (
    "XML-order current-view text: w:t and m:t; tabs and breaks preserved; "
    "w:del/w:moveFrom/w:delText and field instructions excluded. "
    "Cached field results are not recomputed. Math text is linearized; "
    "original OMML is attached. Images are evidence attachments, not OCR text."
)


def extract_docx(path: Path, source: dict) -> dict:
    """Return comments, location evidence and in-memory evidence assets.

    Raises ValueError on a declared SHA mismatch, malformed required structure,
    duplicate comment IDs, duplicate ZIP members or duplicate relationship IDs.
    Missing/ambiguous anchors and unsupported optional features are preserved
    with explicit status/warnings.  No file or network writes occur.
    """
    path = Path(path)
    if not isinstance(source, dict):
        raise TypeError("source must be a dict")
    document_bytes = path.read_bytes()
    digest = hashlib.sha256(document_bytes).hexdigest()
    declared = source.get("sha256")
    if declared is not None and (
        not isinstance(declared, str) or declared.lower() != digest
    ):
        raise ValueError("source SHA-256 does not match the DOCX")
    warnings: list[str] = []
    assets: dict[str, bytes] = {}
    xml_parts: dict[str, bytes] = {}
    paragraphs: list[dict] = []
    comment_records: list[dict] = []

    def warn(message: str) -> None:
        if message not in warnings:
            warnings.append(message)

    # Parse exactly the bytes whose hash was checked, even if the path changes.
    with ZipFile(BytesIO(document_bytes)) as package:
        names = package.namelist()
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        if duplicates:
            raise ValueError("duplicate ZIP members: " + repr(duplicates))
        members = set(names)

        def read_part(name: str) -> bytes:
            info = package.getinfo(name)
            if info.file_size > MAX_PART_BYTES:
                raise ValueError(f"package part exceeds 64 MiB limit: {name}")
            return package.read(name)

        def safe_package_path(name: str) -> bool:
            return bool(
                name and not name.startswith("/") and "\\" not in name
                and posixpath.normpath(name) == name
                and not any(x in ("", ".", "..") for x in name.split("/"))
            )

        for name in names:
            keep = name in ("word/document.xml", "word/_rels/document.xml.rels")
            keep |= name.startswith("word/comments") and name.endswith(".xml")
            keep |= name.startswith("word/_rels/comments") and name.endswith(".rels")
            if keep:
                if not safe_package_path(name):
                    raise ValueError(f"unsafe XML package path: {name}")
                xml_parts[f"xml_parts/{digest}/{name}"] = read_part(name)
        if "word/document.xml" not in members:
            raise ValueError("word/document.xml is missing")
        document = ET.fromstring(read_part("word/document.xml"))
        if document.tag != W + "document":
            raise ValueError("only standard transitional WordprocessingML is supported")
        bodies = document.findall(W + "body")
        if len(bodies) != 1:
            raise ValueError("document must contain exactly one w:body")
        body = bodies[0]
        relationships_cache: dict[str, dict[str, dict]] = {}

        def relationships(part: str) -> dict[str, dict]:
            if part in relationships_cache:
                return relationships_cache[part]
            relname = posixpath.join(
                posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
            )
            result: dict[str, dict] = {}
            if relname in members:
                root = ET.fromstring(read_part(relname))
                for rel in root.findall(PKG + "Relationship"):
                    rid = rel.get("Id")
                    if not rid or rid in result:
                        raise ValueError(f"missing/duplicate relationship ID in {relname}")
                    result[rid] = dict(rel.attrib)
            relationships_cache[part] = result
            return result

        media_cache: dict[tuple[str, str], dict] = {}

        def media_for(part: str, rid: str) -> dict:
            key = (part, rid)
            if key in media_cache:
                return deepcopy(media_cache[key])
            rel = relationships(part).get(rid)
            base = {"relationship_id": rid, "source_part": part}
            if rel is None:
                result = {**base, "status": "missing_relationship", "export_path": None}
                warn(f"missing image relationship {rid} in {part}")
            elif rel.get("TargetMode", "").lower() == "external":
                result = {
                    **base, "status": "external_not_fetched", "external": True,
                    "target": rel.get("Target"), "export_path": None,
                }
                warn(f"external image not fetched: {part} relationship {rid}")
            elif not rel.get("Type", "").endswith("/image"):
                result = {**base, "status": "not_image_relationship", "export_path": None}
                warn(f"non-image relationship used as image: {part} {rid}")
            else:
                target = unquote(rel.get("Target", ""))
                if not target or "\\" in target or urlsplit(target).scheme:
                    result = {**base, "status": "unsafe_target", "export_path": None}
                    warn(f"invalid image package target: {part} {rid}")
                else:
                    resolved = posixpath.normpath(
                        target.lstrip("/") if target.startswith("/")
                        else posixpath.join(posixpath.dirname(part), target)
                    )
                    if not safe_package_path(resolved) or resolved not in members:
                        result = {
                            **base, "status": "missing_or_unsafe_package_part",
                            "package_path": resolved, "export_path": None,
                        }
                        warn(f"missing/unsafe image package part: {part} {rid}")
                    else:
                        data = read_part(resolved)
                        sha = hashlib.sha256(data).hexdigest()
                        suffix = posixpath.splitext(resolved)[1].lower()
                        if not suffix or not suffix[1:].isalnum() or len(suffix) > 12:
                            suffix = ".bin"
                        exported = f"assets/{digest}/images/{sha}{suffix}"
                        assets[exported] = data
                        result = {
                            **base, "status": "embedded", "external": False,
                            "export_path": exported, "package_path": resolved,
                            "sha256": sha, "size_bytes": len(data),
                        }
            media_cache[key] = result
            return deepcopy(result)

        def image_relationship_ids(element: ET.Element) -> list[str]:
            if element.tag == A + "blip":
                return [element.get(attr) for attr in (R + "embed", R + "link")
                        if element.get(attr)]
            if element.tag == V + "imagedata":
                return [element.get(R + "id")] if element.get(R + "id") else []
            return []

        def append_unique(items: list[dict], item: dict) -> None:
            if item not in items:
                items.append(item)

        def text_token(element: ET.Element, deleted: bool) -> str:
            if deleted:
                return ""
            if element.tag in (W + "t", M + "t"):
                return element.text or ""
            return {
                W + "tab": "\t", W + "br": "\n", W + "cr": "\n",
                W + "noBreakHyphen": "\u2011", W + "softHyphen": "\u00ad",
            }.get(element.tag, "")

        comment_elements: list[ET.Element] = []
        if "word/comments.xml" in members:
            comments_root = ET.fromstring(read_part("word/comments.xml"))
            if comments_root.tag != W + "comments":
                raise ValueError("unexpected comments.xml root")
            comment_elements = comments_root.findall(W + "comment")
        else:
            warn("word/comments.xml is absent; no standard comment bodies extracted")
        comment_ids: set[str] = set()
        for comment in comment_elements:
            cid = comment.get(W + "id")
            if cid is None or cid in comment_ids:
                raise ValueError("missing or duplicate comment ID")
            comment_ids.add(cid)

        extension_by_para: dict[str, dict] = {}
        if "word/commentsExtended.xml" in members:
            extension = ET.fromstring(read_part("word/commentsExtended.xml"))
            for item in extension.iter(W15 + "commentEx"):
                para_id = item.get(W15 + "paraId")
                if not para_id or para_id in extension_by_para:
                    raise ValueError("missing/duplicate commentsExtended paraId")
                extension_by_para[para_id] = {
                    "resolved_raw": item.get(W15 + "done"),
                    "parent_para_id": item.get(W15 + "paraIdParent"),
                }
        for name in names:
            if name.startswith("word/comments") and name.endswith(".xml") and name not in (
                "word/comments.xml", "word/commentsExtended.xml"
            ):
                warn(f"unsupported optional comment metadata retained as XML: {name}")

        # One enter/exit traversal of the body; no outer p.iter()/inner p.iter().
        marker_data: dict[str, dict] = {}
        active: set[str] = set()
        paragraph_stack: list[dict] = []
        order = 0
        offset = 0
        formula_number = 0
        formula_stack: list[tuple[dict, bytes]] = []

        def state_for(cid: str) -> dict:
            if cid not in marker_data:
                marker_data[cid] = {
                    "markers": {"starts": [], "ends": [], "references": []},
                    "chunks": [], "paragraph_indices": set(), "formulas": [],
                    "media": [], "unsupported": [], "contains_math": False,
                }
            return marker_data[cid]

        def body_walk(element: ET.Element, deleted: bool = False,
                      in_table: bool = False, in_textbox: bool = False) -> None:
            nonlocal order, offset, formula_number
            order += 1
            deleted = deleted or element.tag in (W + "del", W + "moveFrom")
            in_table = in_table or element.tag == W + "tbl"
            in_textbox = in_textbox or element.tag == W + "txbxContent"
            is_paragraph = element.tag == W + "p"
            if is_paragraph:
                if paragraph_stack:
                    warn("nested paragraphs present; nearest paragraph ownership used")
                paragraph = {
                    "index": len(paragraphs) + 1, "text": "",
                    "para_id": element.get(W14 + "paraId"),
                    "in_table": in_table, "in_textbox": in_textbox,
                    "in_deleted": deleted, "contains_math": False,
                }
                paragraphs.append(paragraph)
                paragraph_stack.append(paragraph)
                for cid in active:
                    state_for(cid)["paragraph_indices"].add(paragraph["index"])
            paragraph = paragraph_stack[-1] if paragraph_stack else None
            marker_kind = {
                W + "commentRangeStart": "starts", W + "commentRangeEnd": "ends",
                W + "commentReference": "references",
            }.get(element.tag)
            if marker_kind:
                cid = element.get(W + "id")
                if cid is None:
                    warn("body contains comment marker without an ID")
                else:
                    state = state_for(cid)
                    state["markers"][marker_kind].append({
                        "order": order, "offset": offset,
                        "paragraph_index": paragraph["index"] if paragraph else None,
                        "paragraph_offset": len(paragraph["text"]) if paragraph else None,
                        "in_deleted": deleted, "in_table": in_table,
                        "in_textbox": in_textbox, "source_part": "word/document.xml",
                    })
                    if marker_kind == "starts":
                        active.add(cid)
                    if marker_kind in ("starts", "ends") and paragraph:
                        state["paragraph_indices"].add(paragraph["index"])
                    if marker_kind == "ends":
                        active.discard(cid)
            if element.tag in (MC + "AlternateContent", W + "altChunk", W + "object"):
                label = element.tag.split("}")[-1]
                warn(f"unsupported body structure {label}; XML retained, text may be partial")
                for cid in active:
                    if label not in state_for(cid)["unsupported"]:
                        state_for(cid)["unsupported"].append(label)
            if element.tag == W + "txbxContent":
                warn("textbox XML order is not guaranteed to match visual reading order")
            is_formula = element.tag == M + "oMath" and not deleted
            if is_formula:
                formula_number += 1
                raw = ET.tostring(element, encoding="utf-8", xml_declaration=True)
                exported = f"assets/{digest}/formulas/formula_{formula_number:04d}.xml"
                description = {
                    "export_path": exported, "package_path": "word/document.xml",
                    "formula_index": formula_number,
                    "paragraph_index": paragraph["index"] if paragraph else None,
                    "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                    "linearized_text": "".join(n.text or "" for n in element.iter(M + "t")),
                    "scope": "containing_formula; selected text is recorded in anchor.text",
                }
                formula_stack.append((description, raw))
                if paragraph:
                    paragraph["contains_math"] = True
            if active and not deleted and formula_stack:
                # Also retain the containing formula for markers inside an equation.
                for description, raw in formula_stack:
                    assets[description["export_path"]] = raw
                    for cid in active:
                        append_unique(state_for(cid)["formulas"], deepcopy(description))
                        state_for(cid)["contains_math"] = True
            for rid in image_relationship_ids(element):
                if active and not deleted:
                    image = media_for("word/document.xml", rid)
                    for cid in active:
                        append_unique(state_for(cid)["media"], deepcopy(image))
            token = text_token(element, deleted)
            if token:
                if paragraph:
                    paragraph["text"] += token
                for cid in active:
                    state_for(cid)["chunks"].append(token)
                offset += len(token)
            for child in element:
                body_walk(child, deleted, in_table, in_textbox)
            if is_formula:
                formula_stack.pop()
            if is_paragraph:
                # Paragraph separators belong to ranges spanning paragraph boundaries.
                if not deleted:
                    for cid in active:
                        state_for(cid)["chunks"].append("\n")
                    offset += 1
                paragraph_stack.pop()

        body_walk(body)
        for cid in sorted(active):
            warn(f"unclosed body comment range: {cid}")
        for cid in sorted(set(marker_data) - comment_ids):
            warn(f"orphan body comment markers without comment body: {cid}")

        # These samples anchor in the main body. Other stories are not silently ignored.
        for name in names:
            if not (name.startswith("word/") and name.endswith(".xml")):
                continue
            if name == "word/document.xml" or name.startswith("word/comments"):
                continue
            raw = read_part(name)
            try:
                story = ET.fromstring(raw)
            except ET.ParseError:
                warn(f"optional XML part could not be parsed: {name}")
                continue
            if any(node.tag in (W + "commentRangeStart", W + "commentRangeEnd",
                                W + "commentReference") for node in story.iter()):
                warn(f"comment anchors in unsupported story: {name}")
                if safe_package_path(name):
                    xml_parts[f"xml_parts/{digest}/{name}"] = raw

        def comment_content(element: ET.Element) -> tuple[list[str], list[dict], str | None]:
            texts: list[str] = []
            paragraph_stack_local: list[int] = []
            paragraph_elements: list[ET.Element] = []
            media: list[dict] = []

            def walk(node: ET.Element, deleted: bool = False) -> None:
                deleted = deleted or node.tag in (W + "del", W + "moveFrom")
                is_p = node.tag == W + "p"
                if is_p:
                    if paragraph_stack_local:
                        warn("nested paragraphs in comment; nearest paragraph ownership used")
                    paragraph_elements.append(node)
                    paragraph_stack_local.append(len(texts))
                    texts.append("")
                if node.tag in (MC + "AlternateContent", W + "altChunk", W + "object"):
                    warn(f"unsupported comment structure {node.tag.split('}')[-1]}; XML retained")
                for rid in image_relationship_ids(node):
                    if not deleted:
                        append_unique(media, media_for("word/comments.xml", rid))
                token = text_token(node, deleted)
                if token:
                    if paragraph_stack_local:
                        texts[paragraph_stack_local[-1]] += token
                    else:
                        warn("comment text outside a paragraph could not be placed")
                for child in node:
                    walk(child, deleted)
                if is_p:
                    paragraph_stack_local.pop()

            walk(element)
            last_para_id = (paragraph_elements[-1].get(W14 + "paraId")
                            if paragraph_elements else None)
            return texts, media, last_para_id

        for element in comment_elements:
            cid = element.get(W + "id")
            texts, comment_media, last_para_id = comment_content(element)
            metadata = extension_by_para.get(last_para_id, {})
            if extension_by_para and last_para_id not in extension_by_para:
                warn(f"commentsExtended metadata not matched for comment {cid}")
            state = state_for(cid)
            marks = state["markers"]
            counts = {key: len(value) for key, value in marks.items()}
            reasons: list[str] = []
            if any(count > 1 for count in counts.values()):
                status = "duplicate_markers"
                reasons.append("one or more marker kinds occurs more than once")
            elif counts == {"starts": 1, "ends": 1, "references": 1}:
                if marks["starts"][0]["order"] < marks["ends"][0]["order"] < marks["references"][0]["order"]:
                    status = "complete"
                else:
                    status = "invalid_order"
                    reasons.append("expected start < end < reference in XML order")
            elif counts == {"starts": 0, "ends": 0, "references": 1}:
                status = "reference_only"
                reasons.append("point reference has no recoverable selected range")
            else:
                status = "missing_markers"
                reasons.append("expected one start, end and reference for a range")
            if status != "complete":
                warn(f"comment {cid} anchor status: {status}")
            indices = sorted(state["paragraph_indices"])
            if indices:
                lo, hi = max(1, indices[0] - 1), min(len(paragraphs), indices[-1] + 1)
                context = deepcopy(paragraphs[lo - 1:hi])
            else:
                ref_indices = [mark["paragraph_index"] for mark in marks["references"]
                               if mark["paragraph_index"] is not None]
                context = [deepcopy(paragraphs[index - 1]) for index in sorted(set(ref_indices))]
            text = "\n".join(texts)
            record = {
                "evidence_id": f"{digest}:r{source.get('revision', 'unknown')}:comment:{cid}",
                "source": deepcopy(source),
                "comment": {
                    "id": cid, "author": element.get(W + "author"),
                    "date": element.get(W + "date"), "text": text,
                    "paragraphs": texts, "empty_text": not text.strip(),
                    "media": comment_media, "resolved_raw": metadata.get("resolved_raw"),
                    "parent_para_id": metadata.get("parent_para_id"),
                    "last_para_id": last_para_id,
                },
                "anchor": {
                    "status": status, "status_reasons": reasons,
                    "text": "".join(state["chunks"]), "paragraph_indices": indices,
                    "context_paragraphs": context, "markers": deepcopy(marks),
                    "marker_counts": counts, "contains_math": state["contains_math"],
                    "formulas": state["formulas"], "media": state["media"],
                    "text_policy": TEXT_POLICY, "unsupported_structures": state["unsupported"],
                    "content_status": "partial_unsupported" if state["unsupported"] else "extracted",
                },
            }
            comment_records.append(record)

    return {
        "source_sha256": digest, "comments": comment_records,
        "assets": assets, "xml_parts": xml_parts,
        "body_paragraphs": paragraphs, "warnings": warnings,
    }
