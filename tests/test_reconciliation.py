import pytest
from fastapi.testclient import TestClient

from claim_sync.web import create_app


@pytest.fixture
def reconciliation(settings, spec):
    settings.enable_runner = False
    app = create_app(settings)
    store = app.state.store
    job_id = store.enqueue(spec, settings.destination)
    store.update_job(job_id, status="needs_attention")
    for index in range(2):
        store.save_delivery(
            settings.destination,
            f"FAR-{index}|S001",
            f"fingerprint-{index}",
            {"far_no": f"FAR-{index}", "sample_no": "S001", "device": "example"},
            "uncertain",
            job_id,
            "original network error",
        )
    with TestClient(app, base_url="http://localhost") as client:
        yield client, store, job_id


def selection(client):
    return [
        {
            "destination": row["destination"],
            "record_key": row["record_key"],
            "expected_updated_at": row["updated_at"],
        }
        for row in client.get("/api/deliveries/unresolved").json()
    ]


@pytest.mark.parametrize("result,status", [("applied", "success"), ("not_applied", "failed")])
def test_batch_resolution_without_reason_records_each_result_and_does_not_send(
    reconciliation, result, status
):
    client, store, job_id = reconciliation
    before = store.query("SELECT * FROM deliveries ORDER BY record_key")
    response = client.post(
        "/api/deliveries/resolve-selected", json={"items": selection(client), "result": result}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "resolved": 2, "result": result}
    assert client.get("/api/deliveries/unresolved").json() == []
    after = store.query("SELECT * FROM deliveries ORDER BY record_key")
    for old, new in zip(before, after, strict=True):
        assert new["status"] == status
        assert new["payload"] == old["payload"] and new["fingerprint"] == old["fingerprint"]
        assert new["updated_at"] != old["updated_at"]
        assert "운영자가 목록에서" in new["note"]
    events = store.query("SELECT * FROM events WHERE step='reconcile'")
    assert len(events) == 2 and all(result in event["message"] for event in events)
    for row in before:
        assert any(row["record_key"] in event["message"] for event in events)
    assert all("API 전송 없음" in event["message"] for event in events)
    assert store.query("SELECT * FROM http_exchanges") == []
    assert store.query("SELECT * FROM mock_target") == []
    assert store.one("SELECT COUNT(*) n FROM jobs")["n"] == 1
    assert store.job(job_id)["status"] == "needs_attention"


@pytest.mark.parametrize("change", ["stale", "sending", "success", "missing", "running"])
def test_invalid_batch_member_rolls_back_entire_selection(reconciliation, spec, settings, change):
    client, store, _ = reconciliation
    items = selection(client)
    second = items[1]
    params = (second["destination"], second["record_key"])
    if change == "stale":
        store.execute(
            "UPDATE deliveries SET updated_at='changed' WHERE destination=? AND record_key=?", params
        )
    elif change == "missing":
        store.execute("DELETE FROM deliveries WHERE destination=? AND record_key=?", params)
    elif change == "running":
        running_job = store.enqueue(spec, settings.destination)
        store.update_job(running_job, status="running")
        store.execute(
            "UPDATE deliveries SET job_id=? WHERE destination=? AND record_key=?", (running_job, *params)
        )
    else:
        store.execute(
            "UPDATE deliveries SET status=? WHERE destination=? AND record_key=?", (change, *params)
        )
    before = store.query("SELECT * FROM deliveries ORDER BY record_key")
    response = client.post("/api/deliveries/resolve-selected", json={"items": items, "result": "applied"})
    assert response.status_code == 409
    assert store.query("SELECT * FROM deliveries ORDER BY record_key") == before
    assert store.query("SELECT * FROM events WHERE step='reconcile'") == []


def test_batch_selection_requires_version_and_unique_nonempty_members(reconciliation):
    client, store, _ = reconciliation
    items = selection(client)
    invalid = [[], [items[0], items[0]], [{"record_key": "FAR", "destination": "test"}], items * 51]
    for members in invalid:
        response = client.post(
            "/api/deliveries/resolve-selected", json={"items": members, "result": "not_applied"}
        )
        assert response.status_code == 422
    assert len(client.get("/api/deliveries/unresolved").json()) == 2
    assert store.query("SELECT * FROM events WHERE step='reconcile'") == []


def test_unresolved_list_disables_running_and_sending_items_without_hiding_details(reconciliation):
    client, store, job_id = reconciliation
    rows = client.get("/api/deliveries/unresolved").json()
    assert all(row["can_resolve"] for row in rows)
    assert all(
        row["payload"]["device"] == "example" and row["note"] == "original network error" for row in rows
    )
    store.update_job(job_id, status="running")
    assert all(not row["can_resolve"] for row in client.get("/api/deliveries/unresolved").json())
    store.update_job(job_id, status="needs_attention")
    store.execute("UPDATE deliveries SET status='sending'")
    assert all(not row["can_resolve"] for row in client.get("/api/deliveries/unresolved").json())


def test_legacy_single_resolution_accepts_no_reason_and_honors_optional_version(reconciliation):
    client, store, _ = reconciliation
    item = selection(client)[0]
    response = client.post("/api/deliveries/resolve", json={**item, "result": "applied"})
    assert response.status_code == 200
    assert store.delivery(item["destination"], item["record_key"])["status"] == "success"
    assert client.post("/api/deliveries/resolve", json={**item, "result": "applied"}).status_code == 409
    remaining = selection(client)[0]
    remaining["expected_updated_at"] = "stale"
    assert client.post("/api/deliveries/resolve", json={**remaining, "result": "applied"}).status_code == 409
