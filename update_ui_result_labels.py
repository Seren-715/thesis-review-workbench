"""Apply reviewed copy and source-kind updates to the existing UI."""
from pathlib import Path

root=Path(__file__).resolve().parent
p=root/'web_frontend/src/NewPaperReview.tsx'
text=p.read_text(encoding='utf-8')
text=text.replace('按当前选项重新批改','继续审阅 / 按当前选项检查')
text=text.replace('同源分页与既有语境证据已复用','同源分页与既有语境证据已复用')
p.write_text(text,encoding='utf-8')
p=root/'web_frontend/src/TeacherRules.tsx'
text=p.read_text(encoding='utf-8')
text=text.replace('source_asset_id?:string;report_asset_id?:string;ledger_asset_id?:string;coverage?',"source_asset_id?:string;source_kind?:'document'|'report';report_asset_id?:string;ledger_asset_id?:string;coverage?")
text=text.replace("label:`${row.student_name??'学生'} · SVN 初稿`,kind:'document'", "label:`${row.student_name??'学生'} · SVN 初稿`,kind:row.source_kind??'document'")
p.write_text(text,encoding='utf-8')
p=root/'teacher_rule_library.py'
text=p.read_text(encoding='utf-8').replace('选最短支持片段，最长120字','优先选80字以内的短句，硬上限600字，不复制参考链接')
p.write_text(text,encoding='utf-8')
