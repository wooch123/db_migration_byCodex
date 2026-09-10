import json

import httpx
import pytest
from fastapi.testclient import TestClient

from claim_sync.clients import AmbiguousDelivery, APIClients, UpstreamError
from claim_sync.diagnostics import ERROR_BODY_BYTES, RequestDiagnostics
from claim_sync.engine import Engine
from claim_sync.mapping import PRODUCT_FIELDS
from claim_sync.web import create_app


@pytest.mark.parametrize("endpoint", ["claims", "schema", "product", "target"])
async def test_http_400_includes_actual_method_url_and_server_error(settings, store, endpoint):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            400,
            json={"detail": "잘못된 날짜 형식", "field": "rcvDateFrom"},
            headers={
                "x-request-id": "server-req-123",
                "set-cookie": "session=must-not-log",
            },
        )

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(UpstreamError) as error:
            if endpoint == "claims":
                await api.claims("2025-01-01", "2025-01-31")
            elif endpoint == "schema":
                await api.schema()
            elif endpoint == "product":
                await api.product("ABC/한 글?#")
            else:
                await api.send({"far_no": "FAR-1"}, "fingerprint")
    message = str(error.value)
    assert len(calls) == 1
    assert f"요청: {calls[0].method} {calls[0].url}" in message.splitlines()
    assert "HTTP 400 Bad Request" in message
    assert "잘못된 날짜 형식" in message and "rcvDateFrom" in message
    assert "x-request-id: server-req-123" in message
    assert "must-not-log" not in message
    if endpoint == "claims":
        assert "rcvDateFrom=2025-01-01&rcvDateTo=2025-01-31&limit=1000" in message
    if endpoint == "target":
        assert not isinstance(error.value, AmbiguousDelivery)


async def test_retry_records_each_failed_url_and_response(settings, store):
    settings.get_retries = 1
    events, calls = [], []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, text=f"service unavailable attempt {len(calls)}")

    async with APIClients(settings, store, lambda *e: events.append(e), httpx.MockTransport(respond)) as api:
        with pytest.raises(UpstreamError) as error:
            await api.claims("2025-01-01", "2025-01-02")
    assert len(calls) == 2 and len(events) == 1
    assert str(calls[0].url) in events[0][2]
    assert "service unavailable attempt 1" in events[0][2]
    assert "조회 재시도 1/1" in events[0][2]
    assert str(calls[1].url) in str(error.value)
    assert "service unavailable attempt 2" in str(error.value)
    assert "총 2회 요청" in str(error.value)


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_timeout_includes_url_and_cause_without_retrying_post(settings, store, method):
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ConnectTimeout("proxy connection timed out", request=request)

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(AmbiguousDelivery if method == "POST" else UpstreamError) as error:
            if method == "GET":
                await api.claims("2025-01-01", "2025-01-02")
            else:
                await api.send({"far_no": "FAR-1"}, "fingerprint")
    assert len(calls) == 1
    assert f"요청: {method} {calls[0].url}" in str(error.value)
    assert "ConnectTimeout: proxy connection timed out" in str(error.value)


@pytest.mark.parametrize("json_body", [True, False])
async def test_diagnostics_redact_echoed_secrets(settings, store, json_body):
    settings.claims_headers = {
        "Authorization": "Bearer header-private-123",
        "X-Custom-Auth": "custom-private-456",
        "Cookie": "session=cookie-private-789",
        "Accept": "application/json",
    }
    body = {
        "detail": "invalid request header-private-123 custom-private-456 cookie-private-789 query-private-xyz",
        "nested": {"access_token": "server-private-token", "password": "server-password"},
        "list": [{"api_key": "server-key"}],
    }
    text_body = (
        "invalid request Bearer header-private-123 custom-private-456 cookie-private-789 query-private-xyz "
        "password=server-password&api_key=server-key https://user:proxy-secret@proxy.local "
        '"access_token": "server-private-token"'
    )

    def respond(request):
        return httpx.Response(400, **({"json": body} if json_body else {"text": text_body}))

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(UpstreamError) as error:
            await api.get(
                "Claim",
                "http://claims.local/claims",
                settings.claims_headers,
                {
                    "rcvDateFrom": "2025-01-01",
                    "access_token": "query-private-xyz",
                },
            )
    message = str(error.value)
    for secret in (
        "header-private-123",
        "custom-private-456",
        "cookie-private-789",
        "query-private-xyz",
        "server-private-token",
        "server-password",
        "server-key",
        "proxy-secret",
    ):
        assert secret not in message
    assert "http://claims.local/claims?rcvDateFrom=2025-01-01" in message
    assert "[REDACTED]" in message and "invalid request" in message
    assert "상태: HTTP 400" in message


async def test_error_preview_bounded_and_truncation_marked(settings, store):
    reads = []

    class LargeBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(1000):
                reads.append(1)
                yield b"x" * 1024

    transport = httpx.MockTransport(lambda _: httpx.Response(400, stream=LargeBody()))
    async with APIClients(settings, store, lambda *_: None, transport) as api:
        with pytest.raises(UpstreamError) as error:
            await api.claims("2025-01-01", "2025-01-02")
    assert len(reads) <= ERROR_BODY_BYTES // 1024 + 1
    assert len(str(error.value)) < 5000
    assert "[응답 일부 생략]" in str(error.value)


@pytest.mark.parametrize("status", [400, 503])
async def test_broken_error_body_preserves_known_status(settings, store, status):
    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 1024
            raise httpx.ReadError("lost connection")

    transport = httpx.MockTransport(lambda _: httpx.Response(status, stream=BrokenBody()))
    async with APIClients(settings, store, lambda *_: None, transport) as api:
        with pytest.raises(UpstreamError) as error:
            await api.send({"far_no": "FAR-1"}, "fingerprint")
    assert isinstance(error.value, AmbiguousDelivery) == (status == 503)
    assert f"HTTP {status}" in str(error.value)
    assert "응답 본문 수신 실패: ReadError" in str(error.value)
    assert settings.target_url in str(error.value)


@pytest.mark.parametrize("endpoint", ["claims", "target"])
async def test_errors_persist_and_are_exposed_in_web_history(settings, store, spec, claim, product, endpoint):
    settings.enable_runner = False
    settings.target_headers = {"Authorization": "Bearer private-echo"}
    spec.end_date = spec.start_date
    failed_urls = []

    def respond(request):
        if (endpoint == "claims" and "searchFlashClaims" in request.url.path) or request.method == "POST":
            failed_urls.append(str(request.url))
            return httpx.Response(400, json={"detail": "invalid field private-echo"})
        if "schema" in request.url.path:
            return httpx.Response(200, json={"properties": dict.fromkeys(PRODUCT_FIELDS, {})})
        return httpx.Response(200, json=[claim] if "searchFlashClaims" in request.url.path else product)

    job_id = store.enqueue(spec, settings.destination)
    await Engine(settings, store, httpx.MockTransport(respond)).run(job_id)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "failed"
        errors = [e["message"] for e in job["events"] if e["level"] == "error"]
        assert any(failed_urls[0] in e and "HTTP 400" in e and "invalid field" in e for e in errors)
        assert "private-echo" not in json.dumps(job)
        if endpoint == "claims":
            assert failed_urls[0] in job["chunks"][0]["error"]
        else:
            rows = client.get(f"/api/jobs/{job_id}/records").json()["items"]
            assert failed_urls[0] in rows[0]["error"]
            exported = json.loads(client.get(f"/api/jobs/{job_id}/export").text.splitlines()[0])
            assert failed_urls[0] in exported["error"]


def test_url_diagnostics_preserve_encoding_and_repeated_query_values():
    request = httpx.Request("GET", "http://user:secret@host.local/ABC%2F123?day=1&day=2&api_key=hide%2Bme")
    message = RequestDiagnostics().message("조회", request, "실패")
    assert "http://host.local/ABC%2F123?day=1&day=2&api_key=[REDACTED]" in message
    assert "user" not in message and "secret" not in message and "hide" not in message


@pytest.mark.parametrize(
    "method,content",
    [
        ("GET", b"<html>gateway login required</html>"),
        ("POST", b"<html>gateway login required</html>"),
        ("POST", b'{"success": false, "error": "unknown far_no"}'),
    ],
)
async def test_invalid_success_response_includes_url_status_and_body(settings, store, method, content):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    async with APIClients(settings, store, lambda *_: None, transport) as api:
        with pytest.raises(AmbiguousDelivery if method == "POST" else UpstreamError) as error:
            if method == "GET":
                await api.claims("2025-01-01", "2025-01-02")
            else:
                await api.send({"far_no": "FAR-1"}, "fingerprint")
    message = str(error.value)
    assert f"요청: {method} http" in message and "HTTP 200" in message
    assert "unknown far_no" in message or "gateway login required" in message
