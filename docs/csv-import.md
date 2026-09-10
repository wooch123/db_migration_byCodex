# 로컬 CSV의 FAR 필드 가져오기

프로젝트의 `csv/` 폴더에 입력 파일을 두고 웹의 **CSV 가져오기**에서 선택합니다. 이 기능은 Claim·제품 API를 조회하지 않고 CSV에 있는 필드만 FAR API로 전송합니다. 새 행은 POST로 생성하고, 기존에 확인된 `far_no + sample_no` 중복 오류가 오면 같은 URL의 PATCH로 수정합니다.

## 파일 준비

[헤더만 있는 입력 양식](../examples/far_import_template.csv)을 `csv/`에 복사한 뒤 값을 채우세요. `far`, `sample`과 사용할 선택 컬럼만 남겨도 됩니다. 파일을 폴더에 넣기만 하면 전송되는 방식은 아닙니다. 웹 또는 CLI에서 실행을 지정합니다.

| CSV 컬럼 | 서버 컬럼 |
| --- | --- |
| far | far_no |
| sample | sample_no |
| 담당자 | name |
| F/W | firmware |
| Release Date | release_date |
| Init. | init |
| SLC Max EC | slc_max_ec |
| SLC Min. EC | slc_min_ec |
| SLC Avg. EC | slc_avg_ec |
| M/TLC Max EC | mlc_max_ec |
| M/TLC Min. EC | mlc_min_ec |
| M/TLC Avg. EC | mlc_avg_ec |
| FTL Open | open_count |
| RTBB | rtbb_count |
| Reclaim | reclaim_count |
| Written Size(GB) | write_size |
| Read Data (GB) | read_size |
| LVD count | lvd_count |
| NPO Count | npor_count |
| SPO Count | spor_count |
| ErrorLogCnt(eMMC) | error_log_count |
| ECID(UFS) or CID(eMMC) | ecid |
| EXT_CSD(eMMC Only) | ext_csd |

- UTF-8, UTF-8 BOM, CP949를 지원합니다. 쉼표로 구분하며 값 안에 쉼표나 줄바꿈이 있으면 CSV의 큰따옴표 규칙을 사용합니다.
- 헤더의 바깥 공백, 연속 공백, 영문 대소문자를 정규화해 비교합니다. 목록에 없는 헤더는 전송에서 제외하고 미리보기에 표시합니다. 중복 헤더는 오류입니다.
- `far`와 `sample`은 모든 데이터 행에 반드시 있어야 합니다. 같은 키가 파일에 두 번 나오면 오류로 표시하므로 한 행으로 정리하세요. 빈 줄은 무시합니다.
- 각 행에는 키 외에 전송할 필드가 하나 이상 필요합니다. 빈 선택 셀은 기본 **전송 제외**입니다. 화면에서 **null 전송**을 선택하면 헤더가 있는 빈 선택 셀을 JSON `null`로 전송합니다. 파일에 없는 컬럼은 두 방식 모두 전송하지 않습니다.
- 입력값은 문자열로 전달합니다. 카운트처럼 보이는 값도 API 컬럼 타입을 추측해 숫자로 바꾸지 않습니다. `0`, 긴 ECID, 16진수, 앞자리 0을 유지합니다. Excel에서 파일 저장 전에 값이 바뀌면 프로그램에서 복원할 수 없습니다.
- `Release Date`는 연-월-일 순서의 `2025-01-02`, `2025/1/2`, `2025.1.2`와 ISO 날짜·시간을 `2025-01-02`로 변환합니다. 월/일 순서가 모호한 날짜나 Excel 날짜 일련번호는 오류입니다.

## 웹에서 실행

1. **CSV 가져오기**에서 파일 목록을 새로 고칩니다. 화면의 폴더 경로가 현재 앱이 읽는 경로입니다.
2. 파일과 빈칸 처리 방법을 선택하고 미리보기를 실행합니다. 처음 50행의 변환 데이터와 전체 오류 건수를 확인할 수 있습니다. 오류 상세는 최대 100개를 표시합니다.
3. 파일 전체에 오류가 있으면 실행을 등록하지 않습니다. 수정 후 다시 미리보기를 실행하세요. 파일이 미리보기 이후 바뀌어도 재확인을 요청합니다.
4. **전송 없이 검증**은 API 호출 없이 실행 이력·행별 전송 JSON을 남깁니다. **API 전송**은 기존 운영 전송 조건을 충족해야 사용할 수 있습니다.
5. 실행 현황과 우측 요청·응답 로그에서 처리 결과를 확인합니다. CSV에서는 조회 기간 대신 파일명과 처리 행 수를 표시합니다.

실행 등록 시 파일 내용을 SQLite `csv_imports`에 저장합니다. 대기 중 파일을 수정·삭제해도 등록된 데이터로 처리하며, 실행 이력의 재실행도 당시 저장한 데이터를 사용합니다. 수정한 최신 파일을 보내려면 CSV 가져오기 화면에서 다시 미리보기·실행하세요. 원본 파일은 변경하거나 이동하지 않습니다.

## 전송 및 재실행

예를 들어 CSV 헤더가 `far,sample,담당자,F/W`이고 한 행에 값을 채웠다면 POST는 `{"values":{"far_no":"...","sample_no":"...","name":"...","firmware":"..."}}`입니다. 다른 FAR 필드는 추가하지 않습니다.

중복일 때 PATCH의 `where`는 `far_no`와 `sample_no`, `values`는 이 CSV 행의 키 외 입력 필드입니다. Claim 전송의 19개 필드 전체로 확장하지 않습니다. CSV에 넣지 않은 기존 서버 컬럼은 PATCH 요청에 포함하지 않습니다. 서버가 신규 생성 시 이 목록 밖의 필드를 필수로 요구하면 실제 거절 응답을 로그에 표시합니다.

전송 URL·인증 헤더·TLS/CA·프록시는 기존 `.env` 설정을 함께 사용합니다. CSV의 업무 키는 `far_no + sample_no`이며 `TARGET_KEY_FIELDS`도 이 두 필드를 사용해야 합니다. 전송 성공·확인 대기 기록은 Claim 동기화와 공유하므로 같은 키의 이전 전송이 불확실하면 CSV도 자동 재전송하지 않습니다. 확인 대기 목록에서 반영 여부를 처리한 뒤 실행하세요.

같은 전송 값이 직전 성공 기록과 같으면 건너뜁니다. Claim과 CSV는 서로 다른 필드 묶음을 보내므로 두 종류의 작업을 번갈아 실행하면 같은 값이 다시 전송될 수 있습니다. POST/PATCH 응답 유실과 중지는 기존 확인 대기 규칙을 적용합니다.

## Windows / Ubuntu CLI

실행 중인 웹 실행기와 같은 데이터 폴더를 쓰는 단발 CLI는 동시에 실행하지 않습니다. 웹을 계속 켜 둘 때는 CSV 화면에서 작업을 등록하면 공유 worker가 처리합니다.

```powershell
.\run.bat csv-import far-fields.csv
.\run.bat csv-import far-fields.csv --send
.\run.bat csv-import far-fields.csv --send --blank-mode null
```

```bash
./run.sh csv-import far-fields.csv
./run.sh csv-import far-fields.csv --send
```

파일 인자는 `CSV_DIR` 바로 아래의 파일명입니다. `.env` 기본값은 아래와 같습니다.

```dotenv
CSV_DIR=./csv
CSV_MAX_BYTES=10485760
CSV_MAX_ROWS=100000
```

파일당 기본 10 MiB, 최대 100,000행입니다. 경로·한도 변경 후 앱과 worker를 재시작하세요. 웹·worker가 같은 DATA_DIR를 사용하면 등록된 CSV는 원본 CSV 파일을 worker가 직접 읽지 않아도 처리할 수 있습니다. Docker에서는 CSV 폴더를 필요한 컨테이너에 읽기 전용으로 마운트하고 `CSV_DIR`를 해당 컨테이너 경로로 지정하세요. 파일 목록·미리보기에 대한 접근은 기존 웹 접속 토큰 설정을 따릅니다.

`csv/`의 실제 입력 파일은 Git 및 Docker 빌드에서 제외됩니다. 별도의 사용자 지정 CSV_DIR가 프로젝트 안에 있다면 그 경로도 Git ignore에 추가하세요. `data/`의 저장된 입력 데이터와 실행 로그도 업무 데이터이므로 기존 백업·접근 권한 정책으로 관리합니다.
