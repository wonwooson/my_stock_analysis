"""Validation and append-only workflow for manually sourced policy records."""
import json
import uuid
from datetime import date
from urllib.parse import urlsplit

STAGES = ['계획 발표', '예산 확정', '지원 공고', '계약', '집행', '가동', '지연', '축소', '취소']


def required(data, key, limit=5000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{key}: 1~{limit}자 텍스트가 필요합니다.')
    return value.strip()


def get_current(db, entity_id):
    if not isinstance(entity_id, str) or len(entity_id) > 100:
        raise ValueError('잘못된 정책 ID입니다.')
    row = db.execute("SELECT * FROM records WHERE kind='policy' AND entity_id=? ORDER BY version DESC LIMIT 1", (entity_id,)).fetchone()
    if row is None:
        raise ValueError('정책이 없습니다.')
    return row, json.loads(row['payload'])


def write_policy(db, data, append):
    action = data.get('action', 'save')
    expected = data.get('expected_version', 0)
    if type(expected) is not int or expected < 0:
        raise ValueError('잘못된 버전입니다.')
    entity_id = data.get('id')
    previous = None
    if entity_id is not None:
        _, previous = get_current(db, entity_id)
        if previous.get('demo'):
            raise ValueError('가상 정책은 실제 자료로 수정할 수 없습니다.')
    elif action != 'save':
        raise ValueError('검토할 정책 ID가 필요합니다.')
    else:
        entity_id = str(uuid.uuid4())
    reason = required(data, 'reason')
    if action in ('approve', 'return'):
        if previous['review_status'] != 'draft':
            raise ValueError('검토 대기 기록만 처리할 수 있습니다.')
        payload = {**previous, 'review_status': 'reviewed' if action == 'approve' else 'returned',
                   'reason': reason, 'reviewer': 'local-user', 'reviewed_source_version': expected}
        if action == 'approve' and data.get('source_checked') is not True:
            raise ValueError('원문 대조 확인이 필요합니다.')
    elif action == 'save':
        stage = data.get('stage')
        if type(stage) is not int or not 0 <= stage < len(STAGES):
            raise ValueError('잘못된 정책 단계입니다.')
        fields = {k: required(data, k, 500 if k in ('title','country','sector','publisher') else 5000)
                  for k in ('title','country','sector','publisher','excerpt','question','answer','next_condition')}
        url = required(data, 'source_url', 2048)
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('인증정보 없는 HTTPS 출처 URL이 필요합니다.')
        dates = {}
        for key in ('event_date', 'published_date'):
            value = data.get(key, '')
            if not isinstance(value, str):
                raise ValueError('날짜 형식이 잘못됐습니다.')
            dates[key] = date.fromisoformat(value).isoformat() if value else None
        payload = {**fields, **dates, 'source_url': url, 'demo': False, 'source_mode': 'manual',
                   'source_access': 'not_fetched', 'stage': stage, 'signal': 'insufficient',
                   'review_status': 'draft', 'reason': reason, 'amount': '금액 미등록',
                   'scope': '사용자 출처 입력 · 검토 필요',
                   'events': [{'date': dates['event_date'], 'published_at': dates['published_date'],
                               'label': STAGES[stage], 'detail': fields['excerpt']}],
                   'uncertainty': '수동 입력 자료입니다. 원문 접근과 종목 영향은 자동 검증하지 않았습니다.'}
    else:
        raise ValueError('잘못된 작업입니다.')
    if action == 'save' and data.get('source_document_id') is not None:
        doc_id = required(data, 'source_document_id', 100)
        if not db.execute("SELECT 1 FROM records WHERE kind='document' AND entity_id=?", (doc_id,)).fetchone():
            raise ValueError('참고 수집 자료가 없습니다.')
        payload['source_document_id'] = doc_id
    version = append(db, 'policy', entity_id, payload, expected)
    return entity_id, version
