import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from scripts.import_motir_policy import extract, run, TITLE
from server import connect, rows


class OfficialImportTests(unittest.TestCase):
    def fixture(self):
        return f'<h1>{TITLE}</h1><p>2026-10-15</p><p>중견기업 성장을 위한</p><p>월드클래스 프로젝트</p><p>과제당 최대 1,000 억원 선행연구 계획</p><p>목록</p><p>푸터</p>'.encode()

    def test_body_boundary_and_validation(self):
        text=extract(self.fixture())
        self.assertIn('1,000',text)
        self.assertNotIn('푸터',text)
        with self.assertRaises(ValueError):extract(b'<html>unrelated page</html>')

    def test_draft_dedup_and_failure_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.sqlite3'
            response=MagicMock()
            response.__enter__.return_value=response
            response.read.return_value=self.fixture()
            opener=MagicMock()
            opener.open.return_value=response
            with patch('scripts.import_motir_policy.build_opener',return_value=opener):
                run(path);run(path)
                response.read.return_value=b'<html>unexpected content</html>'
                with self.assertRaises(ValueError):run(path)
            with connect(path) as db: data=rows(db)
            policies=[r for r in data if r['kind']=='policy' and not r['demo']]
            self.assertEqual(len(policies),1)
            self.assertEqual(policies[0]['review_status'],'draft')
            self.assertEqual(policies[0]['stage'],0)
            self.assertEqual(len([r for r in data if r['kind']=='document']),1)
            self.assertEqual([r for r in data if r['kind']=='collection'][-1]['status'],'import_error')
