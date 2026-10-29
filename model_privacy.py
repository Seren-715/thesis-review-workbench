"""Local deterministic PII masking for external model payloads.

This is a prevention layer, not a guarantee that arbitrary undisclosed PII can
be detected. Source documents and evidence quotes remain unchanged locally.
"""
from copy import deepcopy
import json
import re

VERSION = 'local_pii_mask_v1'
EMAIL = re.compile(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])')
PHONE = re.compile(r'(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)')
STUDENT_ID = re.compile(r'(?<![A-Za-z0-9])(?:19|20|21|22|23|24|25|26)\d{6,10}(?![A-Za-z0-9])')
LABEL_NAME = re.compile(r'((?:学生姓名|姓名|作者|联系人)\s*[：:]\s*)([\u4e00-\u9fff]{2,4})(?=\s|[，,。;；]|$)')


class LocalPIIMask:
    def __init__(self,names=()):
        self.names=sorted({n.strip() for n in names if isinstance(n,str) and 2<=len(n.strip())<=12},key=len,reverse=True)
        self.mapping={}

    def token(self,value,kind):
        if value not in self.mapping:
            self.mapping[value]=f'[{kind}{len(self.mapping)+1}]'
        return self.mapping[value]

    def mask_text(self,text):
        for name in self.names:
            text=text.replace(name,self.token(name,'姓名'))
        text=LABEL_NAME.sub(lambda m:m[1]+self.token(m[2],'姓名'),text)
        text=EMAIL.sub(lambda m:self.token(m[0],'邮箱'),text)
        text=PHONE.sub(lambda m:self.token(m[0],'电话'),text)
        return STUDENT_ID.sub(lambda m:self.token(m[0],'学号'),text)

    def mask_messages(self,messages):
        result=deepcopy(messages)
        for message in result:
            # Every role is masked. System text is a local constant today, but a
            # future prompt that embeds teacher comments or identity text must not
            # bypass the filter silently; masking these constants is a no-op.
            content=message.get('content')
            if isinstance(content,str):message['content']=self.mask_text(content)
            elif isinstance(content,list):
                for block in content:
                    if block.get('type')=='text':block['text']=self.mask_text(block['text'])
        return result

    def restore(self,text):
        if not isinstance(text,str):return text
        for original,token in sorted(self.mapping.items(),key=lambda row:len(row[1]),reverse=True):
            text=text.replace(token,original)
        return text


PLACEHOLDER_NAMES=frozenset({'未填写姓名','未知','学生','同学'})


def identity_names(report):
    """Names that must be masked before any external model call.

    The placeholder written when no student name was supplied is not a real name;
    masking it would tokenize an ordinary sentence instead of protecting anyone.
    """
    names=[report.get('student_name','')]
    for rule in report.get('rules',[]):
        names.extend(e.get('student_name','') for e in rule.get('teacher_evidence',[]))
    return [n.strip() for n in names if isinstance(n,str) and n.strip() and n.strip() not in PLACEHOLDER_NAMES]


def restore_saved_content(output, content):
    """Restore masked paid evidence locally, before exact-source validation."""
    from pathlib import Path
    from atomic_json import read_json
    path=Path(output)/'privacy_map.json'
    if not path.is_file():return content
    value=read_json(path)
    mapping=value.get('mapping')
    if value.get('version')!=VERSION or value.get('local_only') is not True or not isinstance(mapping,dict):
        raise ValueError('Saved local privacy mapping is invalid')
    if not all(isinstance(k,str) and isinstance(v,str) and re.fullmatch(r'\[(?:姓名|邮箱|电话|学号)\d+\]',v) for k,v in mapping.items()):
        raise ValueError('Saved local privacy token is invalid')
    masker=LocalPIIMask();masker.mapping=mapping
    return masker.restore(content)
