import asyncio
import json

import httpx
import pytest

from claim_sync.engine import Engine
from claim_sync.mapping import PRODUCT_FIELDS
from claim_sync.models import RunSpec
from claim_sync.store import Store, encode


async def run(settings, store, spec, transport=None):
    job_id = store.enqueue(spec, settings.destination)
    await Engine(settings, store, transport).run(job_id)
    return store.job(job_id)


def custom_transport(claim, product, handler=None):
    calls = []

    async def respond(request):
        calls.append(request)
        if handler:
            override = await handler(request)
            if override is not None:
                return override
        if "schema" in request.url.path:
            return httpx.Response(200, json={"properties": dict.fromkeys(PRODUCT_FIELDS, {})})
        if "searchFlashClaims" in request.url.path:
            return httpx.Response(200, json=[claim])
        if request.method == "GET":
            return httpx.Response(200, json=product)
        return httpx.Response(200, json={"success": True})

    return httpx.MockTransport(respond), calls


async def test_full_pipeline_and_persistent_skip(settings, store, spec):
    first = await run(settings, store, spec)
    assert first["status"] == "completed" and first["succeeded"] == 18
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 18
    second = await run(settings, Store(settings.data_dir), spec)
    assert second["status"] == "completed" and second["skipped"] == 18 and second["succeeded"] == 0
    payload = json.loads(store.one("SELECT payload FROM mock_target")["payload"])
    assert len(payload) == 21 and payload["nand"] == "V8 1.0"


async def test_dry_run_never_posts_or_populates_delivery_ledger(settings, store, spec):
    job = await run(settings, store, spec.model_copy(update={"dry_run": True}))
    assert job["succeeded"] == 18
    assert store.one("SELECT COUNT(*) n FROM deliveries")["n"] == 0
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 0
    assert (await run(settings, store, spec))["succeeded"] == 18


async def test_adaptive_split_inclusive_complete(settings, store):
    settings.claims_limit = 13
    spec = RunSpec(
        period_mode="absolute", start_date="2025-01-01", end_date="2025-01-10", chunk_days=10, dry_run=False
    )
    job = await run(settings, store, spec)
    assert job["status"] == "completed" and job["fetched"] == 60 and job["succeeded"] == 60
    assert store.one("SELECT COUNT(*) n FROM chunks WHERE status='split'")["n"] > 0
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 60


async def test_response_byte_limit_also_splits(settings, store, spec):
    settings.max_response_bytes = 5000
    spec.chunk_days = 3
    job = await run(settings, store, spec)
    assert job["status"] == "completed" and job["succeeded"] == 18
    assert store.one("SELECT COUNT(*) n FROM chunks WHERE status='split'")["n"] > 0


async def test_one_day_response_too_large_is_not_sent(settings, store, spec):
    settings.max_response_bytes = 1024
    job = await run(settings, store, spec)
    assert job["status"] == "failed" and job["succeeded"] == 0
    assert store.one("SELECT COUNT(*) n FROM deliveries")["n"] == 0


async def test_day_at_cap_stops_without_silent_truncation(settings, store, spec):
    settings.claims_limit = 6
    job = await run(settings, store, spec)
    assert job["status"] == "failed" and job["succeeded"] == 0
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 0
    assert store.one("SELECT COUNT(*) n FROM chunks WHERE status='failed'")["n"] == 3


async def test_product_failure_is_visible_and_other_records_continue(settings, store, spec):
    settings.mock_scenario = "product_missing"
    job = await run(settings, store, spec)
    assert job["status"] == "partial" and job["failed"] == 6 and job["succeeded"] == 12
    assert "HTTP 404" in store.one("SELECT error FROM records WHERE status='failed'")["error"]


async def test_target_rejection_is_not_retried(settings, store, spec):
    settings.mock_scenario = "target_error"
    job = await run(settings, store, spec)
    assert job["status"] == "failed" and job["failed"] == 18
    assert store.one("SELECT COUNT(*) n FROM deliveries WHERE status='failed'")["n"] == 18


async def test_lost_response_blocks_further_posts_and_retries(settings, store, spec):
    settings.mock_scenario = "target_timeout"
    job = await run(settings, store, spec)
    assert job["status"] == "needs_attention" and job["uncertain"] == 1
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 1
    settings.mock_scenario = "none"
    repeat = await run(settings, store, spec)
    assert repeat["status"] == "needs_attention" and repeat["succeeded"] == 0
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 1


async def test_live_writes_locked_before_network(settings, store, spec):
    settings.app_mode = "live"
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request))
    job = await run(settings, store, spec, transport)
    assert job["status"] == "failed" and not calls


async def test_environment_change_does_not_redirect_queued_job(settings, store, spec):
    job_id = store.enqueue(spec, settings.destination)
    settings.target_dataset_id = "different"
    await Engine(settings, store).run(job_id)
    assert store.job(job_id)["status"] == "failed"
    assert store.one("SELECT COUNT(*) n FROM mock_target")["n"] == 0


async def test_changed_product_is_posted_and_a_b_a_uses_new_operation_key(settings, store, claim, product):
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    transport, calls = custom_transport(claim, product)
    assert (await run(settings, store, spec, transport))["succeeded"] == 1
    assert (await run(settings, store, spec, transport))["skipped"] == 1
    product["denstiy"] = "2 TB"
    assert (await run(settings, store, spec, transport))["succeeded"] == 1
    product["denstiy"] = "1 TB"
    assert (await run(settings, store, spec, transport))["succeeded"] == 1
    posts = [request for request in calls if request.method == "POST"]
    assert len(posts) == 3
    assert len({request.headers["Idempotency-Key"] for request in posts}) == 3
    assert json.loads(posts[0].content)["values"]["part_id"] == claim["partId"]


async def test_get_retry_and_product_cache(settings, store, claim, product):
    settings.get_retries = 2
    attempts = 0

    async def handler(request):
        nonlocal attempts
        if "searchFlashClaims" in request.url.path:
            attempts += 1
            if attempts == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=[claim, {**claim, "sampleNo": "S002"}])

    transport, calls = custom_transport(claim, product, handler)
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    job = await run(settings, store, spec, transport)
    assert job["succeeded"] == 2 and attempts == 2
    assert len([r for r in calls if "/record/" in r.url.path]) == 1
    claim_request = next(r for r in calls if "searchFlashClaims" in r.url.path)
    assert claim_request.url.params["rcvDateFrom"] == "2025-01-01"
    assert claim_request.url.params["rcvDateTo"] == "2025-01-01"


async def test_duplicate_rows_do_not_override_success_record(settings, store, claim, product):
    async def handler(request):
        if "searchFlashClaims" in request.url.path:
            return httpx.Response(200, json=[claim, claim])

    transport, calls = custom_transport(claim, product, handler)
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    job = await run(settings, store, spec, transport)
    assert job["succeeded"] == 1 and job["skipped"] == 1
    assert store.one("SELECT status FROM records")["status"] == "sent"
    assert len([r for r in calls if r.method == "POST"]) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(202, json={}),
        httpx.Response(500),
        httpx.Response(200, json={"success": False}),
        httpx.Response(200, text="<html>error</html>"),
    ],
)
async def test_ambiguous_target_acknowledgement(settings, store, claim, product, response):
    async def handler(request):
        return response if request.method == "POST" else None

    transport, calls = custom_transport(claim, product, handler)
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    assert (await run(settings, store, spec, transport))["status"] == "needs_attention"
    assert len([r for r in calls if r.method == "POST"]) == 1


async def test_missing_schema_and_out_of_range_claim(settings, store, claim, product):
    async def handler(request):
        if "schema" in request.url.path:
            return httpx.Response(200, json={"properties": {"app": {}}})

    transport, calls = custom_transport(claim, product, handler)
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    assert (await run(settings, store, spec, transport))["status"] == "failed"
    assert len(calls) == 1
    claim["rcvDate"] = "2025-02-01"
    transport, calls = custom_transport(claim, product)
    assert (await run(settings, store, spec, transport))["failed"] == 1
    assert not [r for r in calls if r.method == "POST"]


async def test_cancel_before_post_and_restart_recovery(settings, store, spec):
    job_id = store.enqueue(spec, settings.destination)
    store.update_job(job_id, cancel_requested=1)
    await Engine(settings, store).run(job_id)
    assert store.job(job_id)["status"] == "cancelled"
    interrupted = store.enqueue(spec, settings.destination)
    store.update_job(interrupted, status="running")
    store.save_delivery(
        settings.destination, encode(["FAR", "S001"]), "fingerprint", {}, "sending", interrupted
    )
    Store(settings.data_dir).recover()
    assert store.job(interrupted)["status"] == "interrupted"
    assert store.one("SELECT status FROM deliveries")["status"] == "uncertain"


async def test_cancellation_during_post_preserves_uncertainty(settings, store, claim, product):
    posting = asyncio.Event()

    async def handler(request):
        if request.method == "POST":
            posting.set()
            await asyncio.Event().wait()

    transport, _ = custom_transport(claim, product, handler)
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    job_id = store.enqueue(spec, settings.destination)
    task = asyncio.create_task(Engine(settings, store, transport).run(job_id))
    await asyncio.wait_for(posting.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.job(job_id)["status"] == "interrupted"
    assert store.one("SELECT status FROM deliveries")["status"] == "uncertain"
