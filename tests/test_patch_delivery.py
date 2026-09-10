"""Exercise the server's duplicate-create contract without contacting production."""

import json

import httpx
import pytest

from claim_sync.clients import AmbiguousDelivery, APIClients, UpstreamError
from claim_sync.engine import Engine
from claim_sync.mapping import PRODUCT_FIELDS
from claim_sync.models import RunSpec
from claim_sync.store import Store


def duplicate_body(code="CREATE_FAIELD, UNIQUE", field="massage", constraint=None):
    return {
        "ok": False,
        "error": {
            "code": code,
            field: constraint or "UNIQUE constraint failed: far_tabl.far_no, far_table.sample_no",
        },
    }


def write_logs(store):
    return [
        store.exchange(row["id"])
        for row in store.query("SELECT id FROM http_exchanges WHERE method IN ('POST','PATCH') ORDER BY id")
    ]


@pytest.mark.parametrize(
    "code,field",
    [("CREATE_FAIELD, UNIQUE", "massage"), ("CREATE_FAILED, UNIQUE", "message")],
)
async def test_duplicate_create_sends_one_patch_with_exact_keys_values_and_audit(
    settings, store, spec, code, field
):
    settings.target_base_url = "https://target.example.test"
    settings.target_path = "/custom/far_table?tenant=example"
    settings.target_headers = {"Authorization": "Bearer target-secret", "X-Client": "client-secret"}
    job_id = store.enqueue(spec, settings.destination)
    values = {
        "far_no": "FAR-001",
        "sample_no": "S001",
        "part_id": "ABCDEFGHIJKLMNO",
        "density": "2 TB",
        "far_comp_date": None,
        "fail_symptom": "수정된 증상",
    }
    original_values = dict(values)
    body = duplicate_body(code, field)
    calls = []

    def respond(request):
        calls.append(request)
        return (
            httpx.Response(400, json=body)
            if request.method == "POST"
            else httpx.Response(200, json={"ok": True})
        )

    async with APIClients(
        settings, store, lambda *_: None, httpx.MockTransport(respond), job_id=job_id
    ) as api:
        await api.send(values, "create-operation", record_key='["FAR-001","S001"]')

    assert [request.method for request in calls] == ["POST", "PATCH"]
    assert all(str(request.url) == settings.target_url for request in calls)
    assert all(request.headers["Authorization"] == "Bearer target-secret" for request in calls)
    assert all(request.headers["X-Client"] == "client-secret" for request in calls)
    assert all(request.headers["Content-Type"] == "application/json" for request in calls)
    assert calls[0].headers["Idempotency-Key"] == "create-operation"
    assert calls[1].headers["Idempotency-Key"] != calls[0].headers["Idempotency-Key"]
    assert json.loads(calls[0].content) == {"values": original_values}
    assert json.loads(calls[1].content) == {
        "where": {"far_no": "FAR-001", "sample_no": "S001"},
        "values": {
            key: value for key, value in original_values.items() if key not in {"far_no", "sample_no"}
        },
    }
    assert values == original_values
    logs = write_logs(store)
    assert [(row["method"], row["status_code"], row["state"]) for row in logs] == [
        ("POST", 400, "failed"),
        ("PATCH", 200, "completed"),
    ]
    assert json.loads(logs[0]["details"]["response"]["body"]) == body
    assert json.loads(logs[1]["details"]["request"]["body"]) == json.loads(calls[1].content)
    assert all(row["job_id"] == job_id for row in logs)
    assert all(row["record_key"] == '["FAR-001","S001"]' for row in logs)
    assert "target-secret" not in json.dumps(logs) and "client-secret" not in json.dumps(logs)


@pytest.mark.parametrize(
    "status,body",
    [
        (409, duplicate_body()),
        (503, duplicate_body()),
        (400, duplicate_body(code="VALIDATION_FAILED")),
        (400, duplicate_body(constraint="UNIQUE constraint failed: far_table.ims_key")),
        (400, duplicate_body(constraint="UNIQUE constraint failed: far_table.far_no")),
        (400, duplicate_body(constraint="UNIQUE constraint failed: other.far_no, other.sample_no")),
        (
            400,
            duplicate_body(
                constraint="UNIQUE constraint failed: far_table.far_no, far_table.sample_no, far_table.ims_key"
            ),
        ),
        (400, {"ok": True, "error": duplicate_body()["error"]}),
        (400, {"error": duplicate_body()["error"]}),
        (400, {"ok": False, "error": "UNIQUE constraint failed: far_table.far_no, far_table.sample_no"}),
    ],
)
async def test_other_rejections_never_switch_to_patch(settings, store, status, body):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(UpstreamError):
            await api.send({"far_no": "FAR-1", "sample_no": "S1", "density": "1 TB"}, "operation")
    assert [request.method for request in calls] == ["POST"]


@pytest.mark.parametrize("key,value", [("far_no", None), ("sample_no", ""), ("sample_no", "   ")])
async def test_duplicate_never_patches_with_incomplete_where(settings, store, key, value):
    values = {"far_no": "FAR-1", "sample_no": "S1", "density": "1 TB"}
    if value is None:
        del values[key]
    else:
        values[key] = value
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(400, json=duplicate_body())

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(UpstreamError):
            await api.send(values, "operation")
    assert [request.method for request in calls] == ["POST"]


@pytest.mark.parametrize("kind", ["html", "truncated", "broken_stream", "timeout"])
async def test_unreadable_or_uncertain_create_response_never_patches(settings, store, kind):
    settings.http_log_body_bytes = 1024
    calls = []

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(duplicate_body()).encode()
            raise httpx.ReadError("body interrupted")

    def respond(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("create acknowledgement lost")
        if kind == "broken_stream":
            return httpx.Response(400, stream=BrokenBody())
        text = (
            json.dumps(duplicate_body()) + " " * 2048
            if kind == "truncated"
            else "<html>UNIQUE constraint failed: far_table.far_no, far_table.sample_no</html>"
        )
        return httpx.Response(400, text=text)

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(UpstreamError):
            await api.send({"far_no": "FAR-1", "sample_no": "S1", "density": "1 TB"}, "operation")
    assert [request.method for request in calls] == ["POST"]


def pipeline_transport(claim, product, patch_response):
    writes = []

    def respond(request):
        if "schema" in request.url.path:
            return httpx.Response(200, json={"properties": dict.fromkeys(PRODUCT_FIELDS, {})})
        if "searchFlashClaims" in request.url.path:
            return httpx.Response(200, json=[claim])
        if request.method == "GET":
            return httpx.Response(200, json=product)
        writes.append(request)
        if request.method == "POST":
            return httpx.Response(400, json=duplicate_body())
        if isinstance(patch_response, Exception):
            raise patch_response
        return patch_response

    return httpx.MockTransport(respond), writes


async def execute(settings, store, transport):
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    job_id = store.enqueue(spec, settings.destination)
    await Engine(settings, store, transport).run(job_id)
    return store.job(job_id)


async def test_successful_patch_updates_ledger_and_unchanged_run_skips(settings, store, claim, product):
    transport, writes = pipeline_transport(claim, product, httpx.Response(200, json={"ok": True}))
    first = await execute(settings, store, transport)
    assert first["status"] == "completed" and first["succeeded"] == 1
    assert store.one("SELECT status FROM deliveries")["status"] == "success"
    patch = json.loads(writes[1].content)
    assert patch["values"]["part_id"] == claim["partId"][:15]
    assert patch["values"]["density"] == product["denstiy"]
    assert patch["values"]["far_comp_date"] is None
    second = await execute(settings, Store(settings.data_dir), transport)
    assert second["status"] == "completed" and second["skipped"] == 1
    assert [request.method for request in writes] == ["POST", "PATCH"]


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_patch_rejection_records_actual_request_and_does_not_loop(
    settings, store, claim, product, status
):
    response = duplicate_body() if status == 400 else {"ok": False, "error": {"message": "수정 거절"}}
    transport, writes = pipeline_transport(claim, product, httpx.Response(status, json=response))
    job = await execute(settings, store, transport)
    assert job["status"] == "failed" and job["failed"] == 1
    assert store.one("SELECT status FROM deliveries")["status"] == "failed"
    assert [request.method for request in writes] == ["POST", "PATCH"]
    log = write_logs(store)[-1]
    assert log["method"] == "PATCH" and log["status_code"] == status and log["state"] == "failed"
    assert json.loads(log["details"]["request"]["body"]) == json.loads(writes[1].content)
    assert json.loads(log["details"]["response"]["body"]) == response
    error = store.one("SELECT error FROM records")["error"]
    assert f"HTTP {status}" in error and f"PATCH {settings.target_url}" in error


@pytest.mark.parametrize(
    "patch_response",
    [
        httpx.ReadTimeout("PATCH acknowledgement lost"),
        httpx.Response(503, json={"error": "upstream unavailable"}),
        httpx.Response(202, json={"ok": True}),
        httpx.Response(200, json={"ok": False}),
        httpx.Response(200, text="<html>proxy response</html>"),
    ],
    ids=["timeout", "server_error", "accepted_pending", "negative_ack", "non_json_ack"],
)
async def test_uncertain_patch_blocks_repeat_and_links_to_original_patch(
    settings, store, claim, product, patch_response
):
    transport, writes = pipeline_transport(claim, product, patch_response)
    first = await execute(settings, store, transport)
    assert first["status"] == "needs_attention" and first["uncertain"] == 1
    assert store.one("SELECT status FROM deliveries")["status"] == "uncertain"
    original = write_logs(store)[-1]
    assert original["method"] == "PATCH" and original["state"] == "failed"
    second = await execute(settings, Store(settings.data_dir), transport)
    assert second["status"] == "needs_attention" and second["uncertain"] == 1
    assert [request.method for request in writes] == ["POST", "PATCH"]
    blocked = write_logs(store)[-1]
    assert blocked["state"] == "blocked" and blocked["details"]["response"] is None
    previous = blocked["details"]["original"]
    assert previous["exchange_id"] == original["id"] and previous["job_id"] == first["id"]
    assert previous["method"] == "PATCH"
    assert json.loads(previous["request_body"]) == json.loads(writes[1].content)
    if isinstance(patch_response, httpx.Response):
        assert original["status_code"] == patch_response.status_code
    else:
        assert original["status_code"] is None
        assert original["details"]["response"] is None


async def test_patch_obeys_configured_success_condition(settings, store):
    settings.target_success_path = "result.updated"
    settings.target_success_value = "true"
    calls = []

    def respond(request):
        calls.append(request)
        return (
            httpx.Response(400, json=duplicate_body())
            if request.method == "POST"
            else httpx.Response(200, json={"ok": True, "result": {"updated": False}})
        )

    async with APIClients(settings, store, lambda *_: None, httpx.MockTransport(respond)) as api:
        with pytest.raises(AmbiguousDelivery):
            await api.send({"far_no": "FAR-1", "sample_no": "S1", "density": "1 TB"}, "operation")
    assert [request.method for request in calls] == ["POST", "PATCH"]
