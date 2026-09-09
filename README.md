# Claim Sync

Claim 접수 이력을 날짜별로 수집하고 제품 정보를 보강해 FAR API로 전송하는 독립 프로젝트입니다. **웹 검증 콘솔과 Ubuntu 백그라운드 실행기가 같은 동기화 엔진을 사용합니다.** Python 3.11 이상을 사용하며 프런트엔드는 별도 Node 빌드나 외부 CDN 없이 동작합니다.

기본값은 **가상 API + 전송 없는 검증**입니다. `APP_MODE=mock`에서는 모든 API 호출이 프로세스 안의 가상 서버로 전달되므로 사내망이 없어도 전체 흐름을 검증할 수 있습니다. 실제 운영 DB에 접속하거나 데이터를 전송하지 않습니다.

## 빠른 시작

### Ubuntu / Linux

```bash
chmod +x run.sh
./run.sh
```

### Windows

프로젝트 폴더의 **`run.bat`를 더블클릭**하거나 PowerShell에서 실행합니다.

```powershell
.\run.bat
```

실행 파일은 `.venv` 생성 → 필요한 패키지 설치 → 프로젝트 설치 → `.env` 초기 생성 → 웹 실행을 자동으로 처리합니다. 기존 `.env`와 데이터는 보존하며, 이미 설치한 패키지는 버전을 확인한 뒤 재사용합니다. 패키지가 제거되었거나 lock 파일의 버전과 다르면 해당 패키지를 다시 설치합니다.

Python 3.11 이상이 없으면 Windows는 **winget으로 Python 3.12 설치**, Ubuntu/Debian은 **apt로 Python과 venv 설치**를 시도합니다. 시스템 설치 시 관리자 권한이나 sudo 암호가 필요할 수 있습니다. 패키지 저장소에서 Python 3.11 이상을 제공하지 않는 배포판이나 winget이 없는 Windows는 Python을 먼저 설치해야 합니다. Python 패키지는 운영체제 전역이 아닌 프로젝트의 `.venv`에 설치합니다.

| 용도 | Windows | Linux |
| --- | --- | --- |
| 웹 화면 실행 | `run.bat` | `./run.sh` |
| 설치·환경 준비만 수행 | `run.bat --setup-only` | `./run.sh --setup-only` |
| 백그라운드 실행기 | `run.bat worker` | `./run.sh worker` |
| 최근 두 달 검증 | `run.bat run --months 2 --chunk-days 7` | `./run.sh run --months 2 --chunk-days 7` |
| 별도 환경 파일 | `run.bat --env-file "settings/test.env" web` | `./run.sh --env-file "settings/test.env" web` |

`worker`는 터미널에서 계속 실행됩니다. Ubuntu 로그인 종료·재부팅 후에도 유지하려면 [systemd 운영 가이드](docs/operations.md)를 사용하세요. 다른 폴더에서 실행해도 프로젝트 폴더를 기준으로 동작하며, `--env-file` 등 상대 경로 인자도 프로젝트 기준입니다.

초기 설치에는 패키지 다운로드가 필요합니다. 폐쇄망 설치, Python 경로 지정과 설치 오류 조치는 [자동 실행 파일 설정](docs/operations.md#자동-실행-파일-설정)을 참고하세요.

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000)을 엽니다.

1. 최근 N개월 또는 시작일·종료일을 선택합니다.
2. 한 번에 조회할 일 수를 정합니다. 예: 7일.
3. **전송 없이 검증**으로 실행하고 데이터 미리보기에서 21개 전송 필드를 확인합니다.
4. **API 전송**으로 실행하면 가상 대상 DB에 저장됩니다. 같은 데이터로 다시 실행하면 `변경 없음`으로 표시됩니다.
5. 자동 갱신 화면에서 간격을 정하고 사용을 켠 후 저장합니다. 대시보드의 현재 기간·조회 단위·실행 방식이 함께 저장됩니다.

초기 가상 데이터는 하루 6건, 제품 4종입니다. 가상 대상 DB 건수는 상단에 표시됩니다. `.env` 변경은 프로세스를 재시작해야 반영됩니다. 환경변수는 `.env`보다 우선합니다.

## 제공 기능

- 최근 1·2·3·6·12개월(엔진/API는 1–120개월), 고정 날짜 범위, 1–366일 단위 분할 조회.
- API 결과가 `limit` 이상이거나 전체 건수/잘림 표시/응답 크기 한도를 감지하면 구간을 재분할.
- 하루에도 한도에 도달하면 해당 날짜를 **전송하지 않고 실패로 기록**. 페이지네이션 없는 API에서 임의로 완전 수집됐다고 판단하지 않습니다.
- 제품 schema 검증, Part ID 앞 15자 조회, 한 실행 안에서 최대 4,096개 제품 캐시.
- ISO 날짜 정규화, NAND/DRAM 조합, 원본 Part ID 보존, 레코드별 검증.
- SQLite에 작업·구간·레코드·이벤트·스케줄·성공 전송 상태 저장.
- 동일 업무 키와 동일 전송 값은 건너뛰고 변경된 값은 다시 POST.
- GET 재시도, POST 응답 유실 보류, 운영자 반영 확인 화면, 실행 중지 및 재실행.
- 자동 갱신, 프로세스 중복 실행 방지, 중단 이력 복구, 웹 없는 CLI 실행.
- 실시간 상태/로그(2초 갱신), 실행 이력, 필터·페이지별 레코드, JSON 미리보기, NDJSON 내보내기.
- 선택적 접속 토큰, 동일 출처 검사, 인증 헤더 비노출, TLS 검증.

## API 주소 및 사내망 전환

제공한 사내 API 주소를 [config.py](claim_sync/config.py)의 `DEFAULT_CLAIMS_BASE_URL`, `DEFAULT_PRODUCT_BASE_URL`, `DEFAULT_TARGET_BASE_URL` 상수와 [.env.example](.env.example)에 기본값으로 저장했습니다. Base URL에는 endpoint 경로를 포함하지 않습니다.

| API | 기본 Base URL |
| --- | --- |
| Claim 접수 이력 | `http://12.81.220.37:8080` |
| 제품 schema / 제품 조회 | `http://12.81.221.145:5273` |
| FAR 전송 | `https://estgtask.samsungds.net` |

실행 시 **환경변수 → `.env` → 코드 기본 상수** 순서로 값을 적용합니다. 주소를 바꿀 때는 `.env`의 `CLAIMS_BASE_URL`, `PRODUCT_BASE_URL`, `TARGET_BASE_URL`만 수정하고 재시작하면 됩니다. 기존 `.env`에 가상 주소나 다른 주소가 있으면 그 값이 계속 우선 적용됩니다. 기본값 `APP_MODE=mock`에서는 위 주소를 사용하더라도 모든 요청을 내장 가상 서버에서 처리하며 사내 API로 네트워크 요청을 보내지 않습니다.

```dotenv
APP_MODE=live
CLAIMS_BASE_URL=http://12.81.220.37:8080
PRODUCT_BASE_URL=http://12.81.221.145:5273
TARGET_BASE_URL=https://estgtask.samsungds.net
CLAIMS_HEADERS={"Authorization":"Bearer REPLACE_LOCALLY"}
PRODUCT_HEADERS={"X-API-Key":"REPLACE_LOCALLY"}
TARGET_HEADERS={"Authorization":"Bearer REPLACE_LOCALLY"}
ALLOW_LIVE_WRITES=false
TARGET_UPSERT_CONFIRMED=false
```

처음에는 `live`에서 **전송 없이 검증**을 실행하세요. 다음 계약은 실제 응답이 제공되지 않아 사내에서 확인해야 합니다.

| 확인 항목 | 구현의 기본 가정 |
| --- | --- |
| 시작일 파라미터 | 제공한 URL 그대로 `rcvDataFrom` 사용. `CLAIMS_FROM_PARAM`으로 변경 가능 |
| 날짜 범위 | 시작일과 종료일 모두 포함. `rcvDate`가 요청 범위 밖이면 오류 |
| Claim 응답 | 배열 또는 `data/items/records/results/claims` 래퍼. 사용자 지정 점 경로 지원 |
| 제품 응답 | 단일 객체 또는 `data/record/result/records/items` 래퍼, 1개짜리 배열 |
| 제품 schema | JSON Schema `properties` 또는 `fields/columns` 배열. [응답 계약 문서](docs/api-contracts.md) 참조 |
| 업무 키 | `far_no + sample_no`. `TARGET_KEY_FIELDS`로 설정 가능 |
| 반복 POST 의미 | 서버가 같은 업무 키를 **upsert**해야 변경 건 갱신 가능 |
| 빈 선택 필드 | JSON `null`로 전송. 필수 키·접수일·Part ID는 비어 있으면 실패 |
| 성공 확인 | 오류 없는 2xx 응답(202 제외). 필요 시 `TARGET_SUCCESS_PATH`와 JSON `TARGET_SUCCESS_VALUE` 지정 |

**POST URL만으로 서버의 insert/update 동작을 알 수는 없습니다.** 서버가 insert 전용이면 이 클라이언트만으로 기존 행을 갱신할 수 없습니다. 업무 키에 대한 upsert 또는 별도의 갱신 API 계약을 먼저 확정해야 합니다. 확인 후 `.env`에서 `ALLOW_LIVE_WRITES=true`, `TARGET_UPSERT_CONFIRMED=true`로 설정하면 운영 전송을 사용할 수 있습니다.

전송 대상·모드·업무 키·데이터셋이 바뀌면 이전 대기 작업을 다른 대상으로 보내지 않습니다. 새 작업을 등록하고 스케줄을 다시 저장하세요. 대상 DB를 초기화했거나 tenant가 바뀌면 `TARGET_DATASET_ID`도 바꾸세요.

## 날짜·재실행·스케줄 의미

- `최근 1개월`: 실행일이 2026-09-09이면 **2026-08-09 ~ 2026-09-09**, 양끝 포함. 30일 고정이 아닌 달력 기준이며 월말은 이전 달 마지막 날로 보정합니다.
- 시각 판단은 기본 `Asia/Seoul`, `TIMEZONE`에서 변경 가능합니다. 상대 기간은 예약 시점이 아닌 **실행 시작 시점**에 계산합니다.
- `7일씩`: 1/1–1/7, 1/8–1/14처럼 겹치지 않게 나눕니다. 잘림이 감지된 구간은 절반씩 재분할합니다.
- 스케줄 저장 시 첫 실행은 현재 시각 + 간격입니다. 즉시 실행은 대시보드 버튼을 사용하세요.
- 긴 실행 중 예약 시각이 여러 번 지나가면 쌓아두지 않고 종료 후 한 번 실행합니다. 서버가 꺼져 있던 기간도 한 번으로 합칩니다.
- 재실행은 같은 설정의 새 작업입니다. 상대 기간은 다시 계산되고 이미 성공한 동일 값은 건너뜁니다. 실패·중단된 작업을 임의로 자동 재개하지 않습니다.
- 기록은 전체 작업 기준 원자적이지 않습니다. 일부 구간 실패/취소 전에 완료한 POST는 유지됩니다.
- UI를 닫아도 프로세스가 살아 있으면 계속 실행됩니다. 프로세스까지 종료해도 계속 실행하려면 Ubuntu 서비스를 등록하세요.

## Ubuntu 백그라운드 실행

```bash
# 웹 없이 저장된 스케줄과 대기 작업 처리
claim-sync worker

# 단발 실행: 기본 검증 전용
claim-sync run --months 2 --chunk-days 7
claim-sync run --start 2025-01-01 --end 2025-12-31 --chunk-days 3 --send

# 파일에서 스케줄 저장
claim-sync schedule examples/schedule.json
```

`--spec`을 지정하면 파일의 모든 설정이 다른 기간·전송 플래그보다 우선합니다. 예: `claim-sync run --spec examples/run.json`.

동일한 `DATA_DIR`에서 실행기는 파일 잠금으로 하나만 동작합니다. 웹과 전용 worker를 같이 쓸 때는 웹 서비스에 `ENABLE_RUNNER=false`를 지정합니다. 단발 `run`은 다른 실행기가 동작 중이면 종료 코드 2로 거절하며, 엔진 실행 결과는 완료 0 / 실패·부분실패·중단 1입니다.

Ubuntu 설치·systemd 서비스·Docker·폐쇄망 설치·백업과 장애 대응은 [운영 가이드](docs/operations.md)를 참고하세요. [worker 서비스](deploy/claim-sync-worker.service)와 [웹 서비스](deploy/claim-sync-web.service)를 제공합니다.

## 구조

```text
claim_sync/
  config.py      .env 설정과 운영 전송 조건
  models.py      기간 및 스케줄 설정, 달력 계산
  mapping.py     요청한 21개 필드 변환
  clients.py     API 어댑터, 응답 파싱, 조회 재시도
  engine.py      수집 → 분할 → 제품 보강 → 검증/전송
  store.py       SQLite 이력·스케줄·전송 상태
  runner.py      영속 큐 소비, 주기 실행, 프로세스 잠금
  cli.py         web / worker / run / schedule / mock-server
  web.py         관리용 REST API와 정적 화면
  mock.py        독립적인 가상 원본·제품·대상 API
  static/        외부 의존성 없는 한국어 웹 화면
tests/           날짜, 변환, HTTP 오류, 전송, 스케줄, 웹 API 테스트
deploy/          Ubuntu systemd 서비스
scripts/bootstrap.py  공통 가상환경·의존성 준비 모듈
run.bat / run.sh      Windows / Linux 자동 설치 및 실행
```

## 개발 검증

```bash
pip install -r requirements-dev.lock
pip install --no-deps -e .
pytest -q
ruff check .
ruff format --check .
```

테스트는 임시 DB와 가상 API만 사용합니다. 주요 결과와 검증 범위는 [검증 기록](docs/verification.md)에 정리합니다. FastAPI [lifespan](https://fastapi.tiangolo.com/advanced/events/), HTTPX [timeout](https://www.python-httpx.org/advanced/timeouts/), Python [SQLite](https://docs.python.org/3/library/sqlite3.html) 공식 문서의 실행·연결 관리 방식을 사용합니다.
