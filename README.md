# Claim Sync

Claim 접수 이력을 날짜별로 수집하고 제품 정보를 보강해 FAR API로 전송하는 독립 프로젝트입니다. **웹 검증 콘솔과 Ubuntu 백그라운드 실행기가 같은 동기화 엔진을 사용합니다.** Python 3.11 이상을 사용하며 프런트엔드는 별도 Node 빌드나 외부 CDN 없이 동작합니다.

Git에서 새로 설치하면 **실제 사내 API 조회 모드(`APP_MODE=live`)**로 실행합니다. 제공한 Claim API에서 접수 이력을 읽고 제품 API에서 정보를 보강합니다. 기본 실행 방식인 **전송 없이 검증**도 실제 데이터를 조회하며, 운영 FAR API로 보낼 JSON을 미리 확인할 수 있습니다. 실제 조회에는 사내망 접속이 필요합니다.

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

실행 파일은 `.venv` 생성 → 필요한 패키지 설치 → 프로젝트 설치 → 동작 확인 → `.env` 초기 생성 → 웹 실행을 자동으로 처리합니다. 기본적으로 검증된 고정 버전을 설치하며, 사내 저장소에 해당 버전이 없으면 **현재 프록시·저장소에서 받을 수 있는 호환 버전 조합을 자동으로 찾아 설치**합니다. 설치 결과는 `.venv/claim-sync-resolved.lock`에 기록하며 다음 실행에서 재사용합니다. 기존 `.env`와 데이터는 보존하고, 패키지가 누락되거나 선택한 버전과 다르면 복구를 시도합니다.

사내망에서 처음부터 호환 버전을 조회하려면 PowerShell에서 `$env:CLAIM_SYNC_INSTALL_MODE = 'compatible'`를 지정한 뒤 `.\run.bat`를 실행하세요. 패키지별 버전을 하나씩 임의로 낮추는 대신 pip가 전체 의존성을 함께 비교합니다. 프록시 설정 방법과 설치 모드는 [사내 저장소 호환 설치](docs/operations.md#사내-저장소-호환-설치)를 참고하세요.

Python 3.11 이상이 없으면 Windows는 **winget으로 Python 3.12 설치**, Ubuntu/Debian은 **apt로 Python과 venv 설치**를 시도합니다. 시스템 설치 시 관리자 권한이나 sudo 암호가 필요할 수 있습니다. 패키지 저장소에서 Python 3.11 이상을 제공하지 않는 배포판이나 winget이 없는 Windows는 Python을 먼저 설치해야 합니다. Python 패키지는 운영체제 전역이 아닌 프로젝트의 `.venv`에 설치합니다.

| 용도 | Windows | Linux |
| --- | --- | --- |
| 웹 화면 실행 | `run.bat` | `./run.sh` |
| 설치·환경 준비만 수행 | `run.bat --setup-only` | `./run.sh --setup-only` |
| 최신 코드 내려받기·덮어쓰기 | `update.bat` | `./update.sh` |
| 백그라운드 실행기 | `run.bat worker` | `./run.sh worker` |
| 최근 두 달 검증 | `run.bat run --months 2 --chunk-days 7` | `./run.sh run --months 2 --chunk-days 7` |
| 별도 환경 파일 | `run.bat --env-file "settings/test.env" web` | `./run.sh --env-file "settings/test.env" web` |

`worker`는 터미널에서 계속 실행됩니다. Ubuntu 로그인 종료·재부팅 후에도 유지하려면 [systemd 운영 가이드](docs/operations.md)를 사용하세요. 다른 폴더에서 실행해도 프로젝트 폴더를 기준으로 동작하며, `--env-file` 등 상대 경로 인자도 프로젝트 기준입니다.

초기 설치에는 패키지 다운로드가 필요합니다. 폐쇄망 설치, Python 경로 지정과 설치 오류 조치는 [자동 실행 파일 설정](docs/operations.md#자동-실행-파일-설정)을 참고하세요.

업데이트할 때는 앱/worker를 종료한 뒤 **`update.bat`를 더블클릭**하세요. 업데이트 스크립트는 `origin/main`을 내려받아 **Git 관리 대상 코드와 로컬 수정·커밋을 원격 최신 상태로 덮어씁니다.** `.env`, 기본 `data`, `.venv`, wheelhouse와 기존 Git 무시 파일은 보존합니다. 커밋·푸시·병합·파일 전체 정리 명령을 실행하지 않으며 Git hook도 비활성화합니다. 완료 후 `run.bat`를 다시 실행하면 필요한 패키지를 확인하고 앱을 시작합니다. Linux는 `./update.sh` 후 `./run.sh`를 실행합니다. [업데이트 동작과 제한](docs/operations.md#코드만-내려받는-업데이트)에서 자세히 확인할 수 있습니다.

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000)을 엽니다.

1. 최근 N개월 또는 시작일·종료일을 선택합니다.
2. 한 번에 조회할 일 수를 정합니다. 예: 7일.
3. **전송 없이 검증**으로 실행하고 데이터 미리보기에서 21개 전송 필드를 확인합니다.
4. 운영 전송 설정을 완료한 뒤 **API 전송**으로 실행하면 실제 FAR API로 POST합니다. 서버가 `far_no + sample_no` 중복을 반환하면 같은 주소에 PATCH로 기존 행을 수정합니다. 이미 전송한 동일한 데이터는 `변경 없음`으로 표시됩니다.
5. 자동 갱신 화면에서 간격을 정하고 사용을 켠 후 저장합니다. 대시보드의 현재 기간·조회 단위·실행 방식이 함께 저장됩니다.

상단의 **LIVE 환경** 표시와 **API 연결 및 매핑** 화면에서 실제 접속 주소를 확인하세요. 조회 실패는 화면과 작업 로그에 표시되며 가상 데이터로 대체하지 않습니다. `.env` 변경은 프로세스를 재시작해야 반영됩니다. 환경변수는 `.env`보다 우선합니다.

이전 버전을 사용 중이라면 기존 `.env`는 자동으로 덮어쓰지 않으므로 아래 네 값을 확인하세요. 특히 `APP_MODE=mock`이나 `*.mock.invalid`가 남아 있으면 수정 후 앱을 재시작합니다. 별도로 지정한 사내 주소가 있다면 해당 주소를 유지해도 됩니다.

```dotenv
APP_MODE=live
CLAIMS_BASE_URL=http://12.81.220.37:8080
PRODUCT_BASE_URL=http://12.81.221.145:5273
TARGET_BASE_URL=https://estgtask.samsungds.net
```

## 제공 기능

- 최근 1·2·3·6·12개월(엔진/API는 1–120개월), 고정 날짜 범위, 1–366일 단위 분할 조회.
- API 결과가 `limit` 이상이거나 전체 건수/잘림 표시/응답 크기 한도를 감지하면 구간을 재분할.
- 하루에도 한도에 도달하면 해당 날짜를 **전송하지 않고 실패로 기록**. 페이지네이션 없는 API에서 임의로 완전 수집됐다고 판단하지 않습니다.
- 제품 schema 검증, Part ID 앞 15자 조회, 한 실행 안에서 최대 4,096개 제품 캐시.
- ISO 날짜 정규화, NAND/DRAM 조합, Part ID 좌측 15자 전송, 레코드별 검증.
- 우측 요청·응답 패널: GET 조건, POST/PATCH JSON, 실제 응답 코드·본문·헤더·소요 시간, 이전 전송 오류 연결, 복사·JSON 저장. 인증 값은 가리며 본문 저장 한도는 `HTTP_LOG_BODY_BYTES`로 설정합니다.
- SQLite에 작업·구간·레코드·이벤트·스케줄·성공 전송 상태 저장.
- 동일 업무 키와 동일 전송 값은 건너뛰고 변경된 값은 다시 POST. CSV 파일 내 중복 키는 경고 후 순서대로 모두 전송. Claim은 지정된 중복 응답, CSV는 모든 HTTP 400 응답에서 PATCH로 한 번 전환.
- GET 재시도, POST/PATCH 응답 유실 보류, 목록에서 체크해 반영됨·미반영을 처리하는 확인 화면, 실행 중지 및 재실행.
- 자동 갱신, 프로세스 중복 실행 방지, 중단 이력 복구, 웹 없는 CLI 실행.
- 실시간 상태/로그(2초 갱신), 실행 이력, 필터·페이지별 레코드, JSON 미리보기, NDJSON 내보내기.
- 선택적 접속 토큰, 동일 출처 검사, 인증 헤더 비노출, TLS 검증.
- 로컬 `csv/`의 추가 FAR 필드 가져오기: 23개 헤더 매핑, UTF-8/CP949, 행별 미리보기·검증, CSV에 채운 필드만 POST/PATCH, 웹·CLI 공통 실행.

## CSV 파일의 추가 필드 전송

프로젝트에 `csv/` 폴더를 제공합니다. [CSV 입력 양식](examples/far_import_template.csv)을 복사해 `far`, `sample`과 필요한 값을 채운 뒤 웹의 **CSV 가져오기**에서 파일을 선택하세요. 미리보기로 헤더·값·경고·전송 제외 행을 확인한 후 검증 또는 API 전송을 실행할 수 있습니다. 빈 셀은 기본적으로 오류 없이 전송에서 제외하며, 업무 키가 비었거나 전송할 선택 값이 없는 행도 건너뜁니다. 같은 `far + sample`이 반복되면 경고만 표시하고 파일 순서대로 모두 전송합니다. 같은 필드는 뒤 행의 값으로 갱신하며 제외한 빈 필드의 기존 값은 유지합니다.

이 기능은 CSV 필드만 전송하며 Claim·제품 API를 조회하지 않습니다. `Release Date`도 텍스트로 전송합니다. POST가 **HTTP 400 Bad Request**를 반환하면 오류 문구와 관계없이 같은 URL로 PATCH를 한 번 보냅니다. `where`는 `far_no`와 `sample_no`, `values`는 CSV에서 전송할 키 외 필드입니다. 입력 파일은 Git에 업로드하지 않습니다. 전체 컬럼 매핑, 빈칸·텍스트 처리, `run.bat csv-import` 및 Ubuntu 사용 방법은 [CSV 가져오기 안내](docs/csv-import.md)를 참고하세요.

## API 주소 및 사내망 전환

제공한 사내 API 주소를 [config.py](claim_sync/config.py)의 `DEFAULT_CLAIMS_BASE_URL`, `DEFAULT_PRODUCT_BASE_URL`, `DEFAULT_TARGET_BASE_URL` 상수와 [.env.example](.env.example)에 기본값으로 저장했습니다. Base URL에는 endpoint 경로를 포함하지 않습니다.

| API | 기본 Base URL |
| --- | --- |
| Claim 접수 이력 | `http://12.81.220.37:8080` |
| 제품 schema / 제품 조회 | `http://12.81.221.145:5273` |
| FAR 전송 | `https://estgtask.samsungds.net` |

실행 시 **환경변수 → `.env` → 코드 기본 상수** 순서로 값을 적용합니다. 주소를 바꿀 때는 `.env`의 `CLAIMS_BASE_URL`, `PRODUCT_BASE_URL`, `TARGET_BASE_URL`만 수정하고 재시작하면 됩니다. 기존 `.env`에 다른 값이 있으면 그 값이 계속 우선 적용됩니다. 코드 기본값과 배포용 `.env.example`은 모두 `APP_MODE=live`입니다.

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

처음에는 `live`에서 **전송 없이 검증**을 실행하세요. 제공받은 API 계약과 아래 처리 규칙이 실제 응답에 맞는지 사내에서 확인하세요.

| 확인 항목 | 구현의 기본 가정 |
| --- | --- |
| 시작일 파라미터 | `rcvDateFrom` 사용. `CLAIMS_FROM_PARAM`으로 변경 가능 |
| 날짜 범위 | 시작일과 종료일 모두 포함. `rcvDate`가 요청 범위 밖이면 오류 |
| Claim 응답 | 배열 또는 `data/items/records/results/claims` 래퍼. 사용자 지정 점 경로 지원 |
| 제품 응답 | 단일 객체 또는 `data/record/result/records/items` 래퍼, 1개짜리 배열 |
| 제품 schema | JSON Schema `properties` 또는 `fields/columns` 배열. [응답 계약 문서](docs/api-contracts.md) 참조 |
| 업무 키 | 로컬 중복 확인은 `TARGET_KEY_FIELDS`로 설정 가능. PATCH의 `where`는 서버 계약에 따라 항상 `far_no + sample_no` |
| 기존 행 갱신 | Claim은 지정된 두 키의 UNIQUE 오류, CSV는 모든 POST HTTP 400에서 같은 URL로 PATCH 1회 |
| 빈 선택 필드 | JSON `null`로 전송. 필수 키·접수일·Part ID는 비어 있으면 실패 |
| 성공 확인 | 오류 없는 2xx 응답(202 제외). 필요 시 `TARGET_SUCCESS_PATH`와 JSON `TARGET_SUCCESS_VALUE` 지정 |

Claim 동기화는 POST가 HTTP 400과 `ok:false`, `error.code="CREATE_FAIELD, UNIQUE"`, `error.massage="UNIQUE constraint failed: far_tabl.far_no, far_table.sample_no"`를 반환하면 PATCH로 전환합니다. 정상 철자인 `CREATE_FAILED`, `message`, `far_table`도 지원합니다. PATCH의 `where`에는 두 키를 넣고 `values`에는 나머지 19개 필드 전체를 `null`까지 포함해 보냅니다. Claim의 다른 400 오류는 수정 요청으로 바꾸지 않습니다. CSV는 모든 POST 400에서 입력 필드만 PATCH로 전송합니다. [정확한 요청·응답 규칙](docs/api-contracts.md#4-post-중복-시-patch-수정)을 참고하세요.

POST와 PATCH는 모두 `.env`의 `TARGET_BASE_URL`, `TARGET_PATH`, `TARGET_HEADERS`, TLS/CA 설정을 사용합니다. 기존 전송 조건인 `ALLOW_LIVE_WRITES=true`, `TARGET_UPSERT_CONFIRMED=true`도 유지합니다. `TARGET_UPSERT_CONFIRMED`는 서버의 POST·PATCH 갱신 계약 확인을 뜻하며, POST 자체가 upsert여야 한다는 뜻은 아닙니다.

**전송 확인 대기**에서는 서버와 값을 대조한 항목을 체크하고 **선택 항목 반영 완료** 또는 **선택 항목 미반영**으로 처리합니다. 사유 입력은 필요하지 않습니다. 미반영 처리는 다음 수동·예약 실행에서 재전송을 허용하며, 버튼을 누르는 순간 API로 재전송하지는 않습니다.

전송 대상·모드·업무 키·데이터셋이 바뀌면 이전 대기 작업을 다른 대상으로 보내지 않습니다. 새 작업을 등록하고 스케줄을 다시 저장하세요. 대상 DB를 초기화했거나 tenant가 바뀌면 `TARGET_DATASET_ID`도 바꾸세요.

### 사내 CA 인증서가 PFX인 경우

Windows에서 `export-ca.bat`를 더블클릭해 PFX 파일 경로와 암호를 입력하거나 다음처럼 실행하세요. 암호는 숨김 입력하며, 암호가 없으면 Enter를 누릅니다. Python·OpenSSL·추가 패키지 설치 없이 Windows 기본 PowerShell/.NET을 사용합니다.

```powershell
.\export-ca.bat "C:\인증서\corporate-ca.pfx"
```

PFX에서 **공개 CA 인증서만** 프로젝트의 `certs/corporate-ca.pem`으로 추출합니다. `.env`를 아래처럼 수정하고 `run.bat` 또는 worker를 재시작하세요.

```dotenv
TLS_VERIFY=true
CA_BUNDLE=certs/corporate-ca.pem
```

PFX 원본·개인키·암호는 앱 설정에 넣지 않습니다. 변환 도구는 Windows 인증서 저장소를 변경하지 않으며, `certs/`와 인증서·키 파일은 Git 및 Docker 빌드에서 제외합니다. Ubuntu에서도 생성한 PEM을 복사해 `CA_BUNDLE` 경로만 맞추면 됩니다. 인증서 갱신·오류 조치는 [PFX CA 인증서 설정](docs/operations.md#pfx-형식의-사내-ca-인증서)을 참고하세요.

## 날짜·재실행·스케줄 의미

- `최근 1개월`: 실행일이 2026-09-09이면 **2026-08-09 ~ 2026-09-09**, 양끝 포함. 30일 고정이 아닌 달력 기준이며 월말은 이전 달 마지막 날로 보정합니다.
- 시각 판단은 기본 `Asia/Seoul`, `TIMEZONE`에서 변경 가능합니다. 상대 기간은 예약 시점이 아닌 **실행 시작 시점**에 계산합니다.
- `7일씩`: 1/1–1/7, 1/8–1/14처럼 겹치지 않게 나눕니다. 잘림이 감지된 구간은 절반씩 재분할합니다.
- 스케줄 저장 시 첫 실행은 현재 시각 + 간격입니다. 즉시 실행은 대시보드 버튼을 사용하세요.
- 긴 실행 중 예약 시각이 여러 번 지나가면 쌓아두지 않고 종료 후 한 번 실행합니다. 서버가 꺼져 있던 기간도 한 번으로 합칩니다.
- 재실행은 같은 설정의 새 작업입니다. 상대 기간은 다시 계산되고 이미 성공한 동일 값은 건너뜁니다. 실패·중단된 작업을 임의로 자동 재개하지 않습니다.
- 기록은 전체 작업 기준 원자적이지 않습니다. 일부 구간 실패/취소 전에 완료한 POST/PATCH는 유지됩니다.
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

개발 중 사내망 없이 검증할 때만 `.env`에 `APP_MODE=mock`을 명시하세요. 가상 데이터는 하루 6건, 제품 4종이며 모든 API를 프로세스 안에서 처리합니다. 설치 중 수행하는 호환성 검사와 자동 테스트는 별도의 임시 설정·DB에서 이 모드를 사용하며, 실제 앱의 실행 모드를 변경하지 않습니다.

```bash
pip install -r requirements-dev.lock
pip install --no-deps -e .
pytest -q
ruff check .
ruff format --check .
```

테스트는 임시 DB와 가상 API만 사용합니다. 주요 결과와 검증 범위는 [검증 기록](docs/verification.md)에 정리합니다. FastAPI [lifespan](https://fastapi.tiangolo.com/advanced/events/), HTTPX [timeout](https://www.python-httpx.org/advanced/timeouts/), Python [SQLite](https://docs.python.org/3/library/sqlite3.html) 공식 문서의 실행·연결 관리 방식을 사용합니다.
