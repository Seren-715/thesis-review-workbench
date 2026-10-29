"""Unified entry v2; old pipeline retained, plus reviewed-check local tools."""
import argparse
import asyncio
from pathlib import Path
import sys


def main():
    if len(sys.argv)>1 and sys.argv[1]=='reviewed-check':
        parser=argparse.ArgumentParser(description='Run six scoped reviewed policies on the accepted P001 demo; no API/Word/SVN')
        parser.add_argument('command',choices=['reviewed-check'])
        parser.add_argument('--run-dir',required=True,type=Path)
        args=parser.parse_args()
        from scoped_rule_checks import execute
        folder,state=asyncio.run(execute(args.run_dir))
        print('状态：',state['status']);print('结果目录：',folder)
        if (folder/'report.md').exists():print('可读报告：',folder/'report.md')
        print('新增LLM/SVN/Word请求：0；新增token：0。')
        if state.get('validation_error'):print('停止依据：',state['validation_error'])
        return 1 if state['status']=='failed_no_retry' else 0
    if len(sys.argv)==1 or sys.argv[1] in {'-h','--help'}:
        print('Commands: replay, plan --config, run --config, resume --run-dir, status --run-dir, reviewed-check --run-dir')
        print('reviewed-check executes scoped nanobot local tools, no API. Other commands reuse preserved paper_pipeline.py.')
        return 0
    from paper_pipeline import main as preserved_main
    return preserved_main()


if __name__=='__main__':raise SystemExit(main())
