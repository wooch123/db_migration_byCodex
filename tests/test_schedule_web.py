import asyncio
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from claim_sync.engine import Engine
from claim_sync.models import ScheduleSpec
from claim_sync.runner import Runner
from claim_sync.store import Store
from claim_sync.web import create_app


def test_schedule_persists_coalesces_and_does_not_overlap(settings, store, spec):
    stored = store.save_schedule(
        ScheduleSpec(enabled=True, interval_minutes=60, run=spec), settings.destination
    )
    due = datetime.fromisoformat(stored["next_run_at"])
    assert store.schedule_tick(due - timedelta(seconds=1)) is None
    job_id = Store(settings.data_dir).schedule_tick(due + timedelta(days=2))
    assert job_id and store.job(job_id)["source"] == "schedule"
    assert store.schedule_tick(due + timedelta(days=4)) is None
    store.update_job(job_id, status="completed")
    assert store.schedule_tick(due + timedelta(days=4))
    store.save_schedule(ScheduleSpec(enabled=False, run=spec), settings.destination)
    assert store.schedule_tick(due + timedelta(days=10)) is None


async def test_two_runners_only_one_consumes_and_standby_takes_over(settings, store, spec):
    first, second = Runner(settings, store), Runner(settings, store)
    task1 = asyncio.create_task(first.serve())
    await asyncio.sleep(0.1)
    task2 = asyncio.create_task(second.serve())
    await asyncio.sleep(0.1)
    assert first.active and not second.active
    job_id = store.enqueue(spec, settings.destination)
    deadline = asyncio.get_running_loop().time() + 10
    while store.job(job_id)["status"] not in {"completed", "failed"}:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.05)
    assert store.job(job_id)["succeeded"] == 18
    await first.stop(task1)
    deadline = asyncio.get_running_loop().time() + 5
    while not second.active:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.05)
    await second.stop(task2)
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 18


def test_web_validation_auth_and_secret_redaction(settings, spec):
    settings.enable_runner = False
    settings.app_access_token = "test-private-token"
    settings.claims_headers = {"Authorization": "Bearer upstream-private-token"}
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/openapi.json").status_code == 401
        headers = {"Authorization": "Bearer test-private-token"}
        assert client.get("/api/state", headers={**headers, "Host": "attacker.example"}).status_code == 400
        state = client.get("/api/state", headers=headers)
        assert state.status_code == 200 and "private-token" not in state.text
        assert client.post("/api/jobs", json={"chunk_days": 0}, headers=headers).status_code == 422
        assert (
            client.post(
                "/api/jobs",
                json=spec.model_dump(mode="json"),
                headers={**headers, "Origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        assert client.post("/api/jobs", content="hello", headers=headers).status_code == 415
        assert client.get("/api/jobs/missing", headers=headers).status_code == 404


def test_web_execution_preview_records_export_and_schedule(settings, spec):
    settings.enable_runner = False
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        body = spec.model_dump(mode="json")
        preview = client.post("/api/preview", json=body).json()
        assert preview["days"] == 3 and preview["chunks"] == 2
        response = client.post("/api/jobs", json=body)
        assert response.status_code == 202
        job_id = response.json()["id"]
        asyncio.run(Engine(settings, app.state.store).run(job_id))
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "completed" and len(job["chunks"]) == 2
        records = client.get(f"/api/jobs/{job_id}/records?limit=5&offset=5").json()
        assert records["total"] == 18 and len(records["items"]) == 5
        export = client.get(f"/api/jobs/{job_id}/export")
        assert len(export.text.splitlines()) == 18
        assert len(json.loads(export.text.splitlines()[0])["request"]["values"]) == 21
        schedule = client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60, "run": body})
        assert schedule.status_code == 200 and schedule.json()["next_run_at"]
        assert client.post(f"/api/jobs/{job_id}/retry", json={}).status_code == 202
        assert client.post(f"/api/jobs/{job_id}/cancel", json={}).status_code == 409


def test_reconcile_ambiguous_delivery_without_resending(settings, spec):
    settings.enable_runner = False
    settings.mock_scenario = "target_timeout"
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        job_id = client.post("/api/jobs", json=spec.model_dump(mode="json")).json()["id"]
        asyncio.run(Engine(settings, app.state.store).run(job_id))
        delivery = client.get("/api/deliveries/unresolved").json()[0]
        response = client.post(
            "/api/deliveries/resolve",
            json={
                "record_key": delivery["record_key"],
                "destination": delivery["destination"],
                "result": "applied",
                "note": "가상 대상 DB 값 대조 완료",
            },
        )
        assert response.status_code == 200
        assert client.get("/api/deliveries/unresolved").json() == []
        settings.mock_scenario = "none"
        retry = client.post(f"/api/jobs/{job_id}/retry", json={}).json()["id"]
        asyncio.run(Engine(settings, app.state.store).run(retry))
        job = app.state.store.job(retry)
        assert job["status"] == "completed" and job["skipped"] == 1 and job["succeeded"] == 17
        assert app.state.store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 18


def test_live_web_write_gate(settings, spec):
    settings.enable_runner = False
    settings.app_mode = "live"
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.post("/api/jobs", json=spec.model_dump(mode="json")).status_code == 409
        assert (
            client.post(
                "/api/jobs", json=spec.model_copy(update={"dry_run": True}).model_dump(mode="json")
            ).status_code
            == 202
        )


def test_cancel_queued_job_via_ui(settings, spec):
    settings.enable_runner = False
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        job_id = client.post("/api/jobs", json=spec.model_dump(mode="json")).json()["id"]
        assert client.post(f"/api/jobs/{job_id}/cancel", json={}).status_code == 200
        asyncio.run(Engine(settings, app.state.store).run(job_id))
        assert app.state.store.job(job_id)["status"] == "cancelled"


def test_persisted_relative_schedule_resolves_on_each_run(settings, store):
    saved = store.save_schedule(ScheduleSpec(enabled=True), settings.destination)
    future = datetime(2030, 1, 1, tzinfo=UTC)
    assert datetime.fromisoformat(saved["next_run_at"]) < future
    job_id = store.schedule_tick(future)
    assert store.job(job_id)["spec"]["period_mode"] == "relative"
    assert store.job(job_id)["spec"]["start_date"] is None
