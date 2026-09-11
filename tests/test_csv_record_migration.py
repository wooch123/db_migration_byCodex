import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from claim_sync.store import Store, encode


@pytest.mark.parametrize("workers", [1, 2])
def test_row_metadata_migration_preserves_legacy_records_and_unique_index(tmp_path, workers):
    payload = encode({"far_no": "LEGACY", "sample_no": "001", "firmware": "old"})
    with sqlite3.connect(tmp_path / "claim-sync.sqlite3") as db:
        db.execute("""CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            record_key TEXT NOT NULL, status TEXT NOT NULL, payload TEXT,
            error TEXT, created_at TEXT NOT NULL, UNIQUE(job_id, record_key))""")
        db.execute(
            "INSERT INTO records VALUES(1,'legacy-job','legacy-key','sent',?,NULL,'2025-01-01')", (payload,)
        )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        stores = list(pool.map(Store, [tmp_path] * workers))
    store = stores[0]
    legacy = store.one("SELECT * FROM records WHERE id=1")
    assert legacy == {
        "id": 1,
        "job_id": "legacy-job",
        "record_key": "legacy-key",
        "status": "sent",
        "payload": payload,
        "error": None,
        "created_at": "2025-01-01",
        "business_key": None,
        "csv_line": None,
        "note": None,
    }
    with store.connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO records(job_id,record_key,status,created_at) VALUES('legacy-job','legacy-key','sent','now')"
        )
    key = encode(["DUPLICATE", "001"])
    for line in (2, 3):
        store.record(
            "csv-job",
            encode(["csv", line]),
            "sent",
            {"far_no": "DUPLICATE", "sample_no": "001"},
            business_key=key,
            csv_line=line,
        )
    assert store.one("SELECT COUNT(*) n FROM records")["n"] == 3
    assert [
        row["csv_line"]
        for row in store.query("SELECT csv_line FROM records WHERE business_key=? ORDER BY id", (key,))
    ] == [2, 3]
    assert Store(tmp_path).one("SELECT * FROM records WHERE id=1") == legacy
