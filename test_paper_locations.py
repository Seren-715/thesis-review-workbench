from unittest import TestCase, main
from paper_locations import annotate_paragraphs, enrich_report_locations


def row(index, text, level=None, **kwargs):
    return dict(index=index, text=text, heading_level=level, **kwargs)


class LocationsTests(TestCase):
    def test_declaration_field_reference_keeps_body_location(self):
        ps = [row(1, '系统实现', 1), row(2, '系统包含“本人声明”字段，我们实现了输入校验。')]
        annotate_paragraphs(ps)
        self.assertEqual(ps[1]['location']['section'], '系统实现')
        self.assertEqual(ps[1]['location']['paragraph_in_section'], 1)

    def test_chapter_path_excludes_table_empty_and_toc_from_text_counter(self):
        ps = [row(0, '第3章 需求分析', 1), row(1, '3.1 功能需求', 2),
              row(2, ''), row(3, '表格文字', in_table=True), row(4, '我们设计系统。'),
              row(5, '目录中的标题', 1, in_toc=True), row(6, '另一个段落')]
        annotate_paragraphs(ps)
        self.assertEqual(ps[4]['location']['heading_path'], ['第3章 需求分析', '3.1 功能需求'])
        self.assertEqual(ps[4]['location']['paragraph_in_section'], 1)
        self.assertEqual(ps[6]['location']['paragraph_in_section'], 2)
        self.assertIn('表格内', ps[3]['location']['label'])

    def test_xml_908_is_not_presented_as_readable_paragraph_count(self):
        report = {'paragraphs': [row(890, '谢辞'), row(907, '我感谢导师。'), row(908, '感谢我的同学。')],
                  'excluded_clues': [{'paragraph_index': 908, 'reason': '位于已识别的谢辞范围'}]}
        enrich_report_locations(report)
        clue = report['excluded_clues'][0]
        self.assertEqual(clue['location']['section'], '谢辞')
        self.assertEqual(clue['location']['paragraph_in_section'], 2)
        self.assertNotIn('908', clue['location']['label'])
        self.assertEqual(clue['disposition'], 'outside_body_scope')

    def test_declaration_does_not_hide_later_unstyled_body(self):
        ps = [row(1, '本毕业设计（论文）是我个人在导师指导下完成的。'),
              row(2, '第1章 绪论'), row(3, '我们研究模型。')]
        annotate_paragraphs(ps)
        self.assertEqual(ps[0]['location']['section'], '独创性声明')
        self.assertEqual(ps[2]['location']['section'], '第1章 绪论')
        self.assertIsNone(ps[1]['heading_level'])
        self.assertTrue(ps[1]['location']['heading_inferred'])

    def test_chapter_organization_prose_does_not_change_real_scope(self):
        ps=[row(90,'绪论',1),row(107,'论文组织',2),
            row(109,'第一章是绪论，介绍论文背景。'),
            row(112,'第四章对系统进行设计与实现。'),
            row(113,'第五章是实现部分，介绍功能实现。'),
            row(114,'最后是论文对本课题进行总结。'),
            row(115,'技术背景介绍',1)]
        annotate_paragraphs(ps)
        for p in ps[2:6]:
            self.assertEqual(p['location']['heading_path'],['绪论','论文组织'])
            self.assertFalse(p['location']['heading_inferred'])

    def test_nominal_unstyled_chapter_heading_still_navigates(self):
        ps=[row(1,'第一章 绪论'),row(2,'研究意义。'),row(3,'第五章系统实现'),row(4,'实现方法。')]
        annotate_paragraphs(ps)
        self.assertEqual(ps[3]['location']['section'],'第五章系统实现')
        self.assertTrue(ps[2]['location']['heading_inferred'])


if __name__ == '__main__':
    main()
