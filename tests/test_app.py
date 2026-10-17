from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

from server import create_server, ASSETS
from browser_support import launch_browser


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'test.sqlite3')
        self.start()

    def start(self):
        self.server = create_server(self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def request(self, route, data=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.base + route, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def note(self, **changes):
        return dict(question='투자가 집행됐나?', answer='<script>alert(1)</script>', signal='mixed',
                    next_condition='계약 확인', reason='공고 확인', analysis_version=2, **changes)

    def test_notes_versions_restart_and_conflict(self):
        status, saved = self.request('/api/notes', self.note())
        self.assertEqual(status, 201)
        self.assertEqual(self.request('/api/notes', self.note(id=saved['id'], expected_version=1))[0], 201)
        self.assertEqual(self.request('/api/notes', self.note(id=saved['id'], expected_version=1))[0], 409)
        self.stop()
        self.start()
        state = self.request('/api/export')[1]
        notes = [r for r in state['records'] if r['kind']=='note']
        self.assertEqual([n['version'] for n in notes], [1,2])
        self.assertEqual(notes[0]['answer'], '<script>alert(1)</script>')
        self.assertTrue(all(n['context_demo'] and not n['demo'] for n in notes))
        self.assertEqual(len([r for r in state['records'] if r['kind']=='policy']), 3)

    def test_validation_and_origin(self):
        for change in [{'question':' '}, {'signal':'buy'}, {'analysis_version':99}, {'answer':'x'*5001}]:
            data=self.note(); data.update(change)
            self.assertEqual(self.request('/api/notes',data)[0],400)
        self.assertEqual(self.request('/api/notes',self.note(),origin='https://untrusted.example')[0],403)

    def test_portfolio_validation_snapshot_and_immutability(self):
        amounts={a[0]:a[2] for a in ASSETS}
        amounts['cash']=2
        self.assertEqual(self.request('/api/portfolio',{'amounts':amounts,'expected_version':1})[0],201)
        self.assertEqual(self.request('/api/portfolio',{'amounts':amounts,'expected_version':1})[0],409)
        amounts['cash']=float('nan')
        self.assertEqual(self.request('/api/portfolio',{'amounts':amounts,'expected_version':2})[0],400)
        with closing(sqlite3.connect(self.db)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM records WHERE kind='portfolio'")
        snapshots=[r for r in self.request('/api/state')[1]['records'] if r['kind']=='portfolio']
        self.assertEqual(len(snapshots),2)
        self.assertEqual(snapshots[0]['holdings'][-1]['amount'],1)

    def policy_data(self):
        return dict(title='테스트 정책', country='한국', sector='반도체', publisher='테스트 기관',
                    source_url='https://example.org/policy', excerpt='테스트 발췌', question='실제 집행됐나?',
                    answer='집행 미확인', next_condition='지급 확인', reason='등록', stage=1,
                    event_date='2026-01-01', published_date='2026-01-02')

    def test_policy_review_revision_validation(self):
        status, saved=self.request('/api/policies',self.policy_data())
        self.assertEqual(status,201)
        review=dict(id=saved['id'],expected_version=1,action='approve',reason='원문 대조')
        self.assertEqual(self.request('/api/policies',review)[0],400)
        self.assertEqual(self.request('/api/policies',{**review,'source_checked':True})[0],201)
        changed={**self.policy_data(),'id':saved['id'],'expected_version':2,'stage':6,'reason':'지연 확인'}
        self.assertEqual(self.request('/api/policies',changed)[0],201)
        self.assertEqual(self.request('/api/policies',changed)[0],409)
        versions=[r for r in self.request('/api/state')[1]['records'] if r['id']==saved['id']]
        self.assertEqual([r['review_status'] for r in versions],['draft','reviewed','draft'])
        self.assertTrue(all(r['signal']=='insufficient' and not r['demo'] for r in versions))
        self.assertEqual(versions[1]['stage'],1)
        for change in [{'source_url':'javascript:alert(1)'},{'published_date':'2026-02-30'},{'id':'p-ai','expected_version':1}]:
            self.assertEqual(self.request('/api/policies',{**self.policy_data(),**change})[0],400)
        self.stop(); self.start()
        self.assertEqual(len([r for r in self.request('/api/state')[1]['records'] if r['id']==saved['id']]),3)

    def test_policy_browser_workflow(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        with sync_playwright() as p:
            browser=launch_browser(p)
            page=browser.new_page(viewport={'width':1440,'height':1080})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.base+'/#sources')
            for k,v in self.policy_data().items():
                if k=='stage':page.locator('#policy-form [name=stage]').select_option(str(v))
                else:page.locator(f'#policy-form [name={k}]').fill(v)
            page.get_by_role('button',name='검토 대기로 저장').click()
            expect(page.locator('.review-form')).to_have_count(1)
            page.goto(self.base+'/#map')
            expect(page.locator('#policy-rows')).not_to_contain_text('테스트 정책')
            page.goto(self.base+'/#sources')
            page.locator('.review-form [name=reason]').fill('발췌 대조 완료')
            page.locator('[name=source_checked]').check()
            page.get_by_role('button',name='검토 완료·지도 반영').click()
            expect(page.locator('.note .pill')).to_have_text('사용자 검토 완료')
            page.goto(self.base+'/#map')
            page.get_by_role('link',name='테스트 정책 ↗').click()
            expect(page.locator('h1')).to_have_text('테스트 정책')
            expect(page.locator('.analysis-card .signal')).to_have_text('○ 판단 보류')
            expect(page.locator('#analysis-version')).to_have_count(0)
            page.goto(self.base+'/#sources')
            page.get_by_role('button',name='수정·새 버전').click()
            page.locator('#policy-form [name=reason]').fill('새 자료 검토 필요')
            page.get_by_role('button',name='검토 대기로 저장').click()
            expect(page.locator('.review-form')).to_have_count(1)
            page.locator('.review-form [name=reason]').fill('내용 보완 필요')
            page.get_by_role('button',name='보완 요청').click()
            expect(page.locator('.note .pill')).to_have_text('보완 필요')
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            self.assertEqual(errors,[])
            browser.close()

    def reviewed_policy(self):
        _, policy=self.request('/api/policies',self.policy_data())
        status,_=self.request('/api/policies',dict(action='approve',id=policy['id'],expected_version=1,source_checked=True,reason='대조'))
        self.assertEqual(status,201)
        return policy['id']

    def relation_data(self, pid):
        return dict(policy_id=pid,policy_version=2,company_id='nvda',relation_type='potential',
                    source_url='https://example.org/contract',published_date='2026-01-03',
                    excerpt='관계 테스트 발췌',question='공급 관계인가?',answer='공급 가능성만 확인',
                    next_condition='계약 원문 확인',reason='최초 등록')

    def test_relations_validation_history_and_restart(self):
        pid=self.reviewed_policy(); data=self.relation_data(pid)
        for change in [{'company_id':'spcx'},{'policy_version':1},{'relation_type':'confirmed'},
                       {'source_url':'javascript:alert(1)'},{'policy_id':'p-ai'}]:
            self.assertEqual(self.request('/api/relations',{**data,**change})[0],400)
        status, saved=self.request('/api/relations',data)
        self.assertEqual(status,201)
        self.assertEqual(self.request('/api/relations',data)[0],400)
        status,_=self.request('/api/relations',{**data,'id':saved['id'],'expected_version':1,
                            'relation_type':'confirmed','contract_checked':True,'reason':'계약 대조'})
        self.assertEqual(status,201)
        self.assertEqual(self.request('/api/relations',{**data,'id':saved['id'],'expected_version':1})[0],409)
        self.assertEqual(self.request('/api/relations',dict(action='withdraw',id=saved['id'],expected_version=2,reason='계약 취소'))[0],201)
        self.stop();self.start()
        versions=[r for r in self.request('/api/state')[1]['records'] if r['id']==saved['id']]
        self.assertEqual([r['version'] for r in versions],[1,2,3])
        self.assertEqual(versions[-1]['status'],'withdrawn')
        self.assertTrue(all(r['signal']=='insufficient' for r in versions))

    def test_relation_browser_recheck_and_withdraw(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        pid=self.reviewed_policy()
        with sync_playwright() as p:
            browser=launch_browser(p)
            page=browser.new_page(viewport={'width':1440,'height':1080})
            page.goto(self.base+'/#relations')
            data=self.relation_data(pid)
            for key in ['source_url','published_date','excerpt','question','answer','next_condition','reason']:
                page.locator(f'#relation-form [name={key}]').fill(data[key])
            page.locator('[name=company_id]').select_option('nvda')
            page.get_by_role('button',name='연결 근거 저장').click()
            expect(page.locator('.relation-card')).to_have_count(1)
            page.goto(self.base+'/#stock/nvda')
            expect(page.locator('.relation-card')).to_contain_text('공급 가능성')
            expect(page.locator('.page-title .signal')).to_contain_text('판단 보류')
            self.request('/api/policies',{**self.policy_data(),'id':pid,'expected_version':2,'reason':'정책 수정'})
            page.reload()
            expect(page.locator('.relation-card')).to_contain_text('정책 변경 · 재검토 필요')
            page.goto(self.base+'/#relations')
            page.locator('.withdraw-form [name=reason]').fill('정책 변경으로 철회')
            page.get_by_role('button',name='관계 철회',exact=True).click()
            expect(page.locator('.relation-card .row small')).to_have_text('철회')
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            browser.close()

    def test_collection_failure_records_and_catalog(self):
        from unittest.mock import patch
        from urllib.error import URLError
        self.assertEqual(len(self.request('/api/collection-sources')[1]['sources']),3)
        self.assertEqual(self.request('/api/collect',{'source_id':'http://127.0.0.1/'})[0],400)
        with patch('collection.fetch_page',side_effect=URLError('Tunnel connection failed: 403 Forbidden')):
            status,result=self.request('/api/collect',{'source_id':'motir'})
        self.assertEqual(status,200)
        self.assertEqual(result['status'],'proxy_blocked')
        self.assertEqual(self.request('/api/collect',{'source_id':'motir'})[0],400)
        state=self.request('/api/state')[1]['records']
        self.assertEqual([r['status'] for r in state if r['kind']=='collection'],['running','proxy_blocked'])
        self.assertFalse(any(r['kind']=='document' for r in state))

    def test_collection_duplicate_and_browser_draft(self):
        from unittest.mock import patch
        from datetime import datetime,timezone
        result={'status':'received','message':'test fixture','http_status':200,'excerpt':'테스트 자료 발췌',
                'sha256':'a'*64,'bytes':20,'content_type':'text/html'}
        with patch('collection.fetch_result',return_value=result):
            self.assertEqual(self.request('/api/collect',{'source_id':'motir'})[0],200)
            with patch('collection.datetime') as fake_time:
                fake_time.now.return_value=datetime(2099,1,1,tzinfo=timezone.utc)
                fake_time.fromisoformat.side_effect=datetime.fromisoformat
                self.assertEqual(self.request('/api/collect',{'source_id':'motir'})[0],200)
        state=self.request('/api/state')[1]['records']
        self.assertEqual(len([r for r in state if r['kind']=='document']),1)
        self.assertEqual(len([r for r in state if r['kind']=='policy']),3)
        try:
            from playwright.sync_api import sync_playwright,expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        with sync_playwright() as p:
            b=launch_browser(p)
            page=b.new_page(viewport={'width':1440,'height':1080})
            page.goto(self.base+'/#collection')
            expect(page.locator('[data-use-document]')).to_have_count(1)
            page.get_by_role('button',name='이 자료로 정책 초안 작성').click()
            expect(page.locator('#policy-form [name=excerpt]')).to_have_value('테스트 자료 발췌')
            expect(page.locator('#policy-form [name=source_url]')).to_have_value('')
            for key,value in self.policy_data().items():
                if key=='stage': page.locator('#policy-form [name=stage]').select_option(str(value))
                else: page.locator(f'#policy-form [name={key}]').fill(value)
            page.get_by_role('button',name='검토 대기로 저장').click()
            expect(page.locator('.review-form')).to_have_count(1)
            latest_policy=[r for r in self.request('/api/state')[1]['records'] if r['kind']=='policy' and not r['demo']][0]
            self.assertIn('source_document_id',latest_policy)
            self.assertEqual(latest_policy['review_status'],'draft')
            page.goto(self.base+'/#collection')
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            b.close()

    def test_structured_api_browser(self):
        from unittest.mock import patch
        from test_structured import sec_fixture,award_fixture
        self.assertEqual(self.request('/api/collect-data',{'source_id':'unknown'})[0],400)
        with patch('structured_data.request_data',return_value=(sec_fixture(),'sec-test')):
            self.assertEqual(self.request('/api/collect-data',{'source_id':'sec-nvda'})[0],200)
        with patch('structured_data.request_data',return_value=(award_fixture(),'award-test')):
            self.assertEqual(self.request('/api/collect-data',{'source_id':'usa-micron'})[0],200)
        try:
            from playwright.sync_api import sync_playwright,expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        with sync_playwright() as p:
            b=launch_browser(p)
            page=b.new_page(viewport={'width':1440,'height':1080})
            page.goto(self.base+'/#collection')
            expect(page.get_by_role('heading',name='실제 데이터 API')).to_be_visible()
            expect(page.locator('.amount').nth(0)).to_have_text('123.45')
            expect(page.locator('.amount').nth(1)).to_have_text('자료 없음')
            page.goto(self.base+'/#stock/nvda')
            expect(page.get_by_role('link',name='0001045810-26-000002 ↗')).to_be_visible()
            expect(page.locator('.page-title .signal')).to_contain_text('판단 보류')
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            b.close()

    def test_browser_journey(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        with sync_playwright() as p:
            browser=launch_browser(p)
            page=browser.new_page(viewport={'width':1440,'height':1080})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.base)
            page.get_by_role('heading',name='흐름을 읽고, 근거를 남기다.').wait_for()
            self.assertEqual(page.evaluate('getComputedStyle(document.body).backgroundColor'),'rgb(245, 246, 248)')
            self.assertEqual(page.locator('.sidebar').evaluate('e=>getComputedStyle(e).position'),'fixed')
            page.locator('#analysis-version').select_option('1')
            self.assertIn('판단 보류',page.locator('.analysis-card .signal').inner_text())
            page.get_by_role('link',name='타임라인과 근거 살펴보기 ↗').click()
            page.get_by_role('heading',name='실행 타임라인').wait_for()
            self.assertIn('판단 보류',page.locator('.analysis-card .signal').inner_text())
            page.get_by_role('link',name='연산 엔비디아 ↗ 정책 수요와의 관계를 조사할 후보').click()
            page.get_by_role('heading',name='엔비디아',exact=True).wait_for()
            self.assertIn('판단 보류',page.locator('.page-title .signal').inner_text())
            page.goto(self.base+'/#map')
            page.locator('#country').select_option('한국')
            self.assertEqual(page.locator('#policy-rows tr').count(),1)
            page.goto(self.base+'/#portfolio')
            page.locator('#impact').wait_for()
            self.assertEqual(page.locator('#impact').inner_text(),'-9.25%')
            page.locator('#asset-cash').fill('2')
            page.get_by_role('button',name='새 자산 기록 저장').click()
            expect(page.locator('#impact')).to_have_text('-7.93%')
            page.goto(self.base+'/#notes')
            page.locator('[name=question]').fill('테스트 질문')
            page.locator('[name=answer]').fill('<img src=x onerror=alert(1)>')
            page.locator('[name=next_condition]').fill('다음 계약 확인')
            page.locator('[name=reason]').fill('초기 판단')
            page.get_by_role('button',name='질문 기록 저장').click()
            page.locator('.note').wait_for()
            self.assertEqual(page.locator('.note img').count(),0)
            page.get_by_role('button',name='판단 업데이트').click()
            page.locator('[name=answer]').fill('두 번째 판단')
            page.locator('[name=reason]').fill('추가 근거 확인')
            page.get_by_role('button',name='새 버전으로 저장').click()
            expect(page.locator('.note .prewrap')).to_have_text('두 번째 판단')
            page.reload()
            page.locator('.note').wait_for()
            self.assertEqual(page.locator('.history').count(),2)
            page.set_viewport_size({'width':390,'height':844})
            for route in ['home','map','policy/p-ai','stock/spcx','portfolio','notes']:
                page.goto(self.base+'/#'+route)
                page.wait_for_timeout(100)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),route)
            self.assertEqual(errors,[])
            browser.close()

    def test_evidence_browser_review(self):
        from unittest.mock import patch
        from test_structured import sec_fixture
        from test_evidence import fixture
        with patch('structured_data.request_data',return_value=(sec_fixture(),'fixture')):
            self.request('/api/collect-data',{'source_id':'sec-nvda'})
        fid='sec-nvda-0001045810-26-000002'
        with patch('filing_evidence.download',return_value=fixture()):
            self.assertEqual(self.request('/api/collect-filing',{'filing_id':fid})[1]['count'],2)
        try:
            from playwright.sync_api import sync_playwright,expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        with sync_playwright() as p:
            b=launch_browser(p)
            page=b.new_page(viewport={'width':1440,'height':1080})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.base+'/#evidence/'+fid)
            expect(page.locator('.evidence-candidate')).to_have_count(2)
            card=page.locator('.evidence-candidate').first
            card.get_by_text('후보 검토·질문 답변 기록',exact=True).click()
            card.locator('[name=answer]').fill('지급 여부는 별도 확인 필요')
            card.locator('[name=reason]').fill('문맥 대조')
            card.locator('[name=source_checked]').check()
            card.get_by_role('button',name='검토 기록 저장').click()
            expect(card).to_contain_text('사용자 채택')
            page.reload()
            expect(card).to_contain_text('지급 여부는 별도 확인 필요')
            expect(card.locator('.signal')).to_contain_text('판단 보류')
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate('Array.from(document.querySelectorAll("main *")).filter(e=>e.getBoundingClientRect().right>395).map(e=>[e.tagName,e.className,e.textContent,e.getBoundingClientRect().width])'))
            self.assertEqual(errors,[])
            b.close()

    def test_progress_references_history_and_browser(self):
        from unittest.mock import patch
        from test_structured import sec_fixture
        from test_evidence import fixture
        pid=self.reviewed_policy()
        _,relation=self.request('/api/relations',self.relation_data(pid))
        with patch('structured_data.request_data',return_value=(sec_fixture(),'progress-test')):
            self.request('/api/collect-data',{'source_id':'sec-nvda'})
        with patch('filing_evidence.download',return_value=fixture()):
            self.request('/api/collect-filing',{'filing_id':'sec-nvda-0001045810-26-000002'})
        evidence=next(r for r in self.request('/api/state')[1]['records'] if r['kind']=='evidence')
        review=dict(evidence_id=evidence['id'],status='accepted',answer='검토 답변',next_condition='조건',reason='대조',source_checked=True,expected_version=0)
        data=dict(relation_id=relation['id'],relation_version=1,evidence_id=evidence['id'],review_version=1,
                  stage='capex',evidence_role='uncertain',observed_date='2025-12-31',question='정책이 투자로 이어졌나?',
                  answer='정책 연결은 불확실',next_condition='지원 계약 대조',reason='최초 기록',link_checked=True)
        self.assertEqual(self.request('/api/progress',data)[0],400)
        self.request('/api/evidence-review',review)
        for change in [{'review_version':0},{'relation_version':0},{'stage':'buy'},{'evidence_role':'certain'},
                       {'link_checked':False},{'observed_date':'2026-02-30'}]:
            self.assertEqual(self.request('/api/progress',{**data,**change})[0],400)
        try:
            from playwright.sync_api import sync_playwright,expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        with sync_playwright() as p:
            b=launch_browser(p)
            page=b.new_page(viewport={'width':1440,'height':1080})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.base+'/#relations')
            form=page.locator('#progress-form')
            form.locator('[name=evidence_id]').select_option(evidence['id'])
            form.locator('[name=stage]').select_option('capex')
            for key in ['observed_date','question','answer','next_condition','reason']:
                form.locator('[name='+key+']').fill(data[key])
            form.locator('[name=link_checked]').check()
            form.get_by_role('button',name='추적 기록 저장').click()
            expect(page.locator('.progress-card')).to_contain_text('정책 연결은 불확실')
            progress=next(r for r in self.request('/api/state')[1]['records'] if r['kind']=='progress')
            changed={**data,'id':progress['id'],'expected_version':1,'answer':'두 번째 판단'}
            self.assertEqual(self.request('/api/progress',changed)[0],201)
            self.assertEqual(self.request('/api/progress',changed)[0],409)
            page.goto(self.base+'/#stock/nvda')
            page.reload()
            expect(page.locator('.progress-card')).to_contain_text('두 번째 판단')
            self.request('/api/evidence-review',{**review,'expected_version':1,'status':'dismissed'})
            page.reload()
            expect(page.locator('.progress-card')).to_contain_text('참조 변경 · 재검토 필요')
            self.assertEqual(self.request('/api/progress',{**changed,'expected_version':2})[0],400)
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            page.goto(self.base+'/#relations')
            expect(page.locator('.progress-card')).to_contain_text('참조 변경')
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            self.assertEqual(errors,[]);b.close()
        self.stop();self.start()
        history=[r for r in self.request('/api/export')[1]['records'] if r['kind']=='progress']
        self.assertEqual([r['version'] for r in history],[1,2])
        self.assertEqual(history[0]['answer'],'정책 연결은 불확실')
        self.assertTrue(all(r['signal']=='insufficient' for r in history))

    def test_weekend_review_and_archive_browser(self):
        _,policy=self.request('/api/policies',self.policy_data())
        self.assertEqual(self.request('/api/history?at=invalid')[0],400)
        queue=self.request('/api/workbench')[1]['queue']
        self.assertEqual(len(queue),1)
        self.assertEqual(queue[0]['target_id'],policy['id'])
        try:
            from playwright.sync_api import sync_playwright,expect
        except ImportError:
            self.skipTest('Optional Playwright is not installed')
        with sync_playwright() as p:
            b=launch_browser(p)
            page=b.new_page(viewport={'width':1440,'height':1080})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.base+'/#review')
            expect(page.locator('.queue-card')).to_have_count(1)
            page.get_by_role('button',name='주말 답변 작성').click()
            form=page.locator('#weekend-form')
            form.locator('[name=answer]').fill('<img src=x onerror=alert(1)>')
            form.locator('[name=reason]').fill('검토 이유')
            form.locator('[name=due_date]').fill('2020-01-01')
            page.get_by_role('button',name='주말 답변 저장').click()
            expect(page.locator('.queue-card')).to_contain_text('재확인 예정일 도래')
            expect(page.locator('.review-editor img')).to_have_count(0)
            form.locator('[name=answer]').fill('두 번째 주말 답변')
            form.locator('[name=reason]').fill('추가 확인')
            form.locator('[name=due_date]').fill('2099-01-01')
            page.get_by_role('button',name='주말 답변 저장').click()
            expect(page.locator('.queue-card')).not_to_contain_text('재확인 예정일 도래')
            expect(page.locator('.queue-card')).to_contain_text('정책 원문 검토 대기')
            self.request('/api/policies',{**self.policy_data(),'id':policy['id'],'expected_version':1,'reason':'수정'})
            page.get_by_role('button',name='최신 기록 불러오기').click()
            expect(page.locator('.queue-card')).to_contain_text('주말 기록 이후 근거 변경')
            page.locator('#queue-filter').select_option('due')
            expect(page.locator('#queue-empty')).to_be_visible()
            page.goto(self.base+'/#history')
            page.locator('#archive-date').fill('2020-01-01')
            page.get_by_role('button',name='당시 기록 조회').click()
            expect(page.locator('#archive-result')).to_contain_text('이 날짜까지 앱에 저장된 실제 기록이 없습니다.')
            page.locator('#archive-date').fill('2099-01-01')
            page.get_by_role('button',name='당시 기록 조회').click()
            expect(page.locator('.archive-card')).to_have_count(2)
            expect(page.locator('#archive-result')).to_contain_text('두 번째 주말 답변')
            expect(page.locator('#archive-result img')).to_have_count(0)
            page.set_viewport_size({'width':390,'height':844})
            for route in ['history','review','home']:
                page.goto(self.base+'/#'+route)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),route)
            self.assertEqual(errors,[])
            b.close()
        self.stop();self.start()
        notes=[r for r in self.request('/api/export')[1]['records'] if r['kind']=='review']
        self.assertEqual([r['version'] for r in notes],[1,2])


if __name__=='__main__':
    unittest.main()
