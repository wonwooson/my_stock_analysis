# 개인 PC 개발 환경

기준일: 2026-10-17, 한국시간.

## 설치 구성

| 도구 | 구성 |
|---|---|
| Python 3.13.16 | `.tools/python`, 프로젝트 가상환경 `.venv` |
| Playwright 1.64.0 | `requirements-dev.txt`에 의존성 버전 고정 |
| Chromium | `.tools/ms-playwright`에 프로젝트 전용 설치 |
| Node.js v25.7.0 | 기존 설치 사용, JavaScript 구문 검사 |
| Git 2.50.0 | 기존 설치 사용 |
| VS Code | 기존 Python·Pylance·Debugger 확장 사용 |

시스템 PATH와 pyenv 전역 버전은 변경하지 않았습니다.
`python` 명령 대신 아래 가상환경 경로를 사용하세요.
앱은 Python 표준 라이브러리로 실행합니다. Playwright는 화면 검증에 사용합니다.

## 실행과 디버깅

1. VS Code에서 `C:\Market_Analysis` 폴더를 여세요.
2. 명령 팔레트에서 `Python: Select Interpreter`를 실행하세요.
3. `.venv\Scripts\python.exe`를 선택하세요.
4. F5에서 `Research: practice (backup before start)`를 실행하세요.
5. Shift+F5로 서버를 종료하세요.

F5는 기존 연습 DB를 백업한 뒤 시작·검증 안내 화면을 엽니다.
실제 검증 순서는 [아침 검증 안내](MORNING_CHECK.md)를 확인하세요.
브라우저에서 수정한 연습 기록은 다음 실행에도 남습니다.
Python 코드를 수정하면 서버를 재시작하세요.

```powershell
.\.venv\Scripts\python.exe launch.py --practice
```

## 검증

`Tasks: Run Test Task`에서 `Verify: all tests and JavaScript`를 실행하세요.
개별 테스트는 왼쪽 Testing 화면에서 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts/verify.py
```

전체 검증은 API·저장·백업·브라우저 테스트와 JavaScript 구문 검사를 실행합니다.
브라우저 테스트가 생략되면 전체 검증은 실패로 처리합니다.
테스트는 임시 DB를 사용하며 개인·연습 DB를 수정하지 않습니다.
외부 API의 최신 응답과 데이터 이용 권한은 이 검증 범위에 포함하지 않습니다.

## 환경 복구

같은 PC에서는 다음 명령으로 개발 의존성과 브라우저를 다시 설치할 수 있습니다.
인터넷 연결이 필요합니다. 개인·연습 DB는 변경하지 않습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-dev.ps1
```

새 PC에서는 Node.js와 pip를 포함한 Python 3.11 이상을 먼저 준비하세요.
기존 Python 경로를 명시할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-dev.ps1 -BootstrapPython "C:\path\to\python.exe"
```

프로젝트 위치를 옮기면 가상환경을 새 위치에서 다시 만드세요.
가상환경 폴더를 다른 위치로 복사해서 재사용하지 마세요.
별도 Chromium을 사용하려면 `PLAYWRIGHT_CHROMIUM_EXECUTABLE`에 실행 파일 경로를 지정하세요.
기본값은 Playwright가 설치한 Chromium입니다.

## 데이터와 Git

| 경로 | 용도 |
|---|---|
| `.data/research.sqlite3` | 개인 기록 |
| `.data/practice.sqlite3` | 연습 기록 |
| `.backups/` | 실행 전 백업 |
| `.venv/`, `.tools/` | 재설치 가능한 개발 도구 |
| `.vscode/` | 공유 가능한 실행·검증 설정 |

`.gitignore`는 DB, 백업, 환경 파일, 개발 도구와 ZIP을 제외합니다.
GitHub 저장소: https://github.com/wonwooson/my_stock_analysis
기본 브랜치는 `main`입니다. 다운로드한 소스와 PC 개발 환경을 첫 기준 커밋으로 관리합니다.
과거 클라우드 작업의 커밋 이력은 포함하지 않습니다.
VS Code의 Source Control에서 변경 사항을 검토하고 커밋·동기화하세요.
Git 실행 계정 차이를 처리하기 위해 이 프로젝트 경로만 사용자 Git 신뢰 경로로 등록했습니다.

## Windows 호환성 수정

SQLite 트랜잭션 종료 후 연결을 명시적으로 닫습니다.
백업 연결도 사용 후 닫아 Windows 파일 잠금을 해제합니다.
브라우저 테스트에서 Linux 전용 실행 파일 경로를 제거했습니다.
한글 경로, 실행기, DB 분리와 백업 검증을 기존 테스트에 포함합니다.

브라우저 설치 방법은 [Playwright 공식 안내](https://playwright.dev/python/docs/browsers)를 따릅니다.
제품 기준과 분석 신호의 의미는 변경하지 않았습니다.
