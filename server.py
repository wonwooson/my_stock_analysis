"""Local research prototype with fixed official sources; no AI calls."""
from contextlib import contextmanager
import argparse
import json
import math
import os
import sqlite3
import uuid
from policy_records import write_policy
from refresh_service import RefreshService
from readiness import readiness
from relation_records import write_relation
from collection import CATALOG, run_collection
from structured_data import SOURCES, run_data_collection
from review_workbench import build_workbench, write_review, archive_snapshot
from progress_records import write_progress
from filing_evidence import collect_filing, review_evidence
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
SIGNALS = {'opportunity', 'mixed', 'risk', 'insufficient'}
ASSETS = [
    ('nvda', '엔비디아', 1.2, True), ('mu', '마이크론', 1, True),
    ('sk', 'SK하이닉스', 1, True), ('spcx', 'SpaceX / SPCX · 식별 미확인', .5, False),
    ('rgti', '리게티 컴퓨팅', .5, False), ('mrvl', '마벨', .5, True),
    ('googl', '알파벳 A', .3, False), ('cash', '현금', 1, False),
]


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            yield db
    finally:
        db.close()


def append(db, kind, entity_id, payload, expected=None):
    version = db.execute('SELECT COALESCE(MAX(version),0) FROM records WHERE kind=? AND entity_id=?', (kind, entity_id)).fetchone()[0]
    if expected is not None and version != expected:
        raise Conflict('다른 창에서 기록이 변경됐습니다. 새로고침 후 다시 저장하세요.')
    db.execute('INSERT INTO records VALUES (?,?,?,?,?,?)',
               (kind, entity_id, version + 1, now(), int(payload.get('demo', False)), json.dumps(payload, ensure_ascii=False)))
    return version + 1


class Conflict(Exception):
    pass


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS records (
          kind TEXT NOT NULL, entity_id TEXT NOT NULL, version INTEGER NOT NULL,
          recorded_at TEXT NOT NULL, demo INTEGER NOT NULL CHECK(demo IN (0,1)), payload TEXT NOT NULL,
          PRIMARY KEY(kind,entity_id,version));
        CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON records
          BEGIN SELECT RAISE(ABORT,'append only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON records
          BEGIN SELECT RAISE(ABORT,'append only'); END;
        ''')
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM records WHERE kind='policy'").fetchone():
            for pid, country, title, sector, stage in [
                ('p-ai', '미국', '공공 AI 연산 인프라 확충', 'AI 인프라', 2),
                ('p-memory', '한국', '차세대 메모리 생산 기반 지원', '반도체', 1),
                ('p-grid', '미국', '데이터센터 전력망 보강', '전력', 0),
            ]:
                append(db, 'policy', pid, {'demo': True, 'country': country, 'title': title, 'sector': sector, 'stage': stage,
                    'scope': '2026–2028 · 가상 계획', 'amount': '금액 미설정',
                    'events': [
                        {'date': '2026-08-10', 'published_at': '2026-08-11', 'label': '계획 발표', 'detail': '시연용 계획을 등록했습니다.'},
                        *([{'date': '2026-09-15', 'published_at': '2026-09-16', 'label': '예산 확정', 'detail': '가상 예산 확정 사건입니다. 실제 집행액은 없습니다.'}] if stage >= 1 else []),
                        *([{'date': '2026-10-12', 'published_at': '2026-10-13', 'label': '지원 공고', 'detail': '가상 지원 공고입니다. 공급사 계약은 미확인입니다.'}] if stage >= 2 else [])]})
            for version, signal, answer, reason in [
                (1, 'insufficient', '가상 계획 발표만 있습니다. 예산과 구매 계약은 확인되지 않았습니다.', '시연 사례 최초 등록'),
                (2, 'mixed', '시연 사례에서 예산과 지원 공고가 추가됐습니다. 실제 집행과 공급사 계약은 아직 확인되지 않았습니다.', '가상 예산·공고 자료 추가. 실제 집행 여부는 미확인'),
            ]:
                append(db, 'analysis', 'ai-demand', {'demo': True, 'policy_id': 'p-ai', 'target': 'AI 인프라 수요',
                    'question': '정책이 실제 AI 장비 수요로 이어지고 있는가?', 'answer': answer, 'signal': signal,
                    'opportunity': '가상 지원 사업이 계획에서 공고 단계로 진전됐습니다.',
                    'risk': '발주·납품·매출 기여는 확인되지 않았습니다.',
                    'next_condition': '지원 대상 선정 및 장비 구매 계약 확인', 'reason': reason,
                    'basis_at': '2026-08-11' if version == 1 else '2026-10-13', 'rule_version': 'demo-manual-1',
                    'sources': [{'label': '시연 사례 설명서 · 실제 공시 아님', 'url': '/demo-source',
                                 'published_at': '2026-08-11' if version == 1 else '2026-10-13', 'source_type': 'synthetic'}]})
            append(db, 'portfolio', 'personal', {'demo': False, 'source': '대화에서 입력한 상대 금액 · 실시간 평가 아님',
                'holdings': [{'id': k, 'name': name, 'amount': amount, 'semiconductor': semi} for k, name, amount, semi in ASSETS]})


def rows(db):
    return [{'kind': r['kind'], 'id': r['entity_id'], 'version': r['version'], 'recorded_at': r['recorded_at'],
             **json.loads(r['payload'])} for r in db.execute('SELECT * FROM records ORDER BY recorded_at, version')]


def text_field(data, key, limit=5000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{key}: 비어 있지 않은 {limit}자 이하 텍스트가 필요합니다.')
    return value.strip()


def create_server(path, port=0, usage_mode='personal'):
    init_db(path)
    refresh = RefreshService(path, connect, append)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log personal request data.

        def send(self, status, body, mime='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path).path
            if route in ('/api/state', '/api/export'):
                with connect(path) as db:
                    return self.send(200, {'schema_version': 1, 'usage_mode': usage_mode, 'records': rows(db)})
            if route == '/api/refresh':
                return self.send(200, refresh.status())
            if route == '/api/readiness':
                with connect(path) as db:
                    return self.send(200, readiness(rows(db), usage_mode))
            if route == '/api/workbench':
                with connect(path) as db:
                    return self.send(200, build_workbench(rows(db)))
            if route == '/api/history':
                try:
                    at = parse_qs(urlsplit(self.path).query).get('at', [''])[0]
                    with connect(path) as db:
                        return self.send(200, archive_snapshot(rows(db), at))
                except (ValueError, OverflowError):
                    return self.send(400, {'error': '유효한 조회 날짜가 필요합니다.'})
            if route == '/api/data-sources':
                return self.send(200, {'sources':[{'id':k,**v} for k,v in SOURCES.items()]})
            if route == '/api/collection-sources':
                return self.send(200, {'sources': [{'id':key,**value} for key,value in CATALOG.items()]})
            if route == '/api/health':
                with connect(path) as db:
                    db.execute('SELECT count(*) FROM records').fetchone()
                return self.send(200, {'status': 'ok', 'mode': 'local-prototype'})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8'), '/demo-source': ('source.html', 'text/html; charset=utf-8')}
            if route not in files:
                return self.send(404, {'error': '찾을 수 없습니다.'})
            name, mime = files[route]
            return self.send(200, (ROOT / 'static' / name).read_bytes(), mime)

        def do_POST(self):
            # Loopback-only server and same-origin JSON writes; not a public deployment.
            host = self.headers.get('Host', '')
            if host not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                return self.send(403, {'error': '로컬 요청만 허용합니다.'})
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return self.send(403, {'error': '다른 사이트의 저장 요청은 허용하지 않습니다.'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': 'JSON이 필요합니다.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 100000:
                    raise ValueError('요청 크기가 잘못됐습니다.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('객체가 필요합니다.')
                route = urlsplit(self.path).path
                if route == '/api/refresh':
                    if type(data.get('retry', False)) is not bool:
                        raise ValueError('재시도 여부가 잘못됐습니다.')
                    return self.send(202, refresh.start(retry=data.get('retry', False)))
                if route == '/api/refresh-settings':
                    return self.send(200, refresh.configure(data))
                if route == '/api/collect-filing':
                    return self.send(200, collect_filing(path, data.get('filing_id'), connect, append))
                if route == '/api/collect-data':
                    return self.send(200, run_data_collection(path, data.get('source_id'), connect, append))
                if route == '/api/collect':
                    return self.send(200, run_collection(path, data.get('source_id'), connect, append))
                with connect(path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    if route == '/api/reviews':
                        entity_id, version = write_review(db, data, append, rows(db))
                    elif route == '/api/progress':
                        entity_id, version = write_progress(db, data, append)
                    elif route == '/api/evidence-review':
                        entity_id, version = review_evidence(db, data, append)
                    elif route == '/api/relations':
                        entity_id, version = write_relation(db, data, append, ASSETS)
                    elif route == '/api/policies':
                        entity_id, version = write_policy(db, data, append)
                    elif route == '/api/notes':
                        entity_id = data.get('id') or str(uuid.uuid4())
                        if not isinstance(entity_id, str) or len(entity_id) > 100:
                            raise ValueError('잘못된 기록 ID입니다.')
                        expected = data.get('expected_version', 0)
                        if type(expected) is not int or expected < 0:
                            raise ValueError('잘못된 버전입니다.')
                        signal = data.get('signal')
                        if signal not in SIGNALS:
                            raise ValueError('잘못된 신호입니다.')
                        analysis_version = data.get('analysis_version')
                        if type(analysis_version) is not int or not db.execute("SELECT 1 FROM records WHERE kind='analysis' AND entity_id='ai-demand' AND version=?", (analysis_version,)).fetchone():
                            raise ValueError('연결할 분석 버전이 없습니다.')
                        payload = {k: text_field(data, k) for k in ('question', 'answer', 'next_condition', 'reason')}
                        payload.update({'demo': False, 'signal': signal, 'author': 'user', 'analysis_id': 'ai-demand',
                                        'analysis_version': analysis_version, 'context_demo': True})
                        version = append(db, 'note', entity_id, payload, expected)
                    elif route == '/api/portfolio':
                        amounts = data.get('amounts')
                        if not isinstance(amounts, dict) or set(amounts) != {a[0] for a in ASSETS}:
                            raise ValueError('모든 자산의 상대 금액이 필요합니다.')
                        for value in amounts.values():
                            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e9:
                                raise ValueError('상대 금액은 0 이상의 유한한 숫자여야 합니다.')
                        if sum(amounts.values()) <= 0:
                            raise ValueError('전체 금액은 0보다 커야 합니다.')
                        expected = data.get('expected_version')
                        if type(expected) is not int:
                            raise ValueError('버전이 필요합니다.')
                        entity_id = 'personal'
                        version = append(db, 'portfolio', entity_id, {'demo': False, 'source': '사용자 상대 금액 입력 · 실시간 평가 아님',
                            'holdings': [{'id': k, 'name': name, 'amount': amounts[k], 'semiconductor': semi} for k, name, _, semi in ASSETS]}, expected)
                    else:
                        return self.send(404, {'error': '찾을 수 없습니다.'})
                self.send(201, {'id': entity_id, 'version': version})
            except Conflict as exc:
                self.send(409, {'error': str(exc)})
            except (ValueError, TypeError) as exc:
                self.send(400, {'error': str(exc)})
            except sqlite3.Error:
                self.send(500, {'error': '저장하지 못했습니다. 다시 시도하세요.'})

    class ResearchServer(ThreadingHTTPServer):
        def server_close(self):
            refresh.close()
            super().server_close()

    server = ResearchServer(('127.0.0.1', port), Handler)
    server.refresh = refresh
    refresh.start_scheduler()
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', default=os.environ.get('RESEARCH_DB', str(ROOT / '.data' / 'research.sqlite3')))
    args = parser.parse_args()
    server = create_server(args.db, args.port)
    print(f'Local research prototype on port {server.server_port}', flush=True)
    server.serve_forever()
