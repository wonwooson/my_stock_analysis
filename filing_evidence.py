"""Deterministic narrative extraction. Keyword matches are evidence candidates only."""
import hashlib
import json
import re
import uuid
from datetime import datetime,timezone
from html.parser import HTMLParser
from urllib import request,error
from urllib.parse import quote
from collection import NoRedirect,LOCK,clock
from policy_records import required

TOPICS={
 'capex':('설비투자','설비투자가 실제 현금 지출로 이어지고 있는가?',r'capital expenditures|purchases of property|capital investments','기간·단위·현금흐름표와 대조'),
 'support':('정책 지원','정부 지원이 기업의 사업과 재무에 반영되는가?',r'government grants|government incentives|tax credits|CHIPS Act|government subsidies','지원 제도·자격·실제 수령액 대조'),
 'restriction':('수출 규제','정책 변화가 판매와 공급에 제약을 주는가?',r'export controls?|export restrictions?|export licenses?','규제 범위·시행일·실제 매출 영향 확인'),
 'contract':('계약·구매 약정','계약과 구매 약정은 어느 쪽의 의무인가?',r'purchase obligations|purchase commitments|supply agreements|customer contracts|non-cancelable','고객 수주와 회사 구매 의무를 구분하고 취소 조건 확인'),
}
PARSER_VERSION='narrative-keywords-1'


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.rows=[];self.anchor=None;self.skip=[]
    def flush(self):
        value=re.sub(r'\s+',' ',''.join(self.parts)).strip()
        if value:self.rows.append({'text':value,'anchor':self.anchor})
        self.parts=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if self.skip:
            if tag not in ('br','img','meta','link','input','hr','wbr'):self.skip.append(tag)
            return
        if tag in ('head','script','style','noscript','ix:hidden','table') or 'hidden' in a or 'display:none' in a.get('style','').replace(' ','').lower():
            self.flush();self.skip=[tag];return
        if tag in ('p','div','h1','h2','h3','h4','li','section'):
            self.flush();self.anchor=a.get('id')
        elif tag=='br':self.parts.append(' ')
    def handle_endtag(self,tag):
        if self.skip:
            if tag in self.skip:
                index=len(self.skip)-1-self.skip[::-1].index(tag)
                self.skip=self.skip[:index]
            return
        if tag in ('p','div','h1','h2','h3','h4','li','section'):
            self.flush();self.anchor=None
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)


def extract(raw):
    p=Paragraphs();p.feed(raw.decode('utf-8'));p.flush()
    paragraphs=[{'index':i+1,**r} for i,r in enumerate(p.rows)]
    matches=[];counts={}
    for topic,(label,question,pattern,next_condition) in TOPICS.items():
        selected=[r for r in paragraphs if 60<=len(r['text'])<=5000 and re.search(pattern,r['text'],re.I)]
        counts[topic]=len(selected)
        seen=set()
        for r in selected:
            if r['text'] in seen:continue
            seen.add(r['text'])
            if len(seen)>5:continue
            matches.append({'topic':topic,'label':label,'question':question,'next_condition':next_condition,
                'quote':r['text'],'paragraph_index':r['index'],'anchor':r['anchor'],
                'answer':'관련 표현을 포함한 원문 문단입니다. 사실·계획·위험 요인 구분과 숫자 대조가 필요합니다.',
                'signal':'insufficient','review_status':'unreviewed'})
    return paragraphs,matches,counts


def download(url):
    req=request.Request(url,headers={'User-Agent':'MarketResearchPrototype/0.1','Accept':'text/html','Accept-Encoding':'identity'})
    with request.build_opener(NoRedirect()).open(req,timeout=15) as response:
        if response.headers.get_content_type()!='text/html':raise ValueError('HTML 공시가 아닙니다.')
        raw=response.read(6_000_001)
        if len(raw)>6_000_000:raise ValueError('본문 크기 제한 초과')
        return raw


def collect_filing(path, filing_id, connect, append):
    if not isinstance(filing_id,str) or len(filing_id)>150:raise ValueError('잘못된 공시 ID')
    with LOCK:
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM records WHERE kind='filing' AND entity_id=? ORDER BY version DESC LIMIT 1",(filing_id,)).fetchone()
            if row is None:raise ValueError('수집된 공시가 없습니다.')
            filing=json.loads(row['payload']);filing_version=row['version']
            url=filing['source_url']
            if not re.fullmatch(r'https://www\.sec\.gov/Archives/edgar/data/1045810/\d{18}/[A-Za-z0-9_.-]+',url):
                raise ValueError('검증된 SEC 엔비디아 원문만 허용합니다.')
            source_id='body-'+filing_id
            prior=db.execute("SELECT payload FROM records WHERE kind='collection' AND version=1 ORDER BY recorded_at DESC").fetchall()
            for r in prior:
                old=json.loads(r['payload'])
                if old['source_id']==source_id:
                    if (datetime.now(timezone.utc)-datetime.fromisoformat(old['started_at'])).total_seconds()<60:raise ValueError('같은 공시는 60초 후 다시 요청하세요.')
                    break
            run_id=str(uuid.uuid4())
            initial={'demo':False,'source_id':source_id,'publisher':f"SEC 본문 · {filing['form']}",'source_url':url,
                'status':'running','started_at':clock(),'message':'본문 수집 시작','http_status':None}
            append(db,'collection',run_id,initial,0)
    try:
        raw=download(url)
        paragraphs,matches,counts=extract(raw)
        full=' '.join(r['text'] for r in paragraphs)
        if 'NVIDIA' not in full.upper() or len(full)<1000:raise ValueError('본문 식별·내용 검증 실패')
        digest=hashlib.sha256(raw).hexdigest()
        body_id=filing_id+'-'+digest
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM records WHERE kind='filing_body' AND entity_id=?",(body_id,)).fetchone():
                append(db,'filing_body',body_id,{'demo':False,'filing_id':filing_id,'filing_version':filing_version,
                    'source_url':url,'sha256':digest,'published_at':filing['published_at'],'collected_at':clock(),
                    'parser_version':PARSER_VERSION,'paragraphs':paragraphs,'counts':counts,
                    'coverage':'HTML 서술 문단만 추출. 표·숨김 XBRL 제외. 60~5000자 문단 중 주제별 최대 5개 후보.'},0)
                for candidate in matches:
                    entity_id=hashlib.sha256((body_id+candidate['topic']+str(candidate['paragraph_index'])).encode()).hexdigest()
                    append(db,'evidence',entity_id,{'demo':False,**candidate,'body_id':body_id,'filing_id':filing_id,
                        'filing_version':filing_version,'company_id':filing['company_id'],'source_url':url,
                        'locator_url':url+('#'+quote(candidate['anchor'],safe='') if candidate['anchor'] else ''),
                        'published_at':filing['published_at'],'report_date':filing['report_date'],'collected_at':clock(),
                        'parser_version':PARSER_VERSION},0)
            append(db,'collection',run_id,{**initial,'status':'received','http_status':200,'finished_at':clock(),
                'message':f'서술 문단 {len(paragraphs)}개, 근거 후보 {len(matches)}개. 자동 검토·신호 변경 없음.','body_id':body_id},1)
        return {'id':run_id,'status':'received','count':len(matches),'body_id':body_id}
    except error.HTTPError as exc:
        status='http_error';code=exc.code
    except error.URLError as exc:
        status='proxy_blocked' if 'Tunnel connection failed: 403' in str(exc.reason) else 'network_error';code=None
    except (ValueError,UnicodeError):
        status='import_error';code=None
    except OSError:
        status='network_error';code=None
    with connect(path) as db:
        append(db,'collection',run_id,{**initial,'status':status,'http_status':code,'finished_at':clock(),
            'message':'본문 수신 또는 검증 실패. 근거를 생성하지 않았습니다.'},1)
    return {'id':run_id,'status':status,'http_status':code}


def review_evidence(db,data,append):
    evidence_id=required(data,'evidence_id',100)
    row=db.execute("SELECT payload FROM records WHERE kind='evidence' AND entity_id=?",(evidence_id,)).fetchone()
    if row is None:raise ValueError('근거 후보가 없습니다.')
    status=data.get('status')
    if status not in ('accepted','dismissed'):raise ValueError('검토 상태 오류')
    if status=='accepted' and data.get('source_checked') is not True:raise ValueError('원문 문맥 대조가 필요합니다.')
    expected=data.get('expected_version',0)
    if type(expected) is not int or expected<0:raise ValueError('검토 버전 오류')
    source=json.loads(row['payload'])
    payload={'demo':False,'evidence_id':evidence_id,'body_id':source['body_id'],'status':status,
        'reviewer':'local-user','reason':required(data,'reason'),'answer':required(data,'answer'),
        'next_condition':required(data,'next_condition'),'question':source['question'],
        'signal':'insufficient','source_checked':data.get('source_checked') is True}
    return evidence_id,append(db,'evidence_review',evidence_id,payload,expected)
