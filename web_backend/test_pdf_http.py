"""PDF upload and original preview through the real HTTP handlers, offline."""
import json
import unittest
from urllib.parse import quote
import fitz
from web_backend.test_new_paper_service import NewPaperHttpTests, wait_complete


class PdfHttpTests(unittest.TestCase):
    setUp = NewPaperHttpTests.setUp
    tearDown = NewPaperHttpTests.tearDown
    request = NewPaperHttpTests.request
    headers = NewPaperHttpTests.headers

    def test_pdf_original_is_registered_and_served_inline_unchanged(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), 'PDF evidence original text')
            original = doc.tobytes()
        headers = self.headers()
        headers['X-File-Name'] = quote('212001234_测试学生_论文.pdf')
        status, body = self.request('/api/new-papers', 'POST', original, headers)
        self.assertEqual(status, 202, body)
        paper = wait_complete(self.service, json.loads(body)['id'])
        status, body = self.request('/api/new-papers/' + paper['id'])
        self.assertEqual(status, 200, body)
        view = json.loads(body)
        self.assertEqual(view['document']['source_format'], 'pdf')
        self.assertEqual(view['document']['pdf_assets'][0]['id'], view['source_asset_id'])
        self.assertEqual((self.service.folder(paper['id']) / 'source.pdf').read_bytes(), original)
        status, content = self.request('/api/assets/' + view['source_asset_id'])
        self.assertEqual(status, 200)
        self.assertEqual(content, original)
        self.workspace.refresh()
        self.assertEqual(self.request('/api/assets/' + view['source_asset_id'])[1], original)

    def test_mislabeled_pdf_is_rejected_without_creating_paper(self):
        headers = self.headers()
        headers['X-File-Name'] = 'paper.pdf'
        self.assertEqual(self.request('/api/new-papers', 'POST', b'not a pdf', headers)[0], 400)
        self.assertEqual(self.service.recent(), [])


if __name__ == '__main__':
    unittest.main()
