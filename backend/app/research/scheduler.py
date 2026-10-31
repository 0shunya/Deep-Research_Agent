"""Background execution: one asyncio event loop in a daemon thread inside the API process.

The API thread submits work (plan a run, execute an approved run) and returns immediately. Durability
comes from Postgres, not from this loop: on startup ``resume_active_runs`` picks up every run that a
previous process left mid-flight. This design assumes a single API instance; running several would need
a lease column (or SKIP LOCKED claiming) so two processes never drive the same run.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Callable

from sqlalchemy import select

from app.db import session_scope
from app.models import Run, RunStatus
from app.research.env import ResearchEnv, build_default_env
from app.research.orchestrator import execute_run, fail_run, plan_run

log = logging.getLogger(__name__)


class RunScheduler:
    def __init__(self, env_factory: Callable[[], ResearchEnv] = build_default_env):
        self._env_factory = env_factory
        self._env: ResearchEnv | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._jobs: dict[uuid.UUID, asyncio.Task] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="run-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop is None:
            return
        for job in list(self._jobs.values()):
            self._loop.call_soon_threadsafe(job.cancel)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._loop = self._thread = None

    def _env_or_fail(self, run_id: uuid.UUID) -> ResearchEnv | None:
        if self._env is None:
            try:
                self._env = self._env_factory()
            except Exception as exc:  # e.g. GEMINI_API_KEY missing
                fail_run(run_id, getattr(exc, "category", "config_missing"), getattr(exc, "message", str(exc)))
                return None
        return self._env

    def _submit(self, run_id: uuid.UUID, make: Callable[[ResearchEnv], object]) -> None:
        assert self._loop is not None, "scheduler not started"

        def create() -> None:
            with self._lock:
                if run_id in self._jobs and not self._jobs[run_id].done():
                    return  # never drive one run twice
            env = self._env_or_fail(run_id)
            if env is None:
                return
            job = self._loop.create_task(make(env))
            with self._lock:
                self._jobs[run_id] = job
            job.add_done_callback(lambda _: self._jobs.pop(run_id, None))

        self._loop.call_soon_threadsafe(create)

    def submit_plan(self, run_id: uuid.UUID) -> None:
        self._submit(run_id, lambda env: plan_run(env, run_id))

    def submit_execute(self, run_id: uuid.UUID) -> None:
        self._submit(run_id, lambda env: execute_run(env, run_id))

    def cancel(self, run_id: uuid.UUID) -> bool:
        """Cancel the in-process job if there is one; the DB flag covers everything else."""
        with self._lock:
            job = self._jobs.get(run_id)
        if job is None or self._loop is None:
            return False
        self._loop.call_soon_threadsafe(job.cancel)
        return True

    def resume_active_runs(self) -> int:
        with session_scope() as db:
            rows = db.execute(select(Run.id, Run.status).where(Run.status.in_(
                [RunStatus.planning.value, RunStatus.researching.value, RunStatus.verifying.value,
                 RunStatus.writing.value]))).all()
        for run_id, status in rows:
            log.info("resuming run", extra={"run_id": str(run_id), "status": status})
            if status == RunStatus.planning.value:
                self.submit_plan(run_id)
            else:
                self.submit_execute(run_id)  # a cancel flag is honoured inside the research loop
        return len(rows)


scheduler: RunScheduler | None = None


def get_scheduler() -> RunScheduler | None:
    return scheduler


def set_scheduler(value: RunScheduler | None) -> None:
    global scheduler
    scheduler = value
