"""Evidence-backed policy/company links. Links never set stock signals."""
import json
import uuid
from datetime import date
from urllib.parse import urlsplit
from policy_records import get_current, required

KINDS = {'potential', 'confirmed', 'indirect', 'adverse'}


def write_relation(db, data, append, assets):
    expected = data.get('expected_version', 0)
    if type(expected) is not int or expected < 0:
        raise ValueError('잘못된 관계 버전입니다.')
    entity_id = data.get('id')
    previous = None
    if entity_id is not None:
        if not isinstance(entity_id, str) or len(entity_id) > 100:
            raise ValueError('잘못된 관계 ID입니다.')
        row = db.execute("SELECT payload FROM records WHERE kind='relation' AND entity_id=? ORDER BY version DESC LIMIT 1", (entity_id,)).fetchone()
        if row is None:
            raise ValueError('관계가 없습니다.')
        previous = json.loads(row['payload'])
    else:
        entity_id = str(uuid.uuid4())
    reason = required(data, 'reason')
    if data.get('action') == 'withdraw':
        if not previous or previous['status'] != 'active':
            raise ValueError('활성 관계만 철회할 수 있습니다.')
        payload = {**previous, 'status': 'withdrawn', 'reason': reason}
    elif data.get('action', 'save') == 'save':
        policy_id = required(data, 'policy_id', 100)
        row, policy = get_current(db, policy_id)
        if policy.get('demo') or policy.get('review_status') != 'reviewed':
            raise ValueError('사용자 검토 완료 정책만 연결할 수 있습니다.')
        if type(data.get('policy_version')) is not int or data['policy_version'] != row['version']:
            raise ValueError('정책이 변경됐습니다. 최신 자료를 확인하세요.')
        company_id = required(data, 'company_id', 100)
        if company_id not in {a[0] for a in assets} - {'cash','spcx'}:
            raise ValueError('연결 가능한 기업을 선택하세요. 식별 미확인 종목은 제외합니다.')
        if previous and (previous['policy_id'] != policy_id or previous['company_id'] != company_id):
            raise ValueError('기존 관계의 대상을 바꿀 수 없습니다. 새 관계로 등록하세요.')
        kind = data.get('relation_type')
        if kind not in KINDS:
            raise ValueError('잘못된 관계 유형입니다.')
        if kind == 'confirmed' and data.get('contract_checked') is not True:
            raise ValueError('원문에서 기업명과 해당 계약을 대조해야 합니다.')
        url = required(data, 'source_url', 2048)
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('인증정보 없는 HTTPS 출처가 필요합니다.')
        published = data.get('published_date','')
        if not isinstance(published,str):
            raise ValueError('잘못된 날짜입니다.')
        published = date.fromisoformat(published).isoformat() if published else None
        # Do not create parallel active links for the same pair.
        if previous is None or previous['status'] == 'withdrawn':
            for candidate in db.execute("SELECT r.* FROM records r WHERE kind='relation' AND version=(SELECT max(version) FROM records WHERE kind=r.kind AND entity_id=r.entity_id)"):
                old = json.loads(candidate['payload'])
                if candidate['entity_id'] != entity_id and old['status'] == 'active' and old['policy_id'] == policy_id and old['company_id'] == company_id:
                    raise ValueError('이미 연결된 기업입니다. 기존 관계를 새 버전으로 수정하세요.')
        payload = {k:required(data,k) for k in ('question','answer','excerpt','next_condition')}
        payload.update({'demo':False,'policy_id':policy_id,'policy_version':row['version'],
                        'company_id':company_id,'relation_type':kind,'source_url':url,
                        'published_date':published,'reason':reason,'status':'active',
                        'contract_checked':kind=='confirmed','reviewer':'local-user',
                        'signal':'insufficient','source_access':'not_fetched'})
    else:
        raise ValueError('잘못된 작업입니다.')
    return entity_id, append(db,'relation',entity_id,payload,expected)
