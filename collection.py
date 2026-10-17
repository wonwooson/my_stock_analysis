"""Bounded, explicit collection from a fixed catalog of official entry pages."""
import hashlib
import json
import socket
import ssl
import threading
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib import request, error

CATALOG = {
    'usaspending': {'name': 'USAspending', 'url': 'https://www.usaspending.gov/', 'country': '미국'},
    'motir': {'name': '한국 산업부', 'url': 'https://www.motir.go.kr/', 'country': '한국'},
    'sec': {'name': 'SEC EDGAR', 'url': 'https://www.sec.gov/edgar/search/', 'country': '미국'},
}
LIMIT = 512000
LOCK = threading.Lock()


def clock():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A redirect requires catalog review; never follow arbitrary hosts.


class Extractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ignore = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.ignore += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.ignore = max(0, self.ignore - 1)

    def handle_data(self, value):
        if not self.ignore and value.strip():
            self.parts.append(value.strip())


def fetch_page(url):
    opener = request.build_opener(NoRedirect())
    req = request.Request(url, headers={'User-Agent': 'MarketResearchPrototype/0.1', 'Accept': 'text/html,text/plain', 'Accept-Encoding': 'identity'})
    with opener.open(req, timeout=12) as response:
        body = response.read(LIMIT + 1)
        if len(body) > LIMIT:
            return {'status': 'too_large', 'message': '응답 크기 제한 초과. 본문을 저장하지 않았습니다.', 'http_status': response.status}
        mime = response.headers.get_content_type()
        if mime not in ('text/html', 'text/plain'):
            return {'status': 'unsupported', 'message': '지원하지 않는 자료 형식입니다.', 'http_status': response.status}
        encoding = response.headers.get_content_charset() or 'utf-8'
        try:
            text = body.decode(encoding)
        except (UnicodeError, LookupError):
            return {'status': 'decode_error', 'message': '문자 인코딩을 확인하지 못했습니다. 본문을 저장하지 않았습니다.', 'http_status': response.status}
        parser = Extractor()
        if mime == 'text/html':
            parser.feed(text)
            text = '\n'.join(parser.parts)
        if not text.strip():
            return {'status': 'empty', 'message': '읽을 수 있는 본문이 없습니다. 스크립트 렌더링이 필요할 수 있습니다.', 'http_status': response.status}
        return {'status': 'received', 'message': '페이지 텍스트 수신. 개별 정책 자료인지 직접 검토해야 합니다.',
                'http_status': response.status, 'excerpt': text[:2000],
                'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body), 'content_type': mime}


def fetch_result(url):
    try:
        return fetch_page(url)
    except error.HTTPError as exc:
        status = 'redirect' if 300 <= exc.code < 400 else 'http_error'
        return {'status': status, 'http_status': exc.code, 'message': 'HTTP 응답 오류. 사이트 또는 중간 프록시의 응답이며 페이지 존재 여부를 단정하지 않습니다.'}
    except error.URLError as exc:
        if 'Tunnel connection failed: 403' in str(exc.reason):
            return {'status': 'proxy_blocked', 'http_status': None, 'message': '환경 프록시 터널 403. 원격 페이지의 HTTP 상태는 확인하지 못했습니다.'}
        if isinstance(exc.reason, ssl.SSLError):
            return {'status': 'tls_error', 'http_status': None, 'message': 'TLS 인증서 검증 실패. 검증을 우회하지 않았습니다.'}
        return {'status': 'network_error', 'http_status': None, 'message': 'DNS·연결·프록시 오류. 원격 응답을 확인하지 못했습니다.'}
    except (TimeoutError, socket.timeout):
        return {'status': 'timeout', 'http_status': None, 'message': '응답 시간 초과.'}
    except OSError:
        return {'status': 'network_error', 'http_status': None, 'message': '네트워크 요청 실행 실패.'}


def run_collection(path, source_id, connect, append):
    if not isinstance(source_id, str) or source_id not in CATALOG:
        raise ValueError('등록된 공식 출처만 요청할 수 있습니다.')
    source = CATALOG[source_id]
    with LOCK:
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT payload FROM records WHERE kind='collection' AND version=1 ORDER BY recorded_at DESC"):
                prior = json.loads(row['payload'])
                if prior['source_id'] == source_id:
                    if (datetime.now(timezone.utc) - datetime.fromisoformat(prior['started_at'])).total_seconds() < 60:
                        raise ValueError('같은 출처는 60초 후 다시 요청하세요.')
                    break
            entity_id = str(uuid.uuid4())
            initial = {'demo': False, 'source_id': source_id, 'source_url': source['url'], 'publisher': source['name'],
                       'status': 'running', 'started_at': clock(), 'message': '수집 요청 시작', 'http_status': None}
            append(db, 'collection', entity_id, initial, 0)
    result = fetch_result(source['url'])
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        document_id = None
        if result['status'] == 'received':
            document_id = hashlib.sha256((source['url'] + result['sha256']).encode()).hexdigest()
            if not db.execute("SELECT 1 FROM records WHERE kind='document' AND entity_id=?", (document_id,)).fetchone():
                append(db, 'document', document_id, {'demo':False, 'source_id': source_id, 'source_url':source['url'],
                    'publisher':source['name'], 'excerpt':result['excerpt'], 'sha256':result['sha256'],
                    'published_at':None, 'collected_at':clock(), 'review_status':'unreviewed',
                    'document_type':'entry_page', 'rights_status':'not_assessed', 'signal':'insufficient',
                    'notice':'공식 사이트 진입 페이지입니다. 정책 근거로 자동 등록하지 않습니다.'}, 0)
        final = {**initial, **{k:v for k,v in result.items() if k!='excerpt'}, 'finished_at':clock(), 'document_id':document_id}
        version = append(db,'collection',entity_id,final,1)
    return {'id':entity_id,'version':version,'status':final['status']}
