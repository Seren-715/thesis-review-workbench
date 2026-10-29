"""Bounded, local DOCX figure evidence. No Word, network, OCR or model calls.

Paragraph indexes follow ``document.iter(w:p)`` (zero based). Extracted pixels
and OOXML dimensions are evidence, never a claim that Word print layout passed.
"""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path, PurePosixPath
import posixpath
import re
import urllib.parse
import warnings
import zipfile

from PIL import Image, ImageDraw

from new_paper_checks import W, _xml, _visible_text, digest, extract_paragraphs, read_docx

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
V = '{urn:schemas-microsoft-com:vml}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
MAX_EDGE = 1600
MAX_PIXELS = 40_000_000
CAPTION = re.compile(r'^\s*图\s*\d+(?:[.－—-]\d+)?\s*')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _part(target):
    # Relationship targets are URI references, never OS filenames.
    parsed = urllib.parse.urlsplit(target)
    decoded = urllib.parse.unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '\\' in decoded or ':' in decoded or '\x00' in decoded:
        raise ValueError('unsafe_relationship_target')
    if decoded.startswith('/'):
        raise ValueError('absolute_relationship_target')
    resolved = posixpath.normpath(posixpath.join('word', decoded))
    if not resolved.startswith('word/media/') or '..' in PurePosixPath(resolved).parts:
        raise ValueError('relationship_outside_word_media')
    return resolved


def _positive(raw, divisor=1):
    try:
        number = float(raw) / divisor
        return round(number, 6) if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def _vml_cm(style, key):
    match = re.search(r'(?:^|;)\s*' + key + r'\s*:\s*([\d.]+)(cm|mm|in|pt|px)\s*(?:;|$)', style or '', re.I)
    if not match or match[2].lower() == 'px':
        return None  # CSS pixels alone do not prove a print dimension.
    return _positive(match[1], {'cm': 1, 'mm': 10, 'in': 1 / 2.54, 'pt': 72 / 2.54}[match[2].lower()])


def _objects(paragraph, parents):
    result = []
    for node in paragraph.iter():
        if node.tag not in {A + 'blip', V + 'imagedata'}:
            continue
        rid = node.get(R + 'embed') or node.get(R + 'link') or node.get(R + 'id')
        if node.tag == V + 'imagedata' and not rid:
            continue  # VML line fallback often carries empty imagedata, not a picture.
        owner, ancestors = node, []
        while owner in parents and owner is not paragraph:
            owner = parents[owner]
            ancestors.append(owner)
        # Textbox descendants have their own paragraph index; don't count twice.
        if any(n.tag == W + 'p' and n is not paragraph for n in ancestors):
            continue
        flags = []
        crop_fractions = None
        if any(n.tag in {V + 'line', '{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}wsp'} for n in paragraph.iter()):
            flags.append('vector_overlay_requires_word_render')
        if node.get(R + 'link') or not rid:
            flags.append('linked_or_missing_relationship')
        if node.tag == A + 'blip':
            containers = [n for n in ancestors if n.tag in {WP + 'inline', WP + 'anchor'}]
            container = containers[0] if containers else paragraph
            extent = container.find(WP + 'extent')
            width = _positive(extent.get('cx'), 360000) if extent is not None else None
            height = _positive(extent.get('cy'), 360000) if extent is not None else None
            if containers and container.tag == WP + 'anchor':
                flags.append('floating_anchor_layout_unknown')
            for n in container.iter():
                if n.tag == A + 'srcRect':
                    try:
                        fractions = {k: int(n.get(k, '0')) / 100000 for k in ('l', 't', 'r', 'b')}
                        if any(v < 0 or v >= 1 for v in fractions.values()) or fractions['l'] + fractions['r'] >= 1 or fractions['t'] + fractions['b'] >= 1:
                            flags.append('crop_outside_source_or_empty')
                        elif any(fractions.values()):
                            crop_fractions = fractions
                    except (ValueError, TypeError):
                        flags.append('invalid_crop')
                if n.tag == A + 'xfrm' and any(n.get(k) not in {None, '0', 'false'} for k in ('rot', 'flipH', 'flipV')):
                    flags.append('rotation_or_flip')
                if n.tag.endswith('}grpSp') or n.tag.endswith('}wgp'):
                    flags.append('grouped_drawing')
                if n.tag in {A + 'effectLst', A + 'effectDag'} and len(n):
                    flags.append('drawing_effects_require_review')
                if n.tag == A + 'prstGeom' and n.get('prst') != 'rect':
                    flags.append('nonrectangular_picture_geometry')
            if any(n.tag != A + 'extLst' for n in node):
                flags.append('image_effects_or_extension')
            if any(n.tag in {A + 'stretch', A + 'tile'} for n in container.iter()):
                # Normal stretch/fillRect with zero coordinates is the common picture case.
                for n in container.iter():
                    if n.tag == A + 'tile' or (n.tag == A + 'fillRect' and any(v not in {'0', '0%'} for v in n.attrib.values())):
                        flags.append('complex_fill')
            kind = 'drawingml'
        else:
            shape = next((n for n in ancestors if n.tag == V + 'shape'), None)
            style = shape.get('style', '') if shape is not None else ''
            width, height = _vml_cm(style, 'width'), _vml_cm(style, 'height')
            if any(k.lower().startswith('crop') and v not in {'0', '0f'} for k, v in node.attrib.items()):
                flags.append('crop')
            if re.search(r'(?:rotation|flip)\s*:', style, re.I) or (shape is not None and shape.get('coordsize')):
                flags.append('vml_transform')
            if 'position:absolute' in style.replace(' ', '').lower():
                flags.append('floating_anchor_layout_unknown')
            kind = 'vml'
        result.append({'relationship_id': rid, 'object_kind': kind,
                       'display_width_cm': width, 'display_height_cm': height,
                       'crop_fractions': crop_fractions,
                       'transform_flags': sorted(set(flags))})
    return result


def _image(raw):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError('image_pixel_limit')
            if getattr(opened, 'n_frames', 1) != 1:
                raise ValueError('animated_or_multiframe_image')
            size, fmt = opened.size, opened.format
            orientation = opened.getexif().get(274, 1)
            if orientation not in {None, 1}:
                raise ValueError('exif_orientation_requires_review')
            opened.load()
            image = opened.convert('RGBA' if 'A' in opened.getbands() or 'transparency' in opened.info else 'RGB')
    return image, size, fmt


def _save_preview(image, folder, token):
    normalized = image.copy()
    normalized.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    path = folder / (token + '.png')
    normalized.save(path, format='PNG')
    return path, normalized


def _display_preview(image, width, height, folder, token):
    # Fixed mm-to-pixel conversion preserves the OOXML dimensions on A4.
    scale = 4  # 101.6 dpi; reference only, never a rendered Word page.
    page = Image.new('RGB', (840, 1188), 'white')
    draw = ImageDraw.Draw(page)
    draw.text((24, 18), 'SYNTHETIC A4 SIZE REFERENCE - NOT A WORD PAGE', fill='black')
    draw.text((24, 38), f'OOXML display: {width:.3f} x {height:.3f} cm', fill='black')
    if width > 21 or height > 27.5:
        return None  # Bound before converting potentially enormous hostile extents.
    pixels = (max(1, round(width * 10 * scale)), max(1, round(height * 10 * scale)))
    if pixels[0] > 840 or pixels[1] > 1100:
        return None  # Avoid misleading scaling or clipping oversized images.
    shown = image.resize(pixels, Image.Resampling.LANCZOS)
    if shown.mode == 'RGBA':
        page.paste(shown, ((840 - pixels[0]) // 2, 80), shown)
    else:
        page.paste(shown, ((840 - pixels[0]) // 2, 80))
    path = folder / (token + '_a4_reference.png')
    page.save(path)
    return path


def prepare_figure_evidence(source_path, output_dir, findings):
    """Attach auditable image evidence to NP007 findings and return counts.

    ``preview_path`` is an absolute local PNG; callers may encode it for a
    model. Only exact, simple, uniquely closest placements with known OOXML
    display dimensions are ``reviewable``. Other candidates remain visible.
    """
    source = Path(source_path).resolve()
    folder = Path(output_dir).resolve()
    if folder.name != 'figure_evidence':
        folder /= 'figure_evidence'
    if source == folder or source in folder.parents:
        raise ValueError('output_directory_is_source')
    document, styles, source_hash, _ = read_docx(source)
    paragraphs = list(document.iter(W + 'p'))
    rows = extract_paragraphs(document, styles)
    parents = {child: node for node in document.iter() for child in node}
    stats = {'total': 0, 'reviewable': 0, 'ambiguous': 0, 'unsupported': 0, 'source_sha256': source_hash}
    with zipfile.ZipFile(source) as archive:
        relationships = {}
        if 'word/_rels/document.xml.rels' in archive.namelist():
            for rel in _xml(archive.read('word/_rels/document.xml.rels')):
                if rel.tag != REL + 'Relationship':
                    continue
                rid = rel.get('Id')
                if rid in relationships:
                    raise ValueError('duplicate_relationship_id')
                relationships[rid] = rel.attrib
        folder.mkdir(parents=True, exist_ok=True)
        for finding in findings:
            if finding.get('rule_id') != 'NP007':
                continue
            stats['total'] += 1
            index = finding.get('paragraph_index')
            evidence = {'schema_version': 1, 'source_sha256': source_hash,
                        'binding_status': 'unsupported', 'reviewable': False,
                        'binding_reasons': [], 'candidates': [],
                        'caption': {'paragraph_index': index, 'text': finding.get('text'), 'match_exact': False},
                        'limitations': ['未执行OCR；未渲染Word页；不代表完整打印视觉核验。'],
                        'note': '仅本地原图与OOXML显示尺寸证据；尚未判断可读性。'}
            finding['figure_evidence'] = evidence
            reasons = evidence['binding_reasons']
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(paragraphs):
                reasons.append('invalid_caption_index')
            elif finding.get('text') != _visible_text(paragraphs[index]) or not CAPTION.match(finding.get('text', '')):
                reasons.append('caption_text_mismatch')
            else:
                evidence['caption']['match_exact'] = True
                indices = finding.get('image_paragraph_indices', [])
                if not isinstance(indices, list):
                    indices = []
                for pi in sorted(set(i for i in indices if isinstance(i, int) and not isinstance(i, bool))):
                    if not 0 <= pi < len(paragraphs) or abs(pi - index) > 3:
                        continue
                    interval = rows[min(pi, index):max(pi, index) + 1]
                    if any(r['heading_level'] or r['in_deleted'] or r['in_textbox'] or r['in_toc'] for r in interval):
                        continue
                    if any(CAPTION.match(r['text']) for r in interval if r['index'] != index):
                        continue
                    if any(r['text'].strip() for r in interval if r['index'] not in {pi, index}):
                        continue  # Substantive intervening prose prevents an exact nearby binding.
                    if rows[pi]['in_table'] != rows[index]['in_table']:
                        continue
                    for ordinal, obj in enumerate(_objects(paragraphs[pi], parents)):
                        obj['paragraph_index'] = pi
                        obj['object_ordinal_in_paragraph'] = ordinal
                        obj['distance_to_caption'] = abs(pi - index)
                        obj['errors'] = []
                        evidence['candidates'].append(obj)
                        rel = relationships.get(obj['relationship_id'])
                        try:
                            if not rel:
                                raise ValueError('missing_relationship')
                            if rel.get('TargetMode', '').lower() == 'external':
                                raise ValueError('external_relationship_rejected')
                            if not rel.get('Type', '').endswith('/image'):
                                raise ValueError('not_image_relationship')
                            part = _part(rel.get('Target', ''))
                            raw = archive.read(part)
                            obj.update(media_part=part, original_media_sha256=_sha(raw), original_media_bytes=len(raw))
                            img, size, fmt = _image(raw)
                            obj.update(original_pixel_width=size[0], original_pixel_height=size[1], original_format=fmt)
                            ext = {'JPEG': '.jpg', 'PNG': '.png', 'TIFF': '.tiff', 'BMP': '.bmp', 'GIF': '.gif', 'WEBP': '.webp'}.get(fmt, '.bin')
                            original = folder / ('original_' + obj['original_media_sha256'] + ext)
                            if not original.exists() or digest(original) != obj['original_media_sha256']:
                                original.write_bytes(raw)
                            obj['original_media_path'] = str(original)
                        except (ValueError, KeyError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                            obj['errors'].append(str(exc))
                candidates = evidence['candidates']
                if not candidates:
                    reasons.append('no_safe_nearby_image_objects')
                else:
                    # A further adjacent caption's image must not displace a unique nearest one.
                    closest = min(c['distance_to_caption'] for c in candidates)
                    chosen = [c for c in candidates if c['distance_to_caption'] == closest]
                    evidence['selection_rule'] = 'uniquely_nearest_paragraph_placement_within_given_indices'
                    evidence['excluded_further_candidates'] = len(candidates) - len(chosen)
                    if len(chosen) != 1:
                        evidence['binding_status'] = 'ambiguous'
                        reasons.append('multiple_equally_near_image_placements')
                    else:
                        obj = chosen[0]
                        evidence['selected_candidate'] = obj
                        if obj['errors'] or obj['transform_flags']:
                            reasons.extend(obj['errors'] + obj['transform_flags'])
                        else:
                            evidence['binding_status'] = 'exact_single_nearby_image'
                            img, _, _ = _image(archive.read(obj['media_part']))
                            source_size = [img.width, img.height]
                            crop = obj.get('crop_fractions')
                            crop_box = None
                            if crop:
                                crop_box = [round(img.width * crop['l']), round(img.height * crop['t']),
                                            round(img.width * (1 - crop['r'])), round(img.height * (1 - crop['b']))]
                                if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                                    reasons.append('crop_empty_after_pixel_rounding')
                                    evidence['binding_status'] = 'unsupported'
                                    stats['unsupported'] += 1
                                    continue
                                img = img.crop(tuple(crop_box))
                                cropped_bytes = io.BytesIO()
                                img.save(cropped_bytes, format='PNG')
                                evidence['crop'] = {'ooxml_fractions': crop, 'source_pixel_box': crop_box,
                                                    'output_pixel_size': [img.width, img.height],
                                                    'output_png_sha256': _sha(cropped_bytes.getvalue()),
                                                    'rounding': 'nearest_integer_pixel'}
                            token = f'figure_p{index}_p{obj["paragraph_index"]}_{obj["original_media_sha256"][:12]}'
                            preview, normalized = _save_preview(img, folder, token)
                            width, height = obj['display_width_cm'], obj['display_height_cm']
                            evidence.update(preview_path=str(preview), preview_sha256=digest(preview), preview_mime='image/png',
                                            image_uri=str(preview), source_image_sha256=obj['original_media_sha256'],
                                            pixel_width=obj['original_pixel_width'], pixel_height=obj['original_pixel_height'],
                                            width_cm=width, height_cm=height,
                                            normalized_pixel_width=normalized.width, normalized_pixel_height=normalized.height,
                                            normalization={'max_long_edge': MAX_EDGE, 'resampling': 'LANCZOS',
                                                           'original_media_sha256': obj['original_media_sha256'],
                                                           'output_sha256': digest(preview), 'output_format': 'PNG',
                                                           'source_pixel_size': source_size,
                                                           'crop_box': crop_box,
                                                           'before_resize_pixel_size': [img.width, img.height],
                                                           'output_pixel_size': [normalized.width, normalized.height]})
                            if width is None or height is None:
                                reasons.append('ooxml_print_dimensions_unknown')
                                evidence['note'] = '原图已提取，但OOXML显示尺寸未知；不能判断Word打印可读性。'
                            else:
                                evidence['reviewable'] = True
                                display = _display_preview(img, width, height, folder, token)
                                evidence['display_preview_note'] = '合成A4比例参考，非Word页渲染证据；图像位置不代表原稿布局。'
                                if display:
                                    evidence.update(display_preview_path=str(display), display_preview_sha256=digest(display))
            if evidence['reviewable']:
                stats['reviewable'] += 1
            elif evidence['binding_status'] == 'ambiguous':
                stats['ambiguous'] += 1
            else:
                stats['unsupported'] += 1
    if digest(source) != source_hash:
        raise ValueError('source_changed_during_figure_extraction')
    return stats
