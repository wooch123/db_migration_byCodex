import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from claim_sync.config import Settings
from claim_sync.engine import Engine
from claim_sync.models import RunSpec
from claim_sync.store import Store


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("config_source", ["defaults", "deployment_template"])
async def test_real_http_to_separate_mock_process(tmp_path, monkeypatch, dry_run, config_source):
    """Exercise actual sockets, URL query, JSON POST and persistent upsert, never intranet endpoints."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "mock.env"
    config.write_text(
        f"APP_MODE=mock\nHOST=127.0.0.1\nPORT={port}\nDATA_DIR={tmp_path.as_posix()}/upstream\n"
        "MOCK_SCENARIO=none\nMOCK_CLAIMS_PER_DAY=6\n",
        encoding="utf-8",
    )
    # Isolate from developer environment variables; preserve OS/runtime paths only.
    allowed = {"path", "systemroot", "windir", "temp", "tmp", "home", "userprofile", "lang"}
    env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    with (tmp_path / "mock.log").open("w", encoding="utf-8") as logs:
        process = subprocess.Popen(
            [sys.executable, "-m", "claim_sync.cli", "--env-file", str(config), "mock-server"],
            env=env,
            stdout=logs,
            stderr=logs,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                deadline = asyncio.get_running_loop().time() + 15
                while True:
                    assert process.poll() is None, (tmp_path / "mock.log").read_text(encoding="utf-8")
                    try:
                        response = await client.get(base + "/dbms/api/product-info/schema")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.1)
            monkeypatch.delenv("APP_MODE", raising=False)

            def reject_in_process_mock(*args, **kwargs):
                pytest.fail("The default runtime must use real HTTP, not the in-process mock.")

            monkeypatch.setattr("claim_sync.mock.create_mock_app", reject_in_process_mock)
            settings = Settings(
                _env_file=(
                    Path(__file__).resolve().parent.parent / ".env.example"
                    if config_source == "deployment_template"
                    else None
                ),
                data_dir=tmp_path / "engine",
                claims_base_url=base,
                product_base_url=base,
                target_base_url=base,
                claims_headers={},
                product_headers={},
                target_headers={},
                allow_live_writes=True,
                target_upsert_confirmed=True,
                target_success_path="success",
                target_success_value="true",
                get_retries=0,
            )
            assert settings.app_mode == "live"
            store = Store(settings.data_dir)
            spec = RunSpec(
                period_mode="absolute",
                start_date="2025-01-01",
                end_date="2025-01-03",
                chunk_days=2,
                dry_run=dry_run,
            )
            job_id = store.enqueue(spec, settings.destination)
            await Engine(settings, store).run(job_id)
            job = store.job(job_id)
            assert job["status"] == "completed" and job["succeeded"] == 18, job
            exchanges = store.query(
                "SELECT id,method,status_code FROM http_exchanges WHERE job_id=?", (job_id,)
            )
            assert exchanges and all(row["status_code"] == 200 for row in exchanges)
            posts = [row for row in exchanges if row["method"] == "POST"]
            assert len(posts) == (0 if dry_run else 18)
            if posts:
                exchange = store.exchange(posts[0]["id"])
                assert '"values"' in exchange["details"]["request"]["body"]
                assert '"success": true' in exchange["details"]["response"]["body"]
            assert Store(tmp_path / "upstream").one("SELECT COUNT(*) n FROM mock_target")["n"] == (
                0 if dry_run else 18
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
