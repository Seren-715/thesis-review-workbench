"""Strict reconciliation of Word Range text and preserved OOXML comment text.

Only two proven terminal structures are supported: one metadata-only final empty
paragraph omitted by Word's range, and one terminal inline VML image represented
by Word's U+0001 object sentinel. Raw evidence is never rewritten or trimmed.
"""
from collections import Counter
import hashlib
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
import zipfile

from process_p002_doc_pilot import normalize_word_text, verify_parser_comments

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
V = '{urn:schemas-microsoft-com:vml}'


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _xml(raw):
    if len(raw) > 16 * 1024 * 1024 or any(x in raw.replace(b'\x00', b'').upper() for x in (b'<!DOCTYPE', b'<!ENTITY')):
        raise ValueError('Comment representation XML is unsafe or oversized')
    return ET.fromstring(raw)


def _word_row(row):
    return (row.get('author', ''), row.get('initials', ''), row.get('date', ''),
            normalize_word_text(row.get('range_text', '')), normalize_word_text(row.get('scope_text', '')))


def _check_fidelity(audit, extracted, docx_path):
    required = ('source_sha256_unchanged', 'comment_fidelity_verified', 'comment_metadata_sequence_verified', 'body_text_hash_equal')
    if audit.get('status') != 'comment_text_fidelity_verified_visual_pending' or any(audit.get(k) is not True for k in required):
        raise ValueError('Word conversion preservation gates are not satisfied')
    if audit.get('errors') or audit.get('cleanup_errors'):
        raise ValueError('Word conversion audit contains errors')
    original_body = audit.get('original_body_text_utf8_sha256')
    if not original_body or original_body != audit.get('derived_body_text_utf8_sha256'):
        raise ValueError('Word body preservation hashes do not match')
    source = audit.get('source', {})
    if not source.get('path') or _sha(source['path']) != source.get('sha256') or audit.get('source_after', {}).get('sha256') != source.get('sha256'):
        raise ValueError('Original source hash is not verified')
    sha = _sha(docx_path)
    if sha != audit.get('derived', {}).get('sha256') or sha != extracted.get('source_sha256'):
        raise ValueError('Derived document/extraction hash mismatch')
    before, after = audit.get('comments_before', []), audit.get('comments_after', [])
    if len(before) != audit.get('comments_before_count') or len(after) != audit.get('comments_after_count') or len(before) != len(after):
        raise ValueError('Word comment counts do not match preservation audit')
    if [_word_row(r) for r in before] != [_word_row(r) for r in after]:
        raise ValueError('Word before/after comment metadata or text changed')
    return sha


def _paragraph_text(node):
    # Only plain paragraphs and a terminal image are representable by this map.
    if any(n.tag in {W + 'del', W + 'moveFrom', W + 'ins', W + 'object', W + 'txbxContent', W + 'fldSimple', W + 'instrText'} for n in node.iter()):
        raise ValueError('Unsupported comment structure in representation mapping')
    pieces = []
    for n in node.iter():
        if n.tag == W + 't':
            pieces.append(n.text or '')
        elif n.tag in {W + 'br', W + 'cr'}:
            pieces.append('\n')
        elif n.tag == W + 'tab':
            pieces.append('\t')
    return ''.join(pieces)


def _image_evidence(comment, record, archive, relationships, extracted):
    pictures = list(comment.iter(W + 'pict'))
    if len(pictures) != 1 or comment.find('.//' + W + 'drawing') is not None:
        raise ValueError('Only one terminal VML picture is supported')
    image_nodes = list(pictures[0].iter(V + 'imagedata'))
    if len(image_nodes) != 1:
        raise ValueError('Image relationship/object count mismatch')
    rid = image_nodes[0].get(R + 'id')
    relationship = relationships.get(rid)
    if not relationship or relationship.get('TargetMode', '').lower() == 'external' or not relationship.get('Type', '').endswith('/image'):
        raise ValueError('Terminal image relationship is not an embedded image')
    target = relationship.get('Target', '')
    if '\\' in target or ':' in target or target.startswith('/'):
        raise ValueError('Image relationship path is unsafe')
    package_path = posixpath.normpath(posixpath.join('word', target))
    if not package_path.startswith('word/media/'):
        raise ValueError('Image relationship does not target packaged media')
    matches = [m for m in record.get('media', []) if m.get('relationship_id') == rid]
    if len(matches) != 1:
        raise ValueError('Extracted image metadata does not uniquely match relationship')
    media = matches[0]
    if media.get('external') is not False or media.get('status') != 'embedded' or media.get('source_part') != 'word/comments.xml' or media.get('package_path') != package_path:
        raise ValueError('Extracted image media is not verified as embedded')
    raw = archive.read(package_path)
    media_sha = hashlib.sha256(raw).hexdigest()
    asset = extracted.get('assets', {}).get(media.get('export_path'))
    if not media.get('sha256') or media.get('sha256') != media_sha or media.get('size_bytes') != len(raw) or asset != raw:
        raise ValueError('Embedded image bytes/hash/extracted asset mismatch')
    return {'relationship_id': rid, 'package_path': package_path, 'sha256': media_sha, 'size_bytes': len(raw),
            'export_path': media.get('export_path'), 'content_verified': True, 'visual_semantics_verified': False}


def audit_parser_comment_representation(audit, extracted, docx_path):
    """Return an additive audit, or reject every unexplained representation delta.

Does not modify the parser's exact XML text, source bytes or conversion audit.
The caller must retain this report separately before enabling textual evidence.
"""
    try:
        verify_parser_comments(audit, extracted)
    except ValueError:
        pass
    else:
        return {'schema_version': 1, 'status': 'exact', 'mapping_applied': False,
                'source_sha256': audit.get('source', {}).get('sha256'), 'derived_sha256': _sha(docx_path),
                'comments': [], 'word_requests': 0, 'llm_requests': 0}
    derived_sha = _check_fidelity(audit, extracted, docx_path)
    word_rows = list(audit['comments_after'])
    xml_rows = [row['comment'] for row in extracted.get('comments', [])]
    if len(word_rows) != len(xml_rows):
        raise ValueError('Parser/Word comment counts differ; mapping cannot recover omissions')
    if len({r.get('id') for r in xml_rows}) != len(xml_rows):
        raise ValueError('Duplicate parsed comment IDs are not eligible for mapping')
    details = []
    with zipfile.ZipFile(docx_path) as archive:
        comment_root = _xml(archive.read('word/comments.xml'))
        comments = {c.get(W + 'id'): c for c in comment_root}
        if len(comments) != len(list(comment_root)) or len(comments) != len(xml_rows):
            raise ValueError('Duplicate or omitted XML comment IDs')
        relname = 'word/_rels/comments.xml.rels'
        relationships = {r.get('Id'): r.attrib for r in _xml(archive.read(relname))} if relname in archive.namelist() else {}
        unused = set(range(len(word_rows)))
        for record in xml_rows:
            cid = record.get('id')
            comment = comments.get(cid)
            if comment is None or record.get('author') != comment.get(W + 'author'):
                raise ValueError('XML comment ID/author does not match parser')
            raw_xml = record.get('text', '')
            paragraphs = list(comment.findall(W + 'p'))
            reconstructed = [_paragraph_text(n) for n in paragraphs]
            if record.get('paragraphs') != reconstructed or raw_xml != '\n'.join(reconstructed):
                raise ValueError('Parser raw comment text does not match inspected XML paragraphs')
            modes = [(raw_xml, 'exact', None)]
            if paragraphs:
                last = paragraphs[-1]
                if reconstructed[-1] == '' and len(paragraphs) >= 2 and all(n.tag == W + 'pPr' for n in last):
                    # Exactly one metadata-only terminal paragraph: never rstrip.
                    modes.append(('\n'.join(reconstructed[:-1]), 'one_metadata_only_terminal_paragraph_outside_word_range', None))
                if reconstructed[-1] == '' and len(paragraphs) >= 2 and last.find('.//' + W + 'pict') is not None:
                    if any(n.tag not in {W + 'pPr', W + 'r'} for n in last) or any(n.tag not in {W + 'rPr', W + 'pict'} for run in last.findall(W + 'r') for n in run):
                        raise ValueError('Terminal image paragraph contains unsupported visible content')
                    media = _image_evidence(comment, record, archive, relationships, extracted)
                    modes.append(('\n'.join(reconstructed[:-1] + ['\x01']), 'one_terminal_embedded_image_word_object_sentinel', media))
            matches = []
            for index in unused:
                row = word_rows[index]
                if row.get('author') != record.get('author'):
                    continue
                word_text = normalize_word_text(row.get('range_text', ''))
                for target, mode, media in modes:
                    if word_text == target:
                        if mode == 'one_terminal_embedded_image_word_object_sentinel' and word_text.count('\x01') != 1:
                            raise ValueError('Word placeholder/image count mismatch')
                        matches.append((index, mode, media))
            # Identical comments may match by exact text; only a transformed match
            # requires unique provenance to avoid silently binding another comment.
            exact = [m for m in matches if m[1] == 'exact']
            if exact:
                chosen = min(exact)
            elif len(matches) == 1:
                chosen = matches[0]
            else:
                raise ValueError(f'Comment {cid}: unexplained or ambiguous Word/XML representation difference')
            index, mode, media = chosen
            unused.remove(index)
            row = word_rows[index]
            details.append({'comment_id': cid, 'author': record['author'], 'word_index': row.get('word_index'),
                            'raw_word_text': row['range_text'], 'normalized_word_range_text': normalize_word_text(row['range_text']),
                            'raw_xml_text': raw_xml, 'raw_xml_paragraph_texts': reconstructed,
                            'text_condition': mode, 'media': [media] if media else [], 'raw_evidence_unchanged': True})
    if unused or _sha(docx_path) != derived_sha:
        raise ValueError('Comment matching incomplete or derived source changed during audit')
    return {'schema_version': 1, 'status': 'strict_comment_representation_verified_visual_pending',
            'mapping_applied': True, 'source_sha256': audit['source']['sha256'], 'derived_sha256': derived_sha,
            'comments': details, 'mapped_comment_count': sum(r['text_condition'] != 'exact' for r in details),
            'raw_xml_text_policy': 'Retained verbatim including final empty paragraphs; image remains a separate hashed asset, without OCR.',
            'limitations': ['Representation mapping proves text/object-marker correspondence, not image visual fidelity or formatting.'],
            'word_requests': 0, 'llm_requests': 0}
