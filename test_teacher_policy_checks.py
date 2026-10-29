import unittest
import xml.etree.ElementTree as ET

from teacher_policy_checks import W, check_policy_rules, load_policy_rules


def rules():
    definitions={'list_numbering':'编号是（1）\n下面的编号是①','native_table_header_repeat':'续列需要重复表头','language_fluency':'语句不通顺','image_exposition':'不要连续贴图，要文字描述+图片+描述+图片'}
    return load_policy_rules({'rules':[{'id':key,'teacher_evidence':[{'quote':value,'student_name':'真实来源测试学生'}],'recurrence_status':'single_student_feedback'} for key,value in definitions.items()]})


def paragraphs(texts, section='第3章 系统实现'):
    doc=ET.Element(W+'document')
    body=ET.SubElement(doc,W+'body')
    rows=[]
    for i,text in enumerate(texts):
        p=ET.SubElement(body,W+'p')
        ET.SubElement(ET.SubElement(p,W+'r'),W+'t').text=text
        rows.append({'index':i,'text':text,'location':{'section':section,'heading_path':[section],'label':section+f' · 第{i+1}个文字段落'}})
    return doc,rows


def table_document(header=False):
    doc=ET.Element(W+'document')
    body=ET.SubElement(doc,W+'body')
    table=ET.SubElement(body,W+'tbl')
    tr=ET.SubElement(table,W+'tr')
    if header:
        ET.SubElement(ET.SubElement(tr,W+'trPr'),W+'tblHeader')
    p=ET.SubElement(ET.SubElement(tr,W+'tc'),W+'p')
    ET.SubElement(ET.SubElement(p,W+'r'),W+'t').text='字段名称'
    return doc,[{'index':0,'text':'字段名称','in_table':True,'location':{'section':'第4章 设计','heading_path':['第4章 设计']}}]


def layout(first=2,last=3,verified=True):
    return {'status':'native_table_layout_collected_visual_pending','source_unchanged':True,'source':{'sha256':'current-document-digest'},'_verified_source_sha256':'current-document-digest' if verified else 'another-digest','table_results':[{'index':1,'first_physical_page':first,'last_physical_page':last}]}


class PolicyTests(unittest.TestCase):
    def test_no_teacher_family_disabled(self):
        loaded=load_policy_rules({'rules':[]})
        self.assertFalse(any(r['enabled'] for r in loaded))
        doc,ps=paragraphs(['(1) 第一项','(1) 第二项'])
        found,coverage=check_policy_rules(doc,ps,loaded)
        self.assertEqual(found,[])
        self.assertTrue(all(c['status']=='disabled_no_teacher_evidence' for c in coverage))

    def test_local_duplicate_and_reverse_with_exact_offsets(self):
        doc,ps=paragraphs(['（1） 第一项','（2） 第二项','（2） 重复项','（1） 回退项'])
        found,_=check_policy_rules(doc,ps,rules())
        lists=[f for f in found if f['rule_id']=='NP014']
        self.assertEqual(len(lists),2)
        self.assertEqual(lists[0]['text'][lists[0]['start']:lists[0]['end']],'（2）')
        self.assertEqual(lists[1]['related_paragraph_indices'],[0])

    def test_independent_lists_scopes_and_gaps_not_errors(self):
        doc,ps=paragraphs(['(1) 第一列表','(3) 允许跳号','说明段落','(1) 第二列表','(2) 正常项','①独立风格','②正常项'])
        found,_=check_policy_rules(doc,ps,rules())
        self.assertFalse(any(f['rule_id']=='NP014' for f in found))

    def test_thanks_and_reference_scopes_excluded(self):
        doc,ps=paragraphs(['（1）甲','（1）乙','这里是一段较长的致谢材料，感谢我的同学和老师一直给予我支持鼓励，并帮助我完成这篇论文。'],'谢辞')
        found,_=check_policy_rules(doc,ps,rules())
        self.assertFalse(any(f['rule_id'] in {'NP014','NP016'} for f in found))

    def test_missing_or_wrong_source_layout_never_guesses_cross_page(self):
        doc,ps=table_document()
        for evidence in (None,layout(verified=False)):
            found,coverage=check_policy_rules(doc,ps,rules(),evidence)
            self.assertFalse(any(f['rule_id']=='NP015' for f in found))
            self.assertEqual(next(c['status'] for c in coverage if c['rule_id']=='NP015'),'needs_review')

    def test_cross_page_header_candidate_and_enabled_header_exclusion(self):
        doc,ps=table_document(False)
        found,_=check_policy_rules(doc,ps,rules(),layout())
        headers=[f for f in found if f['rule_id']=='NP015']
        self.assertEqual(len(headers),1)
        self.assertEqual(headers[0]['physical_pages'],[2,3])
        self.assertEqual(headers[0]['status'],'candidate')
        doc,ps=table_document(True)
        found,_=check_policy_rules(doc,ps,rules(),layout())
        self.assertFalse(any(f['rule_id']=='NP015' for f in found))
        doc,ps=table_document(False)
        found,_=check_policy_rules(doc,ps,rules(),layout(last=2))
        self.assertFalse(any(f['rule_id']=='NP015' for f in found))

    def test_language_sample_bounded_and_no_automatic_error_judgment(self):
        texts=['这是一个需要语言审阅的正文测试段落，用于说明系统的设计和实现，并没有因此直接证明正文存在语病。'+str(i) for i in range(12)]
        doc,ps=paragraphs(texts)
        found,coverage=check_policy_rules(doc,ps,rules())
        language=[f for f in found if f['rule_id']=='NP016']
        self.assertEqual(len(language),3)
        self.assertTrue(all(f['semantic_required'] and not f['section_complete'] for f in language))
        self.assertEqual([f['paragraph_index'] for f in language],[0,6,11])
        self.assertTrue(all(f['end']<=800 for f in language))
        row=next(c for c in coverage if c['rule_id']=='NP016')
        self.assertEqual(row['eligible_paragraph_count'],12)
        self.assertEqual(row['status'],'partial')

    def test_figure_exposition_only_bounded_adjacent_text(self):
        doc,ps=paragraphs(['这一部分说明数据采集的实现过程，系统读取数据库记录，然后按照业务要求进行处理，并向用户展示相关结果。','图4-1 实现效果','另一段实际说明文字，说明用户的操作如何触发后端处理、后台如何更新数据，并将结果返回页面。'])
        node=list(doc.iter(W+'p'))[1]
        ET.SubElement(ET.SubElement(node,W+'r'),W+'drawing')
        found,coverage=check_policy_rules(doc,ps,rules())
        figures=[f for f in found if f['rule_id']=='NP017']
        self.assertEqual(len(figures),1)
        self.assertTrue(figures[0]['semantic_required'])
        self.assertEqual(figures[0]['image_paragraph_index'],1)
        self.assertFalse(figures[0]['section_complete'])


if __name__=='__main__':
    unittest.main()
