import json

from claim_sync.clients import APIClients
from claim_sync.store import encode


async def test_mock_duplicate_updates_only_the_row_matching_both_keys(settings, store):
    values = {"far_no": "FAR-1", "sample_no": "S1", "density": "1 TB", "app": "SSD"}
    async with APIClients(settings, store, lambda *_: None) as api:
        await api.send(values, "insert-first")
        await api.send({**values, "sample_no": "S2"}, "insert-second")
        await api.send({**values, "density": "2 TB", "app": None}, "update-first")
        missing = await api.client.patch(
            settings.target_url,
            json={"where": {"far_no": "FAR-1", "sample_no": "missing"}, "values": {"density": "wrong"}},
        )
        assert missing.status_code == 404
    rows = store.query("SELECT record_key,payload FROM mock_target ORDER BY record_key")
    assert len(rows) == 2
    assert rows[0]["record_key"] == encode(["FAR-1", "S1"])
    assert json.loads(rows[0]["payload"]) == {**values, "density": "2 TB", "app": None}
    assert json.loads(rows[1]["payload"]) == {**values, "sample_no": "S2"}
    assert [
        (row["method"], row["status_code"]) for row in store.query("SELECT * FROM http_exchanges ORDER BY id")
    ] == [("POST", 200), ("POST", 200), ("POST", 400), ("PATCH", 200)]
