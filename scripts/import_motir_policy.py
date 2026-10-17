"""Import one inspected official announcement as an unreviewed policy draft.
No automatic approval, company relationship, or investment signal is created.
"""
import hashlib
import json
import sys
import uuid
from pathlib import Path
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collection import Extractor, NoRedirect, LIMIT, clock
from server import ROOT, init_db, connect, append
from policy_records import write_policy

URL = 'https://www.motir.go.kr/kor/article/ATCL3f49a5a8c/172296/view'
TITLE = '중견기업 성장을 위한 월드클래스 10大 날개 프로젝트 시작한다'


def extract(raw):
    parser = Extractor()
    parser.feed(raw.decode('utf-8'))
    text = '\n'.join(parser.parts)
    if TITLE not in text or '2026-10-15' not in text:
        raise ValueError('예상한 공식 제목·등록일과 일치하지 않습니다.')
    start = text.index('\n중견기업 성장을 위한\n')
    end = text.index('\n목록\n', start)
    body = ' '.join(text[start:end].split())
    if '1,000' not in body or '선행연구' not in body:
        raise ValueError('본문 구조가 변경됐습니다. 수동 확인이 필요합니다.')
    return body[:2000]


def run(path):
    init_db(path)
    initial={'demo':False,'source_id':'motir-worldclass-20261015','source_url':URL,'publisher':'산업통상부',
             'status':'running','started_at':clock(),'http_status':None,'message':'상세 원문 확인 시작'}
    run_id=str(uuid.uuid4())
    with connect(path) as db:
        append(db,'collection',run_id,initial,0)
    try:
        with build_opener(NoRedirect()).open(Request(URL,headers={'User-Agent':'MarketResearchPrototype/0.1'}),timeout=12) as response:
            raw=response.read(LIMIT+1)
            if len(raw)>LIMIT:raise ValueError('본문 크기 제한 초과')
            excerpt=extract(raw)
        digest=hashlib.sha256(raw).hexdigest()
        doc_id=hashlib.sha256((URL+digest).encode()).hexdigest()
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM records WHERE kind='document' AND entity_id=?",(doc_id,)).fetchone():
                append(db,'document',doc_id,{'demo':False,'source_id':initial['source_id'],'source_url':URL,
                    'publisher':'산업통상부','excerpt':excerpt,'sha256':digest,'published_at':'2026-10-15',
                    'collected_at':clock(),'review_status':'unreviewed','document_type':'announcement',
                    'rights_status':'not_assessed','signal':'insufficient','notice':'공식 보도자료 본문 · 검토 대기'},0)
            existing=[r for r in db.execute("SELECT payload FROM records WHERE kind='policy'")
                      if json.loads(r['payload']).get('source_url')==URL]
            if not existing:
                write_policy(db,{'title':TITLE,'country':'한국','sector':'중견기업·산업기술','publisher':'산업통상부',
                    'source_url':URL,'source_document_id':doc_id,'excerpt':excerpt,'event_date':'2026-10-15',
                    'published_date':'2026-10-15','stage':0,'reason':'공식 보도자료 원문 확인 후 검토 대기로 등록',
                    'question':'월드클래스 10대 날개 프로젝트가 실제 투자와 수주로 이어지고 있는가?',
                    'answer':'2026년 10월 15일 과제 기획·발굴 원탁회의가 출범했습니다. 과제당 최대 5년·1,000억 원 지원 방침을 제시했습니다. 2026년 말 후보 과제 발굴, 2027년 선행연구, 2028년 선도과제, 나머지 과제는 예산 당국 협의 후 2029년 지원 계획입니다. 이 발표만으로 전체 예산 확정·실제 집행액·개별 수혜 기업을 확인할 수 없습니다. 10개 과제에 최대액을 곱해 확정 예산으로 합산하지 않습니다.',
                    'next_condition':'2026년 말 후보 과제 선정, 2027년 선행연구 공고·예산 확정, 실제 계약·지급 자료 확인'},append)
            append(db,'collection',run_id,{**initial,'status':'received','finished_at':clock(),'http_status':200,
                   'document_id':doc_id,'message':'제목·등록일·본문을 확인해 정책 검토 대기로 연결. 자동 승인하지 않음.'},1)
        return doc_id
    except Exception:
        with connect(path) as db:
            append(db,'collection',run_id,{**initial,'status':'import_error','finished_at':clock(),
                   'message':'상세 원문 수신 또는 검증 실패. 로그와 원문을 재확인하세요.'},1)
        raise


if __name__=='__main__':
    print('Imported document:',run(ROOT/'.data/research.sqlite3'))
