import base64
import json
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from claim_sync.clients import APIClients, UpstreamError

REPO = Path(__file__).resolve().parent.parent
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows built-in PFX conversion tool")


@pytest.mark.parametrize("suffix", [".pfx", ".P12"])
def test_direct_pfx_path_explains_conversion(settings, store, tmp_path, suffix):
    settings.ca_bundle = str(tmp_path / f"corporate{suffix}")
    with pytest.raises(UpstreamError, match="export-ca.bat"):
        APIClients(settings, store, lambda *_: None)


@pytest.mark.parametrize("exists", [False, True])
def test_invalid_ca_bundle_reports_file_path_and_reason(settings, store, tmp_path, exists):
    path = tmp_path / "corporate-ca.pem"
    if exists:
        path.write_text("invalid certificate", encoding="utf-8")
    settings.ca_bundle = str(path)
    with pytest.raises(UpstreamError) as error:
        APIClients(settings, store, lambda *_: None)
    assert str(path) in str(error.value)
    assert ("SSLError" if exists else "FileNotFoundError") in str(error.value)


def run_powershell(command, **variables):
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    env = dict(os.environ, **{name: str(value) for name, value in variables.items()})
    return subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


@pytest.fixture(scope="module")
def ca_files(tmp_path_factory):
    directory = tmp_path_factory.mktemp("CA files") / "사내 CA & !"
    directory.mkdir()
    result = run_powershell(
        "& $env:TEST_CA_FIXTURE -OutputDirectory $env:TEST_CA_DIR",
        TEST_CA_FIXTURE=REPO / "tests/fixtures/make_test_ca.ps1",
        TEST_CA_DIR=directory,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return directory


def convert(ca_files, output, filename="chain.pfx", password="test-only-password", force=False):
    return run_powershell(
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$password = [Security.SecureString]::new(); "
        "$env:TEST_CA_PASSWORD.ToCharArray() | ForEach-Object { $password.AppendChar($_) }; "
        "& $env:TEST_CA_SCRIPT -PfxPath $env:TEST_CA_INPUT -OutputPath $env:TEST_CA_OUTPUT "
        "-Password $password -Force:($env:TEST_CA_FORCE -eq '1')",
        TEST_CA_SCRIPT=REPO / "scripts/export_ca.ps1",
        TEST_CA_INPUT=ca_files / filename,
        TEST_CA_OUTPUT=output,
        TEST_CA_PASSWORD=password,  # Synthetic test password only; the real tool prompts locally.
        TEST_CA_FORCE="1" if force else "0",
    )


@WINDOWS
@pytest.mark.parametrize("filename,password", [("chain.pfx", "test-only-password"), ("public-only.pfx", "")])
def test_export_ca_only_from_pfx_with_or_without_private_keys(ca_files, tmp_path, filename, password):
    output = tmp_path / "사내 CA & !" / "bundle.pem"
    original = (ca_files / filename).read_bytes()
    result = convert(ca_files, output, filename, password)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (ca_files / filename).read_bytes() == original
    pem = output.read_text(encoding="ascii")
    assert pem.count("-----BEGIN CERTIFICATE-----") == 2
    assert "PRIVATE KEY" not in pem
    data = json.loads((ca_files / "certificates.json").read_text(encoding="utf-8-sig"))
    context = ssl.create_default_context(cafile=str(output))
    assert set(context.get_ca_certs(binary_form=True)) == {
        base64.b64decode(data["root"]),
        base64.b64decode(data["intermediate"]),
    }
    assert "CA_BUNDLE=" in result.stdout and "TLS_VERIFY=true" in result.stdout
    assert password == "" or password not in result.stdout + result.stderr


@WINDOWS
def test_failed_conversion_preserves_existing_files(ca_files, tmp_path):
    output = tmp_path / "existing.pem"
    output.write_bytes(b"preserve existing CA")
    for filename, password, force, message in [
        ("chain.pfx", "test-only-password", False, "already exists"),
        ("chain.pfx", "wrong-test-password", True, "Cannot open PFX"),
        ("leaf-only.pfx", "test-only-password", True, "No CA certificates"),
    ]:
        result = convert(ca_files, output, filename, password, force)
        assert result.returncode == 1
        assert message in result.stderr
        assert password not in result.stdout + result.stderr
        assert output.read_bytes() == b"preserve existing CA"
    original = (ca_files / "chain.pfx").read_bytes()
    same_path = convert(ca_files, ca_files / "chain.pfx", force=True)
    assert same_path.returncode == 1 and "must be different" in same_path.stderr
    assert (ca_files / "chain.pfx").read_bytes() == original
    replacement = convert(ca_files, output, force=True)
    assert replacement.returncode == 0, replacement.stderr
    assert ssl.create_default_context(cafile=str(output)).cert_store_stats()["x509_ca"] == 2
    assert not list(tmp_path.glob("*.tmp"))


@WINDOWS
def test_default_output_is_under_project_even_from_another_directory(ca_files, tmp_path):
    project = tmp_path / "프로젝트 & ! [test]"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "export_ca.ps1"
    shutil.copyfile(REPO / "scripts/export_ca.ps1", script)
    result = run_powershell(
        "$password = [Security.SecureString]::new(); "
        "'test-only-password'.ToCharArray() | ForEach-Object { $password.AppendChar($_) }; "
        "& $env:TEST_CA_SCRIPT -PfxPath $env:TEST_CA_INPUT -Password $password",
        TEST_CA_SCRIPT=script,
        TEST_CA_INPUT=ca_files / "chain.pfx",
    )
    assert result.returncode == 0, result.stderr
    output = project / "certs/corporate-ca.pem"
    assert ssl.create_default_context(cafile=str(output)).cert_store_stats()["x509_ca"] == 2


def rsa_key_pem(parameters):
    """Encode the generated throwaway test key as PKCS#1 using only the stdlib."""

    def der(tag, value):
        size = len(value)
        length = bytes([size]) if size < 128 else bytes([0x82]) + size.to_bytes(2, "big")
        return bytes([tag]) + length + value

    integers = der(2, b"\0")
    for name in ("Modulus", "Exponent", "D", "P", "Q", "DP", "DQ", "InverseQ"):
        value = base64.b64decode(parameters[name]).lstrip(b"\0") or b"\0"
        if value[0] & 0x80:
            value = b"\0" + value
        integers += der(2, value)
    encoded = base64.encodebytes(der(0x30, integers)).decode("ascii")
    return f"-----BEGIN RSA PRIVATE KEY-----\n{encoded}-----END RSA PRIVATE KEY-----\n"


@WINDOWS
async def test_exported_ca_enables_verified_https_in_application(ca_files, tmp_path, settings, store):
    output = tmp_path / "bundle.pem"
    result = convert(ca_files, output)
    assert result.returncode == 0, result.stderr
    data = json.loads((ca_files / "certificates.json").read_text(encoding="utf-8-sig"))
    server_cert = tmp_path / "server.pem"
    server_cert.write_text(
        "".join(ssl.DER_cert_to_PEM_cert(base64.b64decode(data[key])) for key in ("leaf", "intermediate")),
        encoding="ascii",
    )
    server_key = tmp_path / "server.key"
    server_key.write_text(rsa_key_pem(data["key"]), encoding="ascii")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(server_cert, server_key)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"trusted":true}')

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"https://127.0.0.1:{server.server_port}/"
            settings.app_mode = "live"
            settings.tls_verify = True
            async with APIClients(settings, store, lambda *_: None) as api:
                with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                    await api.client.get(url)
            settings.ca_bundle = str(output)
            async with APIClients(settings, store, lambda *_: None) as api:
                response = await api.client.get(url)
                assert response.status_code == 200 and response.json() == {"trusted": True}
        finally:
            server.shutdown()
            thread.join(timeout=5)


@WINDOWS
def test_batch_launcher_propagates_failure_without_pausing(tmp_path):
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(REPO / "export-ca.bat"), str(tmp_path / "missing.pfx")],
        env=dict(os.environ, CLAIM_SYNC_NO_PAUSE="1"),
        capture_output=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 1 and b"[error]" in result.stderr
