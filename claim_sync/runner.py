import asyncio
import logging
import os

from filelock import FileLock, Timeout

from .engine import Engine
from .store import utcnow

logger = logging.getLogger(__name__)


class Runner:
    """One durable queue consumer/scheduler per local data directory, shared by web and CLI."""

    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.stopping = asyncio.Event()
        self.lock = FileLock(str(store.data_dir / "runner.lock"))
        self.active = False

    async def heartbeat(self):
        while not self.stopping.is_set():
            self.store.set_setting(
                "runner", {"pid": os.getpid(), "heartbeat": utcnow(), "mode": self.settings.app_mode}
            )
            await asyncio.sleep(2)

    async def serve(self):
        while not self.stopping.is_set():
            try:
                self.lock.acquire(timeout=0)
                break
            except Timeout:
                await asyncio.sleep(2)
        else:
            return
        self.active = True
        pulse = None
        try:
            self.store.recover()
            pulse = asyncio.create_task(self.heartbeat())
            engine = Engine(self.settings, self.store)
            while not self.stopping.is_set():
                self.store.schedule_tick()
                job = self.store.one("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
                if job:
                    await engine.run(job["id"])
                else:
                    await asyncio.sleep(0.5)
        finally:
            if pulse:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
            self.store.set_setting(
                "runner", {"pid": os.getpid(), "heartbeat": None, "mode": self.settings.app_mode}
            )
            self.active = False
            self.lock.release()

    async def stop(self, task):
        self.stopping.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
