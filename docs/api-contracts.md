# API 계약과 전송 규칙

로컬 CSV의 추가 23개 필드, 빈칸 처리와 실행 규칙은 [CSV 가져오기](csv-import.md)를 참고하세요. 아래 Claim 매핑과 별도로 CSV에 포함한 필드만 같은 FAR POST/PATCH API에 전달합니다.

## 1. Claim 수집

```http
GET {CLAIMS_BASE_URL}{CLAIMS_PATH}?rcvDateFrom=2025-01-01&rcvDateTo=2025-01-07&limit=1000
```

허용 응답 예:

```json
{"data": [{"farNo":"FAR-001","sampleNo":"S001","rcvDate":"2025-01-01","partId":"ABCDEFGHIJKLMNO-EXT"}], "total": 1}
```

배열 자체와 `data/items/records/results/claims`의 최대 4단계 래퍼를 자동 해석합니다. 다른 구조는 `CLAIMS_RECORDS_PATH=result.list`처럼 점 경로로 지정합니다. 알 수 없는 형태나 `{ "error": ... }`를 빈 성공 결과로 취급하지 않습니다.

배열 길이 ≥ `CLAIMS_LIMIT`, 감지한 래퍼의 `total/totalCount/totalElements` > 수신 건수, `hasMore=true`, `truncated=true`, `MAX_RESPONSE_BYTES` 초과 중 하나면 구간을 분할합니다. 원본이 이런 표시 없이 limit보다 적은 데이터를 조용히 자르면 클라이언트가 누락을 판별할 방법은 없습니다. `total`은 요청 날짜 범위의 전체 건수라는 가정입니다. 하루치도 한도에 닿으면 해당 날짜의 수신 행을 보내지 않습니다. 서버의 pagination/cursor 또는 시각 단위 조회 지원이 필요합니다.

원본에서 `analName`, `firstCompDate`, `failMajorCategory`, `failMinorCategory`는 목적지 필드에 없으므로 전송하지 않습니다.

## 2. 제품 schema / 단일 제품

```http
GET {PRODUCT_BASE_URL}{PRODUCT_SCHEMA_PATH}
GET {PRODUCT_BASE_URL}/dbms/api/product-info/record/ABCDEFGHIJKLMNO
```

schema는 JSON Schema `properties` 또는 `fields`/`columns` 배열을 지원합니다. 배열 항목은 문자열 또는 `name`, `field`, `column_name` 속성을 가진 객체입니다. `data`/`schema` 래퍼도 지원합니다.

```json
{"columns": ["app","device","ctrl","denstiy","nand_gen","nand_ver","dram_gen","dram_ver"]}
```

제품 값은 직접 객체, 래퍼 또는 단일 항목 배열을 지원합니다. 별도 구조는 `PRODUCT_RECORDS_PATH`로 지정합니다. 반환 객체에는 8개 필드가 모두 존재해야 하며 값은 문자열·숫자·null을 허용합니다. 2개 이상 제품 레코드가 반환되면 임의로 첫 번째를 선택하지 않고 실패합니다.

제품 schema와 제품 응답의 용량 필드는 `denstiy`입니다. FAR API에 전송할 때는 기존 철자인 `density`로 매핑합니다.

`partId[:15]`를 URL 인코딩해 조회합니다. 15자 미만이면 오류입니다. 목적지 `part_id`에도 원본의 좌측 15자만 전송합니다. 제품 캐시는 실행 간 공유하지 않아 제품 정보 변경을 다음 실행에 반영합니다.

## 3. 전송 값 (21개)

| 목적지 | 원본 | 변환 |
| --- | --- | --- |
| far_no | farNo | 문자열·필수 |
| sample_no | sampleNo | 문자열·필수 |
| rcv_date | rcvDate | yyyy-mm-dd·필수 |
| due_date | dueDate | yyyy-mm-dd 또는 null |
| cust_name | custName | 문자열 또는 null |
| fail_loc | failLoc | 문자열 또는 null |
| fail_symptom | failSymptom | 문자열 또는 null |
| part_id | partId | 좌측 15자·필수 |
| failmode1 | failMode1 | 문자열 또는 null |
| failmode2 | failMode2 | 문자열 또는 null |
| comp_wc | shippingWeekCode | 문자열, 앞자리 0 보존 |
| far_comp_date | actualCompDate | yyyy-mm-dd 또는 null |
| ims_created_date | imsKeyCreatedDate | yyyy-mm-dd 또는 null |
| ims_key | imsKey | 문자열 또는 null |
| lot_id | lotId | 문자열 또는 null |
| app | 제품 app | 문자열 또는 null |
| device | 제품 device | 문자열 또는 null |
| ctrl | 제품 ctrl | 문자열 또는 null |
| nand | 제품 nand_gen + nand_ver | 비어 있지 않은 값을 공백 한 칸으로 결합 |
| dram | 제품 dram_gen + dram_ver | 비어 있지 않은 값을 공백 한 칸으로 결합 |
| density | 제품 denstiy | 문자열 또는 null |

ISO timestamp의 날짜는 원본 시간대에서의 달력 날짜로 유지합니다. 날짜를 UTC로 옮겨 하루가 바뀌지 않습니다. 선택 날짜의 `null`/빈 문자열은 `null`로, 잘못된 날짜는 오류로 처리합니다. NAND/DRAM 양쪽 모두 비면 `null`입니다. 원본 선택 필드가 없는 경우도 null이므로 **운영 서버가 null을 기존 값 삭제로 처리하는지** 사내 검증에서 확인해야 합니다.

```json
{
  "values": {
    "far_no":"FAR-001", "sample_no":"S001", "rcv_date":"2025-01-01", "due_date":"2025-01-15",
    "cust_name":"Example", "fail_loc":"Korea", "fail_symptom":"Read failure", "part_id":"ABCDEFGHIJKLMNO",
    "failmode1":"Read", "failmode2":"Intermittent", "comp_wc":"202501", "far_comp_date":null,
    "ims_created_date":"2025-01-01", "ims_key":"IMS001", "lot_id":"LOT001",
    "app":"SSD", "device":"NVMe", "ctrl":"CTRL", "nand":"V8 1.0", "dram":"LPDDR4 2.0", "density":"1 TB"
  }
}
```

## 4. POST 중복 시 PATCH 수정

처음에는 `POST {TARGET_BASE_URL}{TARGET_PATH}`로 위의 21개 필드를 `{"values":{...}}` 형태로 보냅니다. 아래 조건을 모두 충족하는 응답에만 같은 URL로 PATCH를 한 번 보냅니다.

- HTTP 상태는 **400**이고, 완전히 읽은 유효한 JSON의 `ok` 값이 `false`입니다.
- `error.code`가 `CREATE_FAIELD, UNIQUE` 또는 정상 철자인 `CREATE_FAILED, UNIQUE`입니다.
- `error.massage` 또는 `error.message`가 `far_no`와 `sample_no` 두 칸의 UNIQUE 제약 실패를 나타냅니다. 테이블명은 제공된 오타 `far_tabl`과 `far_table`을 허용합니다. 다른 테이블이나 다른 칸의 중복은 해당하지 않습니다.
- 보낼 값의 `far_no`와 `sample_no`가 모두 비어 있지 않습니다.

제공받은 서버 응답:

```json
{
  "ok": false,
  "error": {
    "code": "CREATE_FAIELD, UNIQUE",
    "massage": "UNIQUE constraint failed: far_tabl.far_no, far_table.sample_no"
  }
}
```

이 경우 수정 요청은 다음과 같습니다. `where`의 두 조건을 모두 만족하는 행을 찾고, `values`에는 키를 제외한 **19개 필드 전체**를 넣습니다. 바뀐 것으로 추측한 칸만 골라 보내지 않으며 `null` 값도 유지합니다. `part_id`는 POST와 동일하게 좌측 15자입니다.

```http
PATCH {TARGET_BASE_URL}{TARGET_PATH}
Content-Type: application/json
```

```json
{
  "where": {"far_no": "FAR-001", "sample_no": "S001"},
  "values": {
    "rcv_date":"2025-01-01", "due_date":"2025-01-15",
    "cust_name":"Example", "fail_loc":"Korea", "fail_symptom":"Read failure", "part_id":"ABCDEFGHIJKLMNO",
    "failmode1":"Read", "failmode2":"Intermittent", "comp_wc":"202501", "far_comp_date":null,
    "ims_created_date":"2025-01-01", "ims_key":"IMS001", "lot_id":"LOT001",
    "app":"SSD", "device":"NVMe", "ctrl":"CTRL", "nand":"V8 1.0", "dram":"LPDDR4 2.0", "density":"1 TB"
  }
}
```

POST와 PATCH는 `.env`의 `TARGET_BASE_URL`·`TARGET_PATH`·`TARGET_HEADERS`와 같은 HTTP 연결의 TLS/CA·프록시 설정을 공유합니다. `TARGET_KEY_FIELDS`를 바꿔도 서버의 PATCH 검색 조건은 `far_no + sample_no`로 고정입니다. 실제 전송에는 기존 `ALLOW_LIVE_WRITES=true`와 `TARGET_UPSERT_CONFIRMED=true`가 모두 필요합니다. 두 번째 설정은 이 POST·PATCH 계약 확인을 뜻하며 POST 자체의 upsert 구현을 요구하지 않습니다.

다른 HTTP 400, 409 등 다른 상태, HTML/잘못된 JSON, 잘리거나 수신 중 끊긴 오류 본문에서는 PATCH로 전환하지 않습니다. PATCH가 다시 중복 오류를 반환해도 반복하지 않습니다. POST 400과 PATCH 응답은 각각 실제 메서드·URL·본문·상태 코드로 우측 패널에 남습니다. 따라서 최종 실행이 성공하더라도 선행 POST 400 기록은 실패 요청으로 표시됩니다.

## 5. 재시도·중복·반영 확인

- GET만 지수 지연으로 재시도합니다. `408/429/500/502/503/504` 및 네트워크 오류에 적용하며 숫자형 `Retry-After`도 최대 60초까지 반영합니다.
- POST와 PATCH는 자동 재시도하지 않습니다. 네트워크 오류, 5xx, 202, 408, 429, 애플리케이션 오류, 확인 응답 파싱 실패는 `uncertain`입니다. 해당 실행을 멈추고 이후 같은 업무 키도 보류합니다. 앞 절의 중복 거절 응답에 따른 PATCH 전환만 허용합니다.
- 일반 4xx 및 redirect는 실패로 기록합니다. 지정된 POST 400 중복 오류만 PATCH로 전환합니다. Redirect를 따라 다른 서버로 인증 정보를 보내지 않습니다.
- 기본 성공 조건은 오류 없는 2xx(202 제외). 예: `{"ok":true}`, `{"success":true}`와 204. `ok:false`도 애플리케이션 오류로 처리합니다. 서버가 `{"code":"OK"}`를 반환하면 `TARGET_SUCCESS_PATH=code`, `TARGET_SUCCESS_VALUE='"OK"'`를 설정할 수 있으며 POST와 PATCH 모두에 적용합니다.
- 성공 ledger는 대상 URL + 모드 + 데이터셋 + 업무 키 설정으로 분리합니다. POST 또는 PATCH 성공 후 같은 전송 값은 건너뛰며 값이 달라지면 다시 POST부터 시작합니다. 인증 헤더의 tenant가 바뀌면 `TARGET_DATASET_ID`를 반드시 바꿉니다.
- 매 작업·값에 대한 `Idempotency-Key`를 전달하고 PATCH에는 POST와 다른 키를 사용합니다. 서버 지원 여부는 알 수 없으므로 정확히 한 번 전달을 보장한다고 가정하지 않습니다. 값이 A→B→A로 돌아와도 과거 A 요청의 응답이 재사용되지 않도록 작업 ID를 포함합니다.
- 대상 DB에서 외부로 수정한 값이나 삭제한 행은 원본 값이 그대로라면 자동 감지하지 못합니다. 이 경우 대상 데이터셋 버전을 바꾸고 전체 재동기화하세요.
- 중지/종료가 POST 또는 PATCH 도중 발생하면 반영 여부 확인을 보류합니다. 강제 종료 후 `sending` ledger는 다음 실행기 시작 시 `uncertain`으로 복구됩니다. 다음 실행의 보류 기록은 원래 실패한 POST 또는 PATCH의 상세 요청을 연결합니다.
- 확인 대기 화면에서 **전송한 모든 값과 업무 키**를 대상 DB와 비교한 뒤 목록의 항목을 체크하고 반영됨/미반영 버튼으로 처리합니다. 사유 입력 없이 선택한 결과와 처리 시각을 저장합니다. 미반영은 다음 수동/예약 실행에서 다시 시도할 수 있으며 이 처리 자체는 서버에 전송하지 않습니다.

## 6. 관리 API

인증을 켰다면 `Authorization: Bearer {APP_ACCESS_TOKEN}`을 넣습니다. 쓰기 요청은 JSON만 받습니다. API 스키마는 `/api/openapi.json`으로 제공하며 외부 CDN이 필요한 Swagger 화면은 사용하지 않습니다.

| Method | Path | 기능 |
| --- | --- | --- |
| GET | /healthz | 웹 프로세스 생존 확인 (민감 정보 없음) |
| GET | /api/state | 환경, 실행기 heartbeat, 최근 30개 이력, 스케줄 |
| POST | /api/preview | 기간 및 기본 구간 수 계산 |
| POST | /api/jobs | 실행 큐 등록 |
| GET | /api/csv/files | CSV 폴더의 파일 목록·헤더 매핑·한도 |
| POST | /api/csv/preview | filename·blank_mode로 전체 검증, 앞 50행·오류·sha256 반환 |
| POST | /api/csv/jobs | filename·sha256·blank_mode·dry_run으로 검증된 CSV 작업 등록 |
| GET | /api/jobs/{id} | 작업, 최근 400개 구간, 최근 500개 이벤트 |
| GET | /api/jobs/{id}/records | 레코드 페이지·상태 필터 |
| GET | /api/jobs/{id}/export | 전체 레코드 NDJSON 스트림 |
| GET | /api/jobs/{id}/delivery-context | 이전 실행의 전송 확인 대기 원인과 요청 데이터 |
| POST | /api/jobs/{id}/cancel | 중지 요청 |
| POST | /api/jobs/{id}/retry | 같은 설정으로 새 실행. CSV는 당시 저장한 파일 데이터 재사용 |
| PUT | /api/schedule | 스케줄 저장 |
| POST | /api/check | 조회 API 연결 검사 |
| GET | /api/deliveries/unresolved | 최근 100개 확인 대기 |
| POST | /api/deliveries/resolve | 개별 반영 여부 기록, 사유 불필요 |
| POST | /api/deliveries/resolve-selected | 체크한 항목의 반영 여부를 함께 기록, 사유 불필요 |
| GET | /api/http-exchanges | GET/POST/PATCH 요청 기록, 실행·업무 키·오류 필터와 페이지 |
| GET | /api/http-exchanges/{id} | 요청 및 실제 응답의 상세 데이터 |

목록에서 선택한 항목은 다음 형식으로 처리합니다. `result`는 `applied`(반영됨) 또는 `not_applied`(미반영)입니다. `expected_updated_at`은 `/api/deliveries/unresolved`에서 받은 해당 항목의 `updated_at`을 그대로 사용합니다.

```json
{
  "items": [
    {
      "destination": "조회 결과의 destination 값",
      "record_key": "[\"FAR-001\",\"S001\"]",
      "expected_updated_at": "조회 결과의 updated_at 값"
    }
  ],
  "result": "not_applied"
}
```

선택한 항목은 한 트랜잭션에서 전부 처리하거나 전부 유지합니다. 그사이 다른 작업에서 상태를 바꿨거나 이미 처리한 항목이면 409로 거절하므로 목록을 다시 읽어 확인합니다. 반영 확인 API는 운영 서버에 POST/PATCH하거나 작업을 즉시 재실행하지 않습니다.

UI는 표시량을 제한하지만 전체 이벤트/구간은 SQLite에 보존됩니다. 내보내기는 각 레코드의 `request.values`와 상태·오류를 함께 제공합니다.
