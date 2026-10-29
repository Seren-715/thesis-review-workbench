"""Meaningful regression checks for false version/object correspondence."""
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from reviewed_rule_catalog import BATCH
from scope_object_alignment import collect_alignment, run_alignment, text_candidates, align_source, AlignmentTool
from nanobot.agent.tools.registry import ToolRegistry, is_tool_error_result


def paragraph(index, text):
    return {'index': index, 'text': text, 'in_deleted': False, 'in_textbox': False, 'in_table': False}


async def main():
    initial = [paragraph(1, '(一) 均方误差'), paragraph(2, '(一) 循环神经网络（RNN）')]
    assert text_candidates('(一)KDJ指标由三条线组成，分别是：', initial) == []
    assert text_candidates('(一)循环神经网络（RNN）', initial)[0]['paragraph']['index'] == 2
    assert len(text_candidates('(一)循环神经网络（RNN）', initial+[paragraph(3, initial[1]['text'])])) == 2
    print('PASS：相同编号不能定位对象；重复标题保留歧义')

    # Same title without the same embedded object must not establish identity.
    p = paragraph(10, '图3-1 模型2初始结构图')
    evidence = {'ref': 'C011', 'original_anchor': {'status': 'complete', 'paragraph_indices': [10]}}
    teacher = {'paragraphs': [p], 'images': [{'paragraph_index': 9, 'sha256': 'teacher', 'decoded_rgba_sha256': 'teacher-pixels'}]}
    initial_inventory = {'source': {}, 'paragraphs': [paragraph(20, '图4-2 模型2初始结构图')],
                         'captions': [paragraph(20, '图4-2 模型2初始结构图')],
                         'images': [{'paragraph_index': 19, 'sha256': 'other', 'decoded_rgba_sha256': 'other-pixels'}]}
    result = align_source('PR003', evidence, initial_inventory, teacher)
    assert result['caption_title_candidates'] and result['status'] == 'no_verified_object_counterpart'
    initial_inventory['images'][0]['decoded_rgba_sha256'] = 'teacher-pixels'
    assert align_source('PR003', evidence, initial_inventory, teacher)['status'] == 'image_content_aligned_scope_review_required'
    print('PASS：题名相同不误认图片；不同编码的相同像素可形成内容对应')

    snapshot = collect_alignment(BATCH)
    flat = {item['ref']: item for rule in snapshot['rules'] for item in rule['items']}
    assert flat['C007']['content_candidates'][0]['paragraph']['index'] == 144
    assert flat['C009']['content_candidates'][0]['paragraph']['index'] == 175
    assert flat['C009']['status'] == 'content_aligned_teacher_bare_number_not_present'
    assert flat['C012']['initial_native_xml_table_count'] == 0
    assert len(flat['C012']['teacher_nearby_native_tables']) == 1
    assert all(flat[ref]['status'].startswith('no_verified_') for ref in ('C005', 'C010', 'C011', 'C012'))
    assert all(item['confirmed_error_count'] is None for item in flat.values())
    print('PASS：实际固定源2条内容对应、4条未确认，未照搬教师稿判错')

    registry = ToolRegistry(); registry.register(AlignmentTool('PR003', BATCH))
    assert is_tool_error_result(await registry.execute('align_pr003', {'rewrite': True}))
    try:
        collect_alignment(BATCH.parent)
        raise AssertionError('Unreviewed source accepted')
    except ValueError:
        pass
    teacher_path = snapshot['teacher_inventories'][0]['source']['document_path']
    import scope_object_alignment as module
    actual_digest = module.digest
    def wrong_digest(path):
        return 'changed' if str(path) == teacher_path else actual_digest(path)
    with patch.object(module, 'digest', side_effect=wrong_digest):
        try:
            collect_alignment(BATCH)
            raise AssertionError('Changed source accepted')
        except ValueError:
            pass
    print('PASS：未知工具参数、未经复核批次和变动原件均拒绝')

    with TemporaryDirectory(prefix='alignment_test_', dir=BATCH.parent) as temporary:
        output, state = await run_alignment(BATCH, output_parent=Path(temporary))
        assert state['status'] == 'local_object_alignment_completed_scope_review_pending', state
        assert len(state['tool_attempts']) == 4 and all(t['status'] == 'completed' for t in state['tool_attempts'])
        assert state['verified_content_mapping_count'] == 2 and state['unresolved_teacher_target_count'] == 4
        assert all(state[name] == 0 for name in ('model_requests', 'svn_requests', 'word_launches', 'new_tokens'))
        assert all(actual_digest(Path(a['path'])) == a['sha256'] for a in state['artifacts'])
    print('PASS：四个nanobot工具、输出证据哈希与零外部请求；临时结果已清理')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
