import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from server import create_server, connect, append, rows
from refresh_service import RefreshService, TASKS
from readiness import readiness
from browser_support import launch_browser
from test_structured import sec_fixture
from test_evidence import fixture
from structured_data import run_data_collection, award_query, AWARD_QUERY
from filing_evidence import collect_filing


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'operations.sqlite3'
        self.server = create_server(self.path, usage_mode='practice')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.server.refresh.runner = lambda key: {'status': 'received', 'count': 0}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, route, data=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.base+route, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def finish(self):
        self.server.refresh.worker.join(timeout=5)
        self.assertFalse(self.server.refresh.worker.is_alive())

    def age_job(self):
        with connect(self.path) as db:
            prior = self.server.refresh.status()['jobs'][0]
            payload = {k:v for k,v in prior.items() if k not in ('version','id','recorded_at')}
            payload['started_at'] = payload['updated_at'] = (datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
            append(db, 'refresh_job', prior['id'], payload)

    def test_async_overlap_and_immutable_completion(self):
        entered, release = threading.Event(), threading.Event()
        def runner(key):
            entered.set()
            release.wait(4)
            return {'status': 'received'}
        self.server.refresh.runner = runner
        try:
            self.assertEqual(self.request('/api/refresh', {})[0], 202)
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request('/api/refresh', {})[0], 400)
            # Other screens remain available while the source is waiting.
            self.assertEqual(self.request('/api/readiness')[1]['usage_mode'], 'practice')
        finally:
            release.set()
        self.finish()
        job = self.request('/api/refresh')[1]['jobs'][0]
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(len(job['results']), 4)
        with connect(self.path) as db:
            history = [r for r in rows(db) if r['kind']=='refresh_job']
        self.assertEqual(history[0]['status'], 'running')
        self.assertEqual(history[0]['results'], {})
        self.assertGreater(len(history), 4)
        self.assertEqual(self.request('/api/refresh', {})[0], 400)

    def test_partial_retry_and_dependency_failure(self):
        seen=[]
        def runner(key):
            seen.append(key)
            return {'status': 'http_error' if key=='sec-nvda' else 'received'}
        self.server.refresh.runner=runner
        self.request('/api/refresh', {})
        self.finish()
        job=self.request('/api/refresh')[1]['jobs'][0]
        self.assertEqual(job['status'],'partial')
        self.assertEqual(job['results']['nvda-body']['status'],'dependency_failed')
        self.assertNotIn('nvda-body',seen)
        self.age_job()
        seen.clear()
        self.server.refresh.runner=lambda key: seen.append(key) or {'status':'received'}
        self.assertEqual(self.request('/api/refresh',{'retry':True})[0],202)
        self.finish()
        self.assertEqual(seen,['sec-nvda','nvda-body'])
        self.assertEqual(self.request('/api/refresh')[1]['jobs'][0]['status'],'completed')

    def test_setting_conflict_schedule_restart_and_shutdown(self):
        self.server.refresh.tick()
        self.assertIsNone(self.server.refresh.worker)
        self.assertEqual(self.request('/api/refresh-settings', {'enabled':True,'expected_version':0})[0],200)
        self.assertEqual(self.request('/api/refresh-settings', {'enabled':False,'expected_version':0})[0],409)
        self.server.refresh.tick()
        self.finish()
        job=self.request('/api/refresh')[1]['jobs'][0]
        self.assertEqual(job['trigger'],'daily')
        self.server.refresh.tick()
        self.assertEqual(len(self.request('/api/refresh')[1]['jobs']),1)
        reopened=RefreshService(self.path,connect,append)
        self.assertTrue(reopened.status()['settings']['enabled'])
        self.assertEqual(reopened.status()['jobs'][0]['status'],'completed')
        reopened.close()
        with self.assertRaises(ValueError):
            reopened.start()
        self.assertEqual(self.request('/api/refresh-settings', {'enabled':False,'expected_version':1})[0],200)

    def test_input_and_origin_validation(self):
        for payload in ({'retry':'yes'},{'retry':1}):
            self.assertEqual(self.request('/api/refresh',payload)[0],400)
        self.assertEqual(self.request('/api/refresh',{},origin='https://example.com')[0],403)
        self.assertEqual(self.request('/api/refresh-settings',{'enabled':'yes','expected_version':0})[0],400)
        self.assertEqual(self.request('/api/refresh',{'retry':True})[0],400)

    def test_readiness_uses_actual_latest_and_preserves_success_time(self):
        data=[{'kind':'policy','id':'demo','version':1,'demo':True},
              {'kind':'policy','id':'p','version':1,'review_status':'draft'},
              {'kind':'policy','id':'p','version':2,'review_status':'reviewed'},
              {'kind':'evidence','id':'e','version':1},
              {'kind':'evidence_review','id':'e','version':1,'status':'accepted'},
              {'kind':'collection','id':'one','version':2,'source_id':'sec','status':'received','started_at':'2020-01-01T00:00:00+00:00','finished_at':'2020-01-01T00:00:01+00:00','recorded_at':'2020-01-01T00:00:01+00:00'},
              {'kind':'collection','id':'two','version':2,'source_id':'sec','status':'http_error','started_at':'2021-01-01T00:00:00+00:00','recorded_at':'2021-01-01T00:00:00+00:00'}]
        state=readiness(data,'practice')
        self.assertEqual(state['counts']['policies'],1)
        self.assertEqual(state['counts']['policies_reviewed'],1)
        self.assertEqual(state['counts']['evidence_pending'],0)
        source=state['sources'][0]
        self.assertEqual(source['status'],'http_error')
        self.assertEqual(source['last_success_at'],'2020-01-01T00:00:01+00:00')
        self.assertTrue(source['check_due'])

    def test_award_period_is_collection_date_without_mutating_template(self):
        with patch('structured_data.datetime') as clock:
            clock.now.return_value=datetime(2026,10,18,tzinfo=timezone(timedelta(hours=9)))
            query=award_query()
        self.assertEqual(query['filters']['time_period'][0]['end_date'],'2026-10-18')
        self.assertEqual(AWARD_QUERY['filters']['time_period'][0]['end_date'],'2026-10-17')

    def test_filing_failure_after_prior_history_releases_cursor(self):
        from urllib.error import HTTPError
        with patch('structured_data.request_data',return_value=(sec_fixture(),'cursor-sec')):
            run_data_collection(self.path,'sec-nvda',connect,append)
        with connect(self.path) as db:
            filing=next(r for r in rows(db) if r['kind']=='filing')
            append(db,'collection','old-body',{'demo':False,'source_id':'body-'+filing['id'],
                   'started_at':'2000-01-01T00:00:00+00:00','status':'received'},0)
        with patch('filing_evidence.download',side_effect=HTTPError(filing['source_url'],403,'Forbidden',{},None)):
            result=collect_filing(self.path,filing['id'],connect,append)
        self.assertEqual(result['status'],'http_error')
        with connect(self.path) as db:
            entries=[r for r in rows(db) if r['kind']=='collection' and r['id']==result['id']]
        self.assertEqual([r['status'] for r in entries],['running','http_error'])
        self.assertEqual(entries[-1]['http_status'],403)

    def test_guide_refresh_filters_and_mobile(self):
        with patch('structured_data.request_data',return_value=(sec_fixture(),'operations-sec')):
            run_data_collection(self.path,'sec-nvda',connect,append)
        with connect(self.path) as db:
            filing=next(r for r in rows(db) if r['kind']=='filing' and r['form']=='10-Q')
        with patch('filing_evidence.download',return_value=fixture()):
            collect_filing(self.path,filing['id'],connect,append)
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as p:
            browser=launch_browser(p)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.goto(self.base+'/#start')
            expect(page.locator('h1')).to_have_text('오늘의 검증을 시작하세요.')
            expect(page.locator('.mode')).to_contain_text('연습 모드')
            page.locator('#refresh-all').click()
            expect(page.locator('#refresh-results')).to_contain_text('시작')
            self.server.refresh.worker.join(timeout=5)
            page.locator('#refresh-status').click()
            expect(page.locator('#refresh-results')).to_contain_text('수신 완료')
            page.locator('#daily-refresh').check()
            expect(page.locator('#daily-refresh')).to_be_checked()
            page.reload()
            expect(page.locator('#daily-refresh')).to_be_checked()
            page.locator('#daily-refresh').uncheck()
            page.goto(self.base+'/#evidence/'+filing['id'])
            expect(page.locator('.evidence-candidate:visible')).to_have_count(2)
            page.locator('#evidence-topic').select_option('capex')
            expect(page.locator('.evidence-candidate:visible')).to_have_count(1)
            page.locator('#evidence-search').fill('not-present-987')
            expect(page.locator('.evidence-candidate:visible')).to_have_count(0)
            expect(page.locator('#evidence-count')).to_contain_text('조건에 맞는 후보가 없습니다')
            page.locator('#evidence-search').fill('')
            page.locator('#evidence-status').select_option('accepted')
            expect(page.locator('.evidence-candidate:visible')).to_have_count(0)
            page.set_viewport_size({'width':390,'height':844})
            for route in ('start','collection','evidence/'+filing['id']):
                page.goto(self.base+'/#'+route)
                expect(page.locator('h1')).to_be_visible()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),route)
            self.assertEqual(errors,[])
            browser.close()
