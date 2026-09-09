# 운영 가이드

## Ubuntu systemd 설치

아래 예시는 프로젝트 `/opt/claim-sync`, 설정 `/etc/claim-sync/claim-sync.env`, 데이터 `/var/lib/claim-sync` 기준입니다. Python 3.11 이상이 필요합니다. Ubuntu 24.04의 Python 3.12를 권장 기준으로 삼되 실제 사내 배포 환경에서 검증하세요.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv git
sudo useradd --system --home /opt/claim-sync --shell /usr/sbin/nologin claimsync
sudo git clone https://github.com/wooch123/db_migration_byCodex.git /opt/claim-sync
sudo python3 -m venv /opt/claim-sync/.venv
sudo /opt/claim-sync/.venv/bin/pip install -r /opt/claim-sync/requirements.lock
sudo /opt/claim-sync/.venv/bin/pip install --no-deps /opt/claim-sync
sudo install -d -m 0750 -o root -g claimsync /etc/claim-sync
sudo install -m 0640 -o root -g claimsync /opt/claim-sync/.env.example /etc/claim-sync/claim-sync.env
sudo install -d -m 0700 -o claimsync -g claimsync /var/lib/claim-sync
```

설정 파일에 `DATA_DIR=/var/lib/claim-sync`를 지정하고 사내 API 주소와 인증 정보를 입력합니다. 배포 예제의 전송 잠금은 검증을 마칠 때까지 유지하세요. 서비스 파일은 `DATA_DIR`을 같은 절대 경로로 고정합니다.

```bash
sudo cp /opt/claim-sync/deploy/claim-sync-worker.service /etc/systemd/system/
sudo cp /opt/claim-sync/deploy/claim-sync-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claim-sync-worker.service claim-sync-web.service
sudo systemctl status claim-sync-worker.service claim-sync-web.service
```

웹은 기본 loopback 바인딩입니다. 다른 PC에서 검증하려면 SSH 터널을 사용합니다.

```bash
ssh -L 8000:127.0.0.1:8000 user@ubuntu-server
```

로컬 브라우저에서 `http://127.0.0.1:8000`으로 접속해 스케줄을 저장합니다. 검증이 끝나면 웹만 종료하고 worker만 유지할 수 있습니다.

```bash
sudo systemctl disable --now claim-sync-web.service
# worker는 저장된 스케줄로 계속 동작
sudo journalctl -u claim-sync-worker.service -f
```

작업별 상세 로그와 오류는 SQLite의 `events`에 저장됩니다. systemd journal은 실행기 프로세스의 시작·종료·예외 확인용입니다. 서버 자체가 꺼져 있을 때 실행할 수는 없으며 부팅 후 다시 시작합니다.

헤드리스 환경에서 스케줄을 저장하는 방법:

```bash
sudo -u claimsync /opt/claim-sync/.venv/bin/claim-sync \
  --env-file /etc/claim-sync/claim-sync.env schedule /opt/claim-sync/examples/schedule.json
```

예제 스케줄은 검증 전용입니다. 실제 운영 전송은 사내 검증을 마친 후 파일의 `dry_run`을 `false`로 바꾸고 `.env`의 전송 조건도 충족해야 합니다.

## 설정 변경·업데이트

`.env`는 프로세스 시작 시 읽습니다. 변경 후 두 서비스를 재시작하세요. 웹과 worker는 같은 환경·전송 대상·DATA_DIR를 사용해야 합니다.

```bash
sudo systemctl restart claim-sync-worker.service claim-sync-web.service
```

코드를 업데이트할 때는 서비스를 멈추고 백업한 다음 설치합니다. 실행 중인 POST는 중단 시 `확인 대기`가 될 수 있으므로 가능하면 활성 작업이 끝난 뒤 중지하세요.

기본 업무 키가 다르면 `TARGET_KEY_FIELDS`를 먼저 맞춥니다. 설정 변경으로 기존 작업 대상이 달라지면 이전 대기 작업은 실패 처리되며 다른 대상으로 전송되지 않습니다. 스케줄을 다시 저장하세요.

## 네트워크·인증

- 각 API의 인증은 `CLAIMS_HEADERS`, `PRODUCT_HEADERS`, `TARGET_HEADERS`에 JSON 객체로 설정합니다. 헤더 값은 화면이나 일반 오류 로그에 노출하지 않습니다.
- 웹을 loopback 외의 주소에 바인딩하려면 `APP_ACCESS_TOKEN`을 설정해야 합니다. UI는 토큰을 탭의 sessionStorage에 저장합니다. 공용 PC에서는 사용 후 탭을 닫으세요.
- 기본 허용 Host는 localhost와 loopback IP입니다. 사내 도메인으로 공개할 때는 `ALLOWED_HOSTS=["sync.internal.example"]`처럼 실제 접속 도메인을 추가하세요. 임의 Host 요청을 차단해 로컬 콘솔의 DNS rebinding을 방지합니다.
- 직접 공유할 때는 사내 HTTPS reverse proxy와 네트워크 접근 제한을 구성합니다. 프록시의 Host/scheme 전달은 실제 외부 URL과 일치시켜 동일 출처 검사에 맞춰야 합니다. 기본 제공은 SSH 터널 방식입니다.
- 내부 인증서 발급자를 사용하면 `CA_BUNDLE=/etc/claim-sync/corporate-ca.pem`을 지정합니다. `TLS_VERIFY=true`를 유지하세요.
- HTTPX는 기본적으로 환경 프록시를 사용하지 않습니다. 사내 정책상 필요할 때만 `TRUST_ENV_PROXY=true`와 프록시 환경변수를 사용하세요.
- mock 모드에서는 Base URL이 실제 주소로 변경되어도 네트워크로 전송하지 않습니다. `live` 모드 전환을 명시해야 합니다.

## Docker

```bash
cp .env.example .env
# .env의 APP_ACCESS_TOKEN에 충분히 긴 임의 토큰을 입력
docker compose up --build -d
docker compose logs -f worker
```

Compose는 웹과 worker를 분리하고 같은 named volume을 공유합니다. 웹은 컨테이너 내부에서 `0.0.0.0`에 바인딩하므로 `APP_ACCESS_TOKEN`이 필수입니다. 호스트 포트는 기본 `127.0.0.1:8000`에만 공개합니다. 인증서 파일은 컨테이너 안으로 별도 read-only mount하고 `CA_BUNDLE`을 그 경로에 맞춰야 합니다. 컨테이너의 `localhost`는 호스트가 아니므로 live 모드에서는 접근 가능한 사내 주소를 입력하세요.

## 폐쇄망 설치

UI는 외부 폰트·CDN·분석 도구를 사용하지 않습니다. 패키지만 반입하면 네트워크 없이 설치할 수 있습니다. 인터넷 가능한 **같은 Ubuntu/Python/CPU 환경**에서 wheelhouse를 준비하세요. Windows에서 받은 native wheel을 Linux로 복사하면 안 됩니다.

```bash
python3 -m venv build-env
source build-env/bin/activate
pip wheel -r requirements.lock --wheel-dir wheelhouse
pip wheel --no-deps . --wheel-dir wheelhouse
```

프로젝트의 설정·deploy 파일과 wheelhouse를 사내에 반입하고:

```bash
python3 -m venv /opt/claim-sync/.venv
/opt/claim-sync/.venv/bin/pip install --no-index --find-links ./wheelhouse claim-sync==1.0.0
```

설치 패키지에 웹 정적 파일이 포함됩니다. systemd 예제는 `/opt/claim-sync` 작업 디렉터리와 외부 `.env` 경로를 사용합니다.

## 상태 저장·백업·보관

`DATA_DIR/claim-sync.sqlite3`에는 실제 처리 데이터가 포함됩니다. 디렉터리는 서비스 계정만 읽도록 관리하고 Git이나 공유 폴더에 넣지 마세요. SQLite WAL과 파일 잠금을 사용하므로 같은 호스트의 로컬 디스크를 사용합니다. **NFS/SMB나 여러 서버가 같은 DB 파일을 공유하는 구성은 지원하지 않습니다.**

온라인 백업은 SQLite backup API로 일관된 사본을 만듭니다. 실행 중인 `.sqlite3` 파일만 단독 복사하면 WAL의 최신 정보가 빠질 수 있습니다.

```bash
sudo -u claimsync /opt/claim-sync/.venv/bin/python -c \
  "import sqlite3; src=sqlite3.connect('/var/lib/claim-sync/claim-sync.sqlite3'); dst=sqlite3.connect('/var/lib/claim-sync/backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
```

기본값은 감사 기록을 자동 삭제하지 않습니다. 장기 운영 시 DB 용량을 관찰하고 사내 보관 정책에 따라 종료된 작업의 `records/chunks/events/jobs`를 보관·정리해야 합니다. `deliveries`는 중복 전송 방지 및 불확실 전송 복구에 필요하므로 단순 로그 정리 대상으로 삭제하지 마세요. 운영 보관·모니터링 체계는 배포 환경에서 구성해야 합니다.

## 문제 해결

| 상황 | 조치 |
| --- | --- |
| 실행기 연결 대기 | worker 프로세스, DATA_DIR, 파일 권한, heartbeat 확인 |
| schema 해석 실패 | 실제 schema 형태와 필수 필드 대조. 지원 형태가 아니면 `clients.py` 어댑터 확장 필요 |
| Claim/제품 JSON 경로 오류 | `.env`의 응답 경로 설정 변경 |
| 하루치 limit 도달 | API 담당자에게 pagination/시각 단위/더 높은 검증된 한도 요청 |
| HTTP 401/403 | 각 API의 인증 헤더와 계정 권한 확인 |
| 인증서 오류 | 사내 CA bundle 경로와 신뢰 체인 확인 |
| 확인 대기 | 대상 DB에서 업무 키와 모든 전송 값을 대조하고 웹에서 결과 기록 |
| 대상 값이 외부에서 변경됨 | 로컬 ledger만으로 대상 변경을 감지할 수 없음. `TARGET_DATASET_ID` 변경 후 재동기화 |
| 프로세스 재시작 후 interrupted | 이전 작업 이력은 보존. 확인 대기 해소 후 수동 재실행 또는 다음 예약 실행 |
| 스케줄 변경 후 기존 작업 남음 | 스케줄 변경은 이미 등록된 작업을 취소하지 않음. 필요하면 해당 작업 중지 |

## 별도 HTTP 가상 서버

내장 mock과 별도로 실제 HTTP 요청 경로를 검증할 수 있습니다.

```bash
PORT=8091 DATA_DIR=./data/mock-server claim-sync mock-server
```

다른 설정 파일에 세 base URL을 모두 `http://127.0.0.1:8091`로 두고 `APP_MODE=live`, 두 전송 허용 조건을 true로 설정합니다. 다른 `DATA_DIR`에서 단발 실행하면 프로세스 간 HTTP로 가상 서버에 전달됩니다. 이 설정은 로컬 테스트에만 사용하세요.
