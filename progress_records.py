"""Versioned links between a policy relationship and reviewed filing evidence."""
import json
import uuid
from datetime import date
from policy_records import required

STAGES = {'payment', 'capex', 'operation', 'revenue'}
ROLES = {'supports', 'contradicts', 'uncertain'}


def current(db, kind, entity_id):
    if not isinstance(entity_id, str) or not entity_id or len(entity_id) > 100:
        raise ValueError('참조 ID가 잘못됐습니다.')
    row = db.execute('SELECT * FROM records WHERE kind=? AND entity_id=? ORDER BY version DESC LIMIT 1', (kind, entity_id)).fetchone()
    if row is None:
        raise ValueError('참조 기록이 없습니다.')
    return row['version'], json.loads(row['payload'])


def write_progress(db, data, append):
    expected = data.get('expected_version', 0)
    if type(expected) is not int or expected < 0:
        raise ValueError('기록 버전 오류')
    entity_id = data.get('id')
    previous = current(db, 'progress', entity_id)[1] if entity_id is not None else None
    entity_id = entity_id or str(uuid.uuid4())
    relation_id = required(data, 'relation_id', 100)
    version, relation = current(db, 'relation', relation_id)
    if relation.get('demo') or relation['status'] != 'active':
        raise ValueError('활성 실제 관계만 추적할 수 있습니다.')
    if type(data.get('relation_version')) is not int or data['relation_version'] != version:
        raise ValueError('관계가 변경됐습니다. 새로고침 후 재검토하세요.')
    policy_version, policy = current(db, 'policy', relation['policy_id'])
    if policy_version != relation['policy_version'] or policy.get('review_status') != 'reviewed':
        raise ValueError('최신 정책에 맞게 관계를 먼저 재검토하세요.')
    evidence_id = required(data, 'evidence_id', 100)
    _, evidence = current(db, 'evidence', evidence_id)
    review_version, review = current(db, 'evidence_review', evidence_id)
    if evidence.get('demo') or evidence['company_id'] != relation['company_id']:
        raise ValueError('해당 기업의 실제 공시 근거가 필요합니다.')
    if review['status'] != 'accepted' or type(data.get('review_version')) is not int or data['review_version'] != review_version:
        raise ValueError('최신 채택 검토를 확인하세요.')
    if previous and previous['relation_id'] != relation_id:
        raise ValueError('다른 관계는 새 기록으로 등록하세요.')
    stage, role = data.get('stage'), data.get('evidence_role')
    if stage not in STAGES or role not in ROLES:
        raise ValueError('추적 단계 또는 근거 방향 오류')
    if data.get('link_checked') is not True:
        raise ValueError('해당 정책·기업·단계와 원문 문맥의 연결을 대조하세요.')
    observed_date = date.fromisoformat(required(data, 'observed_date', 10)).isoformat()
    payload = {k: required(data, k) for k in ('question', 'answer', 'next_condition', 'reason')}
    payload.update(demo=False, relation_id=relation_id, relation_version=version,
                   policy_id=relation['policy_id'], policy_version=policy_version,
                   company_id=relation['company_id'], evidence_id=evidence_id,
                   review_version=review_version, body_id=evidence['body_id'],
                   source_url=evidence['locator_url'], quote=evidence['quote'],
                   published_at=evidence['published_at'], observed_date=observed_date,
                   stage=stage, evidence_role=role, link_checked=True,
                   signal='insufficient', reviewer='local-user')
    return entity_id, append(db, 'progress', entity_id, payload, expected)
