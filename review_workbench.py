"""Derived review queue and immutable personal reviews; no market signals."""
import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from policy_records import required

TARGETS = {'policy', 'evidence', 'relation', 'progress'}
HISTORY_KINDS = TARGETS | {'filing', 'evidence_review', 'review'}
KST = timezone(timedelta(hours=9), name='Asia/Seoul')


def latest_map(records):
    result = {}
    for r in records:
        key = (r['kind'], r['id'])
        if key not in result or r['version'] > result[key]['version']:
            result[key] = r
    return result


def dependencies(r):
    kind = r['kind']
    if kind == 'relation':
        return [('policy', r['policy_id'], r['policy_version'])]
    if kind == 'progress':
        return [('policy', r['policy_id'], r['policy_version']), ('relation', r['relation_id'], r['relation_version']),
                ('evidence', r['evidence_id'], 1), ('evidence_review', r['evidence_id'], r['review_version'])]
    if kind == 'evidence':
        return [('filing', r['filing_id'], r['filing_version'])]
    if kind == 'evidence_review':
        return [('evidence', r['evidence_id'], 1)]
    if kind == 'review':
        return [(r['target_kind'], r['target_id'], r['target_version'])]
    return []


def context(r, current):
    """Latest dependency versions, recursively, for change detection."""
    found = {}
    def walk(item):
        key = (item['kind'], item['id'])
        if key in found:
            return
        found[key] = item['version']
        for kind, entity_id, _ in dependencies(item):
            other = current.get((kind, entity_id))
            if other:
                walk(other)
        if item['kind'] == 'evidence':
            review = current.get(('evidence_review', item['id']))
            if review:
                walk(review)
    walk(r)
    return [{'kind': k, 'id': i, 'version': v} for (k, i), v in sorted(found.items())]


def fingerprint(r, current):
    return hashlib.sha256(json.dumps(context(r, current), sort_keys=True).encode()).hexdigest()


def stale(r, current):
    for kind, entity_id, version in dependencies(r):
        item = current.get((kind, entity_id))
        if not item or item['version'] != version:
            return True
        if kind == 'policy' and item.get('review_status') != 'reviewed':
            return True
        if kind == 'relation' and (item.get('status') != 'active' or stale(item, current)):
            return True
        if kind == 'evidence_review' and item.get('status') != 'accepted':
            return True
    return False


def route(r):
    return {'policy': '#sources', 'relation': '#relations', 'progress': '#relations',
            'evidence': '#evidence/' + r.get('filing_id', '')}.get(r['kind'], '#review')


def target_title(r, current):
    if r['kind'] == 'evidence':
        return f"{r.get('company_id', '')} · {r.get('label', '공시 후보')} · {r.get('report_date', '기준일 미상')} · 문단 {r.get('paragraph_index', '?')}"
    if r['kind'] in {'relation', 'progress'}:
        policy = current.get(('policy', r.get('policy_id')), {})
        return f"{policy.get('title', '정책')} → {r.get('company_id', '기업')}"
    return r.get('title') or r.get('question', '근거 확인')


def build_workbench(records, today=None):
    today = today or datetime.now(KST).date()
    current = latest_map(records)
    targets = [r for r in current.values() if r['kind'] in TARGETS and not r.get('demo')]
    reviews = [r for r in current.values() if r['kind'] == 'review']
    queue = []
    for r in targets:
        reasons = []
        if r['kind'] == 'policy' and r.get('review_status') != 'reviewed':
            reasons.append('정책 원문 검토 대기')
        if r['kind'] == 'evidence' and ('evidence_review', r['id']) not in current:
            reasons.append('공시 후보 검토 대기')
        if r['kind'] in {'relation', 'progress'} and r.get('status') != 'withdrawn' and stale(r, current):
            reasons.append('연결 근거 변경 · 재검토 필요')
        linked = [n for n in reviews if n['target_kind'] == r['kind'] and n['target_id'] == r['id']]
        linked.sort(key=lambda n: (n['recorded_at'], n['id']), reverse=True)
        note = linked[0] if linked else None
        signature = fingerprint(r, current)
        if note and note['context_fingerprint'] != signature:
            reasons.append('주말 기록 이후 근거 변경')
        if note and note['due_date'] <= today.isoformat():
            reasons.append('재확인 예정일 도래')
        if reasons:
            queue.append({'target_kind': r['kind'], 'target_id': r['id'], 'target_version': r['version'],
                          'display_title': target_title(r, current),
                          'question': r.get('question') or r.get('title', '근거 확인'),
                          'answer': r.get('answer', '원문 검토가 필요합니다.'), 'signal': r.get('signal', 'insufficient'),
                          'next_condition': r.get('next_condition', '원문과 연결 근거 확인'),
                          'reasons': reasons, 'route': route(r), 'recorded_at': r['recorded_at'],
                          'due_date': note['due_date'] if note else None,
                          'context_fingerprint': signature})
    queue.sort(key=lambda q: (0 if any('변경' in s for s in q['reasons']) else 1,
                              q['due_date'] or '9999-12-31', q['recorded_at'], q['target_id']))
    latest_runs = {}
    for run in current.values():
        if run['kind'] != 'collection' or run.get('demo'):
            continue
        source = run['source_id']
        if source not in latest_runs or run['started_at'] > latest_runs[source]['started_at']:
            latest_runs[source] = run
    problems = [r for r in latest_runs.values() if r['status'] != 'received']
    return {'today': today.isoformat(), 'queue': queue, 'collection_problems': problems,
            'targets': [dict(r, display_title=target_title(r, current), context_fingerprint=fingerprint(r, current)) for r in targets],
            'reviews': sorted(reviews, key=lambda r: r['recorded_at'], reverse=True)}


def write_review(db, data, append, records):
    kind = required(data, 'target_kind', 30)
    target_id = required(data, 'target_id', 150)
    current = latest_map(records)
    target = current.get((kind, target_id))
    if kind not in TARGETS or not target or target.get('demo'):
        raise ValueError('실제 정책·공시·관계·추적 기록만 선택하세요.')
    if type(data.get('target_version')) is not int or target['version'] != data['target_version']:
        raise ValueError('대상 기록이 변경됐습니다. 새로고침하세요.')
    signature = fingerprint(target, current)
    if data.get('context_fingerprint') != signature:
        raise ValueError('연결 근거가 변경됐습니다. 새로고침 후 검토하세요.')
    expected = data.get('expected_version', 0)
    if type(expected) is not int or expected < 0:
        raise ValueError('잘못된 검토 버전')
    entity_id = data.get('id')
    if entity_id is not None:
        if not isinstance(entity_id, str) or len(entity_id) > 100:
            raise ValueError('잘못된 검토 ID')
        previous = current.get(('review', entity_id))
        if not previous or (previous['target_kind'], previous['target_id']) != (kind, target_id):
            raise ValueError('기존 검토의 대상을 변경할 수 없습니다.')
    else:
        if any(r['kind'] == 'review' and r['target_kind'] == kind and r['target_id'] == target_id for r in current.values()):
            raise ValueError('이미 주말 기록이 있습니다. 기존 기록을 새 버전으로 수정하세요.')
        entity_id = str(uuid.uuid4())
    due = date.fromisoformat(required(data, 'due_date', 10)).isoformat()
    payload = {k: required(data, k) for k in ('question', 'answer', 'next_condition', 'reason')}
    payload.update(demo=False, target_kind=kind, target_id=target_id, target_version=target['version'],
                   context_fingerprint=signature, context_refs=context(target, current),
                   source_question=target.get('question') or target.get('title'),
                   source_answer=target.get('answer'), signal=target.get('signal', 'insufficient'),
                   due_date=due, author='local-user', interpretation='personal-review')
    return entity_id, append(db, 'review', entity_id, payload, expected)


def archive_snapshot(records, at):
    """Strict app-observed history, never a reconstruction of the entire market."""
    day = date.fromisoformat(at)
    cutoff = datetime.combine(day + timedelta(days=1), time.min, KST).astimezone(timezone.utc)
    visible = []
    excluded = 0
    for r in records:
        if r['kind'] not in HISTORY_KINDS or r.get('demo'):
            continue
        if datetime.fromisoformat(r['recorded_at']) >= cutoff:
            excluded += 1
            continue
        published = r.get('published_at') or r.get('published_date')
        if published:
            # Date-only sources are interpreted in the displayed Korea date.
            instant = datetime.fromisoformat(published)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=KST)
            if instant.astimezone(timezone.utc) >= cutoff:
                excluded += 1
                continue
        visible.append(r)
    # Remove records whose exact historical dependencies are unavailable.
    while True:
        keys = {(r['kind'], r['id'], r['version']) for r in visible}
        keep = []
        for r in visible:
            refs = dependencies(r)
            if r['kind'] == 'review':
                refs += [(x['kind'], x['id'], x['version']) for x in r['context_refs']]
            if all(ref in keys for ref in refs):
                keep.append(r)
        if len(keep) == len(visible):
            break
        excluded += len(visible) - len(keep)
        visible = keep
    return {'at': day.isoformat(), 'timezone': 'Asia/Seoul', 'mode': 'app-observed',
            'excluded_versions': excluded, 'records': visible,
            'current': list(latest_map(visible).values())}
