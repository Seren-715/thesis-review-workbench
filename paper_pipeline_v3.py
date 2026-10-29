"""Add bounded local object alignment; preserve every earlier pipeline command."""
import argparse
import asyncio
import sys
from pathlib import Path
import paper_pipeline_v2
from scope_object_alignment import run_alignment


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) > 1 and sys.argv[1] == 'align-objects':
        parser = argparse.ArgumentParser(description='只读对齐教师选区与固定 P001 初稿，不调用模型/SVN/Word。')
        parser.add_argument('command', choices=['align-objects'])
        parser.add_argument('--run-dir', type=Path, required=True)
        args = parser.parse_args()
        output, state = asyncio.run(run_alignment(args.run_dir))
        print('状态：', state['status'])
        print('结果目录：', output)
        if (output/'report.md').exists(): print('可读报告：', output/'report.md')
        print('新增 LLM/SVN/Word 请求：0；新增 token：0。')
        if state.get('validation_error'): print('停止依据：', state['validation_error'])
        return 0 if state['status'] != 'failed_no_retry' else 1
    if len(sys.argv) == 1 or sys.argv[1] in {'-h', '--help'}:
        print('新增命令：align-objects --run-dir 已验收的P001批次目录')
    return paper_pipeline_v2.main()


if __name__ == '__main__':
    raise SystemExit(main())
