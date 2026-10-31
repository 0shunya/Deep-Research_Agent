"""HTTP API, following the roadmap's surface (under /api):

POST /runs, GET /runs/:id/plan, POST /runs/:id/plan/approve, SSE /runs/:id/events,
GET /runs/:id/report, GET /claims/:id, POST /runs/:id/cancel — plus list/detail, sources, claims,
metrics and health for the UI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import __version__
from app.api.errors import ApiError
from app.api.ratelimit import run_limiter
from app.api.schemas import (ApprovePlan, ClaimDetail, ClaimOut, CreateRun, EvidenceOut, ReportOut, RunDetail,
                             RunSummary, SourceOut, TaskOut)
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.llm.schemas import Plan
from app.models import (TERMINAL_RUN_STATUSES, Claim, ModelCall, Report, Run, RunEvent, RunStatus, Source,
                        Task, ToolCall)
from app.research.orchestrator import approve_plan
from app.research.planning import PlanError
from app.research.scheduler import get_scheduler
from app.research.writer import latest_report, load_claim, supporting_sources

router = APIRouter(prefix="/api")
PIPELINE_VERSION = "dra-1"
SSE_POLL_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15
SIMULATED_PROVIDER = "scripted"  # app/research/simulated.py
SIMULATED_SEARCH = "static"


def _get_run(db: Session, run_id: uuid.UUID) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise ApiError(404, "run_not_found", "No research run with that id.")
    return run


def _wall_seconds(run: Run) -> float | None:
    if not run.finished_at:
        return None
    start = run.created_at
    end = run.finished_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return round((end - start).total_seconds(), 1)


def _summary(run: Run) -> dict:
    return dict(id=run.id, question=run.question, depth=run.depth, status=run.status, budget_tokens=run.budget_tokens,
                spent_tokens=run.spent_tokens, cost_usd=run.cost_usd, created_at=run.created_at,
                finished_at=run.finished_at, wall_seconds=_wall_seconds(run), stop_reason=run.stop_reason)


def _scheduler():
    scheduler = get_scheduler()
    if scheduler is None:
        raise ApiError(503, "worker_off", "Background research is disabled on this server (WORKER_MODE=off).")
    return scheduler


@router.get("/health")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001
        database = "unavailable"
    return {"status": "ok" if database == "ok" else "degraded", "version": __version__, "database": database,
            "model_configured": settings.gemini_configured, "models": settings.model_list,
            "search_provider": settings.resolved_search_provider, "worker": settings.worker_mode,
            "access_code_required": bool(settings.demo_access_code)}


@router.post("/runs", status_code=202, response_model=RunDetail)
def create_run(body: CreateRun, request: Request, db: Session = Depends(get_db),
               idempotency_key: str | None = Header(default=None, max_length=128)):
    settings = get_settings()
    key = None
    if idempotency_key:
        key = hashlib.sha256(f"{PIPELINE_VERSION}:{idempotency_key}".encode()).hexdigest()
        existing = db.scalar(select(Run).where(Run.idempotency_key == key))
        if existing:
            return run_detail(existing.id, db)
    run_limiter.check(request)
    scheduler = _scheduler()
    budget = min(body.budget_tokens or settings.default_budget_tokens, settings.max_budget_tokens)
    run = Run(question=body.question.strip(), depth=body.depth, budget_tokens=budget,
              max_workers=body.max_workers or settings.max_parallel_workers,
              search_provider=settings.resolved_search_provider, idempotency_key=key)
    db.add(run)
    try:
        db.commit()
    except IntegrityError:  # a concurrent request with the same key won the race
        db.rollback()
        return run_detail(db.scalar(select(Run.id).where(Run.idempotency_key == key)), db)
    scheduler.submit_plan(run.id)
    return run_detail(run.id, db)


@router.get("/runs", response_model=list[RunSummary])
def list_runs(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    runs = db.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit))
    return [_summary(r) for r in runs]


@router.get("/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.scalar(select(Run).where(Run.id == run_id).options(selectinload(Run.tasks)))
    if run is None:
        raise ApiError(404, "run_not_found", "No research run with that id.")
    verdicts = dict(db.execute(select(Claim.verdict, func.count()).where(Claim.run_id == run_id)
                               .group_by(Claim.verdict)).all())
    sources = db.scalar(select(func.count(Source.id)).where(Source.run_id == run_id)) or 0
    domains = db.scalar(select(func.count(func.distinct(Source.domain))).where(Source.run_id == run_id)) or 0
    counts = {"sources": sources, "domains": domains, "claims": sum(verdicts.values()),
              **{v: verdicts.get(v, 0) for v in ("supported", "disputed", "unverified", "pending")}}
    return RunDetail(**_summary(run), max_workers=run.max_workers, search_provider=run.search_provider,
                     search_calls=run.search_calls, error_code=run.error_code, error_message=run.error_message,
                     cancel_requested=run.cancel_requested, plan=run.plan, approved_plan=run.approved_plan,
                     tasks=[TaskOut.model_validate(t) for t in run.tasks], counts=counts)


@router.get("/runs/{run_id}/plan")
def get_plan(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = _get_run(db, run_id)
    if run.plan is None:
        raise ApiError(409, "plan_not_ready", "The planner has not produced a plan yet.",
                       {"status": run.status})
    return {"status": run.status, "plan": run.plan, "approved_plan": run.approved_plan}


@router.post("/runs/{run_id}/plan/approve", response_model=RunDetail)
def approve(run_id: uuid.UUID, body: ApprovePlan, db: Session = Depends(get_db)):
    run = _get_run(db, run_id)
    if run.status != RunStatus.awaiting_approval.value:
        raise ApiError(409, "not_awaiting_approval", f"The run is {run.status}; only a proposed plan can be approved.")
    scheduler = _scheduler()
    plan = body.plan or Plan.model_validate(run.plan)
    try:
        approve_plan(db, run, plan)
    except PlanError as exc:
        db.rollback()
        raise ApiError(422, "invalid_plan", str(exc)) from exc
    db.commit()
    scheduler.submit_execute(run.id)
    return run_detail(run.id, db)


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
def cancel(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = _get_run(db, run_id)
    if run.status in {s.value for s in TERMINAL_RUN_STATUSES}:
        raise ApiError(409, "already_finished", f"The run is already {run.status}.")
    run.cancel_requested = True
    if run.status in (RunStatus.planning.value, RunStatus.awaiting_approval.value):
        # Nothing expensive has started; stop right here.
        from app.models import utcnow
        run.status, run.stop_reason, run.finished_at = RunStatus.cancelled.value, "cancelled", utcnow()
        db.add(RunEvent(run_id=run.id, type="status", data={"status": "cancelled"}))
        db.commit()
        scheduler = get_scheduler()
        if scheduler:
            scheduler.cancel(run.id)
    else:
        db.commit()
        scheduler = get_scheduler()
        if scheduler:
            scheduler.cancel(run.id)  # the research loop also polls the flag
    return run_detail(run.id, db)


def _fetch_events(run_id: uuid.UUID, after: int) -> tuple[list[RunEvent], str | None]:
    with SessionLocal() as db:
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > after)
                                 .order_by(RunEvent.id).limit(200)))
        status = db.scalar(select(Run.status).where(Run.id == run_id))
        db.expunge_all()
    return events, status


@router.get("/runs/{run_id}/events")
async def events(run_id: uuid.UUID, request: Request, after: int = Query(default=0, ge=0),
                 last_event_id: str | None = Header(default=None)):
    """Server-sent events: tails run_events, so a reconnect (Last-Event-ID) resumes where it left off."""
    with SessionLocal() as db:
        _get_run(db, run_id)
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    terminal = {s.value for s in TERMINAL_RUN_STATUSES} | {RunStatus.awaiting_approval.value}

    async def stream():
        nonlocal cursor
        idle = 0.0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            batch, status = await asyncio.to_thread(_fetch_events, run_id, cursor)
            for event in batch:
                cursor = event.id
                payload = json.dumps({"type": event.type, "at": event.created_at.isoformat(), **event.data},
                                     default=str)
                yield f"id: {event.id}\nevent: progress\ndata: {payload}\n\n"
            if batch:
                idle = 0.0
            elif status in terminal:
                yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                return
            else:
                idle += SSE_POLL_SECONDS
                if idle >= SSE_HEARTBEAT_SECONDS:
                    idle = 0.0
                    yield ": ping\n\n"
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _claim_out(c: Claim) -> ClaimOut:
    return ClaimOut(id=c.id, task_id=c.task_id, text=c.text, verdict=c.verdict, verdict_reason=c.verdict_reason,
                    support_domains=c.support_domains, group_key=c.group_key)


@router.get("/runs/{run_id}/report", response_model=ReportOut)
def report(run_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_run(db, run_id)
    rep = latest_report(db, run_id)
    if rep is None:
        raise ApiError(404, "report_not_ready", "This run has no report yet.")
    ids = {uuid.UUID(cid) for s in rep.sections for sent in s["sentences"] for cid in sent["claim_ids"]}
    claims = list(db.scalars(select(Claim).where(Claim.id.in_(ids)))) if ids else []
    sources = supporting_sources(list(ids)) if ids else {}
    return ReportOut(id=rep.id, run_id=rep.run_id, version=rep.version, title=rep.stats.get("title", "Report"),
                     markdown=rep.markdown, sections=rep.sections, stats=rep.stats, created_at=rep.created_at,
                     claims={str(c.id): _claim_out(c) for c in claims},
                     sources={cid: [SourceOut.model_validate(s) for s in srcs] for cid, srcs in sources.items()})


@router.get("/runs/{run_id}/sources", response_model=list[SourceOut])
def sources(run_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_run(db, run_id)
    return db.scalars(select(Source).where(Source.run_id == run_id).order_by(Source.fetched_at)).all()


@router.get("/runs/{run_id}/claims", response_model=list[ClaimOut])
def claims(run_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_run(db, run_id)
    rows = db.scalars(select(Claim).where(Claim.run_id == run_id).order_by(Claim.created_at))
    return [_claim_out(c) for c in rows]


def _context(text_: str, quote: str, width: int = 280) -> str | None:
    """The stored page text around a quote, so an auditor sees it in place."""
    from app.research.web import normalize_text

    haystack = text_.lower()
    probe = quote.strip().lower()[:60]
    pos = haystack.find(probe)
    if pos < 0:
        norm = normalize_text(quote)[:40]
        pos = normalize_text(text_).find(norm)
        if pos < 0:
            return None
    start, end = max(0, pos - width), min(len(text_), pos + len(quote) + width)
    return ("…" if start else "") + text_[start:end] + ("…" if end < len(text_) else "")


@router.get("/claims/{claim_id}", response_model=ClaimDetail)
def claim_detail(claim_id: uuid.UUID, db: Session = Depends(get_db)):
    claim = load_claim(db, claim_id)
    if claim is None:
        raise ApiError(404, "claim_not_found", "No claim with that id.")
    task = db.get(Task, claim.task_id)
    order = {"supports": 0, "contradicts": 1, "insufficient": 2, "pending": 3}
    evidence = sorted(claim.evidence, key=lambda e: (order.get(e.stance, 4), -(e.score or 0)))
    return ClaimDetail(**_claim_out(claim).model_dump(), run_id=claim.run_id,
                       task_question=task.question if task else None,
                       evidence=[EvidenceOut(id=e.id, quote=e.quote, quote_found=e.quote_found, origin=e.origin,
                                             stance=e.stance, score=e.score, rationale=e.rationale,
                                             context=_context(e.source.text, e.quote) if e.quote_found else None,
                                             source=SourceOut.model_validate(e.source)) for e in evidence])


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1], 1)


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    """The cost dashboard: per-feature model usage and per-run cost and wall time, from model_calls.

    Offline demo data (scripted model, static web) is excluded so it never mixes with measured numbers."""
    calls = db.execute(select(ModelCall.feature, ModelCall.model, ModelCall.status, ModelCall.error_category,
                              ModelCall.input_tokens, ModelCall.output_tokens, ModelCall.thinking_tokens,
                              ModelCall.cost_usd, ModelCall.latency_ms)
                       .where(ModelCall.provider != SIMULATED_PROVIDER)).all()
    features: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0,
                                                     "cost_usd": 0.0, "latencies": []})
    errors: Counter = Counter()
    models: Counter = Counter()
    for c in calls:
        f = features[c.feature]
        f["calls"] += 1
        models[c.model] += 1
        if c.status != "success":
            f["errors"] += 1
            errors[c.error_category or "unknown"] += 1
            continue
        f["input_tokens"] += c.input_tokens or 0
        f["output_tokens"] += (c.output_tokens or 0) + (c.thinking_tokens or 0)
        f["cost_usd"] += float(c.cost_usd or 0)
        f["latencies"].append(c.latency_ms)
    by_feature = {k: {**{x: y for x, y in v.items() if x != "latencies"}, "cost_usd": round(v["cost_usd"], 6),
                      "p50_latency_ms": _pct(v["latencies"], 50), "p95_latency_ms": _pct(v["latencies"], 95)}
                  for k, v in features.items()}
    real = Run.search_provider != SIMULATED_SEARCH
    runs = list(db.scalars(select(Run).where(Run.status == RunStatus.completed.value, real)))
    walls = [w for r in runs if (w := _wall_seconds(r)) is not None]
    costs = [float(r.cost_usd) for r in runs]
    reports = [r.stats for r in db.scalars(select(Report).join(Run, Run.id == Report.run_id).where(real))]
    cited = sum(s.get("cited_claims", 0) for s in reports)
    cited_supported = sum(s.get("cited_supported", 0) for s in reports)
    tools = dict(db.execute(select(ToolCall.tool, func.count()).where(ToolCall.provider != SIMULATED_SEARCH)
                            .group_by(ToolCall.tool)).all())
    status_counts = dict(db.execute(select(Run.status, func.count()).where(real).group_by(Run.status)).all())
    settings = get_settings()
    return {
        "model_calls": len(calls), "by_feature": by_feature, "errors": dict(errors), "by_model": dict(models),
        "tool_calls": tools, "runs": status_counts,
        "completed_runs": len(runs),
        "avg_cost_usd": round(statistics.mean(costs), 6) if costs else None,
        "avg_tokens": round(statistics.mean([r.spent_tokens for r in runs])) if runs else None,
        "p50_wall_seconds": _pct(walls, 50), "p95_wall_seconds": _pct(walls, 95),
        "cited_claims": cited, "cited_supported_share": round(cited_supported / cited, 3) if cited else None,
        "pricing": {"input_per_mtok": settings.price_input_per_mtok, "output_per_mtok": settings.price_output_per_mtok},
    }
