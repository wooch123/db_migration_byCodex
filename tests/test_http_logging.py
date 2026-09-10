import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from claim_sync.clients import APIClients, UpstreamError
from claim_sync.diagnostics import RequestDiagnostics
from claim_sync.engine import Engine
from claim_sync.http_log import HTTPExchange
from claim_sync.mapping import PRODUCT_FIELDS
from claim_sync.store import Store, encode, utcnow
from claim_sync.web import create_app


@pytest.mark.parametrize("status", [200, 400, 503])
async def test_request_response_details_survive_restart_and_web_export(settings, store, spec, status):
    settings.enable_runner = False
    settings.app_access_token = "console-secret"
    settings.target_headers = {"Authorization": "Bearer upstream-secret", "X-Tenant": "tenant-secret"}
    job_id = store.enqueue(spec, settings.destination)
    payload = {"far_no": "FAR-POST-001", "sample_no": "S1", "density": "2 TB"}
    body = {"success": status == 200, "message": "서버에서 반환한 상세 원인", "token": "server-secret"}
    calls = []

    def respond(request):
        calls.append(request)
        # The audit request must already exist when the transport starts.
        pending = store.one("SELECT * FROM http_exchanges ORDER BY id DESC LIMIT 1")
        assert pending["state"] == "sending"
        return httpx.Response(
            status, json=body, headers={"x-request-id": "request-001", "set-cookie": "session=server-cookie"}
        )

    async with APIClients(
        settings, store, lambda *_: None, httpx.MockTransport(respond), job_id=job_id
    ) as api:
        if status == 200:
            await api.send(payload, "operation-key", record_key='["FAR-POST-001","S1"]')
        else:
            with pytest.raises(UpstreamError):
                await api.send(payload, "operation-key", record_key='["FAR-POST-001","S1"]')
    assert len(calls) == 1
    store = Store(settings.data_dir)
    row = store.exchange(store.one("SELECT id FROM http_exchanges")["id"])
    d = row["details"]
    assert row["status_code"] == status
    assert row["duration_ms"] >= 0 and row["finished_at"]
    assert d["request"]["url"] == str(calls[0].url)
    assert json.loads(d["request"]["body"]) == json.loads(calls[0].content) == {"values": payload}
    assert d["request"]["headers"]["idempotency-key"] == "operation-key"
    assert json.loads(d["response"]["body"])["message"] == body["message"]
    assert d["response"]["headers"]["x-request-id"] == "request-001"
    for secret in ("console-secret", "upstream-secret", "tenant-secret", "server-secret", "server-cookie"):
        assert secret not in json.dumps(row)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get("/api/http-exchanges").status_code == 401
        assert client.get(f"/api/http-exchanges/{row['id']}").status_code == 401
        client.headers["Authorization"] = "Bearer console-secret"
        listing = client.get(f"/api/http-exchanges?job_id={job_id}").json()
        assert listing["total"] == 1 and "details" not in listing["items"][0]
        assert client.get(f"/api/http-exchanges/{row['id']}").json() == row
        assert client.get("/api/http-exchanges/999999").status_code == 404


async def test_get_query_response_retry_attempts_and_filters(settings, store, spec):
    settings.enable_runner = False
    settings.get_retries = 1
    job_id = store.enqueue(spec, settings.destination)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, json={"items": [{"farNo": "FAR-1"}]})

    async with APIClients(
        settings, store, lambda *_: None, httpx.MockTransport(respond), job_id=job_id
    ) as api:
        await api.claims("2025-01-01", "2025-01-02")
    rows = store.query("SELECT id FROM http_exchanges ORDER BY id")
    assert [store.exchange(r["id"])["details"]["attempt"] for r in rows] == [1, 2]
    second = store.exchange(rows[-1]["id"])["details"]
    assert second["request"]["query"] == [
        ["rcvDateFrom", "2025-01-01"],
        ["rcvDateTo", "2025-01-02"],
        ["limit", "1000"],
    ]
    assert second["request"]["body"] == ""
    assert json.loads(second["response"]["body"]) == {"items": [{"farNo": "FAR-1"}]}
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get(f"/api/http-exchanges?job_id={job_id}&errors_only=true").json()["total"] == 1
        listing = client.get("/api/http-exchanges?offset=1&limit=1").json()
        assert listing["total"] == 2 and listing["items"][0]["status_code"] == 503
        assert client.get("/api/http-exchanges?record_key=unknown").json()["total"] == 0
        assert client.get("/api/http-exchanges?checks_only=true").json()["total"] == 0


@pytest.mark.parametrize("legacy", [True, False])
async def test_blocked_retry_keeps_original_ssl_failure_payload_and_source_link(
    settings, store, spec, claim, product, legacy
):
    settings.enable_runner = False
    spec.end_date = spec.start_date
    posts = []

    def respond(request):
        if "schema" in request.url.path:
            return httpx.Response(200, json={"properties": dict.fromkeys(PRODUCT_FIELDS, {})})
        if "searchFlashClaims" in request.url.path:
            return httpx.Response(200, json=[claim])
        if request.method == "GET":
            return httpx.Response(200, json=product)
        posts.append(request)
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")

    first = store.enqueue(spec, settings.destination)
    await Engine(settings, store, httpx.MockTransport(respond)).run(first)
    original = store.one("SELECT * FROM http_exchanges WHERE method='POST'")
    assert original["status_code"] is None and original["state"] == "failed"
    if legacy:
        # Simulate an older installation that stored only the record and delivery note.
        store.execute("DELETE FROM http_exchanges")
    second = store.enqueue(spec, settings.destination)
    await Engine(settings, store, httpx.MockTransport(respond)).run(second)
    assert len(posts) == 1 and store.job(second)["status"] == "needs_attention"
    assert "CERTIFICATE_VERIFY_FAILED" in store.job(second)["error"]
    assert first in store.job(second)["error"]
    blocked = store.exchange(store.one("SELECT id FROM http_exchanges WHERE state='blocked'")["id"])
    assert blocked["status_code"] is None and blocked["details"]["response"] is None
    previous = blocked["details"]["original"]
    assert previous["job_id"] == first
    assert previous["exchange_id"] == (None if legacy else original["id"])
    assert json.loads(previous["request_body"]) == json.loads(posts[0].content)
    if not legacy:
        assert store.exchange(previous["exchange_id"])["details"]["response"] is None
    # A later delivery note must not overwrite the audit snapshot of the blocked execution.
    store.execute("UPDATE deliveries SET note='later operator note'")
    assert "CERTIFICATE_VERIFY_FAILED" in store.exchange(blocked["id"])["details"]["original"]["error"]


def test_legacy_blocked_job_exposes_original_delivery_without_fabricating_http_response(
    settings, store, spec
):
    settings.enable_runner = False
    first, second = [store.enqueue(spec, settings.destination) for _ in range(2)]
    key = '["FAR-1","S1"]'
    payload = {"far_no": "FAR-1", "sample_no": "S1"}
    store.save_delivery(
        settings.destination, key, "hash", payload, "uncertain", first, "ConnectError: original SSL error"
    )
    store.execute(
        "INSERT INTO records(job_id,record_key,status,payload,error,created_at) VALUES(?,?,'uncertain',?,?,?)",
        (second, key, encode(payload), "이 업무 키의 이전 전송 결과가 불확실합니다.", utcnow()),
    )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get(f"/api/http-exchanges?job_id={second}").json()["total"] == 0
        context = client.get(f"/api/jobs/{second}/delivery-context").json()
        assert len(context) == 1
        assert context[0]["job_id"] == first and context[0]["exchange_id"] is None
        assert context[0]["error"] == "ConnectError: original SSL error"
        assert json.loads(context[0]["request_body"]) == {"values": payload}
        assert "status_code" not in context[0]


async def test_bounded_response_body_and_known_status_after_midstream_failure(settings, store):
    settings.http_log_body_bytes = 1024

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 2048
            raise httpx.ReadError("response cut off")

    async with APIClients(
        settings,
        store,
        lambda *_: None,
        httpx.MockTransport(lambda _: httpx.Response(200, stream=BrokenBody())),
    ) as api:
        with pytest.raises(UpstreamError):
            await api.send({"far_no": "FAR-1"}, "operation")
    row = store.exchange(store.one("SELECT id FROM http_exchanges")["id"])
    assert row["status_code"] == 200
    assert row["details"]["response"]["body"] == "x" * 1024
    assert row["details"]["response"]["body_truncated"] is True
    assert row["details"]["response"]["received_bytes"] == 2048
    assert "ReadError" in row["details"]["error"]


def test_interrupted_attempt_preserves_request_and_received_status(settings, store):
    request = httpx.Request("POST", settings.target_url, json={"values": {"far_no": "FAR-1"}})
    log = HTTPExchange(
        store,
        RequestDiagnostics(),
        request,
        job_id="interrupted",
        record_key="key",
        stage="FAR 전송",
        attempt=1,
        limit=1024,
    )
    log.receive(httpx.Response(200, request=request))
    store.recover()
    row = Store(settings.data_dir).exchange(log.id)
    assert row["state"] == "interrupted" and row["status_code"] == 200
    assert json.loads(row["details"]["request"]["body"])["values"]["far_no"] == "FAR-1"
    assert "프로세스가 중단" in row["details"]["error"]


async def test_cancelled_attempt_persists_interrupted_state(settings, store):
    async def respond(request):
        raise asyncio.CancelledError()

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(asyncio.CancelledError):
            await api.send({"far_no": "FAR-1"}, "operation")
    row = store.exchange(store.one("SELECT id FROM http_exchanges")["id"])
    assert row["state"] == "interrupted" and row["status_code"] is None


def test_connection_checks_are_logged_separately(settings):
    settings.enable_runner = False
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.post("/api/check", json={}).status_code == 200
        listing = client.get("/api/http-exchanges?checks_only=true").json()
        assert listing["total"] == 2
        assert all(row["status_code"] == 200 and row["job_id"] is None for row in listing["items"])
