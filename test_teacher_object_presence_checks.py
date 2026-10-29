import unittest
from xml.etree import ElementTree as ET
from teacher_object_presence_checks import W, check_object_presence, load_object_presence_rules
from classification_scope_corrections import propose_scope_corrections

class PresenceTests(unittest.TestCase):
    def check(self, texts, table_index=None, image_index=None):
        d=ET.Element(W+'document'); rows=[]
        for i,text in enumerate(texts):
            p=ET.SubElement(d,W+'p'); ET.SubElement(ET.SubElement(p,W+'r'),W+'t').text=text
            if i==image_index: ET.SubElement(p,W+'drawing')
            rows.append(dict(index=i,text=text,in_table=i==table_index))
        rules=[dict(id='NP050',title='对象',enabled=True,teacher_evidence=[{'quote':'在哪里？'}],policy='specific')]
        return check_object_presence(d,rows,rules,'source')
    def test_unbound_source_label_is_candidate_not_absence(self):
        f,c=self.check(['表2国内投资参与度。图源：报告','进一步介绍调查'])
        self.assertEqual(len(f),1); self.assertFalse(f[0]['reference_diagnostic']['object_absence_proven'])
        self.assertTrue(f[0]['semantic_required'])
    def test_table_and_reference_are_bound(self):
        f,c=self.check(['表2 数据表','字段','见表2。'],table_index=1)
        self.assertEqual(f,[])
    def test_picture_table_is_not_declared_missing(self):
        f,c=self.check(['表2 数据表','','见表2。'],image_index=1); self.assertEqual(f,[])
    def test_prose_is_not_invented_into_caption(self):
        self.assertEqual(self.check(['表2说明了数据分布'])[0],[])
    def test_cross_chapter_reference_is_left_to_existing_checker(self):
        self.assertEqual(self.check(['第5章也可参见图4-3。'])[0],[])
    def test_disabled_rule_has_no_findings(self):
        self.assertFalse(load_object_presence_rules({'rules':[]})[0]['enabled'])
    def test_agent_target_selection_rejects_invented_simple_label(self):
        from new_paper_agent import validate_decisions
        import json
        findings,_=self.check(['图2 数据界面','','见表2。'],image_index=1)
        f=findings[0]; target=f['reference_diagnostic']['candidates'][0]
        row=dict(id=f['id'],decision='suggested_revision',quote='见表2。',target_id=target['id'],
                 reason='所述对象与图题相符。',suggestion='核实对应界面后改为图2。')
        valid=validate_decisions(json.dumps({'items':[row]},ensure_ascii=False),[f])[f['id']]
        self.assertEqual(valid['target_label'],'图2')
        for changes in ({'target_id':'invented'},{'suggestion':'改为图3。'},{'target_id':''}):
            with self.assertRaises(ValueError):
                validate_decisions(json.dumps({'items':[dict(row,**changes)]},ensure_ascii=False),[f])
    def test_scope_correction_requires_exact_marker(self):
        c=dict(rule_ids=['headings'],status='semantic_candidate',text='编号错误要全部自查',
            comment_id='1',source_sha256='sha',source_path='file',
            anchor=dict(status='complete',text='4-3',paragraph_indices=[2],
                context_paragraphs=[dict(index=2,text='如图4-3：')],
                markers={'starts':[dict(paragraph_index=2,paragraph_offset=2)]}))
        corrected,changes=propose_scope_corrections({'comments':[c]})
        self.assertEqual(corrected['comments'][0]['rule_ids'],['figure_numbering']); self.assertEqual(len(changes),1)
        self.assertEqual(c['rule_ids'],['headings'])
        c['anchor']['markers']['starts'][0]['paragraph_offset']=1
        self.assertEqual(propose_scope_corrections({'comments':[c]})[1],[])
    def test_explicit_chapter_instruction_not_reclassified(self):
        c=dict(rule_ids=['headings'],text='章节编号错误要全部自查')
        self.assertEqual(propose_scope_corrections({'comments':[c]})[1],[])

if __name__=='__main__': unittest.main()
