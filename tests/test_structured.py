import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from structured_data import normalize_sec,normalize_awards,run_data_collection
from server import init_db,connect,append,rows


def sec_fixture():
    return {'cik':1045810,'name':'NVIDIA CORP','tickers':['NVDA'],'filings':{'recent':{
        'accessionNumber':['0001045810-26-000001','0001045810-26-000002'],
        'form':['10-Q','10-Q/A'],'filingDate':['2026-01-02','2026-01-03'],
        'reportDate':['2025-12-31','2025-12-31'],'acceptanceDateTime':['2026-01-02T16:00:00Z','2026-01-03T16:00:00Z'],
        'primaryDocument':['report.htm','amended.htm']}}}


def award_fixture(outlay=None):
    return {'results':[{'generated_internal_id':'ASST_NON_TEST_001','Award ID':'TEST',
        'Recipient Name':'MICRON TEST FIXTURE','Awarding Agency':'Test agency','Award Amount':'123.45',
        'Total Outlays':outlay,'Description':'Test only','Start Date':'2025-01-01','End Date':None,
        'Last Modified Date':'2026-01-01 12:34:56'}],'page_metadata':{'page':1,'hasNext':True}}


class StructuredTests(unittest.TestCase):
    def test_sec_identity_dates_and_amendments(self):
        items,_=normalize_sec(sec_fixture())
        self.assertTrue(items[0]['is_amendment'])
        self.assertEqual(items[0]['report_date'],'2025-12-31')
        self.assertTrue(items[0]['source_url'].startswith('https://www.sec.gov/Archives/'))
        bad=sec_fixture();bad['cik']=1
        with self.assertRaises(ValueError):normalize_sec(bad)
        bad=sec_fixture();bad['filings']['recent']['form'].pop()
        with self.assertRaises(ValueError):normalize_sec(bad)

    def test_award_null_zero_negative_and_coverage(self):
        items,coverage=normalize_awards(award_fixture())
        self.assertIsNone(items[0]['total_outlays_usd'])
        self.assertTrue(coverage['has_next'])
        self.assertEqual(items[0]['modified_date'],'2026-01-01')
        unrelated=award_fixture();unrelated['results'][0]['Recipient Name']='COLLEGE OF MICRONESIA'
        filtered,meta=normalize_awards(unrelated)
        self.assertEqual(filtered,[])
        self.assertEqual(meta['excluded_name_mismatch'],1)
        self.assertEqual(normalize_awards(award_fixture(0))[0][0]['total_outlays_usd'],'0')
        self.assertEqual(normalize_awards(award_fixture(-12.3))[0][0]['total_outlays_usd'],'-12.3')
        with self.assertRaises(ValueError):normalize_awards(award_fixture(float('nan')))

    def test_snapshots_changes_and_invalid_no_partial_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.db';init_db(path)
            with patch('structured_data.request_data',return_value=(award_fixture(),'digest1')):
                self.assertEqual(run_data_collection(path,'usa-micron',connect,append)['count'],1)
            def run_later(payload,digest,year):
                with patch('structured_data.request_data',return_value=(payload,digest)),patch('structured_data.datetime') as clock:
                    clock.now.return_value=datetime(year,1,1,tzinfo=timezone.utc)
                    clock.fromisoformat.side_effect=datetime.fromisoformat
                    return run_data_collection(path,'usa-micron',connect,append)
            run_later(award_fixture(),'digest1',2090)
            run_later(award_fixture(0),'digest2',2091)
            result=run_later(award_fixture('invalid'),'digest3',2092)
            self.assertEqual(result['status'],'import_error')
            with connect(path) as db:data=rows(db)
            awards=[r for r in data if r['kind']=='award']
            self.assertEqual(len(awards),2)
            self.assertIsNone(awards[0]['total_outlays_usd'])
            self.assertEqual(awards[1]['total_outlays_usd'],'0')
            self.assertEqual(len([r for r in data if r['kind']=='api_snapshot']),3)
