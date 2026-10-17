"""Persistent refresh jobs. Collection never approves evidence or changes signals."""
import json
import threading
import uuid
from datetime import datetime, timezone, timedelta

TASKS = {
    'sec-nvda': '엔비디아 공시 목록',
    'usa-micron': 'MICRON 지원 검색',
    'motir-policy': '산업부 정책 원문',
    'nvda-body': '최신 엔비디아 정기 공시 본문',
}
SUCCESS = {'received'}


def now():
    return datetime.now(timezone.utc).isoformat()


def latest(db, kind):
    rows = db.execute('SELECT entity_id, version, recorded_at, payload FROM records WHERE kind=? ORDER BY recorded_at, version', (kind,))
    result = {}
    for row in rows:
        result[row['entity_id']] = {**json.loads(row['payload']), 'id': row['entity_id'], 'version': row['version'], 'recorded_at': row['recorded_at']}
    return list(result.values())


class RefreshService:
    def __init__(self, path, connect, append, runner=None):
        self.path, self.connect, self.append = path, connect, append
        self.runner = runner or self.collect
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.worker = None
        self.scheduler = None

    def status(self):
        with self.connect(self.path) as db:
            jobs = sorted(latest(db, 'refresh_job'), key=lambda j: j['started_at'], reverse=True)
            settings = latest(db, 'refresh_settings')
        setting = settings[-1] if settings else {'enabled': False, 'version': 0}
        due = None
        if setting['enabled']:
            reference = jobs[0]['started_at'] if jobs else setting.get('recorded_at', now())
            due = (datetime.fromisoformat(reference) + timedelta(hours=24)).isoformat() if jobs else now()
        return {'jobs': jobs[:20], 'settings': setting, 'next_due_at': due,
                'tasks': TASKS, 'active': bool(self.worker and self.worker.is_alive())}

    def configure(self, data):
        if type(data.get('enabled')) is not bool or type(data.get('expected_version')) is not int or data['expected_version'] < 0:
            raise ValueError('자동 확인 여부와 설정 버전이 필요합니다.')
        with self.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self.append(db, 'refresh_settings', 'daily', {'demo': False, 'enabled': data['enabled'],
                        'interval_hours': 24}, data['expected_version'])
        return self.status()

    def start(self, retry=False, trigger='manual'):
        with self.lock:
            if self.stop.is_set():
                raise ValueError('앱이 종료 중입니다.')
            if self.worker and self.worker.is_alive():
                raise ValueError('자료 확인이 진행 중입니다.')
            with self.connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE')
                jobs = sorted(latest(db, 'refresh_job'), key=lambda j: j['started_at'], reverse=True)
                previous = jobs[0] if jobs else None
                # Cross-process lease prevents two app instances from collecting together.
                if previous:
                    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous['updated_at'])).total_seconds()
                    if previous['status'] == 'running' and elapsed < 600:
                        raise ValueError('자료 확인이 진행 중입니다. 중단된 작업은 마지막 기록 10분 후 다시 시작할 수 있습니다.')
                    if (datetime.now(timezone.utc) - datetime.fromisoformat(previous['started_at'])).total_seconds() < 60:
                        raise ValueError('자료 확인은 60초 후 다시 요청하세요.')
                selected = list(TASKS)
                if retry:
                    if not previous or previous['status'] == 'completed':
                        raise ValueError('재시도할 실패 작업이 없습니다.')
                    selected = [key for key in TASKS if previous['results'].get(key, {}).get('status') not in SUCCESS]
                    if 'sec-nvda' in selected and 'nvda-body' not in selected:
                        selected.append('nvda-body')
                job_id = str(uuid.uuid4())
                job = {'demo': False, 'status': 'running', 'trigger': trigger, 'started_at': now(),
                       'updated_at': now(), 'tasks': selected, 'results': {}, 'current_task': None,
                       'retry_of': previous['id'] if retry else None}
                self.append(db, 'refresh_job', job_id, job, 0)
            self.worker = threading.Thread(target=self.run, args=(job_id, job), daemon=True)
            self.worker.start()
            return {'id': job_id, 'status': 'running'}

    def save(self, job_id, job):
        job['updated_at'] = now()
        with self.connect(self.path) as db:
            self.append(db, 'refresh_job', job_id, job)

    def run(self, job_id, job):
        for key in job['tasks']:
            if self.stop.is_set():
                job['status'] = 'interrupted'
                break
            job['current_task'] = key
            self.save(job_id, job)
            try:
                if key == 'nvda-body' and 'sec-nvda' in job['results'] and job['results']['sec-nvda']['status'] != 'received':
                    result = {'status': 'dependency_failed', 'message': '공시 목록 갱신 실패. 기존 본문을 새 자료로 표시하지 않습니다.'}
                else:
                    result = self.runner(key)
            except ValueError as exc:
                result = {'status': 'blocked', 'message': str(exc)}
            except Exception:
                result = {'status': 'error', 'message': '수집 처리 오류. 출처별 요청 이력을 확인하세요.'}
            job['results'][key] = result
            self.save(job_id, job)
        if job['status'] == 'running':
            job['status'] = 'completed' if all(r['status'] in SUCCESS for r in job['results'].values()) else 'partial'
        job['current_task'] = None
        job['finished_at'] = now()
        self.save(job_id, job)

    def collect(self, key):
        if key in ('sec-nvda', 'usa-micron'):
            from structured_data import run_data_collection
            return run_data_collection(self.path, key, self.connect, self.append)
        if key == 'motir-policy':
            from scripts.import_motir_policy import run
            return {'status': 'received', 'document_id': run(self.path)}
        from filing_evidence import collect_filing
        with self.connect(self.path) as db:
            filings = [f for f in latest(db, 'filing') if not f.get('demo') and f.get('company_id') == 'nvda'
                       and f.get('form') in ('10-Q', '10-K', '10-Q/A', '10-K/A')]
        if not filings:
            return {'status': 'missing', 'message': '수집 가능한 정기 공시 목록이 없습니다.'}
        filing = max(filings, key=lambda f: (f.get('filing_date') or '', f.get('published_at') or ''))
        return collect_filing(self.path, filing['id'], self.connect, self.append)

    def tick(self):
        state = self.status()
        if state['settings']['enabled'] and not state['active'] and state['next_due_at'] <= now():
            try:
                self.start(trigger='daily')
            except ValueError:
                pass

    def start_scheduler(self):
        def loop():
            while not self.stop.wait(30):
                try:
                    self.tick()
                except Exception:
                    # Persistent jobs retain unfinished state if storage is unavailable.
                    continue
        self.scheduler = threading.Thread(target=loop, daemon=True)
        self.scheduler.start()

    def close(self):
        self.stop.set()
        if self.scheduler:
            self.scheduler.join(timeout=2)
        if self.worker:
            self.worker.join(timeout=20)
