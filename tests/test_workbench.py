import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
from server import init_db, connect, append, rows, Conflict
from review_workbench import build_workbench, archive_snapshot, write_review, fingerprint, latest_map


def record(kind, entity_id, version=1, recorded='2026-01-01T00:00:00+00:00', **values):
    return {**dict(kind=kind,id=entity_id,version=version,recorded_at=recorded,demo=False),**values}


class WorkbenchTests(unittest.TestCase):
    def test_queue_actual_only_due_and_nested_change(self):
        policy=record('policy','p',review_status='reviewed',question='Policy?',signal='insufficient')
        relation=record('relation','r',policy_id='p',policy_version=1,status='active')
        review=record('review','n',target_kind='relation',target_id='r',target_version=1,
                      due_date='2026-02-01',context_fingerprint=fingerprint(relation,latest_map([policy,relation])))
        data=[policy,relation,review,record('policy','fake',demo=True,review_status='draft')]
        self.assertEqual(build_workbench(data,date(2026,1,31))['queue'],[])
        queue=build_workbench(data,date(2026,2,1))['queue']
        self.assertEqual(queue[0]['reasons'],['재확인 예정일 도래'])
        data.append({**policy,'version':2,'review_status':'draft'})
        queue=build_workbench(data,date(2026,1,31))['queue']
        r=next(q for q in queue if q['target_id']=='r')
        self.assertIn('연결 근거 변경 · 재검토 필요',r['reasons'])
        self.assertIn('주말 기록 이후 근거 변경',r['reasons'])
        self.assertEqual(len(queue),2)

    def test_archive_cutoff_publication_and_exact_dependencies(self):
        p=record('policy','p',published_date='2026-01-01',answer='then')
        records=[p,
            {**p,'version':2,'recorded_at':'2026-01-01T15:00:00+00:00','answer':'future'},
            record('policy','late',recorded='2026-01-03T00:00:00+00:00',published_date='2025-12-01'),
            record('policy','future-public',published_date='2026-01-02'),
            record('relation','invalid-ref',policy_id='p',policy_version=2),
            record('relation','valid-ref',policy_id='p',policy_version=1),
            record('policy','unknown-date'),
        ]
        snapshot=archive_snapshot(records,'2026-01-01')
        self.assertEqual({r['id'] for r in snapshot['current']},{'p','valid-ref','unknown-date'})
        self.assertEqual(snapshot['current'][0]['answer'],'then')
        self.assertEqual(snapshot['excluded_versions'],4)
        self.assertEqual(archive_snapshot(records,'2025-12-31')['records'],[])
        with self.assertRaises(ValueError):archive_snapshot(records,'2026-02-30')

    def test_review_versions_validation_and_context_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.db';init_db(path)
            with connect(path) as db:
                append(db,'policy','actual',dict(demo=False,title='Actual',review_status='draft',signal='insufficient'),0)
                data=rows(db);target=next(r for r in data if r['id']=='actual')
                review=dict(target_kind='policy',target_id='actual',target_version=1,
                            context_fingerprint=fingerprint(target,latest_map(data)),question='Question',answer='My interpretation',
                            next_condition='Check source',reason='Review',due_date='2026-03-01')
                for changes in [{'target_id':'p-ai'},{'context_fingerprint':'old'},{'due_date':'bad'},{'target_version':True}]:
                    with self.assertRaises(ValueError):write_review(db,{**review,**changes},append,data)
                saved,version=write_review(db,review,append,data)
                self.assertEqual(version,1)
                with self.assertRaises(ValueError):write_review(db,review,append,rows(db))
                changed={**review,'id':saved,'expected_version':1,'answer':'Second answer'}
                self.assertEqual(write_review(db,changed,append,rows(db))[1],2)
                with self.assertRaises(Conflict):write_review(db,changed,append,rows(db))
                append(db,'policy','actual',dict(demo=False,title='Changed',review_status='draft',signal='insufficient'),1)
                with self.assertRaises(ValueError):write_review(db,{**changed,'expected_version':2},append,rows(db))
            with connect(path) as db:
                notes=[r for r in rows(db) if r['kind']=='review']
                self.assertEqual([r['answer'] for r in notes],['My interpretation','Second answer'])
                self.assertTrue(all(r['signal']=='insufficient' for r in notes))

    def test_review_archive_does_not_leak_future_context(self):
        p=record('policy','p',published_date='2026-01-01')
        future=record('policy','p',version=2,recorded='2026-01-02T00:00:00+00:00',published_date='2026-01-02')
        note=record('review','n',target_kind='policy',target_id='p',target_version=1,
                    context_refs=[{'kind':'policy','id':'p','version':2}],answer='Future knowledge')
        result=archive_snapshot([p,future,note],'2026-01-01')
        self.assertEqual([r['kind'] for r in result['records']],['policy'])

    def test_latest_collection_result_replaces_old_error(self):
        failed=record('collection','old',source_id='sec',started_at='2026-01-01T00:00:00Z',status='http_error')
        running=record('collection','new',source_id='sec',started_at='2026-01-02T00:00:00Z',status='running')
        success={**running,'version':2,'status':'received'}
        self.assertEqual(len(build_workbench([failed,running])['collection_problems']),1)
        self.assertEqual(build_workbench([failed,running,success])['collection_problems'],[])
