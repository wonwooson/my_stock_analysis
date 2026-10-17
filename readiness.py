"""Read-only readiness: data coverage is separate from investment signals."""
from datetime import datetime, timezone
from review_workbench import latest_map


def readiness(records, usage_mode):
    current = [r for r in latest_map(records).values() if not r.get('demo')]
    kinds = lambda kind: [r for r in current if r['kind'] == kind]
    reviews = {r['id']: r for r in kinds('evidence_review')}
    policies = kinds('policy')
    evidence = kinds('evidence')
    counts = {
        'policies': len(policies),
        'policies_reviewed': sum(r.get('review_status') == 'reviewed' for r in policies),
        'filings': len(kinds('filing')),
        'evidence': len(evidence),
        'evidence_pending': sum(r['id'] not in reviews for r in evidence),
        'evidence_accepted': sum(reviews.get(r['id'], {}).get('status') == 'accepted' for r in evidence),
        'relations': sum(r.get('status') == 'active' for r in kinds('relation')),
        'progress': len(kinds('progress')),
        'reviews': len(kinds('review')),
    }
    sources = {}
    for r in sorted(kinds('collection'), key=lambda r: r.get('started_at', '')):
        key = r['source_id']
        item = sources.setdefault(key, {'source_id': key, 'last_success_at': None})
        item.update({'name': r.get('publisher', key), 'status': r.get('status'),
                     'last_attempt_at': r.get('started_at'), 'message': r.get('message', '')})
        if r.get('status') == 'received':
            item['last_success_at'] = r.get('finished_at', r['recorded_at'])
    for item in sources.values():
        last = item['last_success_at']
        item['check_due'] = item['status'] != 'received' or last is None or (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() >= 86400
    return {'usage_mode': usage_mode, 'counts': counts, 'sources': list(sources.values()),
            'notice': '24시간은 수집 확인 주기입니다. 원문 만료나 종목 신호 기준이 아닙니다.'}
