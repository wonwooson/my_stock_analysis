"""Official API adapters. Bounded discovery, not full historical coverage."""
import hashlib
import json
import re
import ssl
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib import request, error
from collection import NoRedirect, LOCK, clock

SOURCES = {
    'sec-nvda': {'name':'SEC · 엔비디아 최근 공시', 'url':'https://data.sec.gov/submissions/CIK0001045810.json',
                 'scope':'최근 목록 중 10-K·10-Q·8-K 및 정정 최대 10건. 과거 전체 이력과 본문은 미수집.'},
    'usa-micron': {'name':'USAspending · MICRON 지원 검색', 'url':'https://api.usaspending.gov/api/v2/search/spending_by_award/',
                   'scope':'2024-01-01~2026-10-17, 미 상무부·수혜자 이름 MICRON, 보조금 유형, 첫 페이지 최대 10건. 전체 집계 아님.'},
}
AWARD_QUERY = {'filters': {'award_type_codes':['02','03','04','05'], 'recipient_search_text':['MICRON'],
              'agencies':[{'type':'awarding','tier':'toptier','name':'Department of Commerce'}],
              'time_period':[{'start_date':'2024-01-01','end_date':'2026-10-17'}]},
              'fields':['Award ID','Recipient Name','Award Amount','Total Outlays','Awarding Agency',
                        'Description','Start Date','End Date','Last Modified Date','generated_internal_id'],
              'page':1,'limit':10,'sort':'Award Amount','order':'desc','subawards':False}


def string(value, required=True):
    if value is None and not required:return None
    if not isinstance(value,str) or (required and not value.strip()):raise ValueError('문자열 필드 불일치')
    return value


def day(value):
    if value in (None,''):return None
    return date.fromisoformat(string(value)).isoformat()


def modified_day(value):
    if value in (None,''):return None
    value=string(value)
    # The API returns a naive date-time. Retain the raw value; do not assume UTC.
    return datetime.fromisoformat(value).date().isoformat()


def money(value):
    if value is None:return None
    if isinstance(value,bool):raise ValueError('금액 형식 오류')
    try:
        result=Decimal(str(value))
    except InvalidOperation:raise ValueError('금액 형식 오류')
    if not result.is_finite():raise ValueError('금액은 유한해야 합니다.')
    return str(result)


def normalize_sec(data):
    if not isinstance(data,dict) or str(data.get('cik')).lstrip('0')!='1045810':
        raise ValueError('요청한 엔비디아 CIK와 응답이 일치하지 않습니다.')
    name=string(data.get('name'))
    if 'NVDA' not in data.get('tickers',[]):raise ValueError('응답 티커 불일치')
    recent=data.get('filings',{}).get('recent',{})
    fields=['accessionNumber','filingDate','reportDate','acceptanceDateTime','form','primaryDocument']
    if not all(isinstance(recent.get(k),list) for k in fields):raise ValueError('SEC 배열 구조 불일치')
    if len({len(recent[k]) for k in fields})!=1:raise ValueError('SEC 배열 길이 불일치')
    output=[]
    for i,form in enumerate(recent['form']):
        if form not in ('10-K','10-Q','8-K','10-K/A','10-Q/A','8-K/A'):continue
        accession=string(recent['accessionNumber'][i]);document=string(recent['primaryDocument'][i])
        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession) or not re.fullmatch(r'[A-Za-z0-9_.-]+',document):
            raise ValueError('공시 경로 형식 불일치')
        accepted=recent['acceptanceDateTime'][i]
        if accepted:
            parsed=datetime.fromisoformat(string(accepted).replace('Z','+00:00'))
            if parsed.tzinfo is None:raise ValueError('공개 시각에 시간대가 없습니다.')
        output.append({'kind':'filing','key':accession,'company_id':'nvda','company_name':name,'cik':'1045810',
            'accession':accession,'form':form,'is_amendment':form.endswith('/A'),
            'filing_date':day(recent['filingDate'][i]),'report_date':day(recent['reportDate'][i]),
            'published_at':accepted or None,'source_url':f'https://www.sec.gov/Archives/edgar/data/1045810/{accession.replace("-", "")}/{document}',
            'signal':'insufficient','review_status':'unreviewed','body_collected':False})
    output.sort(key=lambda x:(x['filing_date'] or '',x['accession']),reverse=True)
    return output[:10], {'candidate_count':len(output),'selected_count':min(len(output),10),
                        'scope':'최근 목록만 조회. 과거 파일·공시 본문 미수집.'}


def normalize_awards(data):
    if not isinstance(data,dict) or not isinstance(data.get('results'),list):raise ValueError('USAspending 결과 구조 불일치')
    meta=data.get('page_metadata')
    if not isinstance(meta,dict) or type(meta.get('hasNext')) is not bool:raise ValueError('페이지 정보가 없습니다.')
    if len(data['results'])>10:raise ValueError('요청한 결과 한도 초과')
    output=[]
    excluded=0
    for row in data['results']:
        recipient=string(row.get('Recipient Name'))
        if not re.search(r'\bMICRON\b',recipient,re.IGNORECASE):
            excluded+=1
            continue
        key=string(row.get('generated_internal_id'))
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',key):raise ValueError('지원 식별자 형식 불일치')
        output.append({'kind':'award','key':key,'award_id':string(row.get('Award ID')),
            'recipient':string(row.get('Recipient Name')),'agency':string(row.get('Awarding Agency'),False),
            'award_amount_usd':money(row.get('Award Amount')),'total_outlays_usd':money(row.get('Total Outlays')),
            'description':string(row.get('Description'),False),'start_date':day(row.get('Start Date')),
            'end_date':day(row.get('End Date')),'modified_date':modified_day(row.get('Last Modified Date')),
            'modified_at_raw':row.get('Last Modified Date'),
            'source_url':f'https://www.usaspending.gov/award/{key}', 'signal':'insufficient',
            'review_status':'unreviewed','company_match':'unverified','published_at':None})
    return output, {'has_next':meta['hasNext'],'page':1,'selected_count':len(output),
                    'excluded_name_mismatch':excluded, 'scope':SOURCES['usa-micron']['scope'], 'messages':data.get('messages',[])}


def request_data(source_id):
    source=SOURCES[source_id]
    body=json.dumps(AWARD_QUERY).encode() if source_id=='usa-micron' else None
    req=request.Request(source['url'],data=body,headers={'User-Agent':'MarketResearchPrototype/0.1',
        'Accept':'application/json','Content-Type':'application/json','Accept-Encoding':'identity'})
    with request.build_opener(NoRedirect()).open(req,timeout=15) as response:
        raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('응답 크기 제한 초과')
        return json.loads(raw),hashlib.sha256(raw).hexdigest()


def run_data_collection(path, source_id, connect, append):
    if not isinstance(source_id,str) or source_id not in SOURCES:raise ValueError('등록된 데이터 출처만 허용합니다.')
    source=SOURCES[source_id]
    with LOCK:
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT payload FROM records WHERE kind='collection' AND version=1 ORDER BY recorded_at DESC"):
                previous=json.loads(row['payload'])
                if previous['source_id']==source_id:
                    if (datetime.now(timezone.utc)-datetime.fromisoformat(previous['started_at'])).total_seconds()<60:
                        raise ValueError('같은 출처는 60초 후 다시 요청하세요.')
                    break
            run_id=str(uuid.uuid4())
            initial={'demo':False,'source_id':source_id,'publisher':source['name'],'source_url':source['url'],
                     'status':'running','started_at':clock(),'message':'공식 API 요청 시작','http_status':None,
                     'query':AWARD_QUERY if source_id=='usa-micron' else None,'scope':source['scope']}
            append(db,'collection',run_id,initial,0)
    try:
        data,digest=request_data(source_id)
        items,coverage=(normalize_sec(data) if source_id=='sec-nvda' else normalize_awards(data))
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            snapshot_id=source_id+'-'+digest
            if not db.execute("SELECT 1 FROM records WHERE kind='api_snapshot' AND entity_id=?",(snapshot_id,)).fetchone():
                append(db,'api_snapshot',snapshot_id,{'demo':False,'source_id':source_id,'source_url':source['url'],
                       'collected_at':clock(),'sha256':digest,'data':data,'query':initial['query'],'coverage':coverage},0)
            changes=0
            for item in items:
                entity_id=source_id+'-'+item['key'];kind=item.pop('kind')
                prior=db.execute('SELECT payload FROM records WHERE kind=? AND entity_id=? ORDER BY version DESC LIMIT 1',(kind,entity_id)).fetchone()
                normalized={'demo':False,**item}
                old=json.loads(prior['payload']) if prior else None
                if old:
                    old.pop('snapshot_id',None);old.pop('collected_at',None)
                if old!=normalized:
                    append(db,kind,entity_id,{**normalized,'snapshot_id':snapshot_id,'collected_at':clock()})
                    changes+=1
            final={**initial,'status':'received','http_status':200,'finished_at':clock(),
                   'message':f'{len(items)}건 확인, 신규·변경 {changes}건. 검토 전이며 자동 기업·정책 연결 없음.',
                   'snapshot_id':snapshot_id,'coverage':coverage}
            append(db,'collection',run_id,final,1)
        return {'id':run_id,'status':'received','count':len(items)}
    except error.HTTPError as exc:
        status='http_error';message=f'HTTP {exc.code}. 요청 거부 또는 API 오류. 본문을 검토해야 합니다.';code=exc.code
    except error.URLError as exc:
        status='proxy_blocked' if 'Tunnel connection failed: 403' in str(exc.reason) else 'network_error'
        if isinstance(exc.reason,ssl.SSLError):status='tls_error'
        message='공식 API 연결 실패. 프록시·인증서·접근 정책 확인 필요.';code=None
    except (ValueError,TypeError,KeyError,AttributeError):
        status='import_error';message='API 응답 구조·식별자·금액·날짜 검증 실패. 자료를 반영하지 않았습니다.';code=None
    except OSError:
        status='network_error';message='네트워크 연결 실패 또는 시간 초과.';code=None
    with connect(path) as db:
        append(db,'collection',run_id,{**initial,'status':status,'message':message,'http_status':code,'finished_at':clock()},1)
    return {'id':run_id,'status':status}
