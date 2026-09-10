import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .clients import APIClients, UpstreamError
from .config import Settings
from .csv_import import CsvImportError, list_csv_files, load_csv
from .csv_jobs import enqueue_csv
from .diagnostics import RequestDiagnostics
from .http_log import previous_delivery
from .models import RunSpec, ScheduleSpec
from .runner import Runner
from .store import Store, encode, utcnow


class Resolution(BaseModel):
    record_key: str
    destination: str
    result: Literal["applied", "not_applied"]
    expected_updated_at: str | None = None


class ResolutionSelection(BaseModel):
    record_key: str
    destination: str
    expected_updated_at: str = Field(min_length=1)


class SelectedResolution(BaseModel):
    items: list[ResolutionSelection] = Field(min_length=1, max_length=100)
    result: Literal["applied", "not_applied"]


class CsvPreviewRequest(BaseModel):
    filename: str = Field(min_length=1)
    blank_mode: Literal["omit", "null"] = "omit"


class CsvJobRequest(CsvPreviewRequest):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dry_run: bool = True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.data_dir)
    runner = Runner(settings, store)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(runner.serve()) if settings.enable_runner else None
        yield
        if task:
            await runner.stop(task)

    app = FastAPI(
        title="Claim Sync",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.store, app.state.settings, app.state.runner = store, settings, runner
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if settings.app_access_token:
                token = request.headers.get("Authorization", "").removeprefix("Bearer ")
                if not secrets.compare_digest(token, settings.app_access_token):
                    return JSONResponse({"detail": "접속 토큰이 필요합니다."}, status_code=401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                expected = f"{request.url.scheme}://{request.url.netloc}"
                if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                    return JSONResponse(
                        {"detail": "외부 사이트의 요청은 허용하지 않습니다."}, status_code=403
                    )
                if "application/json" not in request.headers.get("content-type", ""):
                    return JSONResponse({"detail": "application/json 요청이 필요합니다."}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    def require_job(job_id):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "작업을 찾을 수 없습니다.")
        return job

    def validate_write(spec):
        if not spec.dry_run and not settings.can_write:
            raise HTTPException(409, "실제 전송이 잠겨 있습니다. .env에서 운영 전송 조건을 확인하세요.")

    @app.get("/api/state")
    def state():
        jobs = store.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 30")
        for job in jobs:
            job["spec"] = json.loads(job["spec"])
        pulse = store.get_setting("runner", {})
        heartbeat = pulse.get("heartbeat")
        alive = bool(
            heartbeat and (datetime.now(UTC) - datetime.fromisoformat(heartbeat)).total_seconds() < 15
        )
        return {
            "mode": settings.app_mode,
            "can_write": settings.can_write,
            "timezone": settings.timezone,
            "limit": settings.claims_limit,
            "destination": settings.destination,
            "runner_alive": alive,
            "runner": pulse,
            "jobs": jobs,
            "schedule": store.schedule(),
            "endpoints": {
                "claims": settings.claims_base_url.rstrip("/") + settings.claims_path,
                "schema": settings.product_base_url.rstrip("/") + settings.product_schema_path,
                "product": settings.product_base_url.rstrip("/") + settings.product_record_path,
                "target": settings.target_url,
            },
            "key_fields": settings.target_key_fields,
            "unresolved": store.one(
                "SELECT COUNT(*) n FROM deliveries WHERE status IN ('uncertain','sending')"
            )["n"],
            "mock_target_count": store.one("SELECT COUNT(*) n FROM mock_target")["n"]
            if settings.app_mode == "mock"
            else None,
            "mock_scenario": settings.mock_scenario if settings.app_mode == "mock" else None,
        }

    @app.post("/api/jobs", status_code=202)
    def create_job(spec: RunSpec):
        if spec.source_type == "csv":
            raise HTTPException(422, "CSV 가져오기 화면에서 파일을 확인하고 작업을 등록하세요.")
        validate_write(spec)
        if store.one("SELECT COUNT(*) n FROM jobs WHERE status='queued'")["n"] >= 20:
            raise HTTPException(409, "대기 작업이 20개입니다. 기존 작업을 처리하거나 취소하세요.")
        return {"id": store.enqueue(spec, settings.destination)}

    @app.post("/api/preview")
    def preview(spec: RunSpec):
        if spec.source_type == "csv":
            raise HTTPException(422, "CSV 미리보기 API를 사용하세요.")
        start, end = spec.resolve(settings.timezone)
        days = (end - start).days + 1
        return {
            "start_date": str(start),
            "end_date": str(end),
            "days": days,
            "chunks": (days + spec.chunk_days - 1) // spec.chunk_days,
            "timezone": settings.timezone,
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str, after_event: int = Query(0, ge=0)):
        job = require_job(job_id)
        job["chunks"] = store.query(
            "SELECT * FROM chunks WHERE job_id=? ORDER BY id DESC LIMIT 400", (job_id,)
        )[::-1]
        job["chunk_count"] = store.one("SELECT COUNT(*) n FROM chunks WHERE job_id=?", (job_id,))["n"]
        job["chunk_summary"] = store.one(
            """SELECT COUNT(CASE WHEN status='failed' THEN 1 END) failures,
            COALESCE(SUM(CASE WHEN status IN ('completed','partial','failed')
            THEN julianday(end_date)-julianday(start_date)+1 ELSE 0 END),0) finished_days
            FROM chunks WHERE job_id=?""",
            (job_id,),
        )
        job["events"] = store.query(
            "SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id DESC LIMIT 500", (job_id, after_event)
        )[::-1]
        return job

    @app.get("/api/jobs/{job_id}/records")
    def records(
        job_id: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), status: str = ""
    ):
        require_job(job_id)
        clause = "job_id=?" + (" AND status=?" if status else "")
        params = (job_id, status) if status else (job_id,)
        rows = store.query(
            f"SELECT * FROM records WHERE {clause} ORDER BY id LIMIT ? OFFSET ?", (*params, limit, offset)
        )
        for row in rows:
            row["payload"] = json.loads(row["payload"]) if row["payload"] else None
        return {
            "items": rows,
            "total": store.one(f"SELECT COUNT(*) n FROM records WHERE {clause}", params)["n"],
        }

    @app.get("/api/http-exchanges")
    def http_exchanges(
        job_id: str | None = None,
        record_key: str | None = None,
        checks_only: bool = False,
        errors_only: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ):
        clauses, params = [], []
        if job_id:
            require_job(job_id)
            clauses.append("job_id=?")
            params.append(job_id)
        if checks_only:
            clauses.append("job_id IS NULL")
        if record_key is not None:
            clauses.append("record_key=?")
            params.append(record_key)
        if errors_only:
            clauses.append("state IN ('failed','blocked','interrupted')")
        where = " AND ".join(clauses) or "1=1"
        items = store.query(
            f"""SELECT id,job_id,record_key,started_at,finished_at,stage,method,url,status_code,state,duration_ms
            FROM http_exchanges WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        return {
            "items": items,
            "total": store.one(f"SELECT COUNT(*) n FROM http_exchanges WHERE {where}", params)["n"],
        }

    @app.get("/api/http-exchanges/{exchange_id}")
    def http_exchange(exchange_id: int):
        row = store.exchange(exchange_id)
        if row is None:
            raise HTTPException(404, "요청·응답 기록을 찾을 수 없습니다.")
        return row

    @app.get("/api/jobs/{job_id}/delivery-context")
    def delivery_context(job_id: str, record_key: str | None = None):
        require_job(job_id)
        # Older databases have payloads and the original delivery note, but no HTTP exchange rows.
        rows = store.query(
            """SELECT DISTINCT d.* FROM records r JOIN jobs j ON j.id=r.job_id
            JOIN deliveries d ON d.destination=j.destination AND d.record_key=r.record_key
            WHERE r.job_id=? AND r.status='uncertain'"""
            + (" AND r.record_key=?" if record_key is not None else "")
            + " LIMIT 50",
            (job_id, record_key) if record_key is not None else (job_id,),
        )
        diagnostics = RequestDiagnostics(
            settings.claims_headers, settings.product_headers, settings.target_headers
        )
        return [previous_delivery(store, diagnostics, row, settings.http_log_body_bytes) for row in rows]

    @app.get("/api/jobs/{job_id}/export")
    def export(job_id: str):
        require_job(job_id)

        def lines():
            with store.connect() as db:
                for row in db.execute(
                    "SELECT record_key,status,payload,error FROM records WHERE job_id=? ORDER BY id",
                    (job_id,),
                ):
                    item = dict(row)
                    item["request"] = {"values": json.loads(item.pop("payload"))} if item["payload"] else None
                    item.pop("payload", None)
                    yield encode(item) + "\n"

        return StreamingResponse(
            lines(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="claim-sync-{job_id}.ndjson"'},
        )

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        job = require_job(job_id)
        if job["status"] not in {"queued", "running"}:
            raise HTTPException(409, "이미 종료된 작업입니다.")
        store.update_job(job_id, cancel_requested=1)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        job = require_job(job_id)
        if job["status"] in {"running", "queued"}:
            raise HTTPException(409, "실행 중인 작업은 다시 실행할 수 없습니다.")
        if job["destination"] != settings.destination:
            raise HTTPException(409, "전송 대상이 변경되었습니다. 작업을 새로 등록하세요.")
        spec = RunSpec.model_validate(job["spec"])
        validate_write(spec)
        if store.one("SELECT COUNT(*) n FROM jobs WHERE status='queued'")["n"] >= 20:
            raise HTTPException(409, "대기 작업이 20개입니다. 기존 작업을 먼저 처리하세요.")
        if spec.source_type == "csv":
            snapshot = store.csv_snapshot(job_id)
            if not snapshot:
                raise HTTPException(409, "저장된 CSV 실행 데이터가 없습니다. 파일을 다시 가져오세요.")
            return {"id": store.enqueue_csv(spec, settings.destination, snapshot, source="csv-retry")}
        return {"id": store.enqueue(spec, settings.destination, source="retry")}

    @app.get("/api/csv/files")
    def csv_files():
        try:
            return list_csv_files(settings)
        except CsvImportError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/csv/preview")
    def csv_preview(body: CsvPreviewRequest):
        try:
            result = load_csv(settings, body.filename, body.blank_mode)
        except CsvImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {**result, "rows": result["rows"][:50], "preview_limit": 50}

    @app.post("/api/csv/jobs", status_code=202)
    def csv_job(body: CsvJobRequest):
        validate_write(body)
        if store.one("SELECT COUNT(*) n FROM jobs WHERE status='queued'")["n"] >= 20:
            raise HTTPException(409, "대기 작업이 20개입니다. 기존 작업을 먼저 처리하세요.")
        try:
            job_id = enqueue_csv(
                settings,
                store,
                body.filename,
                dry_run=body.dry_run,
                blank_mode=body.blank_mode,
                sha256=body.sha256,
            )
        except CsvImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": job_id}

    @app.put("/api/schedule")
    def save_schedule(spec: ScheduleSpec):
        if spec.enabled:
            validate_write(spec.run)
        return store.save_schedule(spec, settings.destination)

    @app.post("/api/check")
    async def check():
        result = {}
        async with APIClients(settings, store, lambda *_: None) as api:
            for label, action in (("product_schema", api.schema),):
                try:
                    await action()
                    result[label] = {"ok": True, "message": "필수 8개 필드 확인"}
                except UpstreamError as exc:
                    result[label] = {"ok": False, "message": str(exc)}
            start, end = RunSpec(months=1).resolve(settings.timezone)
            try:
                rows, truncated = await api.claims(end, end)
                result["claims"] = {
                    "ok": True,
                    "message": f"오늘 접수 {len(rows)}건 조회"
                    + (" · 한도 확인 필요" if truncated or len(rows) >= settings.claims_limit else ""),
                }
            except UpstreamError as exc:
                result["claims"] = {"ok": False, "message": str(exc)}
        result["target"] = {"ok": None, "message": "전송 API는 검증 실행 또는 API 전송으로 확인합니다."}
        return result

    @app.get("/api/deliveries/unresolved")
    def unresolved():
        rows = store.query(
            """SELECT d.*, j.status AS job_status FROM deliveries d
            LEFT JOIN jobs j ON j.id=d.job_id
            WHERE d.status IN ('uncertain','sending') ORDER BY d.updated_at DESC LIMIT 100"""
        )
        for row in rows:
            row["payload"] = json.loads(row["payload"])
            row["can_resolve"] = row["status"] == "uncertain" and row["job_status"] != "running"
        return rows

    def resolve_deliveries(items: list[Resolution | ResolutionSelection], result: str):
        keys = {(item.destination, item.record_key) for item in items}
        if len(keys) != len(items):
            raise HTTPException(422, "같은 항목을 여러 번 선택할 수 없습니다.")
        note = (
            "운영자가 목록에서 서버 반영 완료로 확인했습니다."
            if result == "applied"
            else "운영자가 목록에서 서버 미반영으로 확인했습니다. 다음 동기화에서 다시 전송할 수 있습니다."
        )
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            deliveries = []
            for item in items:
                delivery = db.execute(
                    "SELECT * FROM deliveries WHERE destination=? AND record_key=?",
                    (item.destination, item.record_key),
                ).fetchone()
                if not delivery or delivery["status"] != "uncertain":
                    raise HTTPException(
                        409, "선택 항목의 상태가 변경되었습니다. 목록을 확인하고 다시 선택하세요."
                    )
                if item.expected_updated_at and item.expected_updated_at != delivery["updated_at"]:
                    raise HTTPException(
                        409, "선택한 이후 전송 정보가 변경되었습니다. 목록을 확인하고 다시 선택하세요."
                    )
                active = db.execute(
                    "SELECT 1 FROM jobs WHERE id=? AND status='running'", (delivery["job_id"],)
                ).fetchone()
                if active:
                    raise HTTPException(409, "해당 작업이 종료된 후 확인 처리하세요.")
                deliveries.append(delivery)
            timestamp = utcnow()
            for delivery in deliveries:
                db.execute(
                    "UPDATE deliveries SET status=?,note=?,updated_at=? WHERE destination=? AND record_key=?",
                    (
                        "success" if result == "applied" else "failed",
                        note,
                        timestamp,
                        delivery["destination"],
                        delivery["record_key"],
                    ),
                )
                db.execute(
                    "INSERT INTO events(job_id,time,level,step,message) VALUES(?,?,?,?,?)",
                    (
                        delivery["job_id"],
                        timestamp,
                        "warning",
                        "reconcile",
                        f"운영자 반영 확인: {result} · {delivery['record_key']} · {note} (API 전송 없음)",
                    ),
                )
        return {"ok": True, "resolved": len(items), "result": result}

    @app.post("/api/deliveries/resolve")
    def resolve(body: Resolution):
        return resolve_deliveries([body], body.result)

    @app.post("/api/deliveries/resolve-selected")
    def resolve_selected(body: SelectedResolution):
        return resolve_deliveries(body.items, body.result)

    return app
