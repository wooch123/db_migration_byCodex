import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

import uvicorn
from filelock import Timeout

from .config import Settings
from .engine import Engine
from .models import RunSpec, ScheduleSpec
from .runner import Runner
from .store import Store


def read_spec(args):
    if getattr(args, "spec", None):
        return RunSpec.model_validate_json(Path(args.spec).read_text(encoding="utf-8"))
    return RunSpec(
        period_mode="absolute" if args.start or args.end else "relative",
        start_date=args.start,
        end_date=args.end,
        months=args.months,
        chunk_days=args.chunk_days,
        dry_run=not args.send,
    )


async def worker(settings, store):
    runner = Runner(settings, store)
    task = asyncio.create_task(runner.serve())
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, task.cancel)
        except NotImplementedError:
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(task.cancel))
    try:
        await task
    except asyncio.CancelledError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Claim Sync web console and Ubuntu background worker")
    parser.add_argument("--env-file", default=".env", help="Environment file path")
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web", help="Start the web console and optional shared runner")
    web.add_argument("--host")
    web.add_argument("--port", type=int)
    commands.add_parser("worker", help="Run scheduler and queue without a web server")
    commands.add_parser("mock-server", help="Expose the mock APIs on an HTTP port for integration tests")
    run = commands.add_parser("run", help="Run once, exit nonzero on incomplete synchronization")
    run.add_argument("--spec", help="RunSpec JSON file (overrides period and --send flags)")
    run.add_argument("--months", type=int, default=1)
    run.add_argument("--start")
    run.add_argument("--end")
    run.add_argument("--chunk-days", type=int, default=7)
    run.add_argument("--send", action="store_true", help="POST mapped data; defaults to validation only")
    schedule = commands.add_parser("schedule", help="Save a persistent schedule from a JSON file")
    schedule.add_argument("file")
    args = parser.parse_args()
    try:
        settings = Settings(_env_file=args.env_file)
        if args.command in {"web", "mock-server"}:
            host = getattr(args, "host", None) or settings.host
            if host not in {"127.0.0.1", "localhost", "::1"} and not settings.app_access_token:
                parser.error("APP_ACCESS_TOKEN is required for a non-loopback web binding")
            if args.command == "web":
                from .web import create_app

                print(
                    f"[run] API mode: {settings.app_mode.upper()} "
                    f"({'configured HTTP servers' if settings.app_mode == 'live' else 'in-process test fixtures'}).",
                    flush=True,
                )
                app = create_app(settings)
            else:
                if host not in {"127.0.0.1", "localhost", "::1"}:
                    parser.error("The mock server may only bind to loopback")
                from .mock import create_mock_app

                app = create_mock_app(settings, Store(settings.data_dir))
            uvicorn.run(app, host=host, port=getattr(args, "port", None) or settings.port)
        else:
            store = Store(settings.data_dir)
            if args.command == "worker":
                asyncio.run(worker(settings, store))
            elif args.command == "schedule":
                spec = ScheduleSpec.model_validate_json(Path(args.file).read_text(encoding="utf-8"))
                if spec.enabled and not spec.run.dry_run and not settings.can_write:
                    parser.error("Live writes are locked by .env")
                print(
                    json.dumps(store.save_schedule(spec, settings.destination), ensure_ascii=False, indent=2)
                )
            elif args.command == "run":
                spec = read_spec(args)
                runner = Runner(settings, store)
                try:
                    with runner.lock.acquire(timeout=0):
                        store.recover()
                        job_id = store.enqueue(spec, settings.destination, source="cli")
                        asyncio.run(Engine(settings, store).run(job_id))
                except Timeout:
                    parser.error(
                        "Another runner is active. Use the web queue or stop the service before a one-shot run."
                    )
                result = store.job(job_id)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["status"] == "completed" else 1
    except (ValueError, OSError) as exc:
        # Avoid printing Settings ValidationError input values (which may contain secrets).
        print(
            f"Configuration or file error ({type(exc).__name__}). Check .env and the input file.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
