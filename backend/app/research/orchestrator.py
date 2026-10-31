"""Orchestrator: runs the approved plan as a task DAG with caps, then verify -> write.

- Ready tasks (all dependencies done) run in parallel, up to ``max_workers`` at once.
- Caps on every run: sub-questions (plan validation), steps per agent, a token budget (research stops at
  ``research_budget_share`` so verify + write still fit), attempts per task, and a wall-clock timeout.
- State lives in Postgres rows, not in memory: after a crash the scheduler calls ``execute_run`` again and
  it resumes from the last finished task (running tasks go back to pending; finished ones are kept).
- Cancel stops work immediately and keeps partial results: claims already found are checked with the
  deterministic layer only and written into a no-model report.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.db import session_scope
from app.llm.gateway import CallContext, ModelCallFailed
from app.llm.schemas import Plan
from app.models import Run, RunStatus, Task, TaskStatus, utcnow
from app.research.env import ResearchEnv
from app.research.planning import PlanError, depth_profile, plan_prompt, plan_to_tasks, validate_plan
from app.research.researcher import budget_left, run_research_task
from app.research.verifier import verify_run
from app.research.writer import write_report
from app.services.observability import emit

log = logging.getLogger(__name__)

# Failures that another attempt cannot fix.
FATAL_CATEGORIES = {"quota_exhausted", "daily_limit", "auth_error", "config_missing", "model_not_found"}
POLL_SECONDS = 0.5


def set_status(run_id: uuid.UUID, status: RunStatus, **fields) -> None:
    with session_scope() as db:
        run = db.get(Run, run_id)
        run.status = status.value
        for key, value in fields.items():
            setattr(run, key, value)
        if status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled):
            run.finished_at = utcnow()
    emit(run_id, "status", status=status.value, **{k: v for k, v in fields.items() if isinstance(v, (str, int))})


def fail_run(run_id: uuid.UUID, code: str, message: str) -> None:
    log.warning("run failed", extra={"run_id": str(run_id), "error_code": code})
    set_status(run_id, RunStatus.failed, error_code=code, error_message=message[:1000])


async def plan_run(env: ResearchEnv, run_id: uuid.UUID) -> None:
    with session_scope() as db:
        run = db.get(Run, run_id)
        question, depth = run.question, run.depth
    profile = depth_profile(depth)
    try:
        result = await env.gateway.generate(plan_prompt(question, depth), Plan, CallContext(str(run_id)),
                                            max_output_tokens=4096)
        plan = validate_plan(result.output, profile.max_sub_questions)
    except ModelCallFailed as exc:
        fail_run(run_id, exc.category, exc.message)
        return
    except PlanError as exc:
        fail_run(run_id, "invalid_plan", str(exc))
        return
    plan.report.max_words = min(plan.report.max_words, profile.max_words)
    set_status(run_id, RunStatus.awaiting_approval, plan=plan.model_dump())
    emit(run_id, "plan_ready", sub_questions=len(plan.sub_questions))


def approve_plan(db, run: Run, plan: Plan) -> list[Task]:
    """Validate the (possibly edited) plan and turn it into task rows. Caller commits."""
    validate_plan(plan, depth_profile(run.depth).max_sub_questions)
    tasks = [Task(run_id=run.id, **row) for row in plan_to_tasks(plan)]
    db.add_all(tasks)
    run.approved_plan = plan.model_dump()
    run.status = RunStatus.researching.value
    run.research_started_at = utcnow()
    return tasks


def _task_rows(run_id: uuid.UUID) -> list[Task]:
    with session_scope() as db:
        rows = list(db.scalars(select(Task).where(Task.run_id == run_id).order_by(Task.position)))
        db.expunge_all()
    return rows


def _set_task(task_id: uuid.UUID, **fields) -> None:
    with session_scope() as db:
        task = db.get(Task, task_id)
        for key, value in fields.items():
            setattr(task, key, value)


def _cancel_requested(run_id: uuid.UUID) -> bool:
    with session_scope() as db:
        return bool(db.scalar(select(Run.cancel_requested).where(Run.id == run_id)))


async def _run_task(env: ResearchEnv, run_id: uuid.UUID, task: Task, brief: str) -> None:
    """One worker. Never raises (except cancellation): outcomes are written to the task row."""
    attempt = task.attempts + 1
    _set_task(task.id, status=TaskStatus.running.value, attempts=attempt, started_at=utcnow(), error=None)
    emit(run_id, "task_started", task_id=str(task.id), key=task.key, question=task.question, attempt=attempt)
    with session_scope() as db:
        depth = depth_profile(db.get(Run, run_id).depth)
    try:
        outcome = await run_research_task(env, run_id, task.id, brief, depth)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure is recorded on the task row
        category = getattr(exc, "category", type(exc).__name__)
        message = getattr(exc, "message", str(exc))[:500]
        retry = category not in FATAL_CATEGORIES and attempt < env.settings.max_task_attempts
        _set_task(task.id, status=(TaskStatus.pending if retry else TaskStatus.failed).value,
                  error=f"{category}: {message}", finished_at=None if retry else utcnow())
        emit(run_id, "task_retry" if retry else "task_failed", task_id=str(task.id), key=task.key,
             error=category, message=message, attempt=attempt)
        return
    if outcome.steps == 0:
        reason = "budget" if outcome.stopped_for_budget else "no readable sources"
        _set_task(task.id, status=TaskStatus.skipped.value, error=reason, finished_at=utcnow())
        emit(run_id, "task_skipped", task_id=str(task.id), key=task.key, reason=reason)
        return
    _set_task(task.id, status=TaskStatus.done.value)
    emit(run_id, "task_done", task_id=str(task.id), key=task.key, claims=outcome.claims, sources=outcome.sources,
         steps=outcome.steps, quotes_found=outcome.quotes_found, quotes_missing=outcome.quotes_missing)


async def _research_phase(env: ResearchEnv, run_id: uuid.UUID) -> str | None:
    """Run the DAG. Returns a stop reason (timeout, budget, cancelled) or None when every task finished."""
    settings = env.settings
    with session_scope() as db:
        run = db.get(Run, run_id)
        brief = (run.approved_plan or {}).get("brief", run.question)
        max_workers = run.max_workers
        started = run.research_started_at or utcnow()
        # Resume: anything left "running" by a crashed process starts again from pending.
        db.execute(update(Task).where(Task.run_id == run_id, Task.status == TaskStatus.running.value)
                   .values(status=TaskStatus.pending.value))
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    deadline = started + timedelta(seconds=settings.run_timeout_seconds)
    running: dict[asyncio.Task, uuid.UUID] = {}
    stop_reason: str | None = None
    try:
        while True:
            if _cancel_requested(run_id):
                stop_reason = "cancelled"
                break
            tasks = _task_rows(run_id)
            status = {str(t.id): t.status for t in tasks}
            if stop_reason is None and datetime.now(timezone.utc) >= deadline:
                stop_reason = "timeout"
            if stop_reason is None and not budget_left(run_id, settings.research_budget_share):
                stop_reason = "budget"
            for t in tasks:
                if t.status != TaskStatus.pending.value or t.id in running.values():
                    continue
                if any(status.get(d) in (TaskStatus.failed.value, TaskStatus.skipped.value) for d in t.depends_on):
                    _set_task(t.id, status=TaskStatus.skipped.value, error="a dependency did not finish",
                              finished_at=utcnow())
                    emit(run_id, "task_skipped", task_id=str(t.id), key=t.key, reason="dependency")
                elif stop_reason:
                    _set_task(t.id, status=TaskStatus.skipped.value, error=stop_reason, finished_at=utcnow())
                    emit(run_id, "task_skipped", task_id=str(t.id), key=t.key, reason=stop_reason)
                elif (all(status.get(d) == TaskStatus.done.value for d in t.depends_on)
                      and len(running) < max_workers):
                    job = asyncio.create_task(_run_task(env, run_id, t, brief))
                    running[job] = t.id
            if stop_reason == "timeout" and running:
                break  # stop the clock now; running workers are cancelled below
            if not running:
                if any(t.status == TaskStatus.pending.value for t in _task_rows(run_id)) and not stop_reason:
                    await asyncio.sleep(0)
                    continue  # retries just went back to pending
                break
            wait_for = max(0.05, min(POLL_SECONDS, (deadline - datetime.now(timezone.utc)).total_seconds()))
            done, _ = await asyncio.wait(running, timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            for job in done:
                running.pop(job)
                if job.exception() is not None:  # defensive: _run_task records its own failures
                    log.error("worker crashed", exc_info=job.exception())
    finally:
        if running:
            final = TaskStatus.cancelled if stop_reason == "cancelled" or stop_reason is None else TaskStatus.skipped
            for job, task_id in running.items():
                job.cancel()
                _set_task(task_id, status=final.value, error=stop_reason or "cancelled", finished_at=utcnow())
            await asyncio.gather(*running, return_exceptions=True)
    if stop_reason == "cancelled":
        with session_scope() as db:
            db.execute(update(Task).where(Task.run_id == run_id, Task.status == TaskStatus.pending.value)
                       .values(status=TaskStatus.cancelled.value, error="cancelled"))
    return stop_reason


async def finish_cancelled(env: ResearchEnv, run_id: uuid.UUID) -> None:
    """Keep partial results: deterministic checks only, and a report written without the model."""
    try:
        await verify_run(env, run_id, use_model=False)
        await write_report(env, run_id, use_model=False, reason="run cancelled")
    finally:
        set_status(run_id, RunStatus.cancelled, stop_reason="cancelled")


async def execute_run(env: ResearchEnv, run_id: uuid.UUID) -> None:
    """Drive an approved run to a terminal state. Safe to call again after a crash (it resumes)."""
    try:
        with session_scope() as db:
            status = db.get(Run, run_id).status
        if status == RunStatus.researching.value:
            stop = await _research_phase(env, run_id)
            if stop == "cancelled":
                await finish_cancelled(env, run_id)
                return
            with session_scope() as db:
                done = db.scalar(select(func.count(Task.id)).where(Task.run_id == run_id,
                                                                    Task.status == TaskStatus.done.value))
                errors = [t.error for t in db.scalars(select(Task).where(Task.run_id == run_id)) if t.error]
            if not done:
                # Nothing was researched: a "completed" run with an empty report would hide the failure.
                fail_run(run_id, "research_failed", "No sub-question could be researched. "
                         + (f"Last error: {errors[-1]}" if errors else f"Stopped: {stop}"))
                return
            if stop:
                with session_scope() as db:
                    db.get(Run, run_id).stop_reason = stop
                emit(run_id, "research_stopped", reason=stop)
            status = RunStatus.verifying.value
        if status == RunStatus.verifying.value:
            set_status(run_id, RunStatus.verifying)
            try:
                await verify_run(env, run_id, use_model=budget_left(run_id))
            except ModelCallFailed as exc:
                # Degrade, don't lose the research: unjudged pairs count as insufficient evidence.
                emit(run_id, "verify_degraded", error=exc.category)
                await verify_run(env, run_id, use_model=False)
            status = RunStatus.writing.value
        if status == RunStatus.writing.value:
            set_status(run_id, RunStatus.writing)
            await write_report(env, run_id, use_model=budget_left(run_id),
                               reason="" if budget_left(run_id) else "token budget used up")
            set_status(run_id, RunStatus.completed)
    except asyncio.CancelledError:
        await finish_cancelled(env, run_id)
    except ModelCallFailed as exc:
        fail_run(run_id, exc.category, exc.message)
    except Exception as exc:  # noqa: BLE001
        log.exception("run crashed", extra={"run_id": str(run_id)})
        fail_run(run_id, "internal_error", f"{type(exc).__name__}: {exc}")
