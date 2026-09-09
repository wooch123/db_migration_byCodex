"""Exercise real pip backtracking through a local HTTP proxy serving a restricted index."""

import importlib.util
import io
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("resolver_bootstrap", REPO / "scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def wheel(name, version, requires=()):
    normalized = name.replace("-", "_")
    filename = f"{normalized}-{version}-py3-none-any.whl"
    prefix = f"{normalized}-{version}.dist-info"
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(
            f"{prefix}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires),
        )
        archive.writestr(f"{prefix}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{prefix}/RECORD", "")
    return filename, content.getvalue()


@pytest.fixture
def restricted_proxy(tmp_path, monkeypatch):
    packages = {
        "resolver-example": [
            wheel("resolver-example", "1.0.0", ["annotated-doc==0.0.5"]),
            wheel("resolver-example", "0.9.0", ["resolver-dependency>=1,<2"]),
        ],
        "resolver-dependency": [wheel("resolver-dependency", "1.0.0"), wheel("resolver-dependency", "2.0.0")],
    }
    files = dict(item for versions in packages.values() for item in versions)
    requests = []

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            parsed = urlsplit(self.path)
            assert parsed.hostname == "mirror.invalid", "Requests must use the configured proxy"
            path = parsed.path
            if path.startswith("/simple/"):
                name = path.removeprefix("/simple/").strip("/")
                payload = "".join(
                    f'<a href="http://mirror.invalid/files/{filename}">{filename}</a>'
                    for filename, _ in packages.get(name, [])
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            elif path.removeprefix("/files/") in files:
                payload = files[path.removeprefix("/files/")]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
            else:
                payload = b"not found"
                self.send_response(404)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for key in list(os.environ):
        if key.upper().startswith("PIP_") or key.upper() in {
            "NO_PROXY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        }:
            monkeypatch.delenv(key)
    monkeypatch.delenv("CLAIM_SYNC_WHEELHOUSE", raising=False)
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("PIP_INDEX_URL", "http://mirror.invalid/simple")
    monkeypatch.setenv("PIP_PROXY", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("PIP_TRUSTED_HOST", "mirror.invalid")  # HTTP fixture only; no real TLS bypass.
    monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")
    monkeypatch.setattr(bootstrap, "ENV_DIR", tmp_path)
    monkeypatch.setattr(bootstrap, "ENV_PYTHON", Path(sys.executable))
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_resolver_backtracks_when_proxy_has_no_annotated_doc(restricted_proxy):
    selected = bootstrap.resolve_compatible(
        {"dependencies": ["resolver-example>=0.9,<2"]}, {"pip_version": "24.0"}
    )
    assert selected == {"resolver-example": "0.9.0", "resolver-dependency": "1.0.0"}
    assert any("/simple/annotated-doc/" in path for path in restricted_proxy)
    assert "annotated-doc" not in json.dumps(selected)


def test_resolver_stops_when_proxy_has_no_compatible_combination(restricted_proxy):
    with pytest.raises(bootstrap.PackageInstallError, match="No compatible package set"):
        bootstrap.resolve_compatible({"dependencies": ["resolver-example>=1,<2"]}, {"pip_version": "24.0"})
