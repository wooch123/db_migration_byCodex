import asyncio
import os
import socket
import subprocess
import sys

import httpx

from claim_sync.config import Settings
from claim_sync.engine import Engine
from claim_sync.models import RunSpec
from claim_sync.store import Store


async def test_real_http_to_separate_mock_process(tmp_path):
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
            settings = Settings(
                _env_file=None,
                app_mode="live",
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
            store = Store(settings.data_dir)
            spec = RunSpec(
                period_mode="absolute",
                start_date="2025-01-01",
                end_date="2025-01-03",
                chunk_days=2,
                dry_run=False,
            )
            job_id = store.enqueue(spec, settings.destination)
            await Engine(settings, store).run(job_id)
            job = store.job(job_id)
            assert job["status"] == "completed" and job["succeeded"] == 18, job
            assert Store(tmp_path / "upstream").one("SELECT COUNT(*) n FROM mock_target")["n"] == 18
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
