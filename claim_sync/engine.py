import asyncio
import hashlib
from collections import OrderedDict
from datetime import date, timedelta

from .clients import AmbiguousDelivery, APIClients, OversizedResponse, UpstreamError
from .config import Settings
from .mapping import map_record, normalized_date, part_prefix
from .models import RunSpec, chunks
from .store import Store, encode, utcnow


class CancelledRun(Exception):
    pass


class Engine:
    def __init__(self, settings: Settings, store: Store, transport=None):
        self.settings = settings
        self.store = store
        self.transport = transport

    def check_cancel(self, job_id):
        if self.store.job(job_id)["cancel_requested"]:
            raise CancelledRun()

    async def run(self, job_id):
        job = self.store.job(job_id)
        if not job or job["status"] != "queued":
            return
        spec = RunSpec.model_validate(job["spec"])

        def log(level, step, message):
            self.store.event(job_id, level, step, message)

        self.store.update_job(job_id, status="running", started_at=utcnow())
        try:
            self.check_cancel(job_id)
            if job["destination"] != self.settings.destination:
                raise UpstreamError(
                    "실행 환경 또는 전송 대상이 변경되었습니다. 설정을 확인하고 작업을 새로 등록하세요."
                )
            if not spec.dry_run and not self.settings.can_write:
                raise UpstreamError("실제 전송이 잠겨 있습니다. .env의 운영 전송 조건을 확인하세요.")
            start, end = spec.resolve(self.settings.timezone)
            self.store.update_job(job_id, start_date=str(start), end_date=str(end))
            log(
                "info",
                "start",
                f"{start} ~ {end} · {spec.chunk_days}일 단위 · {'전송 없이 검증' if spec.dry_run else 'API 전송'}",
            )
            cache = OrderedDict()
            async with APIClients(self.settings, self.store, log, self.transport) as api:
                await api.schema()
                log("info", "schema", "제품 schema의 필수 8개 필드를 확인했습니다.")
                for lower, upper in chunks(start, end, spec.chunk_days):
                    await self.process_chunk(api, job_id, spec, lower, upper, cache)
            final = self.store.job(job_id)
            bad_chunks = self.store.one(
                "SELECT COUNT(*) n FROM chunks WHERE job_id=? AND status='failed'", (job_id,)
            )["n"]
            status = "completed"
            if final["failed"] or bad_chunks:
                status = "partial" if final["succeeded"] or final["skipped"] else "failed"
            self.store.update_job(job_id, status=status, finished_at=utcnow())
            log(
                "info" if status == "completed" else "warning",
                "finish",
                f"실행 종료 · 처리 {final['processed']} · 성공 {final['succeeded']} · 변경 없음 {final['skipped']} · 실패 {final['failed']} · 실패 구간 {bad_chunks}",
            )
        except CancelledRun:
            self.store.update_job(job_id, status="cancelled", finished_at=utcnow())
            log("warning", "cancel", "요청에 따라 중지했습니다. 이미 완료된 전송은 유지됩니다.")
        except AmbiguousDelivery as exc:
            self.store.update_job(job_id, status="needs_attention", error=str(exc), finished_at=utcnow())
            log("error", "delivery", str(exc))
        except asyncio.CancelledError:
            self.store.update_job(
                job_id, status="interrupted", error="실행기 종료로 중단되었습니다.", finished_at=utcnow()
            )
            log("warning", "shutdown", "실행기가 종료되었습니다. 재실행 전 전송 확인 대기 항목을 확인하세요.")
            raise
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, (ValueError, UpstreamError))
                else f"내부 오류 ({type(exc).__name__}). 서버 로그와 상태 저장소를 확인하세요."
            )
            self.store.update_job(job_id, status="failed", error=message, finished_at=utcnow())
            log("error", "fatal", message)
        finally:
            self.store.execute(
                "UPDATE deliveries SET status='uncertain',note='전송 중 실행 중단: 반영 여부 확인 필요' WHERE job_id=? AND status='sending'",
                (job_id,),
            )
            self.store.execute(
                "UPDATE chunks SET status='interrupted' WHERE job_id=? AND status IN ('fetching','processing')",
                (job_id,),
            )

    async def process_chunk(self, api, job_id, spec, start, end, cache):
        self.check_cancel(job_id)
        chunk_id = self.store.execute(
            "INSERT INTO chunks(job_id,start_date,end_date,status) VALUES(?,?,?,'fetching')",
            (job_id, str(start), str(end)),
        )

        def log(level, step, message):
            self.store.event(job_id, level, step, message)

        log("info", "claims", f"Claim 조회: {start} ~ {end}")
        try:
            oversized = False
            try:
                rows, truncated = await api.claims(start, end)
            except OversizedResponse:
                rows, truncated, oversized = [], True, True
            self.check_cancel(job_id)
            if truncated or len(rows) >= self.settings.claims_limit:
                if start == end:
                    raise UpstreamError(
                        f"{start}: 하루치 데이터가 한도({self.settings.claims_limit}건/응답 크기)에 도달했습니다. 페이지네이션 또는 더 세밀한 조회 API가 필요합니다. 이 날짜는 전송하지 않았습니다."
                    )
                self.store.execute(
                    "UPDATE chunks SET status='split',count=? WHERE id=?", (len(rows), chunk_id)
                )
                log(
                    "warning",
                    "split",
                    f"{start} ~ {end}: {'응답 크기' if oversized else '건수'} 한도 감지, 두 구간으로 분할합니다.",
                )
                midpoint = start + timedelta(days=(end - start).days // 2)
                await self.process_chunk(api, job_id, spec, start, midpoint, cache)
                await self.process_chunk(api, job_id, spec, midpoint + timedelta(days=1), end, cache)
                return
            self.store.execute(
                "UPDATE chunks SET status='processing',count=? WHERE id=?", (len(rows), chunk_id)
            )
            self.store.increment(job_id, fetched=len(rows))
            before = self.store.job(job_id)["failed"]
            for index, claim in enumerate(rows):
                self.check_cancel(job_id)
                await self.process_record(
                    api, job_id, spec, claim, start, end, f"invalid:{chunk_id}:{index}", cache
                )
                await asyncio.sleep(0)  # Let UI, cancellation and runner heartbeat make progress.
            after = self.store.job(job_id)["failed"]
            self.store.execute(
                "UPDATE chunks SET status=? WHERE id=?",
                ("partial" if after > before else "completed", chunk_id),
            )
            log("info", "chunk", f"{start} ~ {end}: {len(rows)}건 처리 완료")
        except (CancelledRun, AmbiguousDelivery, asyncio.CancelledError):
            raise
        except (UpstreamError, ValueError) as exc:
            self.store.execute("UPDATE chunks SET status='failed',error=? WHERE id=?", (str(exc), chunk_id))
            log("error", "chunk", str(exc))

    async def process_record(self, api, job_id, spec, claim, start, end, fallback_key, cache):
        key, values = fallback_key, None
        status, error = "failed", None
        duplicate = False
        try:
            claim_date = normalized_date(claim.get("rcvDate"), "rcvDate")
            if not claim_date or not start <= date.fromisoformat(claim_date) <= end:
                raise ValueError("rcvDate가 요청한 조회 구간에 포함되지 않습니다.")
            prefix = part_prefix(claim)
            if prefix not in cache:
                cache[prefix] = await api.product(prefix)
                if len(cache) > 4096:
                    cache.popitem(last=False)
            else:
                cache.move_to_end(prefix)
            values = map_record(claim, cache[prefix])
            identity = [values.get(field) for field in self.settings.target_key_fields]
            if any(value is None or not str(value).strip() for value in identity):
                raise ValueError("전송 대상의 업무 키 값이 비어 있습니다.")
            key = encode(identity)
            prior = self.store.one(
                "SELECT payload FROM records WHERE job_id=? AND record_key=?", (job_id, key)
            )
            if prior:
                if prior["payload"] != encode(values):
                    raise ValueError(
                        "같은 업무 키에 서로 다른 데이터가 반환되었습니다. 원본 중복을 확인하세요."
                    )
                duplicate = True
                self.store.increment(job_id, processed=1, skipped=1)
                return
            fingerprint = hashlib.sha256(
                encode([self.settings.destination, key, values]).encode()
            ).hexdigest()
            delivery = self.store.delivery(self.settings.destination, key)
            if spec.dry_run:
                status = "validated"
            elif delivery and delivery["status"] in {"sending", "uncertain"}:
                raise AmbiguousDelivery(
                    "이 업무 키의 이전 전송 결과가 불확실합니다. 전송 확인 대기 목록에서 반영 여부를 확인하세요."
                )
            elif delivery and delivery["status"] == "success" and delivery["fingerprint"] == fingerprint:
                status = "skipped"
            else:
                self.check_cancel(job_id)
                self.store.save_delivery(
                    self.settings.destination, key, fingerprint, values, "sending", job_id
                )
                try:
                    # A new operation key for A -> B -> A avoids reusing an old idempotent response.
                    operation_key = hashlib.sha256(f"{fingerprint}:{job_id}".encode()).hexdigest()
                    await api.send(values, operation_key)
                except (AmbiguousDelivery, asyncio.CancelledError) as exc:
                    self.store.save_delivery(
                        self.settings.destination,
                        key,
                        fingerprint,
                        values,
                        "uncertain",
                        job_id,
                        str(exc) or "전송 중 실행기 종료",
                    )
                    raise
                except UpstreamError as exc:
                    self.store.save_delivery(
                        self.settings.destination, key, fingerprint, values, "failed", job_id, str(exc)
                    )
                    raise
                self.store.save_delivery(
                    self.settings.destination, key, fingerprint, values, "success", job_id
                )
                status = "sent"
            self.store.increment(
                job_id, processed=1, **({"skipped": 1} if status == "skipped" else {"succeeded": 1})
            )
        except AmbiguousDelivery as exc:
            status, error = "uncertain", str(exc)
            self.store.increment(job_id, processed=1, uncertain=1)
            raise
        except (UpstreamError, ValueError) as exc:
            error = str(exc)
            self.store.increment(job_id, processed=1, failed=1)
            self.store.event(job_id, "error", "record", f"레코드 처리 실패 ({key}): {error}")
        except (CancelledRun, asyncio.CancelledError):
            status, error = "interrupted", "실행 중단"
            raise
        finally:
            if not duplicate:
                self.store.execute(
                    """INSERT INTO records(job_id,record_key,status,payload,error,created_at) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(job_id,record_key) DO UPDATE SET status=excluded.status,error=excluded.error""",
                    (job_id, key, status, encode(values) if values else None, error, utcnow()),
                )
