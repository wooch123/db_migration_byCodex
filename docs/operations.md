# 운영 가이드

## 자동 실행 파일 설정

웹 검증은 Windows의 `run.bat`, Linux의 `./run.sh`로 시작할 수 있습니다. 기본 모드는 `web`이며 `worker`, `run`, `schedule` 등 기존 CLI 명령과 인자는 그대로 전달합니다. `--setup-only`는 설치만 수행하고 종료합니다. 두 실행 파일은 프로젝트 폴더로 이동하므로 다른 폴더에서 실행해도 설정과 데이터 경로가 일관됩니다.

신규 설치는 `APP_MODE=live`로 실제 사내 API를 조회합니다. **전송 없이 검증**은 실제 Claim·제품 정보를 읽고 FAR 전송 데이터를 미리 확인하는 기능입니다. 기존 `.env`에 `APP_MODE=mock`이 있으면 `live`로 바꾸고, 가상 주소가 남아 있으면 `.env.example`의 사내 주소로 교체한 뒤 재시작하세요. 기존 설정을 자동으로 덮어쓰지는 않습니다.

실행 시 패키지 버전, 설치 기록과 의존성 충돌을 확인합니다. 정상 설치된 환경은 다운로드 없이 재사용합니다. 설치에 실패하면 앱을 실행하지 않고 오류를 표시하므로 원인을 해결한 뒤 같은 파일을 다시 실행하면 됩니다. 초기 설치 파일 잠금으로 여러 실행 파일이 동시에 같은 환경에 패키지를 설치하지 않도록 처리합니다. 앱을 실행 중인 상태에서 의존성 버전 변경이 필요한 업데이트는 앱을 먼저 종료한 뒤 진행하세요.

| 환경변수 | 의미 |
| --- | --- |
| `CLAIM_SYNC_PYTHON` | 초기 준비에 사용할 Python 실행 파일의 경로. 공백이 있어도 지원하며 기존 정상 `.venv`는 유지 |
| `CLAIM_SYNC_WHEELHOUSE` | 패키지를 받을 로컬 wheel 폴더. 지정하면 `--no-index`로 인터넷 조회를 차단 |
| `CLAIM_SYNC_NO_SYSTEM_INSTALL=1` | Python/venv가 없을 때 winget·apt 자동 설치를 하지 않고 종료 |
| `CLAIM_SYNC_NO_PAUSE=1` | Windows에서 인자 없는 실행 실패 시 키 입력 대기를 생략 |
| `CLAIM_SYNC_INSTALL_MODE` | `auto`(기본): 고정 버전 실패 시 호환 조합 조회. `compatible`: 처음부터 호환 조합 조회. `locked`: 고정 버전만 허용 |

이 변수는 `.env`를 읽기 전 설치 단계에서 사용하므로 **실행할 터미널의 환경변수로 지정**합니다.

```powershell
$env:CLAIM_SYNC_PYTHON = 'C:\Program Files\Python312\python.exe'
.\run.bat --setup-only
```

```bash
CLAIM_SYNC_PYTHON=/usr/bin/python3.12 ./run.sh --setup-only
```

Python 자동 설치는 Windows의 winget(`Python.Python.3.12`, 사용자 범위)과 Ubuntu/Debian의 apt를 지원합니다. Ubuntu/Debian에서 venv/ensurepip 모듈이 누락되면 선택한 Python 버전에 맞는 `python3.x-venv` 설치도 시도합니다. sudo 암호를 물어볼 수 있습니다. 별도 PPA 추가나 시스템 Python 교체는 하지 않으므로 기본 저장소가 Python 3.10 이하인 배포판은 3.11 이상의 Python을 별도로 준비하세요. 다른 Linux 배포판에서도 Python과 venv가 이미 있으면 Python 패키지 자동 설치는 그대로 동작합니다.

Windows에서는 `CLAIM_SYNC_PYTHON`으로 명시한 실행 파일, 기존 `.venv`, 현재 PATH의 Python, `py -3` 순서로 선택합니다. 여러 Python이 설치되어 있어도 현재 터미널에서 패키지를 준비한 Python을 우선 사용합니다. 오프라인 wheel은 실제 사용할 Python의 버전·운영체제·CPU와 일치해야 합니다.

폐쇄망에서는 동일한 운영체제·Python 버전·CPU의 인터넷 연결 환경에서 런타임뿐 아니라 설치용 build 도구도 준비합니다.

```bash
python -m pip wheel -r requirements.lock --wheel-dir wheelhouse
python -m pip wheel 'setuptools>=68' wheel --wheel-dir wheelhouse
```

프로젝트와 wheelhouse를 반입한 후 실행합니다. 운영체제의 Python/venv 패키지는 사내 배포판 저장소 또는 별도 설치 매체로 준비해야 합니다.

```bash
CLAIM_SYNC_WHEELHOUSE=/path/to/wheelhouse ./run.sh
```

```powershell
$env:CLAIM_SYNC_WHEELHOUSE = 'D:\wheelhouse'
.\run.bat
```

사내 PyPI mirror와 프록시는 pip 표준 설정(`PIP_INDEX_URL`, `HTTPS_PROXY` 등)을 사용할 수 있습니다. 인증 정보를 Git에 저장하지 마세요. `.env`는 없을 때만 예제를 복사하며 설치 재시도·재실행 시 원래 파일을 덮어쓰지 않습니다.

## 사내 저장소 호환 설치

`annotated-doc==0.0.5` 등 고정한 버전이 사내 저장소에 없으면 기본 `auto` 모드가 호환 설치로 전환합니다. `run.bat`와 `run.sh`가 같은 설치 모듈을 사용하므로 두 환경에 모두 적용됩니다. 바로 호환 설치를 시작하려면 Windows PowerShell에서 실행합니다.

```powershell
$env:CLAIM_SYNC_INSTALL_MODE = 'compatible'
.\run.bat
```

기존 pip 설정(`pip.ini`, `PIP_CONFIG_FILE`, `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, `PIP_PROXY`, `HTTPS_PROXY`, `PIP_CERT` 등)을 모든 조회·다운로드에 그대로 사용합니다. 설치 프로그램이 공개 PyPI 주소를 추가하거나 프록시를 우회하지 않습니다. 앞서 터미널에서 공개 PyPI 주소를 임시로 지정했다면 새 터미널을 열어 기존 사내 pip 설정을 사용하세요. 사내 저장소/프록시 주소를 직접 지정해야 하는 경우 아래 예시 주소를 실제 값으로 바꿉니다.

```powershell
$env:PIP_INDEX_URL = 'https://packages.company.example/simple'
$env:PIP_PROXY = 'http://proxy.company.example:8080'
$env:CLAIM_SYNC_INSTALL_MODE = 'compatible'
.\run.bat --setup-only
```

사내 저장소에 직접 연결하는 환경이라면 프록시 변수는 설정하지 않아도 됩니다. 설치용 변수는 앱의 `.env`가 아닌 **터미널 환경변수 또는 pip 설정**에 지정합니다.

호환 설치 과정:

1. `pyproject.toml`의 지원 범위로 pip의 의존성 해석기를 실행합니다. `--dry-run --ignore-installed --report`로 현재 저장소의 후보와 하위 의존성을 함께 비교합니다. 예를 들어 최신 FastAPI가 필요한 `annotated-doc`을 찾지 못하면, 지원 범위 안에서 그 패키지가 필요 없는 이전 FastAPI도 검토합니다.
2. Python 버전·운영체제에 맞는 wheel이 있는 조합을 선택하고 이름·버전을 콘솔에 출력합니다. Windows에서 C/Rust 빌드 도구 설치를 요구하지 않도록 호환 런타임 조회는 wheel 파일로 제한합니다.
3. 선택한 버전을 설치하고 `pip check`로 충돌을 검사합니다. 새로 준비하거나 복구한 환경은 임시 DB에서 가상 Claim 6건의 조회·제품 보강·전송, 웹 응답과 OpenAPI 생성도 확인합니다. 이 검사는 사용자 `.env`, 실제 DB와 사내 API를 사용하지 않습니다.
4. 성공한 조합을 `.venv/claim-sync-resolved.lock`, 설치 상태를 `.venv/.claim-sync-setup.json`에 기록합니다. 원본 pip 보고서는 삭제하며 인증 URL은 기록하지 않습니다. 다음 실행은 이 버전을 재사용하고 누락 시 같은 버전으로 복구합니다. 그 버전도 더 이상 제공되지 않으면 다시 호환 조합을 찾습니다.

패키지 요구사항이나 설치·검증 모듈이 바뀌면 재검증합니다. 새 버전을 다시 선택하려면 앱을 종료한 뒤 `.venv/.claim-sync-setup.json` 파일만 삭제하고 `compatible` 모드로 실행하세요. 설치가 완료되지 않았거나 의존성·동작 검사가 실패하면 성공 상태를 저장하지 않고 앱을 시작하지 않습니다.

호환 범위는 버전 번호만으로 모든 조합의 정상 동작을 보장하지 않으므로 선택한 환경에서 실제 동작 검사도 수행합니다. 범위 내 모든 후보가 없거나 프록시·SSL 연결이 실패하면 자동 설치도 완료할 수 없습니다. 콘솔의 마지막 누락 패키지/의존성 충돌 메시지를 확인하고 사내 저장소에 필요한 wheel을 등록하세요. `from versions: none`만으로 버전 미등록과 접속 실패를 구분할 수는 없습니다.

호환 조회에는 pip 22.2 이상이 필요하며 더 오래된 pip는 같은 저장소에서 업그레이드를 시도합니다. `CLAIM_SYNC_WHEELHOUSE`가 설정되어 있으면 호환 조회와 다운로드도 해당 폴더만 사용합니다. `locked` 모드는 기존 `requirements.lock`과 정확히 일치하는 버전만 허용하며 자동 대체하지 않습니다.

조회 방식은 pip 공식 문서의 [설치 결과 보고서](https://pip.pypa.io/en/stable/reference/installation-report/)와 [의존성 해석](https://pip.pypa.io/en/stable/topics/dependency-resolution/)을 따릅니다.

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

### 코드만 내려받는 업데이트

Windows에서는 앱을 종료한 뒤 프로젝트의 `update.bat`를 더블클릭하거나 PowerShell에서 실행합니다.

```powershell
.\update.bat
.\run.bat
```

Linux에서는 웹/worker 서비스를 중지한 상태에서 `./update.sh`를 실행한 다음 `./run.sh --setup-only`로 패키지를 확인하고 서비스를 다시 시작합니다. 실행 파일은 호출한 터미널의 위치에 관계없이 자신이 들어 있는 프로젝트 폴더만 업데이트합니다. Python 3.11 이상과 Git이 필요하며, Git clone으로 받은 `main` checkout에서 사용합니다. ZIP 다운로드 폴더나 다른 브랜치에서는 덮어쓰지 않고 종료합니다.

일반 `git pull`은 로컬 수정·커밋에 따라 충돌 또는 병합이 필요할 수 있으므로, 이 스크립트는 **`git fetch origin refs/heads/main` 후 해당 커밋으로 `git reset --hard`**를 수행합니다. 다운로드가 성공하고 변경 대상 검사가 끝난 뒤에만 코드가 교체됩니다. **Git 관리 대상 파일의 로컬 수정, staging 상태와 로컬 커밋은 원격 최신 상태로 덮어씁니다.**

- 업로드 동작은 없습니다. 업데이트 모듈은 허용한 Git 명령만 실행하며 `commit`, `push`, `merge`, `clean`은 허용하지 않습니다. 로컬 Git hook과 자동 유지보수도 비활성화합니다.
- `.env`, `.env.*` 사용자 설정, 기본 `data/`, `.venv/`, `wheelhouse/`는 보존합니다. `.env.example`은 코드와 함께 최신 템플릿으로 갱신됩니다. 기존 Git 무시 파일·디렉터리와 충돌하는 원격 파일이 있어도 업데이트를 중단합니다.
- Git이 관리하지 않는 일반 파일은 전체 삭제하지 않습니다. 다만 원격 코드와 경로가 겹치는 일반 비관리 파일은 교체될 수 있습니다. 별도 데이터 폴더를 프로젝트 안에 사용한다면 `.gitignore`에 등록하세요.
- 로컬 설정·데이터가 이미 Git에 등록돼 있거나 새 원격 코드가 해당 경로를 포함하면 보호를 위해 중단합니다. 다운로드 실패 시 기존 코드와 로컬 수정은 유지합니다.
- 패키지 설치나 앱/worker 시작·종료는 수행하지 않습니다. 업데이트 후 기존 실행 파일이나 서비스 관리 명령으로 재시작하세요.
- 기존 Git 인증·프록시 설정을 사용합니다. 원격 주소나 인증 설정을 변경하지 않습니다. `CLAIM_SYNC_PYTHON`으로 Python을 지정할 수 있으며 `CLAIM_SYNC_NO_PAUSE=1`이면 Windows 완료 화면의 키 입력 대기를 생략합니다.

### 실행 중인 서비스의 설정 변경

`.env`는 프로세스 시작 시 읽습니다. 변경 후 두 서비스를 재시작하세요. 웹과 worker는 같은 환경·전송 대상·DATA_DIR를 사용해야 합니다.

Claim 시작일 파라미터는 `rcvDateFrom`입니다. 기존 `.env`에 `CLAIMS_FROM_PARAM`이 있으면 `CLAIMS_FROM_PARAM=rcvDateFrom`으로 맞추고 재시작하세요. 업데이트 스크립트는 사용자 `.env`를 보존하므로 코드만 갱신해도 기존 설정값이 자동으로 바뀌지는 않습니다.

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
- 기본 `live` 모드는 설정된 API로 HTTP 요청을 보냅니다. `APP_MODE=mock`을 별도로 지정한 개발·테스트 환경만 내장 가상 API를 사용합니다.

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

### HTTP 오류 상세 확인

웹의 **실행 이력 → 해당 실행 → 처리 로그**에서 `오류만`을 선택하면 실패한 요청과 재시도 원인을 확인할 수 있습니다. 개별 레코드의 상세 화면과 전송 JSON 내보내기에도 해당 오류가 포함됩니다. **API 연결 및 매핑 → 연결 확인** 결과에도 같은 형식으로 표시합니다.

HTTP 오류에는 아래 정보가 저장됩니다.

- 실패 단계, GET/POST 방식, **실제로 요청한 전체 URL**. Claim 조회의 `rcvDateFrom`, `rcvDateTo`, `limit`와 제품 조회의 인코딩된 part ID 경로를 포함합니다.
- HTTP 상태 코드와 상태 설명, 응답 Content-Type.
- 서버가 제공한 `x-request-id`, `x-correlation-id`, `traceparent`. API 담당자가 서버 로그와 대조할 때 사용할 수 있습니다.
- 서버 응답 본문. JSON은 읽기 쉽게 표시하고 HTML·일반 텍스트 오류도 보존합니다. 오류 응답은 최대 8 KiB를 읽고, 마스킹 후 최대 4,096자까지 기록하며 초과분은 `[응답 일부 생략]`으로 표시합니다.
- 연결 실패·시간 초과의 예외 종류와 원인, GET 재시도 횟수. POST의 자동 재전송 보류 여부도 표시합니다.

예시:

```text
Claim: HTTP 400
요청: GET http://12.81.220.37:8080/api/searchFlashClaims?rcvDateFrom=2025-01-01&rcvDateTo=2025-01-31&limit=1000
상태: HTTP 400 Bad Request
content-type: application/json
서버 응답:
{
  "detail": "Invalid date range"
}
```

요청 헤더 전체와 전송 데이터 전체는 오류 메시지에 추가하지 않습니다. 설정한 인증 헤더 값이 응답에 그대로 되돌아오면 가리며, 토큰·비밀번호·쿠키·API key 항목과 URL 인증 정보도 `[REDACTED]`로 표시합니다. 일반 업무 필드와 서버 메시지는 원인 파악을 위해 남깁니다.

작업 오류는 `DATA_DIR/claim-sync.sqlite3`의 `events`, `chunks.error`, `records.error` 등에 저장되므로 웹을 종료해도 보존되고 Ubuntu worker에도 동일하게 적용됩니다. 이전 버전이 상태 코드만 저장했던 과거 오류의 URL·응답은 복원되지 않습니다. 업데이트 후 다시 실행해 새 오류를 확인하세요.

| 상황 | 조치 |
| --- | --- |
| 실행기 연결 대기 | worker 프로세스, DATA_DIR, 파일 권한, heartbeat 확인 |
| schema 해석 실패 | 실제 schema 형태와 필수 필드 대조. 지원 형태가 아니면 `clients.py` 어댑터 확장 필요 |
| Claim/제품 JSON 경로 오류 | `.env`의 응답 경로 설정 변경 |
| 하루치 limit 도달 | API 담당자에게 pagination/시각 단위/더 높은 검증된 한도 요청 |
| HTTP 400/422 | 오류의 요청 URL·조회 조건과 서버 응답의 필드 오류 확인. 전송 오류는 해당 레코드의 전송 JSON과 비교 |
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
