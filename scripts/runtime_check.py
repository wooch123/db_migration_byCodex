"""Check the selected dependencies using temporary data and in-process mock APIs only."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from filelock import FileLock

from claim_sync.config import Settings
from claim_sync.engine import Engine
from claim_sync.models import RunSpec
from claim_sync.store import Store
from claim_sync.web import create_app


class CheckSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # Installation checks must never inherit live endpoints, credentials or data paths.
        return (init_settings,)


async def check(folder: Path):
    settings = CheckSettings(app_mode="mock", data_dir=folder, enable_runner=False, get_retries=0)
    store = Store(folder)
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-01-01", dry_run=False)
    job_id = store.enqueue(spec, settings.destination)
    await Engine(settings, store).run(job_id)
    job = store.job(job_id)
    if job["status"] != "completed" or job["succeeded"] != 6:
        raise RuntimeError("Mock claim/product/FAR pipeline did not complete successfully.")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            for path in ("/healthz", "/", "/api/state", "/api/openapi.json"):
                response = await client.get(path)
                if response.status_code != 200:
                    raise RuntimeError(f"Web compatibility check failed: {path} ({response.status_code}).")


def main():
    with TemporaryDirectory(prefix="claim-sync-check-") as directory:
        folder = Path(directory)
        with FileLock(folder / "check.lock"):
            asyncio.run(check(folder))
    print("[setup] Runtime check passed: web responses and 6 mock records.")


if __name__ == "__main__":
    main()
