"""Exercise the built engine over HTTP with no host Python, Node, Word or keys."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def copy_engine(source, destination):
    if destination.exists():
        raise FileExistsError(f'Use a fresh verification directory: {destination}')
    destination.mkdir(parents=True)
    files = [p for p in source.rglob('*') if p.is_file()]
    for folder in {p.parent.relative_to(source) for p in files}:
        (destination/folder).mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda p:shutil.copy2(p,destination/p.relative_to(source)),files))


def verify(source, destination, reuse=False):
    if not reuse:
        copy_engine(source, destination)
    manifest = json.loads((destination/'bundle-manifest.json').read_text(encoding='utf-8'))
    for relative, expected in manifest['files'].items():
        if sha256((destination/relative).read_bytes()).hexdigest() != expected:
            raise AssertionError(f'Bundle content mismatch: {relative}')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    environment = {k:v for k,v in os.environ.items() if not
        (k.upper().startswith(('PYTHON', 'THESIS_', 'DEEPSEEK', 'OPENAI')) or k.upper()=='PATH')}
    environment.update(PATH='',PYTHONHOME='Z:\\absent-python',PYTHONPATH='Z:\\absent-source')
    result = {'workspace':str(destination),'host_tools_on_path':False,'api_key_supplied':False}
    base = f'http://127.0.0.1:{port}'
    def request(path, body=None, headers=None):
        with urlopen(Request(base+path,data=body,headers=headers or {}),timeout=15) as response:
            raw=response.read()
            return json.loads(raw) if response.headers.get_content_type()=='application/json' else raw
    with (destination.parent/'backend-verification.log').open('wb') as log:
        process = subprocess.Popen([str(destination/'runtime/python.exe'),'-B',str(destination/'run_agent_ui.py'),
                                   '--port',str(port),'--desktop-stdio'],cwd=destination,
                                  env=environment,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(100):
                if process.poll() is not None: raise AssertionError('Bundled service exited; see backend-verification.log')
                try:
                    health=request('/api/health'); break
                except OSError: time.sleep(.2)
            else: raise TimeoutError('Bundled service failed to become ready')
            assert Path(health['workspace_root']).resolve()==destination.resolve()
            result['health']=health
            workspace=request('/api/workspace')
            assert workspace['startup']['mode']=='packaged_workspace' and workspace['startup']['checks_enabled']
            assert workspace['students']==[] and workspace['documents']==[] and workspace['actions']==[]
            result['startup']=workspace['startup']
            for route in ['/api/new-papers','/api/rule-library','/api/thesis-workflow','/']:
                response=request(route)
                if route=='/api/rule-library':
                    result['enabled_rule_count']=response['summary']['enabled_check_count']
            from docx import Document
            document=Document();document.add_heading('摘要',1);document.add_paragraph('本文设计并实现论文管理系统。')
            document.add_heading('1 系统实现',1);document.add_paragraph('我完成了输入校验，系统可保存审阅记录。')
            stream=BytesIO();document.save(stream)
            import fitz
            pdf=fitz.open();page=pdf.new_page();page.insert_text((70,70),'1 Implementation\nThe application validates input and saves review results.')
            samples=[('独立验证.docx',stream.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
                     ('independent.pdf',pdf.tobytes(),'application/pdf')]
            pdf.close()
            result['reviews']=[]
            for filename, raw, content_type in samples:
                headers={'Content-Type':'application/octet-stream','X-File-Name':quote(filename),'X-Student-Name':quote('合成测试'),
                         'X-Use-Agent':'false','X-Chapter-Policy':'false','X-Three-Line-Tables':'false',
                         'X-Session-Token':workspace['session_token']}
                uploaded=request('/api/new-papers',raw,headers)
                for _ in range(200):
                    paper=request('/api/new-papers/'+uploaded['id'])
                    if paper['status'] not in {'queued','running','cancelling'}:break
                    time.sleep(.1)
                assert paper['status']=='completed',paper.get('error',paper['status'])
                report=paper['report']; run=report['run']
                assert run['request_attempts']==0 and run.get('dispatched_requests',0)==0
                assert report['source_sha256']==sha256(raw).hexdigest()
                assert request('/api/assets/'+paper['source_asset_id'])==raw
                review=destination/'data/new_papers'/paper['id']/paper['review_id']
                assert (review/'report.md').is_file() and (review/'report.json').is_file()
                result['reviews'].append({'filename':filename,'status':paper['status'],'result_status':paper.get('result_status'),
                                          'findings':len(report['findings']),'model_requests':run['request_attempts'],
                                          'report_markdown':str(review/'report.md')})
            process.stdin.write(b'{"type":"shutdown","force":false}\n');process.stdin.flush()
            process.wait(timeout=20)
            assert process.returncode==0
            result['safe_shutdown']=True
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=15)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'output/standalone_20261022/engine')
    parser.add_argument('--destination',type=Path,default=ROOT/'output/standalone_20261022/verification/中文 空格/engine')
    parser.add_argument('--reuse',action='store_true',help='Rerun the existing synthetic verification workspace without recopying it.')
    args=parser.parse_args()
    result=verify(args.source.resolve(),args.destination.resolve(),args.reuse)
    report=args.destination.resolve().parent/'verification.json'
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
