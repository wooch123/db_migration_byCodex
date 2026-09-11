import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock

from .models import RunSpec, ScheduleSpec


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "claim-sync.sqlite3"
        with FileLock(str(self.data_dir / ".schema.lock"), timeout=15), self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, source TEXT NOT NULL, destination TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT, start_date TEXT, end_date TEXT,
                    fetched INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0, uncertain INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, time TEXT NOT NULL,
                    level TEXT NOT NULL, step TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS csv_imports (
                    job_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
                CREATE TABLE IF NOT EXISTS http_exchanges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, record_key TEXT,
                    started_at TEXT NOT NULL, finished_at TEXT, stage TEXT NOT NULL,
                    method TEXT NOT NULL, url TEXT NOT NULL, status_code INTEGER,
                    state TEXT NOT NULL, duration_ms INTEGER, details TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS http_exchanges_job ON http_exchanges(job_id, id);
                CREATE INDEX IF NOT EXISTS http_exchanges_record ON http_exchanges(job_id, record_key, id);
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL, status TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0, error TEXT
                );
                CREATE INDEX IF NOT EXISTS chunks_job ON chunks(job_id, id);
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    record_key TEXT NOT NULL, status TEXT NOT NULL, payload TEXT,
                    error TEXT, created_at TEXT NOT NULL,
                    UNIQUE(job_id, record_key)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    destination TEXT NOT NULL, record_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                    job_id TEXT NOT NULL, updated_at TEXT NOT NULL, note TEXT,
                    PRIMARY KEY(destination, record_key)
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS mock_target (
                    record_key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL
                );
            """)
            # Add row identity without rewriting existing records or their unique index.
            # Serializing this migration also supports web/worker starting together.
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(records)")}
            for name, kind in (("business_key", "TEXT"), ("csv_line", "INTEGER"), ("note", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE records ADD COLUMN {name} {kind}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def execute(self, sql: str, params=()):
        with self.connect() as db:
            cursor = db.execute(sql, params)
            return cursor.lastrowid

    def query(self, sql: str, params=()) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def event(self, job_id, level: str, step: str, message: str):
        self.execute(
            "INSERT INTO events(job_id,time,level,step,message) VALUES(?,?,?,?,?)",
            (job_id, utcnow(), level, step, message),
        )

    def record(self, job_id, key, status, values, error=None, *, business_key=None, csv_line=None, note=None):
        self.execute(
            """INSERT INTO records(job_id,record_key,status,payload,error,created_at,business_key,csv_line,note)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,record_key) DO UPDATE SET
            status=excluded.status,error=excluded.error""",
            (
                job_id,
                key,
                status,
                encode(values) if values else None,
                error,
                utcnow(),
                business_key,
                csv_line,
                note,
            ),
        )

    def enqueue(self, spec: RunSpec, destination: str, source="manual") -> str:
        if spec.source_type == "csv":
            raise ValueError("CSV 작업은 검증한 파일 데이터를 함께 등록해야 합니다.")
        job_id = uuid.uuid4().hex
        self.execute(
            "INSERT INTO jobs(id,spec,source,destination,created_at) VALUES(?,?,?,?,?)",
            (job_id, spec.model_dump_json(), source, destination, utcnow()),
        )
        self.event(job_id, "info", "queue", "실행 대기열에 등록했습니다.")
        return job_id

    def enqueue_csv(self, spec: RunSpec, destination: str, snapshot: dict, source="csv") -> str:
        if spec.source_type != "csv" or spec.csv_filename != snapshot["filename"]:
            raise ValueError("CSV 실행 설정과 파일 데이터가 일치하지 않습니다.")
        if snapshot.get("error_count") or not (snapshot.get("rows") or snapshot.get("skipped_rows")):
            raise ValueError("오류가 없고 전송할 데이터가 있는 CSV만 등록할 수 있습니다.")
        job_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs(id,spec,source,destination,created_at) VALUES(?,?,?,?,?)",
                (job_id, spec.model_dump_json(), source, destination, utcnow()),
            )
            db.execute("INSERT INTO csv_imports VALUES(?,?)", (job_id, encode(snapshot)))
            db.execute(
                "INSERT INTO events(job_id,time,level,step,message) VALUES(?,?,?,?,?)",
                (
                    job_id,
                    utcnow(),
                    "info",
                    "queue",
                    f"CSV {spec.csv_filename}: 전송 대상 {len(snapshot['rows'])}행 · 제외 {len(snapshot.get('skipped_rows', []))}행의 데이터를 저장하고 작업을 등록했습니다.",
                ),
            )
        return job_id

    def csv_snapshot(self, job_id: str) -> dict | None:
        row = self.one("SELECT snapshot FROM csv_imports WHERE job_id=?", (job_id,))
        return json.loads(row["snapshot"]) if row else None

    def job(self, job_id: str) -> dict | None:
        row = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row:
            row["spec"] = json.loads(row["spec"])
        return row

    def update_job(self, job_id: str, **values):
        allowed = {
            "status",
            "started_at",
            "finished_at",
            "start_date",
            "end_date",
            "error",
            "cancel_requested",
        }
        if not values.keys() <= allowed:
            raise ValueError("Invalid job update")
        self.execute(
            f"UPDATE jobs SET {','.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), job_id)
        )

    def increment(self, job_id: str, **values):
        if not values.keys() <= {"fetched", "processed", "succeeded", "skipped", "failed", "uncertain"}:
            raise ValueError("Invalid counter")
        self.execute(
            f"UPDATE jobs SET {','.join(k + '=' + k + '+?' for k in values)} WHERE id=?",
            (*values.values(), job_id),
        )

    def get_setting(self, key: str, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value):
        self.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, encode(value)),
        )

    def schedule(self):
        return self.get_setting("schedule", {**ScheduleSpec().model_dump(mode="json"), "next_run_at": None})

    def save_schedule(self, spec: ScheduleSpec, destination: str):
        value = spec.model_dump(mode="json")
        value["destination"] = destination
        value["next_run_at"] = (
            (datetime.now(UTC) + timedelta(minutes=spec.interval_minutes)).isoformat()
            if spec.enabled
            else None
        )
        self.set_setting("schedule", value)
        return value

    def schedule_tick(self, now: datetime | None = None):
        now = now or datetime.now(UTC)
        # Saving settings and claiming a due schedule share the same transaction boundary.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM settings WHERE key='schedule'").fetchone()
            if not row:
                return
            value = json.loads(row[0])
            if (
                not value["enabled"]
                or not value["next_run_at"]
                or datetime.fromisoformat(value["next_run_at"]) > now
            ):
                return
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                return  # Coalesce missed intervals into one run after existing work completes.
            spec = RunSpec.model_validate(value["run"])
            job_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO jobs(id,spec,source,destination,created_at) VALUES(?,?,?,?,?)",
                (job_id, spec.model_dump_json(), "schedule", value["destination"], now.isoformat()),
            )
            value["next_run_at"] = (now + timedelta(minutes=value["interval_minutes"])).isoformat()
            db.execute("UPDATE settings SET value=? WHERE key='schedule'", (encode(value),))
            return job_id

    def recover(self):
        # Only the process holding the runner file lock may call this.
        with self.connect() as db:
            db.execute(
                "UPDATE http_exchanges SET state='interrupted',finished_at=? WHERE state='sending'",
                (utcnow(),),
            )
            db.execute(
                "UPDATE deliveries SET status='uncertain',note='프로세스 중단: 운영 서버 수신 여부 확인 필요' WHERE status='sending'"
            )
            db.execute(
                "UPDATE jobs SET status='interrupted',finished_at=?,error=? WHERE status='running'",
                (utcnow(), "이전 실행기가 중단되었습니다. 전송 결과를 확인한 후 다시 실행하세요."),
            )
            db.execute("UPDATE chunks SET status='interrupted' WHERE status IN ('fetching','processing')")

    def delivery(self, destination: str, key: str):
        return self.one("SELECT * FROM deliveries WHERE destination=? AND record_key=?", (destination, key))

    def exchange(self, exchange_id: int):
        row = self.one("SELECT * FROM http_exchanges WHERE id=?", (exchange_id,))
        if row:
            row["details"] = json.loads(row["details"])
            if row["state"] == "interrupted" and not row["details"].get("error"):
                row["details"]["error"] = "프로세스가 중단되어 요청 완료 결과를 기록하지 못했습니다."
        return row

    def save_delivery(self, destination, key, fingerprint, payload, status, job_id, note=None):
        self.execute(
            """INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(destination,record_key) DO UPDATE SET fingerprint=excluded.fingerprint,
            payload=excluded.payload,status=excluded.status,job_id=excluded.job_id,
            updated_at=excluded.updated_at,note=excluded.note""",
            (destination, key, fingerprint, encode(payload), status, job_id, utcnow(), note),
        )
