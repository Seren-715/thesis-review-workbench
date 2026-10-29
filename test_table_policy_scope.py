"""Require the current paper's format policy before judging table borders."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import new_paper_checks as checks
from teacher_format_checks import check_format_rules, load_format_rules
from table_format_checks import check_native_tables, W
from test_teacher_format_checks import execute, library, p
from test_table_format_checks import table, edges, ROWS, BASE
from test_new_paper_checks import CT, STYLES


GRID = '<w:tblBorders>'+edges(top='single',bottom='single',left='single',right='single',insideH='single',insideV='single')+'</w:tblBorders>'


class TablePolicyScopeTests(unittest.TestCase):
    def body(self):
        return p('1 系统设计','<w:outlineLvl w:val="0"/>')+p('表4-10 exam_paper表')+table(ROWS,GRID)

    def coverage(self, rows):
        return next(c for c in rows if c['rule_id']=='NP022')

    def test_default_omitted_policy_does_not_turn_source_evidence_into_error(self):
        findings,coverage=execute(self.body())
        self.assertFalse(any(f['rule_id']=='NP022' for f in findings))
        c=self.coverage(coverage)
        self.assertEqual(c['status'],'not_applicable_policy')
        self.assertTrue(c['policy_not_applicable'])
        self.assertFalse(c['three_line_tables'])
        self.assertEqual(c['checked_count'],0)
        self.assertEqual(c['observed_count'],1)
        self.assertEqual(c['counts']['not_judged_policy'],1)
        self.assertEqual(c['counts']['suggested_revision'],0)
        self.assertEqual(c['counts']['not_issue'],0)
        self.assertEqual(c['counts']['needs_review'],0)
        obj=c['checked_objects'][0]
        self.assertEqual(obj['scope_evidence']['captions'],['表4-10 exam_paper表'])
        self.assertEqual(obj['decision'],'not_judged_policy')
        self.assertEqual(obj['format_evidence']['status'],'resolved')
        self.assertTrue(any(s['axis']=='vertical' and s['visible'] for s in obj['format_evidence']['segments']))

    def test_explicit_false_does_not_generate_one_manual_item_per_database_table(self):
        body=self.body()+p('表4-11 student_paper表')+table(ROWS,GRID)
        findings,coverage=execute(body,three_line_tables=False)
        self.assertFalse(any(f['rule_id']=='NP022' for f in findings))
        self.assertEqual(self.coverage(coverage)['observed_count'],2)

    def test_true_applies_structure_rule_only_after_confirmation(self):
        findings,coverage=execute(self.body(),three_line_tables=True)
        rows=[f for f in findings if f['rule_id']=='NP022']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['status'],'candidate')
        self.assertEqual(rows[0]['local_review']['decision'],'suggested_revision')
        self.assertFalse(self.coverage(coverage)['policy_not_applicable'])
        self.assertEqual(self.coverage(coverage)['counts']['suggested_revision'],1)

    def test_three_line_structure_is_not_called_compliant_without_policy(self):
        cells={(0,c):'<w:tcBorders>'+edges(bottom='single')+'</w:tcBorders>' for c in range(2)}
        body=p('1 结果','<w:outlineLvl w:val="0"/>')+p('表1-1 结果')+table(ROWS,'<w:tblBorders>'+BASE+'</w:tblBorders>',cells)
        findings,coverage=execute(body)
        self.assertFalse(any(f['rule_id']=='NP022' for f in findings))
        self.assertEqual(self.coverage(coverage)['counts']['not_issue'],0)
        findings,_=execute(body,three_line_tables=True)
        self.assertEqual(next(f for f in findings if f['rule_id']=='NP022')['local_review']['decision'],'not_issue')

    def test_unknown_style_without_policy_is_evidence_only_not_manual_failure(self):
        body=p('1 正文','<w:outlineLvl w:val="0"/>')+table(ROWS,'<w:tblStyle w:val="Unknown"/>')
        findings,coverage=execute(body)
        self.assertFalse(any(f['rule_id']=='NP022' for f in findings))
        self.assertEqual(self.coverage(coverage)['checked_objects'][0]['format_evidence']['status'],'unresolved')
        self.assertEqual(self.coverage(coverage)['counts']['needs_review'],0)

    def test_prompt_box_exclusion_remains_when_policy_is_on_or_off(self):
        body=p('1 正文','<w:outlineLvl w:val="0"/>')+p('表4-1 提示词')+table([['请扮演一位分析员。']],GRID)
        for policy in (False,True):
            with self.subTest(policy=policy):
                findings,coverage=execute(body,three_line_tables=policy)
                self.assertFalse(any(f['rule_id']=='NP022' for f in findings))
                self.assertEqual(self.coverage(coverage)['excluded_count'],1)
                self.assertEqual(self.coverage(coverage)['excluded_objects'][0]['scope_evidence']['category'],'content_box')

    def test_rule_metadata_preserves_teacher_candidate_status_and_declares_policy(self):
        lib=deepcopy(library())
        source=next(r for r in lib['rules'] if r['id']=='native_table')
        source['recurrence_status']='single_student_feedback'
        rule=next(r for r in load_format_rules(lib) if r['id']=='NP022')
        self.assertTrue(rule['enabled'])  # A source-backed candidate is available.
        self.assertEqual(rule['requires_policy'],'three_line_tables')
        self.assertEqual(rule['recurrence_status'],'single_student_feedback')
        self.assertFalse(rule['automatic_error_judgment_enabled'])
        self.assertEqual(rule['teacher_evidence'],source['teacher_evidence'])

    def test_all_entrypoints_reject_non_boolean_policy(self):
        rule=next(r for r in load_format_rules(library()) if r['id']=='NP022')
        for invalid in (None,0,1,'true','false',[],{}):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError,'布尔值'):
                    checks.execute_checks('does-not-exist.docx',three_line_tables=invalid)
                with self.assertRaisesRegex(ValueError,'布尔值'):
                    check_format_rules(None,None,[],[],three_line_tables=invalid)
                with self.assertRaisesRegex(ValueError,'布尔值'):
                    check_native_tables(None,None,[],rule,three_line_tables=invalid)

    def test_execute_checks_threads_confirmation_and_preserves_original(self):
        rules=[{'id':i,'title':i,'scope':'test teacher','teacher_evidence':[{'quote':'test'}]} for i in ('NP001','NP002','NP003')]
        rules.extend(load_format_rules(library()))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new.docx'
            with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as package:
                package.writestr('[Content_Types].xml',CT)
                package.writestr('word/document.xml',f'<w:document xmlns:w="{W[1:-1]}"><w:body>{self.body()}</w:body></w:document>')
                package.writestr('word/styles.xml',STYLES)
            original=path.read_bytes()
            with patch.object(checks,'load_teacher_rules',return_value=rules):
                implicit=checks.execute_checks(path)
                explicit=checks.execute_checks(path,three_line_tables=True)
            self.assertFalse(any(f['rule_id']=='NP022' for f in implicit['findings']))
            self.assertTrue(self.coverage(implicit['coverage'])['policy_not_applicable'])
            self.assertEqual(len([f for f in explicit['findings'] if f['rule_id']=='NP022']),1)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(implicit['source_sha256'],explicit['source_sha256'])


if __name__=='__main__':unittest.main()
