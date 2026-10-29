import unittest
import xml.etree.ElementTree as ET

from test_teacher_format_checks import p,execute
from table_format_checks import W,resolve_table_borders


def edges(**values):
    return ''.join(f'<w:{name} w:val="{value}" w:sz="4"/>' for name,value in values.items())


def table(rows,props='',cells=None,rowprops=None):
    cells=cells or {};rowprops=rowprops or {}
    return '<w:tbl><w:tblPr>'+props+'</w:tblPr>'+''.join('<w:tr>'+rowprops.get(ri,'')+''.join(
        '<w:tc><w:tcPr>'+cells.get((ri,ci),'')+'</w:tcPr>'+p(text)+'</w:tc>'
        for ci,text in enumerate(row))+'</w:tr>' for ri,row in enumerate(rows))+'</w:tbl>'


def check(content,style='',three_line_tables=True):
    findings,coverage=execute(p('1 正文','<w:outlineLvl w:val="0"/>')+content,style,three_line_tables=three_line_tables)
    return [f for f in findings if f['rule_id']=='NP022'],next(c for c in coverage if c['rule_id']=='NP022')


ROWS=[['字段名','类型'],['id','Integer'],['name','String']]
BASE=edges(top='single',bottom='single',left='none',right='none',insideH='none',insideV='none')


class TableScopeTests(unittest.TestCase):
    def test_captioned_grid_is_structural_suggestion_not_human_confirmation(self):
        items,cov=check(p('表1-1 各地区参与度')+table(ROWS,'<w:tblBorders>'+edges(top='single',bottom='single',insideV='single',insideH='single')+'</w:tblBorders>'))
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['status'],'candidate')
        self.assertEqual(items[0]['local_review']['decision'],'suggested_revision')
        self.assertTrue(items[0]['local_review']['validated_structure'])
        self.assertEqual(items[0]['scope_evidence']['captions'],['表1-1 各地区参与度'])
        self.assertEqual(cov['counts']['needs_review'],0)

    def test_prompt_caption_single_cell_is_explicit_exclusion(self):
        items,cov=check(p('表4-1 ICIO框架提示词')+table([['意图：优化组合。输入：股票数据。']],'<w:tblBorders>'+edges(left='single',right='single')+'</w:tblBorders>'))
        self.assertEqual(items,[])
        self.assertEqual(cov['excluded_count'],1)
        self.assertEqual(cov['excluded_objects'][0]['scope_evidence']['captions'],['表4-1 ICIO框架提示词'])

    def test_no_caption_or_data_headers_does_not_guess_layout_grid(self):
        items,cov=check(table([['A','B'],['内容甲','内容乙']],'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>'))
        self.assertEqual(items,[])
        self.assertEqual(cov['excluded_objects'][0]['scope_evidence']['category'],'scope_unresolved')

    def test_caption_binding_never_walks_through_prose_to_previous_caption(self):
        items,cov=check(p('表1-1 不相关题注')+p('另一个对象的长段落描述。')+table([['A','B'],['x','y']]))
        self.assertEqual(items,[])
        self.assertEqual(cov['excluded_objects'][0]['scope_evidence']['captions'],[])

    def test_caption_after_table_is_supported(self):
        items,_=check(table([['A','B'],['x','y']],'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>')+p('表1-1 比较表'))
        self.assertEqual(items[0]['scope_evidence']['captions'],['表1-1 比较表'])

    def test_caption_between_two_tables_not_stolen_by_first_table(self):
        first=table([['A','B'],['x','y']],'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>')
        second=table([['C','D'],['m','n']],'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>')
        items,cov=check(first+p('表1-2 第二张资料表')+p('')+second)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['table_index'],1)
        self.assertEqual(items[0]['scope_evidence']['captions'],['表1-2 第二张资料表'])
        self.assertEqual(cov['excluded_objects'][0]['table_index'],0)
        self.assertEqual(cov['excluded_objects'][0]['scope_evidence']['captions'],[])

    def test_preceding_caption_has_priority_over_following_unrelated_caption(self):
        items,_=check(p('表1-1 当前表')+table(ROWS,'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>')+p('表1-2 后一对象'))
        self.assertEqual(items[0]['scope_evidence']['captions'],['表1-1 当前表'])

    def test_data_header_without_caption_establishes_scope(self):
        items,_=check(table(ROWS,'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>'))
        self.assertEqual(items[0]['scope_evidence']['category'],'structured_data_table')

    def test_explicit_table_none_overrides_inherited_style_vertical(self):
        style='<w:style w:type="table" w:styleId="Grid"><w:tblPr><w:tblBorders>'+edges(insideV='single',left='single',right='single')+'</w:tblBorders></w:tblPr></w:style>'
        props='<w:tblStyle w:val="Grid"/><w:tblBorders>'+BASE+'</w:tblBorders>'
        header_cells={(0,c):'<w:tcBorders>'+edges(bottom='single')+'</w:tcBorders>' for c in range(2)}
        items,_=check(table(ROWS,props,header_cells),style)
        self.assertEqual(items[0]['local_review']['decision'],'not_issue')
        self.assertFalse(any(s['visible'] for s in items[0]['format_evidence']['segments'] if s['axis']=='vertical'))

    def test_nil_cell_edge_suppresses_table_grid_without_adding_them(self):
        props='<w:tblBorders>'+BASE.replace('insideV w:val="none"','insideV w:val="single"')+'</w:tblBorders>'
        cells={(r,c):'<w:tcBorders>'+edges(left='nil',right='nil',bottom='single' if r==0 else 'none')+'</w:tcBorders>' for r in range(3) for c in range(2)}
        items,_=check(table(ROWS,props,cells))
        self.assertEqual(items[0]['local_review']['decision'],'not_issue')

    def test_word_cell_none_is_fallback_not_suppressing_table_inside_v(self):
        props='<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>'
        cells={(r,c):'<w:tcBorders>'+edges(left='none',right='none')+'</w:tcBorders>' for r in range(3) for c in range(2)}
        items,_=check(table(ROWS,props,cells))
        self.assertEqual(items[0]['local_review']['decision'],'suggested_revision')

    def test_adjacent_nil_suppresses_opposing_visible_cell(self):
        props='<w:tblBorders>'+BASE+'</w:tblBorders>'
        cells={(r,0):'<w:tcBorders>'+edges(right='single')+'</w:tcBorders>' for r in range(3)}
        cells.update({(r,1):'<w:tcBorders>'+edges(left='nil')+'</w:tcBorders>' for r in range(3)})
        items,_=check(table(ROWS,props,cells))
        self.assertEqual(items[0]['status'],'needs_review')  # missing header line, not false grid error
        self.assertFalse(any(s['visible'] for s in items[0]['format_evidence']['segments'] if s['axis']=='vertical'))

    def test_direct_cell_vertical_line_proves_difference(self):
        props='<w:tblBorders>'+BASE+'</w:tblBorders>'
        items,_=check(table(ROWS,props,{(1,0):'<w:tcBorders>'+edges(right='single')+'</w:tcBorders>'}))
        self.assertEqual(items[0]['local_review']['decision'],'suggested_revision')
        visible=[s for s in items[0]['format_evidence']['segments'] if s['axis']=='vertical' and s['visible']]
        self.assertEqual(len(visible),1)
        self.assertIn('cell_direct:1:0',str(visible))

    def test_unknown_conditional_style_does_not_blindly_add_verticals(self):
        style='<w:style w:type="table" w:styleId="Conditional"><w:tblPr><w:tblBorders>'+edges(insideV='single')+'</w:tblBorders></w:tblPr><w:tblStylePr w:type="firstRow"><w:tcPr><w:tcBorders>'+edges(left='nil',right='nil')+'</w:tcBorders></w:tcPr></w:tblStylePr></w:style>'
        items,_=check(table(ROWS,'<w:tblStyle w:val="Conditional"/>'),style)
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertNotIn('local_review',items[0])
        self.assertIn('cnfStyle',items[0]['reason'])

    def test_merged_cells_have_specific_unsupported_reason(self):
        items,_=check(table(ROWS,'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>',{(0,0):'<w:gridSpan w:val="2"/>'}))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertIn('合并单元格',items[0]['reason'])

    def test_inherited_regular_table_style_resolves(self):
        style='<w:style w:type="table" w:styleId="Base"><w:tblPr><w:tblBorders>'+edges(insideV='single')+'</w:tblBorders></w:tblPr></w:style><w:style w:type="table" w:styleId="Child"><w:basedOn w:val="Base"/></w:style>'
        items,_=check(table(ROWS,'<w:tblStyle w:val="Child"/>'),style)
        self.assertEqual(items[0]['local_review']['decision'],'suggested_revision')
        self.assertEqual(items[0]['format_evidence']['style_chain'],['Base','Child'])

    def test_missing_style_is_unknown_not_error_or_pass(self):
        items,_=check(table(ROWS,'<w:tblStyle w:val="Missing"/><w:tblBorders>'+BASE+'</w:tblBorders>'))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertIn('table_style_missing',items[0]['reason'])

    def test_no_borders_does_not_claim_three_line_pass(self):
        items,_=check(table(ROWS))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertNotIn('local_review',items[0])

    def test_extra_horizontal_lines_not_assumed_error_if_header_unresolved(self):
        items,_=check(table(ROWS,'<w:tblBorders>'+edges(top='single',bottom='single',insideH='single',insideV='none',left='none',right='none')+'</w:tblBorders>'))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertIn('多层表头',items[0]['reason'])

    def test_row_border_exception_overrides_table_borders(self):
        props='<w:tblBorders>'+BASE+'</w:tblBorders>'
        ex='<w:tblPrEx><w:tblBorders>'+edges(insideV='single')+'</w:tblBorders></w:tblPrEx>'
        items,_=check(table(ROWS,props,rowprops={1:ex}))
        self.assertEqual(items[0]['local_review']['decision'],'suggested_revision')
        self.assertIn('row_exception:1',str(items[0]['format_evidence']['segments']))

    def test_white_border_requires_rendering_not_fabricated_visibility(self):
        items,_=check(table(ROWS,'<w:tblBorders><w:insideV w:val="single" w:sz="4" w:color="FFFFFF"/></w:tblBorders>'))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertIn('colour_requires_rendering',items[0]['reason'])

    def test_bad_border_value_is_unresolved(self):
        items,_=check(table(ROWS,'<w:tblBorders><w:insideV w:sz="4"/></w:tblBorders>'))
        self.assertEqual(items[0]['status'],'needs_review')

    def test_nonzero_spacing_not_silently_collapsed(self):
        items,_=check(table(ROWS,'<w:tblCellSpacing w:w="40" w:type="dxa"/><w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>'))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertIn('单元格间距',items[0]['reason'])

    def test_logical_style_side_does_not_survive_direct_physical_none(self):
        style='<w:style w:type="table" w:styleId="Logical"><w:tblPr><w:tblBorders>'+edges(start='single',end='single')+'</w:tblBorders></w:tblPr></w:style>'
        props='<w:tblStyle w:val="Logical"/><w:tblBorders>'+BASE+'</w:tblBorders>'
        cells={(0,c):'<w:tcBorders>'+edges(bottom='single')+'</w:tcBorders>' for c in range(2)}
        items,_=check(table(ROWS,props,cells),style)
        self.assertEqual(items[0]['local_review']['decision'],'not_issue')

    def test_zero_width_single_is_unknown_not_invented_no_border(self):
        items,_=check(table(ROWS,'<w:tblBorders><w:insideV w:val="single" w:sz="0"/></w:tblBorders>'))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertIn('border_width_invalid',items[0]['reason'])

    def test_invalid_border_style_is_not_assumed_visible(self):
        items,_=check(table(ROWS,'<w:tblBorders><w:insideV w:val="banana"/></w:tblBorders>'))
        self.assertEqual(items[0]['status'],'needs_review')
        self.assertIn('border_style_unsupported',items[0]['reason'])

    def test_empty_first_cell_uses_visible_original_quote(self):
        items,_=check(p('表1-1 统计表')+table([['','总体','男'],['参与','50%','49%']],'<w:tblBorders>'+edges(insideV='single')+'</w:tblBorders>'))
        self.assertEqual(items[0]['text'],'总体')


if __name__=='__main__':unittest.main()
