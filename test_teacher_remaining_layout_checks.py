import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fitz
from PIL import Image
from docx import Document
from docx.shared import Inches

from new_paper_checks import read_docx, extract_paragraphs
from teacher_remaining_layout_checks import load_remaining_layout_rules, check_remaining_layout_rules


def library():
    return {'rules':[
        {'id':'blank_pages','teacher_evidence':[{'quote':'不要留空白页','evidence_id':'a'}, {'quote':'去掉多于空行','evidence_id':'b'}]},
        {'id':'image_page_bounds','teacher_evidence':[{'quote':'图片超过边界了','evidence_id':'c'}]},
        {'id':'figure_layout','teacher_evidence':[{'quote':'不要画在同一行','evidence_id':'d'}, {'quote':'这两张图叠在一起了，','evidence_id':'e'}]}]}


class RemainingLayoutTests(unittest.TestCase):
    def fixture(self, folder, rectangles=(), labels=(), blank=False, multiple_sections=False, mirrored=False, transparent=False):
        folder=Path(folder)
        source=folder/'source.docx'; pdf_path=folder/'preview.pdf'
        doc=Document()
        if mirrored:
            from docx.oxml import OxmlElement
            doc.settings.element.append(OxmlElement('w:mirrorMargins'))
        doc.add_paragraph('Current document body')
        for number, rectangle in enumerate(rectangles):
            image=folder/f'image{number}.png'
            Image.new('RGBA' if transparent else 'RGB',(50,40),(30+number*100,40,50,120) if transparent else (30+number*100,40,50)).save(image)
            shape=doc.add_paragraph().add_run().add_picture(str(image),width=Inches(2))
            if number < len(labels):
                shape._inline.docPr.set('descr',labels[number])
        if multiple_sections:
            doc.add_section()
        doc.save(source)
        page_width=doc.sections[0].page_width/12700
        page_height=doc.sections[0].page_height/12700
        pdf=fitz.open(); page=pdf.new_page(width=page_width,height=page_height)
        if not blank:
            page.insert_text((120,120),'Current document body')
        for number, rectangle in enumerate(rectangles):
            page.insert_image(fitz.Rect(rectangle),filename=str(folder/f'image{number}.png'),keep_proportion=False)
        pdf.save(pdf_path);pdf.close()
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        layout={'status':'native_table_layout_collected_visual_pending','source_unchanged':True,
            'source':{'path':str(source),'sha256':sha},
            'pdf':{'path':str(pdf_path),'sha256':hashlib.sha256(pdf_path.read_bytes()).hexdigest()}}
        document,styles,_,_=read_docx(source)
        paragraphs=extract_paragraphs(document,styles)
        return document,paragraphs,sha,layout

    def check(self, args):
        d,p,s,l=args
        return check_remaining_layout_rules(d,p,load_remaining_layout_rules(library()),s,l)

    def test_blank_page_requirement_does_not_convert_blank_lines_into_pages(self):
        rules=load_remaining_layout_rules(library())
        self.assertEqual([e['evidence_id'] for e in rules[0]['teacher_evidence']],['a'])
        self.assertEqual(rules[0]['excluded_evidence_ids'],['b'])
        vague={'rules':[{'id':'blank_pages','teacher_evidence':[{'quote':'不要留白'}]}]}
        self.assertFalse(load_remaining_layout_rules(vague)[0]['enabled'])

    def test_absent_verified_word_layout_never_reports_errors(self):
        with TemporaryDirectory() as folder:
            d,p,s,l=self.fixture(folder)
            f,c=check_remaining_layout_rules(d,p,load_remaining_layout_rules(library()),s,None)
            self.assertFalse(f)
            self.assertTrue(all(x['status']=='partial' for x in c))

    def test_actual_full_white_page_detected_without_text_heuristics(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,blank=True))
            self.assertEqual([x['rule_id'] for x in f],['NP047'])
            self.assertEqual(c[0]['blank_pages'],[1])

    def test_vector_and_scan_content_are_not_blank_despite_no_extractable_text(self):
        with TemporaryDirectory() as folder:
            args=self.fixture(folder,blank=True)
            path=Path(args[3]['pdf']['path'])
            pdf=fitz.open(path);pdf[0].draw_line((120,150),(300,150));new=path.with_suffix('.new.pdf');pdf.save(new);pdf.close();new.replace(path)
            args[3]['pdf']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            f,c=self.check(args)
            self.assertNotIn('NP047',[x['rule_id'] for x in f])
            self.assertEqual(c[0]['blank_pages'],[])

    def test_tampered_pdf_and_source_fail_closed(self):
        with TemporaryDirectory() as folder:
            args=self.fixture(folder,blank=True)
            Path(args[3]['pdf']['path']).write_bytes(b'not current PDF')
            f,c=self.check(args)
            self.assertFalse(f)
            self.assertTrue(all(x['status']=='partial' for x in c))
        with TemporaryDirectory() as folder:
            args=self.fixture(folder,blank=True)
            Path(args[3]['source']['path']).write_bytes(b'changed source')
            self.assertFalse(self.check(args)[0])

    def test_unique_current_media_pixels_bind_actual_outside_image(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(20,200,250,350)]))
            bounds=[x for x in f if x['rule_id']=='NP048']
            self.assertEqual(len(bounds),1)
            self.assertEqual(bounds[0]['layout_evidence']['physical_page'],1)
            self.assertEqual(c[1]['bound_images'],1)

    def test_large_image_within_actual_margins_not_error(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(100,200,480,450)]))
            self.assertNotIn('NP048',[x['rule_id'] for x in f])
            self.assertEqual(c[1]['bound_images'],1)

    def test_multisection_margins_not_guessed(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(20,200,250,350)],multiple_sections=True))
            self.assertNotIn('NP048',[x['rule_id'] for x in f])
            self.assertEqual(c[1]['status'],'partial')

    def test_mirrored_gutter_margins_not_guessed_from_one_section(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(20,200,250,350)],mirrored=True))
            self.assertNotIn('NP048',[x['rule_id'] for x in f])

    def test_transparent_media_not_bound_without_compositing_evidence(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(20,200,250,350)],transparent=True))
            self.assertNotIn('NP048',[x['rule_id'] for x in f])
            self.assertEqual(c[1]['bound_images'],0)

    def test_repeated_pdf_media_not_arbitrarily_bound_to_one_drawing(self):
        with TemporaryDirectory() as folder:
            args=self.fixture(folder,rectangles=[(20,200,250,350)])
            path=Path(args[3]['pdf']['path']);pdf=fitz.open(path)
            pdf[0].insert_image(fitz.Rect(300,200,500,350),filename=str(Path(folder)/'image0.png'))
            new=path.with_suffix('.new.pdf');pdf.save(new);pdf.close();new.replace(path)
            args[3]['pdf']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            f,c=self.check(args)
            self.assertNotIn('NP048',[x['rule_id'] for x in f])
            self.assertEqual(c[1]['bound_images'],0)

    def test_caption_metadata_required_not_nearby_caption_guess(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(100,200,300,350),(200,250,400,400)]))
            self.assertNotIn('NP049',[x['rule_id'] for x in f])
            self.assertEqual(c[2]['bound_captioned_images'],0)

    def test_distinct_explicit_metadata_images_actual_overlap(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(100,200,300,350),(200,250,400,400)],labels=['图2-1','图2-2']))
            objects=[x for x in f if x['rule_id']=='NP049']
            self.assertEqual(len(objects),1)
            self.assertEqual([e['evidence_id'] for e in objects[0]['teacher_evidence']],['e'])

    def test_same_row_does_not_apply_overlap_clause_or_combined_subfigure(self):
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(100,200,260,350),(280,200,440,350)],labels=['图2-1','图2-2']))
            objects=[x for x in f if x['rule_id']=='NP049']
            self.assertEqual(len(objects),1)
            self.assertEqual([e['evidence_id'] for e in objects[0]['teacher_evidence']],['d'])
        with TemporaryDirectory() as folder:
            f,c=self.check(self.fixture(folder,rectangles=[(100,200,260,350),(280,200,440,350)],labels=['图2-1','图2-1']))
            self.assertNotIn('NP049',[x['rule_id'] for x in f])


if __name__ == '__main__':
    unittest.main()
