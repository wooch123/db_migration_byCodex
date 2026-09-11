import asyncio
import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from claim_sync import cli
from claim_sync.csv_import import CsvImportError, load_csv
from claim_sync.csv_jobs import enqueue_csv
from claim_sync.engine import Engine
from claim_sync.models import RunSpec
from claim_sync.runner import Runner
from claim_sync.store import Store, encode
from claim_sync.web import create_app


@pytest.fixture(autouse=True)
def csv_directory(settings, tmp_path):
    settings.csv_dir = tmp_path / "csv"
    settings.csv_dir.mkdir()
    settings.enable_runner = False


def write_csv(settings, content, filename="추가 정보.csv"):
    path = settings.csv_dir / filename
    path.write_text(content, encoding="utf-8-sig")
    return path


async def run_csv(settings, store, path, **kwargs):
    job_id = enqueue_csv(settings, store, path.name, dry_run=False, **kwargs)
    await Engine(settings, store).run(job_id)
    return store.job(job_id)


async def test_csv_validation_uses_frozen_input_without_api_or_tls_initialization(settings, store):
    path = write_csv(settings, "far,sample,담당자,Release Date\nFAR-1,0001,홍길동,2025/01/02\n")
    settings.ca_bundle = str(settings.csv_dir / "missing-ca.pem")
    job_id = enqueue_csv(settings, store, path.name)
    snapshot = store.csv_snapshot(job_id)
    path.write_text("This file is no longer valid CSV", encoding="utf-8")
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request))

    await Engine(settings, Store(settings.data_dir), transport).run(job_id)

    assert not calls
    assert store.csv_snapshot(job_id) == snapshot
    assert store.job(job_id)["status"] == "completed"
    assert store.job(job_id)["succeeded"] == store.job(job_id)["fetched"] == 1
    record = store.one("SELECT * FROM records WHERE job_id=?", (job_id,))
    assert record["status"] == "validated"
    assert json.loads(record["payload"]) == {
        "far_no": "FAR-1",
        "sample_no": "0001",
        "name": "홍길동",
        "release_date": "2025/01/02",
    }
    for table in ("deliveries", "http_exchanges", "mock_target", "chunks"):
        assert store.one(f"SELECT COUNT(*) n FROM {table}")["n"] == 0


async def test_csv_insert_skip_and_subset_patch_preserve_other_server_fields(settings, store, spec):
    # Establish an existing Claim row, then enrich exactly that row using CSV.
    claim_id = store.enqueue(spec, settings.destination)
    await Engine(settings, store).run(claim_id)
    key = encode(["FAR-20250101-0000", "S001"])
    original = json.loads(store.one("SELECT payload FROM mock_target WHERE record_key=?", (key,))["payload"])
    path = write_csv(
        settings,
        "far,sample,담당자,F/W,EXT_CSD(eMMC Only),Release Date,Init.\n"
        "FAR-20250101-0000,S001,홍길동,001.02,0000ABCDEF,미정 / RC2,001\n"
        "NEW-FAR,0002,김철수,002.01,000F,2025-02-29,002\n",
    )
    first = await run_csv(settings, store, path)
    assert first["status"] == "completed" and first["succeeded"] == 2
    exchanges = store.query(
        "SELECT id,method,status_code FROM http_exchanges WHERE job_id=? ORDER BY id", (first["id"],)
    )
    assert [(row["method"], row["status_code"]) for row in exchanges] == [
        ("POST", 400),
        ("PATCH", 200),
        ("POST", 200),
    ]
    patch = json.loads(store.exchange(exchanges[1]["id"])["details"]["request"]["body"])
    assert patch == {
        "where": {"far_no": "FAR-20250101-0000", "sample_no": "S001"},
        "values": {
            "name": "홍길동",
            "firmware": "001.02",
            "ext_csd": "0000ABCDEF",
            "release_date": "미정 / RC2",
            "initialize": "001",
        },
    }
    post = json.loads(store.exchange(exchanges[2]["id"])["details"]["request"]["body"])
    assert post["values"]["release_date"] == "2025-02-29"
    assert post["values"]["initialize"] == "002"
    assert "init" not in post["values"]
    saved = json.loads(store.one("SELECT payload FROM mock_target WHERE record_key=?", (key,))["payload"])
    assert saved == {**original, **patch["values"]}
    repeated = await run_csv(settings, store, path)
    assert repeated["skipped"] == 2 and repeated["succeeded"] == 0
    assert store.one("SELECT COUNT(*) n FROM http_exchanges WHERE job_id=?", (repeated["id"],))["n"] == 0
    write_csv(settings, "far,sample,F/W\nFAR-20250101-0000,S001,003.04\n")
    changed = await run_csv(settings, store, path)
    assert changed["succeeded"] == 1
    saved = json.loads(store.one("SELECT payload FROM mock_target WHERE record_key=?", (key,))["payload"])
    assert saved == {**original, **patch["values"], "firmware": "003.04"}


async def test_csv_explicit_null_clears_only_selected_blank_columns(settings, store):
    path = write_csv(settings, "far,sample,담당자,F/W\nF1,01,담당자 A,FW1\n")
    assert (await run_csv(settings, store, path))["succeeded"] == 1
    write_csv(settings, "far,sample,F/W\nF1,01,\n")
    assert (await run_csv(settings, store, path, blank_mode="null"))["succeeded"] == 1
    assert json.loads(store.one("SELECT payload FROM mock_target")["payload"]) == {
        "far_no": "F1",
        "sample_no": "01",
        "name": "담당자 A",
        "firmware": None,
    }


async def test_csv_patch_lost_response_stops_later_rows_and_preserves_original_patch_log(settings, store):
    path = write_csv(settings, "far,sample,F/W\nF1,01,old\n")
    assert (await run_csv(settings, store, path))["succeeded"] == 1
    write_csv(settings, "far,sample,F/W\nF1,01,new\nF2,02,second\n")
    settings.mock_scenario = "target_timeout"
    failed = await run_csv(settings, store, path)
    assert failed["status"] == "needs_attention" and failed["uncertain"] == 1
    writes = store.query("SELECT * FROM http_exchanges WHERE job_id=? ORDER BY id", (failed["id"],))
    assert [(row["method"], row["status_code"]) for row in writes] == [("POST", 400), ("PATCH", None)]
    record = store.one("SELECT error FROM records WHERE job_id=?", (failed["id"],))
    assert path.name in record["error"] and "CSV 2행" in record["error"] and "PATCH" in record["error"]
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 1
    assert json.loads(store.one("SELECT payload FROM mock_target")["payload"])["firmware"] == "new"
    settings.mock_scenario = "none"
    blocked = await run_csv(settings, store, path)
    assert blocked["status"] == "needs_attention" and blocked["succeeded"] == 0
    trace = store.exchange(store.one("SELECT id FROM http_exchanges WHERE job_id=?", (blocked["id"],))["id"])
    assert trace["state"] == "blocked" and trace["details"]["original"]["exchange_id"] == writes[1]["id"]


@pytest.mark.parametrize("body_kind", ["json", "text", "truncated", "broken_stream"])
async def test_csv_bad_request_patches_partial_fields_and_preserves_both_http_logs(
    settings, store, body_kind
):
    path = write_csv(settings, "far,sample,F/W,Release Date\nF1,0001,001-FW,출시 일정 미정 / RC2\n")
    settings.target_base_url = "https://target.example.test"
    settings.target_path = "/custom/far_table?tenant=csv"
    settings.http_log_body_bytes = 1024
    job_id = enqueue_csv(settings, store, path.name, dry_run=False)
    calls = []

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"Bad Request: required create field missing" + b" " * 1024
            raise httpx.ReadError("error response body interrupted")

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            if body_kind == "broken_stream":
                return httpx.Response(400, stream=BrokenBody())
            return (
                httpx.Response(400, json={"ok": False, "error": "required create field missing"})
                if body_kind == "json"
                else httpx.Response(
                    400,
                    text="Bad Request: required create field missing"
                    + (" " * 2048 if body_kind == "truncated" else ""),
                )
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(respond)
    await Engine(settings, store, transport).run(job_id)
    result = store.job(job_id)
    assert result["status"] == "completed" and result["succeeded"] == 1
    assert [request.method for request in calls] == ["POST", "PATCH"]
    assert all(str(request.url) == settings.target_url for request in calls)
    assert json.loads(calls[0].content) == {
        "values": {
            "far_no": "F1",
            "sample_no": "0001",
            "firmware": "001-FW",
            "release_date": "출시 일정 미정 / RC2",
        }
    }
    assert json.loads(calls[1].content) == {
        "where": {"far_no": "F1", "sample_no": "0001"},
        "values": {"firmware": "001-FW", "release_date": "출시 일정 미정 / RC2"},
    }
    logs = [
        store.exchange(row["id"])
        for row in store.query("SELECT id FROM http_exchanges WHERE job_id=? ORDER BY id", (job_id,))
    ]
    assert [(row["method"], row["status_code"], row["state"]) for row in logs] == [
        ("POST", 400, "failed"),
        ("PATCH", 200, "completed"),
    ]
    assert "required create field missing" in logs[0]["details"]["response"]["body"]
    assert json.loads(logs[1]["details"]["response"]["body"]) == {"ok": True}
    for log, request in zip(logs, calls, strict=True):
        assert json.loads(log["details"]["request"]["body"]) == json.loads(request.content)
    assert store.one("SELECT status FROM deliveries")["status"] == "success"
    repeat_id = enqueue_csv(settings, store, path.name, dry_run=False)
    await Engine(settings, store, transport).run(repeat_id)
    assert store.job(repeat_id)["skipped"] == 1 and len(calls) == 2


async def test_csv_patch_rejection_keeps_file_line_and_continues_without_loop(settings, store):
    path = write_csv(settings, "far,sample,F/W\nF1,01,bad-firmware\nF2,02,good-firmware\n")
    job_id = enqueue_csv(settings, store, path.name, dry_run=False)
    calls = []

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        key = body["where"] if request.method == "PATCH" else body["values"]
        if key["far_no"] == "F1":
            return httpx.Response(400, json={"ok": False, "error": "firmware validation failed"})
        return httpx.Response(200, json={"ok": True})

    await Engine(settings, store, httpx.MockTransport(respond)).run(job_id)
    result = store.job(job_id)
    assert result["status"] == "partial" and result["failed"] == result["succeeded"] == 1
    assert [request.method for request in calls] == ["POST", "PATCH", "POST"]
    logs = store.query("SELECT method,status_code FROM http_exchanges WHERE job_id=? ORDER BY id", (job_id,))
    assert [(row["method"], row["status_code"]) for row in logs] == [
        ("POST", 400),
        ("PATCH", 400),
        ("POST", 200),
    ]
    record = store.one("SELECT error FROM records WHERE job_id=? AND status='failed'", (job_id,))
    assert path.name in record["error"] and "CSV 2행" in record["error"]
    assert "HTTP 400" in record["error"] and "firmware validation failed" in record["error"]
    assert f"PATCH {settings.target_url}" in record["error"]


@pytest.mark.parametrize("status", [403, 409, 422])
async def test_csv_non_bad_request_rejection_never_patches(settings, store, status):
    path = write_csv(settings, "far,sample,F/W\nF1,01,FW1\n")
    job_id = enqueue_csv(settings, store, path.name, dry_run=False)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"ok": False, "error": "request rejected"})

    await Engine(settings, store, httpx.MockTransport(respond)).run(job_id)
    assert store.job(job_id)["status"] == "failed"
    assert store.job(job_id)["failed"] == 1
    assert [request.method for request in calls] == ["POST"]
    assert store.one("SELECT status FROM deliveries")["status"] == "failed"


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("failure", ["timeout", "server_error", "negative_ack"])
async def test_csv_uncertain_write_stops_remaining_rows_and_blocks_repeat(settings, store, method, failure):
    path = write_csv(settings, "far,sample,F/W\nF1,01,FW1\nF2,02,FW2\n")
    job_id = enqueue_csv(settings, store, path.name, dry_run=False)
    calls = []

    def respond(request):
        calls.append(request)
        if request.method != method:
            return httpx.Response(400, text="Bad Request")
        if failure == "timeout":
            raise httpx.ReadTimeout("write acknowledgement lost")
        if failure == "server_error":
            return httpx.Response(503, json={"error": "upstream unavailable"})
        return httpx.Response(200, json={"ok": False})

    transport = httpx.MockTransport(respond)
    await Engine(settings, store, transport).run(job_id)
    result = store.job(job_id)
    assert result["status"] == "needs_attention" and result["uncertain"] == 1
    assert result["succeeded"] == 0
    expected_methods = ["POST"] if method == "POST" else ["POST", "PATCH"]
    assert [request.method for request in calls] == expected_methods
    assert store.one("SELECT COUNT(*) n FROM records WHERE job_id=?", (job_id,))["n"] == 1
    assert store.one("SELECT status FROM deliveries")["status"] == "uncertain"
    original = store.exchange(
        store.one("SELECT id FROM http_exchanges WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,))["id"]
    )
    assert original["method"] == method
    assert original["status_code"] == {"timeout": None, "server_error": 503, "negative_ack": 200}[failure]
    retry_id = enqueue_csv(settings, store, path.name, dry_run=False)
    await Engine(settings, store, transport).run(retry_id)
    assert store.job(retry_id)["status"] == "needs_attention"
    assert [request.method for request in calls] == expected_methods
    blocked = store.exchange(store.one("SELECT id FROM http_exchanges WHERE job_id=?", (retry_id,))["id"])
    assert blocked["state"] == "blocked"
    assert blocked["details"]["original"]["exchange_id"] == original["id"]
    assert blocked["details"]["original"]["method"] == method


@pytest.mark.parametrize("first_source", ["claims", "csv"])
async def test_uncertain_delivery_blocks_other_source_for_same_business_key(
    settings, store, spec, first_source
):
    path = write_csv(settings, "far,sample,담당자\nFAR-20250101-0000,S001,홍길동\n")
    settings.mock_scenario = "target_timeout"
    if first_source == "claims":
        first_id = store.enqueue(spec, settings.destination)
    else:
        first_id = enqueue_csv(settings, store, path.name, dry_run=False)
    await Engine(settings, store).run(first_id)
    assert store.job(first_id)["status"] == "needs_attention"
    settings.mock_scenario = "none"
    if first_source == "claims":
        next_id = enqueue_csv(settings, store, path.name, dry_run=False)
    else:
        next_id = store.enqueue(spec, settings.destination)
    await Engine(settings, store).run(next_id)
    assert store.job(next_id)["status"] == "needs_attention"
    assert store.job(next_id)["succeeded"] == 0
    writes = store.query(
        "SELECT * FROM http_exchanges WHERE job_id=? AND method IN ('POST','PATCH')", (next_id,)
    )
    assert len(writes) == 1 and writes[0]["state"] == "blocked"
    assert writes[0]["status_code"] is None
    details = store.exchange(writes[0]["id"])["details"]
    assert details["original"]["job_id"] == first_id
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 1


def test_csv_web_preview_snapshot_retry_after_source_deleted_and_export(settings):
    path = write_csv(settings, "far,sample,담당자,F/W,Release Date\nF1,001,원래 담당자,FW1,출시 일정 미정\n")
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        listing = client.get("/api/csv/files")
        assert listing.status_code == 200
        assert [row["name"] for row in listing.json()["files"]] == [path.name]
        preview = client.post("/api/csv/preview", json={"filename": path.name}).json()
        assert preview["error_count"] == 0
        assert preview["rows"][0]["values"]["release_date"] == "출시 일정 미정"
        queued = client.post(
            "/api/csv/jobs", json={"filename": path.name, "sha256": preview["sha256"], "dry_run": False}
        )
        assert queued.status_code == 202
        job_id = queued.json()["id"]
        assert app.state.store.csv_snapshot(job_id)["rows"] == preview["rows"]
        assert client.post(f"/api/jobs/{job_id}/retry", json={}).status_code == 409
        path.unlink()
        settings.mock_scenario = "target_error"
        asyncio.run(Engine(settings, app.state.store).run(job_id))
        assert app.state.store.job(job_id)["status"] == "failed"
        settings.mock_scenario = "none"
        retried = client.post(f"/api/jobs/{job_id}/retry", json={})
        assert retried.status_code == 202
        retry_id = retried.json()["id"]
        assert app.state.store.csv_snapshot(retry_id) == app.state.store.csv_snapshot(job_id)
        asyncio.run(Engine(settings, app.state.store).run(retry_id))
        result = client.get(f"/api/jobs/{retry_id}").json()
        assert result["status"] == "completed" and result["succeeded"] == 1
        assert result["spec"]["source_type"] == "csv" and result["chunks"] == []
        exported = client.get(f"/api/jobs/{retry_id}/export")
        assert exported.status_code == 200
        assert json.loads(exported.text)["request"] == {
            "values": {
                "far_no": "F1",
                "sample_no": "001",
                "name": "원래 담당자",
                "firmware": "FW1",
                "release_date": "출시 일정 미정",
            }
        }


def test_web_rejects_changed_file_and_all_rows_preflight_before_queue(settings):
    path = write_csv(settings, "far,sample,담당자\nF1,01,Valid\n")
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        old = client.post("/api/csv/preview", json={"filename": path.name}).json()
        write_csv(settings, "far,sample,담당자\nF1,01,Valid\nF2,,Invalid missing key\n")
        changed = client.post("/api/csv/jobs", json={"filename": path.name, "sha256": old["sha256"]})
        assert changed.status_code == 422 and "변경" in changed.json()["detail"]
        current = client.post("/api/csv/preview", json={"filename": path.name}).json()
        assert current["valid_rows"] == 1 and current["error_count"] == 1
        assert current["errors"][0]["line"] == 3
        rejected = client.post(
            "/api/csv/jobs", json={"filename": path.name, "sha256": current["sha256"], "dry_run": False}
        )
        assert rejected.status_code == 422
        for table in ("jobs", "csv_imports", "records", "deliveries", "http_exchanges"):
            assert app.state.store.one(f"SELECT COUNT(*) n FROM {table}")["n"] == 0


def test_preview_truncates_display_but_preflights_entire_file(settings):
    content = "far,sample,F/W\n" + "".join(f"F{i},01,FW{i}\n" for i in range(60))
    path = write_csv(settings, content + "BAD,,FW\n")
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        preview = client.post("/api/csv/preview", json={"filename": path.name}).json()
        assert preview["total_rows"] == 61 and preview["valid_rows"] == 60
        assert len(preview["rows"]) == preview["preview_limit"] == 50
        assert preview["errors"][0]["line"] == 62
        assert (
            client.post(
                "/api/csv/jobs", json={"filename": path.name, "sha256": preview["sha256"]}
            ).status_code
            == 422
        )


def test_csv_routes_enforce_auth_origin_input_paths_and_live_write_gate(settings):
    path = write_csv(settings, "far,sample,F/W\nF1,01,001\n")
    settings.app_access_token = "csv-console-secret"
    settings.app_mode = "live"
    settings.allow_live_writes = False
    settings.target_upsert_confirmed = False
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/csv/files").status_code == 401
        assert client.post("/api/csv/preview", json={"filename": path.name}).status_code == 401
        assert client.post("/api/csv/jobs", json={}).status_code == 401
        client.headers["Authorization"] = "Bearer csv-console-secret"
        assert (
            client.post(
                "/api/csv/preview",
                json={"filename": path.name},
                headers={"Origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        assert client.post("/api/csv/preview", content=path.name).status_code == 415
        for filename in ("../outside.csv", "..\\outside.csv", "C:\\outside.csv", "x.csv:secret"):
            assert client.post("/api/csv/preview", json={"filename": filename}).status_code == 422
        assert (
            client.post("/api/csv/preview", json={"filename": path.name, "blank_mode": "unknown"}).status_code
            == 422
        )
        preview = client.post("/api/csv/preview", json={"filename": path.name}).json()
        body = {"filename": path.name, "sha256": preview["sha256"]}
        assert client.post("/api/csv/jobs", json={"filename": path.name}).status_code == 422
        assert client.post("/api/csv/jobs", json={**body, "dry_run": False}).status_code == 409
        assert client.post("/api/csv/jobs", json=body).status_code == 202
        forged = RunSpec(source_type="csv", csv_filename=path.name).model_dump(mode="json")
        assert client.post("/api/jobs", json=forged).status_code == 422
        assert client.put("/api/schedule", json={"enabled": True, "run": forged}).status_code == 422


async def test_csv_runtime_rechecks_target_and_write_gate_before_any_request(settings, store):
    path = write_csv(settings, "far,sample,F/W\nF1,01,001\n")
    settings.app_mode = "live"
    settings.allow_live_writes = settings.target_upsert_confirmed = True
    job_id = enqueue_csv(settings, store, path.name, dry_run=False)
    settings.allow_live_writes = False
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request))
    await Engine(settings, store, transport).run(job_id)
    assert store.job(job_id)["status"] == "failed" and not requests
    settings.allow_live_writes = True
    redirected_id = enqueue_csv(settings, store, path.name, dry_run=False)
    settings.target_dataset_id = "changed-destination"
    await Engine(settings, store, transport).run(redirected_id)
    assert store.job(redirected_id)["status"] == "failed" and not requests
    assert store.one("SELECT COUNT(*) n FROM deliveries")["n"] == 0


def test_csv_rejects_incompatible_business_key_configuration_without_queued_job(settings, store):
    path = write_csv(settings, "far,sample,F/W\nF1,01,001\n")
    settings.target_key_fields = ["far_no"]
    with pytest.raises(CsvImportError, match="TARGET_KEY_FIELDS"):
        enqueue_csv(settings, store, path.name)
    assert store.one("SELECT COUNT(*) n FROM jobs")["n"] == 0


@pytest.mark.parametrize("send", [False, True])
def test_csv_cli_runs_without_date_parameters_and_defaults_to_validation(
    settings, store, monkeypatch, capsys, send
):
    path = write_csv(settings, "far,sample,F/W\nF1,0001,000FW\n")
    monkeypatch.setattr(cli, "Settings", lambda **_: settings)
    monkeypatch.setattr(sys, "argv", ["claim-sync", "csv-import", path.name, *(["--send"] if send else [])])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed" and result["succeeded"] == 1
    assert result["source"] == "csv-cli" and result["spec"]["dry_run"] is not send
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == int(send)
    assert load_csv(settings, path.name)["rows"] == store.csv_snapshot(result["id"])["rows"]


def test_csv_cli_respects_existing_runner_lock_without_enqueuing(settings, store, monkeypatch, capsys):
    path = write_csv(settings, "far,sample,F/W\nF1,01,001\n")
    monkeypatch.setattr(cli, "Settings", lambda **_: settings)
    monkeypatch.setattr(sys, "argv", ["claim-sync", "csv-import", path.name, "--send"])
    runner = Runner(settings, store)
    with runner.lock, pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    assert "Another runner is active" in capsys.readouterr().err
    assert store.one("SELECT COUNT(*) n FROM jobs")["n"] == 0
