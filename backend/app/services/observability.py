"""Model-call and tool-call logging (each in its own transaction, so failed runs keep their history),
run token accounting, the daily spend guard, and the append-only run event log."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timezone

from sqlalchemy import func, select, update

from app.db import session_scope
from app.llm.gateway import CallRecord
from app.models import ModelCall, Run, RunEvent, ToolCall

log = logging.getLogger("model_calls")


def _uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def record_call_to_db(record: CallRecord) -> None:
    with session_scope() as db:
        db.add(ModelCall(
            trace_id=record.trace_id, run_id=_uuid(record.run_id), task_id=_uuid(record.task_id),
            feature=record.feature, provider=record.provider, model=record.model,
            prompt_version=record.prompt_version, attempt=record.attempt, status=record.status,
            error_category=record.error_category, error_message=record.error_message,
            input_tokens=record.input_tokens, output_tokens=record.output_tokens,
            thinking_tokens=record.thinking_tokens, cost_usd=record.cost_usd, latency_ms=record.latency_ms,
        ))
        if record.run_id and record.total_tokens:
            # Atomic increment: parallel workers charge the same run concurrently.
            db.execute(update(Run).where(Run.id == _uuid(record.run_id)).values(
                spent_tokens=Run.spent_tokens + record.total_tokens, cost_usd=Run.cost_usd + record.cost_usd))
    # Structured log line: metadata only, never prompts, page text or credentials.
    log.info("model_call", extra={
        "trace_id": record.trace_id, "run_id": record.run_id, "feature": record.feature, "model": record.model,
        "attempt": record.attempt, "status": record.status, "error_category": record.error_category,
        "latency_ms": record.latency_ms, "input_tokens": record.input_tokens, "output_tokens": record.output_tokens,
    })


def record_tool_call(run_id: uuid.UUID | None, task_id: uuid.UUID | None, tool: str, provider: str, target: str,
                     status: str, latency_ms: int, result_count: int | None = None, error: str | None = None) -> None:
    with session_scope() as db:
        db.add(ToolCall(run_id=run_id, task_id=task_id, tool=tool, provider=provider, target=target[:2000],
                        status=status, latency_ms=latency_ms, result_count=result_count, error=error))
        if tool == "search" and run_id is not None:
            db.execute(update(Run).where(Run.id == run_id).values(search_calls=Run.search_calls + 1))


def calls_today(db) -> int:
    start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    return db.scalar(select(func.count(ModelCall.id)).where(ModelCall.created_at >= start)) or 0


def under_daily_limit() -> bool:
    from app.config import get_settings

    limit = get_settings().daily_model_call_limit
    if limit <= 0:
        return True
    with session_scope() as db:
        return calls_today(db) < limit


def emit(run_id: uuid.UUID, type_: str, **data) -> None:
    """Append a progress event. The SSE endpoint tails this table, so events survive reconnects."""
    with session_scope() as db:
        db.add(RunEvent(run_id=run_id, type=type_, data=data))
