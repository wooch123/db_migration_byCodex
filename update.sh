#!/usr/bin/env bash
set -eu
CLAIM_SYNC_UPDATE_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${CLAIM_SYNC_PYTHON:-}" ]]; then
    if ! "$CLAIM_SYNC_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        echo '[error] CLAIM_SYNC_PYTHON must select Python 3.11+.' >&2
        exit 1
    fi
    exec "$CLAIM_SYNC_PYTHON" "$CLAIM_SYNC_UPDATE_ROOT/scripts/update.py" "$@"
fi
for candidate in "$CLAIM_SYNC_UPDATE_ROOT/.venv/bin/python" python3 python; do
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        exec "$candidate" "$CLAIM_SYNC_UPDATE_ROOT/scripts/update.py" "$@"
    fi
done
echo '[error] Python 3.11+ is required. Run ./run.sh first or set CLAIM_SYNC_PYTHON.' >&2
exit 1
