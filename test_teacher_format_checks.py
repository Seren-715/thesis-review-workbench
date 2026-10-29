import html
import unittest
import xml.etree.ElementTree as ET

from teacher_format_checks import W,M,load_format_rules,check_format_rules
from new_paper_checks import extract_paragraphs
from paper_locations import annotate_paragraphs


def p(text,props='',runprops='',extra=''):
    return '<w:p><w:pPr>'+props+'</w:pPr><w:r><w:rPr>'+runprops+'</w:rPr><w:t>'+html.escape(text)+'</w:t></w:r>'+extra+'</w:p>'


def library():
    definitions=[('code_font_size','代码要使用五号字','def demo(): pass'),('line_spacing','段落间距应该是固定值20磅','研究结果说明有效。'),
                 ('paragraph_spacing','段后应该是0行','public void save(){ }'),('native_formula','公式要采用公式的格式。下面也是一样',''),
                 ('native_table','表格应该使用三线表，不要用图片的方式',''),('figure_page_binding','图序和图名必须紧跟着图','')]
    return {'rules':[{'id':family,'teacher_evidence':[{'quote':quote,'anchor':{'text':anchor},'student_name':'学生'}]} for family,quote,anchor in definitions]}


def execute(body,style='',lib=None,three_line_tables=False):
    doc=ET.fromstring(f'<w:document xmlns:w="{W[1:-1]}" xmlns:m="{M[1:-1]}"><w:body>{body}</w:body></w:document>')
    styles=ET.fromstring(f'<w:styles xmlns:w="{W[1:-1]}">{style}</w:styles>')
    rows=annotate_paragraphs(extract_paragraphs(doc,styles))
    return check_format_rules(doc,styles,rows,load_format_rules(library() if lib is None else lib),three_line_tables=three_line_tables)


class TeacherFormatTests(unittest.TestCase):
    def test_no_teacher_quote_never_enables_format_rules(self):
        findings,coverage=execute(p('1 正文','<w:outlineLvl w:val="0"/>')+p('def demo(): pass',runprops='<w:sz w:val="32"/>'),lib={'rules':[]})
        self.assertEqual(findings,[])
        self.assertTrue(all(c['status']=='not_applicable' for c in coverage))

    def test_code_font_inherited_and_plain_prose_not_scored(self):
        styles='<w:style w:type="paragraph" w:styleId="Base"><w:rPr><w:sz w:val="24"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Code"><w:basedOn w:val="Base"/></w:style>'
        body=p('1 方法','<w:outlineLvl w:val="0"/>')+p('def demo(): pass','<w:pStyle w:val="Code"/>')+p('正文介绍代码实现的设计思路。',runprops='<w:sz w:val="32"/>')
        findings,_=execute(body,styles)
        code=[f for f in findings if f['rule_id']=='NP018']
        self.assertEqual(len(code),1)
        self.assertEqual(code[0]['format_evidence'][0]['points'],12)
        self.assertEqual(code[0]['format_evidence'][0]['source'],'paragraph_style:Base')

    def test_correct_code_size_and_unknown_not_pass_as_error(self):
        body=p('1 方法','<w:outlineLvl w:val="0"/>')+p('def ok(): pass',runprops='<w:sz w:val="21"/>')+p('def unknown(): pass')
        findings,_=execute(body)
        code=[f for f in findings if f['rule_id']=='NP018']
        self.assertEqual(len(code),1)
        self.assertEqual(code[0]['status'],'needs_review')

    def test_body_line_spacing_resolves_style_and_excludes_thanks(self):
        styles='<w:style w:type="paragraph" w:styleId="Main"><w:pPr><w:spacing w:line="300" w:lineRule="exact"/></w:pPr></w:style>'
        body=p('1 研究','<w:outlineLvl w:val="0"/>')+p('本研究采用系统方法分析数据并报告验证结果。','<w:pStyle w:val="Main"/>')
        body+=p('谢辞')+p('感谢老师帮助研究采用系统方法分析数据。','<w:pStyle w:val="Main"/>')
        findings,_=execute(body,styles)
        rows=[f for f in findings if f['rule_id']=='NP019']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['format_evidence']['line']['source'],'paragraph_style:Main')

    def test_after_zero_rule_scoped_only_code_anchor(self):
        body=p('1 方法','<w:outlineLvl w:val="0"/>')+p('public void save(){ }','<w:spacing w:after="120"/>')+p('这是普通正文文字，应当不按代码格式要求处理。','<w:spacing w:after="120"/>')
        findings,_=execute(body)
        self.assertEqual(len([f for f in findings if f['rule_id']=='NP020']),1)
        lib=library();next(r for r in lib['rules'] if r['id']=='paragraph_spacing')['teacher_evidence'][0]['anchor']['text']='普通段落'
        rules=load_format_rules(lib)
        self.assertFalse(next(r for r in rules if r['id']=='NP020')['enabled'])

    def test_malformed_spacing_value_becomes_unknown_not_crash(self):
        body=p('1 方法','<w:outlineLvl w:val="0"/>')+p('public void save(){ }','<w:spacing w:after="bad"/>')
        findings,coverage=execute(body)
        self.assertFalse(any(f['rule_id']=='NP020' for f in findings))
        self.assertIn('待核对1项',next(c for c in coverage if c['rule_id']=='NP020')['detail'])

    def test_any_image_is_not_a_formula(self):
        body=p('1 研究','<w:outlineLvl w:val="0"/>')+p('这是系统截图','',extra='<w:r><w:drawing/></w:r>')
        body+=p('（2-1）',extra='<w:r><w:pict/></w:r>')+p('（2-2）',extra='<m:oMathPara><m:oMath/></m:oMathPara>')
        findings,_=execute(body)
        formulas=[f for f in findings if f['rule_id']=='NP021']
        self.assertEqual(len(formulas),1)
        self.assertEqual(formulas[0]['text'],'（2-1）')
        self.assertEqual(formulas[0]['status'],'needs_review')

    def test_single_cell_display_box_not_subject_to_data_table_rule(self):
        body=p('1 正文','<w:outlineLvl w:val="0"/>')+'<w:tbl><w:tblPr><w:tblBorders><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr><w:tr><w:tc>'+p('字段内容')+'</w:tc></w:tr></w:tbl>'
        findings,coverage=execute(body,three_line_tables=True)
        self.assertFalse(any(f['rule_id']=='NP022' for f in findings))
        row=next(c for c in coverage if c['rule_id']=='NP022')
        self.assertEqual(row['excluded_count'],1)
        self.assertEqual(row['excluded_objects'][0]['scope_evidence']['category'],'content_box')

    def test_caption_same_page_not_inferred_from_xml(self):
        body=p('1 正文','<w:outlineLvl w:val="0"/>')+p('图1-1 系统结构')+p('',extra='<w:r><w:drawing/></w:r>')
        findings,_=execute(body)
        row=next(f for f in findings if f['rule_id']=='NP023')
        self.assertEqual(row['status'],'needs_review')
        self.assertNotIn('physical_pages',row)


if __name__=='__main__':unittest.main()
