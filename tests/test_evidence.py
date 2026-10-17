import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from filing_evidence import extract, collect_filing, review_evidence
from server import init_db, connect, append, rows, Conflict
from structured_data import normalize_sec
from test_structured import sec_fixture


def fixture():
    return ('<html><body><p>NVIDIA '+('Business context. '*70)+'</p>'
            '<p id="capex">Our capital <b>expenditures</b> include equipment obligations; this paragraph is not proof of cash payment.</p>'
            '<table><tr><td>Government grants '+('hidden table '*10)+'</td></tr></table>'
            '<div style="display:none">Export controls '+('hidden text '*10)+'</div>'
            '<p>Purchase obligations are company commitments, not customer orders, and require review of cancellation terms.</p>'
            '</body></html>').encode()


class EvidenceTests(unittest.TestCase):
    def test_narrative_extraction_scope_and_locator(self):
        paragraphs, candidates, counts=extract(fixture())
        self.assertEqual(len(candidates),2)
        self.assertEqual(counts['support'],0)
        self.assertEqual(counts['restriction'],0)
        self.assertEqual(candidates[0]['anchor'],'capex')
        self.assertIn('capital expenditures',candidates[0]['quote'])
        self.assertEqual(paragraphs[candidates[0]['paragraph_index']-1]['text'],candidates[0]['quote'])
        self.assertTrue(all(c['signal']=='insufficient' for c in candidates))

    def test_collection_dedup_validation_and_review_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.db';init_db(path)
            filing=normalize_sec(sec_fixture())[0][0]
            fid='sec-nvda-'+filing['key'];filing.pop('kind')
            with connect(path) as db:append(db,'filing',fid,filing,0)
            with patch('filing_evidence.download',return_value=fixture()):
                self.assertEqual(collect_filing(path,fid,connect,append)['count'],2)
            with patch('filing_evidence.datetime') as clock,patch('filing_evidence.download',return_value=fixture()):
                clock.now.return_value=datetime(2090,1,1,tzinfo=timezone.utc)
                clock.fromisoformat.side_effect=datetime.fromisoformat
                collect_filing(path,fid,connect,append)
            with connect(path) as db:
                data=rows(db)
                self.assertEqual(len([r for r in data if r['kind']=='filing_body']),1)
                evidence=[r for r in data if r['kind']=='evidence']
                self.assertEqual(len(evidence),2)
                review=dict(evidence_id=evidence[0]['id'],status='accepted',answer='Need cash flow verification',next_condition='Check period',reason='Read context',expected_version=0)
                with self.assertRaises(ValueError):review_evidence(db,review,append)
                review['source_checked']=True
                self.assertEqual(review_evidence(db,review,append)[1],1)
                with self.assertRaises(Conflict):review_evidence(db,review,append)
                review.update(status='dismissed',expected_version=1)
                review_evidence(db,review,append)
            with connect(path) as db:
                history=[r for r in rows(db) if r['kind']=='evidence_review']
                self.assertEqual([r['status'] for r in history],['accepted','dismissed'])
            with self.assertRaises(ValueError):collect_filing(path,'unknown',connect,append)
