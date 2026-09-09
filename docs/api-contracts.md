# API 계약과 전송 규칙

## 1. Claim 수집

```http
GET {CLAIMS_BASE_URL}{CLAIMS_PATH}?rcvDataFrom=2025-01-01&rcvDateTo=2025-01-07&limit=1000
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
{"columns": ["app","device","ctrl","density","nand_gen","nand_ver","dram_gen","dram_ver"]}
```

제품 값은 직접 객체, 래퍼 또는 단일 항목 배열을 지원합니다. 별도 구조는 `PRODUCT_RECORDS_PATH`로 지정합니다. 반환 객체에는 8개 필드가 모두 존재해야 하며 값은 문자열·숫자·null을 허용합니다. 2개 이상 제품 레코드가 반환되면 임의로 첫 번째를 선택하지 않고 실패합니다.

`partId[:15]`를 URL 인코딩해 조회합니다. 15자 미만이면 오류입니다. 목적지 `part_id`에는 원본 전체 문자열을 보존합니다. 제품 캐시는 실행 간 공유하지 않아 제품 정보 변경을 다음 실행에 반영합니다.

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
| part_id | partId | 원본 전체 문자열·필수 |
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
| density | 제품 density | 문자열 또는 null |

ISO timestamp의 날짜는 원본 시간대에서의 달력 날짜로 유지합니다. 날짜를 UTC로 옮겨 하루가 바뀌지 않습니다. 선택 날짜의 `null`/빈 문자열은 `null`로, 잘못된 날짜는 오류로 처리합니다. NAND/DRAM 양쪽 모두 비면 `null`입니다. 원본 선택 필드가 없는 경우도 null이므로 **운영 서버가 null을 기존 값 삭제로 처리하는지** 사내 검증에서 확인해야 합니다.

```json
{
  "values": {
    "far_no":"FAR-001", "sample_no":"S001", "rcv_date":"2025-01-01", "due_date":"2025-01-15",
    "cust_name":"Example", "fail_loc":"Korea", "fail_symptom":"Read failure", "part_id":"ABCDEFGHIJKLMNO-EXT",
    "failmode1":"Read", "failmode2":"Intermittent", "comp_wc":"202501", "far_comp_date":null,
    "ims_created_date":"2025-01-01", "ims_key":"IMS001", "lot_id":"LOT001",
    "app":"SSD", "device":"NVMe", "ctrl":"CTRL", "nand":"V8 1.0", "dram":"LPDDR4 2.0", "density":"1 TB"
  }
}
```

## 4. 재시도·중복·반영 확인

- GET만 지수 지연으로 재시도합니다. `408/429/500/502/503/504` 및 네트워크 오류에 적용하며 숫자형 `Retry-After`도 최대 60초까지 반영합니다.
- POST는 자동 재시도하지 않습니다. 네트워크 오류, 5xx, 202, 408, 429, 애플리케이션 오류, 확인 응답 파싱 실패는 `uncertain`입니다. 해당 실행을 멈추고 이후 같은 업무 키도 보류합니다.
- 일반 4xx 및 redirect는 실패로 기록합니다. Redirect를 따라 다른 서버로 인증 정보를 보내지 않습니다.
- 기본 성공 조건은 오류 없는 2xx(202 제외). 예: `{"success":true}`와 204. 서버가 `{"code":"OK"}`를 반환하면 `TARGET_SUCCESS_PATH=code`, `TARGET_SUCCESS_VALUE='"OK"'`를 설정할 수 있습니다.
- 성공 ledger는 대상 URL + 모드 + 데이터셋 + 업무 키 설정으로 분리합니다. 키의 전송 값이 달라지면 새로운 POST입니다. 인증 헤더의 tenant가 바뀌면 `TARGET_DATASET_ID`를 반드시 바꿉니다.
- 매 작업·값에 대한 `Idempotency-Key`를 전달합니다. 서버 지원 여부는 알 수 없으므로 정확히 한 번 전달을 보장한다고 가정하지 않습니다. 값이 A→B→A로 돌아와도 과거 A 요청의 응답이 재사용되지 않도록 작업 ID를 포함합니다.
- 대상 DB에서 외부로 수정한 값이나 삭제한 행은 원본 값이 그대로라면 자동 감지하지 못합니다. 이 경우 대상 데이터셋 버전을 바꾸고 전체 재동기화하세요.
- 중지/종료가 POST 도중 발생하면 반영 여부 확인을 보류합니다. 강제 종료 후 `sending` ledger는 다음 실행기 시작 시 `uncertain`으로 복구됩니다.
- 확인 대기 화면에서 **전송한 모든 값과 업무 키**를 대상 DB와 비교한 뒤 반영됨/미반영을 근거와 함께 기록합니다. 미반영은 다음 수동/예약 실행에서 다시 시도할 수 있습니다.

## 5. 관리 API

인증을 켰다면 `Authorization: Bearer {APP_ACCESS_TOKEN}`을 넣습니다. 쓰기 요청은 JSON만 받습니다. API 스키마는 `/api/openapi.json`으로 제공하며 외부 CDN이 필요한 Swagger 화면은 사용하지 않습니다.

| Method | Path | 기능 |
| --- | --- | --- |
| GET | /healthz | 웹 프로세스 생존 확인 (민감 정보 없음) |
| GET | /api/state | 환경, 실행기 heartbeat, 최근 30개 이력, 스케줄 |
| POST | /api/preview | 기간 및 기본 구간 수 계산 |
| POST | /api/jobs | 실행 큐 등록 |
| GET | /api/jobs/{id} | 작업, 최근 400개 구간, 최근 500개 이벤트 |
| GET | /api/jobs/{id}/records | 레코드 페이지·상태 필터 |
| GET | /api/jobs/{id}/export | 전체 레코드 NDJSON 스트림 |
| POST | /api/jobs/{id}/cancel | 중지 요청 |
| POST | /api/jobs/{id}/retry | 같은 설정으로 새 실행 |
| PUT | /api/schedule | 스케줄 저장 |
| POST | /api/check | 조회 API 연결 검사 |
| GET | /api/deliveries/unresolved | 최근 100개 확인 대기 |
| POST | /api/deliveries/resolve | 반영 여부 기록 |

UI는 표시량을 제한하지만 전체 이벤트/구간은 SQLite에 보존됩니다. 내보내기는 각 레코드의 `request.values`와 상태·오류를 함께 제공합니다.
