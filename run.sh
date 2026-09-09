#!/usr/bin/env bash
set -eu

CLAIM_SYNC_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$CLAIM_SYNC_ROOT"
export PYTHONUTF8=1
CLAIM_SYNC_BOOTSTRAP_PYTHON=""

find_python() {
    local candidate
    if [[ -n "${CLAIM_SYNC_PYTHON:-}" ]]; then
        if "$CLAIM_SYNC_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
            CLAIM_SYNC_BOOTSTRAP_PYTHON="$CLAIM_SYNC_PYTHON"
            return 0
        fi
        echo '[error] CLAIM_SYNC_PYTHON must point to a working Python 3.11+ executable.' >&2
        exit 1
    fi
    for candidate in "$CLAIM_SYNC_ROOT/.venv/bin/python" python3 python3.13 python3.12 python3.11 python; do
        if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
            CLAIM_SYNC_BOOTSTRAP_PYTHON="$candidate"
            return 0
        fi
    done
    return 1
}

install_system_packages() {
    if [[ "${CLAIM_SYNC_NO_SYSTEM_INSTALL:-0}" == "1" ]] || ! command -v apt-get >/dev/null 2>&1; then
        echo '[error] Install Python 3.11+ with venv support, then rerun this script.' >&2
        echo 'Automatic system installation is supported on Ubuntu/Debian with apt.' >&2
        exit 1
    fi
    local -a elevate=()
    if [[ "$(id -u)" != "0" ]]; then
        if ! command -v sudo >/dev/null 2>&1; then
            echo '[error] Installing system packages requires root or sudo.' >&2
            exit 1
        fi
        elevate=(sudo)
    fi
    echo '[setup] Installing required system packages with apt (sudo may ask for your password)...'
    "${elevate[@]}" apt-get update
    "${elevate[@]}" apt-get install -y "$@"
}

if ! find_python; then
    install_system_packages python3 python3-venv
    if ! find_python; then
        echo '[error] This distribution supplies an older Python. Install Python 3.11+ and set CLAIM_SYNC_PYTHON.' >&2
        exit 1
    fi
fi

CLAIM_SYNC_SETUP_STATUS=0
"$CLAIM_SYNC_BOOTSTRAP_PYTHON" "$CLAIM_SYNC_ROOT/scripts/bootstrap.py" || CLAIM_SYNC_SETUP_STATUS=$?
if [[ "$CLAIM_SYNC_SETUP_STATUS" == "20" ]]; then
    CLAIM_SYNC_PYTHON_VERSION="$("$CLAIM_SYNC_BOOTSTRAP_PYTHON" -c 'import sys; print("%s.%s" % sys.version_info[:2])')"
    install_system_packages "python${CLAIM_SYNC_PYTHON_VERSION}-venv"
    "$CLAIM_SYNC_BOOTSTRAP_PYTHON" "$CLAIM_SYNC_ROOT/scripts/bootstrap.py"
elif [[ "$CLAIM_SYNC_SETUP_STATUS" != "0" ]]; then
    exit "$CLAIM_SYNC_SETUP_STATUS"
fi

if [[ "${1:-}" == "--setup-only" ]]; then
    if [[ "$#" != "1" ]]; then
        echo '[error] --setup-only does not accept additional arguments.' >&2
        exit 1
    fi
    exit 0
fi
if [[ "$#" == "0" ]]; then
    echo '[run] Starting the web console. Press Ctrl+C to stop.'
    set -- web
fi
# Replace the shell so signals go directly to the web server or worker.
exec "$CLAIM_SYNC_ROOT/.venv/bin/python" -m claim_sync.cli "$@"
