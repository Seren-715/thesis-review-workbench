"""Source-bound object alignment. Matching is distinct from error adjudication."""
import asyncio
import hashlib
import json
import posixpath
import re
import unicodedata
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from lxml import etree
from PIL import Image
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.registry import ToolRegistry, is_tool_error_result
from cross_student_rule_pilot import digest, save
from check_rules_pilot import read_check_document
from reviewed_rule_catalog import load_catalog

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'w': W}
RULES = ('PR003', 'PR004', 'PR005', 'PR006')


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def list_content(text):
    return re.sub(r'^(?:\([一二三四五六七八九十0-9]+\)|[一二三四五六七八九十]+[、.]|[0-9]+[、.]?)', '', compact(text))


def caption_title(text):
    return re.sub(r'^[图表]\d+[-－—]\d+', '', compact(text))


def text_candidates(teacher_text, paragraphs):
    """Exact unique text or a substantial complete initial paragraph prefix."""
    target = list_content(teacher_text)
    exact, prefixes = [], []
    for p in paragraphs:
        if p['in_deleted'] or p['in_textbox'] or p['in_table']:
            continue
        text = list_content(p['text'])
        if text and text == target:
            exact.append({'paragraph': p, 'method': 'unique_content_equal_ignoring_space_and_leading_list_marker'})
        elif len(text) >= 40 and target.startswith(text):
            prefixes.append({'paragraph': p, 'method': 'complete_initial_paragraph_is_substantial_teacher_prefix'})
    return exact or prefixes


def document_inventory(source):
    path = Path(source['document_path'])
    document = read_check_document(path, source)
    paragraphs = document['body_paragraphs']
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read('word/document.xml'))
        xml_paragraphs = list(root.iter('{'+W+'}p'))
        if len(xml_paragraphs) != len(paragraphs):
            raise ValueError('Paragraph indexes differ between readers')
        relations = {r.get('Id'): r for r in etree.fromstring(z.read('word/_rels/document.xml.rels'))}
        images = []
        for p in paragraphs:
            for rid in p['image_relationship_ids']:
                rel = relations[rid]
                if rel.get('TargetMode') == 'External':
                    images.append({'paragraph_index': p['index'], 'relationship_id': rid, 'status': 'external_image_not_read'})
                    continue
                part = posixpath.normpath(posixpath.join('word', rel.get('Target')))
                if not part.startswith('word/media/'):
                    raise ValueError('Unexpected embedded picture part')
                data = z.read(part)
                pixel_hash, dimensions, frames = None, None, None
                try:
                    with Image.open(BytesIO(data)) as image:
                        frames = getattr(image, 'n_frames', 1)
                        dimensions = list(image.size)
                        if frames == 1:
                            rgba = image.convert('RGBA')
                            pixel_hash = hashlib.sha256(str(rgba.size).encode('ascii')+rgba.tobytes()).hexdigest()
                except (OSError, ValueError):
                    pass
                images.append({'paragraph_index': p['index'], 'relationship_id': rid,
                               'part': part, 'sha256': hashlib.sha256(data).hexdigest(),
                               'decoded_rgba_sha256': pixel_hash, 'pixel_dimensions': dimensions,
                               'frame_count': frames, 'status': 'embedded_image_recorded'})
        captions = [p for p in paragraphs if re.match(r'^[图表]\d+[-－—]\d+', compact(p['text']))]
        tables = []
        indexes = {id(p): i+1 for i, p in enumerate(xml_paragraphs)}
        for table in root.iter('{'+W+'}tbl'):
            indices = [indexes[id(p)] for p in table.iter('{'+W+'}p')]
            tables.append({'paragraph_indices': indices,
                           'text': ''.join(t.text or '' for t in table.iter('{'+W+'}t'))})
        numbering = []
        for p, element in zip(paragraphs, xml_paragraphs):
            properties = element.find('w:pPr', NS)
            numbering.append({'paragraph_index': p['index'],
                'direct_numPr_xml': None if properties is None or properties.find('w:numPr', NS) is None else
                                  etree.tostring(properties.find('w:numPr', NS), encoding='unicode'),
                'direct_outline_level': None if properties is None or properties.find('w:outlineLvl', NS) is None else
                                        properties.find('w:outlineLvl', NS).get('{'+W+'}val')})
        numbering_part_present = 'word/numbering.xml' in z.namelist()
    return {'source': source, 'paragraphs': paragraphs, 'captions': captions, 'images': images,
            'native_xml_tables': tables, 'paragraph_properties': numbering,
            'numbering_part_present': numbering_part_present,
            'limits': 'Current-view XML text and w:drawing blip images only; no OCR, OLE/VML identity or reconstructed inherited list levels. No matched caption/image does not prove absence.'}


def context(inventory, index, radius=2):
    return [p for p in inventory['paragraphs'] if index-radius <= p['index'] <= index+radius]


def align_source(rule_id, evidence, initial, teacher):
    anchor = evidence['original_anchor']
    if anchor['status'] != 'complete' or len(anchor['paragraph_indices']) != 1:
        raise ValueError('Only complete single-paragraph teacher targets are eligible')
    index = anchor['paragraph_indices'][0]
    paragraph = next(p for p in teacher['paragraphs'] if p['index'] == index)
    output = {'ref': evidence['ref'], 'teacher_evidence': evidence, 'teacher_target_paragraph': paragraph,
              'teacher_context': context(teacher, index), 'initial_source': initial['source'],
              'confirmed_error_count': None, 'automatic_error_judgment_enabled': False}
    if rule_id == 'PR004':
        matches = text_candidates(paragraph['text'], initial['paragraphs'])
        output['content_candidates'] = matches
        if len(matches) == 1:
            matched = matches[0]['paragraph']
            output['initial_context'] = context(initial, matched['index'])
            output['initial_paragraph_properties'] = initial['paragraph_properties'][matched['index']-1]
            output['teacher_paragraph_properties'] = teacher['paragraph_properties'][index-1]
            output['status'] = 'content_aligned_numbering_review_required'
            if evidence['ref'] == 'C009' and not compact(matched['text']).startswith('2异常值检测'):
                output['status'] = 'content_aligned_teacher_bare_number_not_present'
            output['limits'] = 'Content identity is established, not formal list level or expected numbering. A changed leading marker is not itself proof of correction/error.'
        else:
            output['status'] = 'no_verified_text_counterpart' if not matches else 'ambiguous_text_counterparts'
            output['limits'] = 'No reliable text mapping in the extracted scope; image-only text was not OCRed. Matching by a shared list number is forbidden.'
        return output
    # A caption and the immediately preceding embedded picture give a bounded target.
    picture_targets = [m for m in teacher['images'] if m['paragraph_index'] == index-1]
    output['teacher_adjacent_images'] = picture_targets
    title = caption_title(paragraph['text'])
    titles = [p for p in initial['captions'] if caption_title(p['text']) == title]
    image_matches = []
    for target in picture_targets:
        for candidate in initial['images']:
            byte_equal = target.get('sha256') and target.get('sha256') == candidate.get('sha256')
            pixel_equal = target.get('decoded_rgba_sha256') and target['decoded_rgba_sha256'] == candidate.get('decoded_rgba_sha256')
            if byte_equal or pixel_equal:
                image_matches.append({'teacher_image': target, 'initial_image': candidate,
                                      'method': 'embedded_bytes_equal' if byte_equal else 'decoded_rgba_equal',
                                      'initial_context': context(initial, candidate['paragraph_index'])})
    output['caption_title_candidates'] = titles
    output['identical_image_candidates'] = image_matches
    # Equal caption wording locates content, but alone is insufficient picture/table identity.
    verified = len(image_matches) == 1 and len(picture_targets) == 1
    output['status'] = 'image_content_aligned_scope_review_required' if verified else 'no_verified_object_counterpart'
    output['limits'] = 'No matching media/caption is a mapping limit, not proof of absence or a pass. Resized/cropped/redrawn objects require visual comparison; identical pixels do not establish page layout.'
    if rule_id == 'PR006':
        output['teacher_nearby_native_tables'] = [t for t in teacher['native_xml_tables']
            if t['paragraph_indices'] and 0 <= index-max(t['paragraph_indices']) <= 3]
        output['initial_native_xml_table_count'] = len(initial['native_xml_tables'])
        output['initial_table_caption_candidates'] = [p for p in initial['captions'] if compact(p['text']).startswith('表')]
        output['limits'] += ' Zero native XML tables does not mean no rasterized tables; rasterized content has not been OCRed or adjudicated here.'
    return output


def collect_alignment(run_dir):
    catalog = load_catalog(run_dir)
    initial = document_inventory(catalog['initial_source'])
    teachers, results = {}, []
    for rule in catalog['rules']:
        if rule['rule_id'] not in RULES:
            continue
        items = []
        for evidence in rule['sources']:
            source = evidence['source']; sha = source['sha256']
            if sha not in teachers:
                teachers[sha] = document_inventory(source)
            items.append(align_source(rule['rule_id'], evidence, initial, teachers[sha]))
        results.append({'rule_id': rule['rule_id'], 'items': items, 'confirmed_error_count': None})
    for inventory in [initial, *teachers.values()]:
        if digest(Path(inventory['source']['document_path'])) != inventory['source']['sha256']:
            raise ValueError('Document changed during alignment')
    return {'catalog': catalog, 'initial_inventory': initial, 'teacher_inventories': list(teachers.values()),
            'rules': results, 'full_coverage_verified': False, 'independent_accuracy_test': False,
            'note': 'Exact source-bound content matching, not teacher diagnosis transfer or automated semantic judgment.'}


@tool_parameters({'type': 'object', 'properties': {}, 'additionalProperties': False})
class AlignmentTool(Tool):
    _plugin_discoverable = False
    def __init__(self, rule_id, run_dir):
        self.rule_id, self.run_dir, self.result = rule_id, run_dir, None
    @property
    def name(self): return 'align_'+self.rule_id.lower()
    @property
    def description(self): return self.rule_id+'教师对象与固定初稿内容对应，只读；不判错。'
    @property
    def read_only(self): return True
    async def execute(self):
        all_results = collect_alignment(self.run_dir)
        self.result = next(r for r in all_results['rules'] if r['rule_id'] == self.rule_id)
        return json.dumps(self.result, ensure_ascii=False)


async def run_alignment(run_dir, output_parent=None):
    root = Path(run_dir).resolve()
    snapshot = collect_alignment(root)
    output = Path(output_parent or root)/('object_alignment_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(exist_ok=False)
    names = ('scope_object_alignment.py', 'paper_pipeline_v3.py', 'reviewed_rule_catalog.py',
             'check_rules_pilot.py', 'docx_comment_reader.py', 'pipeline_candidates.py')
    code = {name: digest(Path(__file__).with_name(name)) for name in names}
    state = {'status': 'prepared', 'model_requests': 0, 'svn_requests': 0, 'word_launches': 0,
             'new_tokens': 0, 'tool_attempts': [], 'code_hashes': code,
             'framework': 'nanobot_ToolRegistry_explicit_local_sequence',
             'automatic_error_judgment_enabled': False, 'independent_accuracy_test': False}
    save(output/'run.json', state)
    try:
        registry = ToolRegistry(); results = []
        for rule_id in RULES:
            tool = AlignmentTool(rule_id, root); registry.register(tool)
            attempt = {'name': tool.name, 'status': 'started'}
            state['tool_attempts'].append(attempt); save(output/'run.json', state)
            answer = await registry.execute(tool.name, {})
            if is_tool_error_result(answer) or tool.result is None:
                raise ValueError('Object alignment tool failed: '+tool.name)
            results.append(tool.result); save(output/(rule_id+'_alignment.json'), tool.result)
            attempt['status'] = 'completed'; save(output/'run.json', state)
            print(rule_id+'：'+'；'.join(i['ref']+' '+i['status'] for i in tool.result['items']), flush=True)
        if results != snapshot['rules'] or collect_alignment(root) != snapshot:
            raise ValueError('Alignment/source changed before finalization')
        if code != {name: digest(Path(__file__).with_name(name)) for name in names}:
            raise ValueError('Alignment code changed')
        save(output/'alignment.json', snapshot)
        media = output/'teacher_target_media'; media.mkdir()
        artifacts = []
        for rule in results:
            for item in rule['items']:
                source = item['teacher_evidence']['source']
                with zipfile.ZipFile(source['document_path']) as z:
                    for image in item.get('teacher_adjacent_images', []):
                        if 'part' not in image: continue
                        target = media/(item['ref']+'_'+Path(image['part']).name)
                        target.write_bytes(z.read(image['part']))
                        if digest(target) != image['sha256']: raise ValueError('Extracted media differs')
                        artifacts.append({'path': str(target), 'sha256': digest(target)})
        lines = ['# P001 教师对象与 r194 初稿对齐', '',
                 '只读内容匹配；新增 LLM/SVN/Word 请求及 token 均为 0。段落号为 XML 顺序，不是页码。', '',
                 '| 项目 | 批注 | 内容对应结果 |', '|---|---|---|']
        for rule in results:
            for item in rule['items']:
                indices = [m['paragraph']['index'] for m in item.get('content_candidates', [])]
                lines.append(f'| {rule["rule_id"]} | {item["ref"]} | {item["status"]}；初稿候选段落 {indices or "未确认"} |')
        lines += ['', '## 已确认的内容对应与限制', '',
          '- C007：r247 p147 RNN 标题与 r194 p144 去空格后的正文一致；编号标准仍未确认，不能把“编号”解释为任意指定格式。',
          '- C009：r194 p175 是教师异常值检测段落去掉首位 2 后的完整前缀；初稿没有这个裸 2，不能照搬教师稿的同一编号错误。',
          '- C005：未找到 KDJ 三线说明的可靠文字对应；不得用另一个“(一)”段落替代。',
          '- C011/C010/C012：尚未确认模型 2 图、数据分布图、基础数据初始模型训练结果表的初稿身份。相同图号、类似题名或原生表格数为 0 均不足以判定。',
          '', '结果只关闭对象检索这一步，不把未对应项记为无错误、合格或已修改。初稿早于教师稿，不构成批改后修复证据。',
          '图片哈希覆盖已提取嵌入图片；无相同图片不等于对象不存在。未做 OCR、图片可读性阈值判定或真实版式上下关系判定。',
          '', f'[完整原文、匹配方法和图片清单](<{(output/"alignment.json").as_posix()}>)']
        (output/'report.md').write_text('\n'.join(lines), encoding='utf-8')
        for name in ['alignment.json', 'report.md', *(r+'_alignment.json' for r in RULES)]:
            artifacts.append({'path': str(output/name), 'sha256': digest(output/name)})
        if collect_alignment(root) != snapshot:
            raise ValueError('Source changed during evidence export')
        items = [i for rule in results for i in rule['items']]
        matched_count = sum(i['status'].startswith(('content_aligned_', 'image_content_aligned_')) for i in items)
        state.update(status='local_object_alignment_completed_scope_review_pending', artifacts=artifacts,
                     verified_content_mapping_count=matched_count, unresolved_teacher_target_count=len(items)-matched_count)
    except Exception as error:
        state.update(status='failed_no_retry', error_type=type(error).__name__)
        if isinstance(error, ValueError): state['validation_error'] = str(error)
    save(output/'run.json', state)
    return output, state
