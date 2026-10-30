"""Relational schema, following the roadmap's Build 04 data model.

runs -> tasks -> claims -> evidence <- sources, and reports -> report_citations -> claims.
Evidence is the join table between claims and sources, so one claim can be backed or contradicted by
many pages and one page can back many claims. Sources keep the raw page, a content hash and dates, so a
report can be re-checked later against exactly what the agent read.

Deviation from the roadmap: ``tasks.depends_on`` is a JSON list of task ids instead of ``uuid[]`` so the
same models run on SQLite in unit tests. On PostgreSQL it is JSONB.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index,
                        Integer, Numeric, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")
BigId = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _in(column: str, enum_cls: type[enum.Enum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class RunStatus(str, enum.Enum):
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    researching = "researching"
    verifying = "verifying"
    writing = "writing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


ACTIVE_RUN_STATUSES = (RunStatus.planning, RunStatus.researching, RunStatus.verifying, RunStatus.writing)
TERMINAL_RUN_STATUSES = (RunStatus.completed, RunStatus.failed, RunStatus.cancelled)


class TaskKind(str, enum.Enum):
    research = "research"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"      # a dependency failed, or the budget or clock ran out first
    cancelled = "cancelled"


class Verdict(str, enum.Enum):
    pending = "pending"
    supported = "supported"
    disputed = "disputed"
    unverified = "unverified"


class Stance(str, enum.Enum):
    pending = "pending"
    supports = "supports"
    contradicts = "contradicts"
    insufficient = "insufficient"


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (CheckConstraint(_in("status", RunStatus), name="ck_runs_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    depth: Mapped[str] = mapped_column(String(16), default="standard")
    status: Mapped[str] = mapped_column(String(24), default=RunStatus.planning.value, index=True)
    budget_tokens: Mapped[int] = mapped_column(Integer)
    spent_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    max_workers: Mapped[int] = mapped_column(Integer, default=3)
    search_provider: Mapped[str] = mapped_column(String(32))
    plan: Mapped[dict | None] = mapped_column(JSONType, nullable=True)        # the proposed plan, as JSON
    approved_plan: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)  # budget, timeout, cancelled...
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_calls: Mapped[int] = mapped_column(Integer, default=0)
    # Hash of the client's Idempotency-Key plus the pipeline version: a retried or double-clicked
    # "start research" returns the existing run instead of paying for a second one.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    research_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tasks: Mapped[list[Task]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                             order_by="Task.position")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (CheckConstraint(_in("status", TaskStatus), name="ck_tasks_status"),
                      UniqueConstraint("run_id", "key", name="uq_tasks_run_key"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(16))           # the planner's id, e.g. "q1"
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default=TaskKind.research.value)
    question: Mapped[str] = mapped_column(Text)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    depends_on: Mapped[list] = mapped_column(JSONType, default=list)  # task ids (str uuid)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.pending.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # compressed findings handed to dependants
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="tasks")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("run_id", "url", name="uq_sources_run_url"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), index=True)       # registrable domain, e.g. bbc.co.uk
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256 of the extracted text
    shingle_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # near-duplicate fingerprint
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)  # the page as fetched (capped)
    text: Mapped[str] = mapped_column(Text)                              # readable text the agent saw
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    search_provider: Mapped[str] = mapped_column(String(32))
    search_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (CheckConstraint(_in("verdict", Verdict), name="ck_claims_verdict"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    group_key: Mapped[str] = mapped_column(String(64), index=True)  # duplicates across tasks share one key
    verdict: Mapped[str] = mapped_column(String(16), default=Verdict.pending.value)
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    support_domains: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    evidence: Mapped[list[Evidence]] = relationship(back_populates="claim", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (CheckConstraint(_in("stance", Stance), name="ck_evidence_stance"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    quote: Mapped[str] = mapped_column(Text)
    quote_found: Mapped[bool] = mapped_column(Boolean, default=False)  # the quote occurs verbatim in the stored page
    origin: Mapped[str] = mapped_column(String(16), default="extracted")  # extracted | cross_check
    stance: Mapped[str] = mapped_column(String(16), default=Stance.pending.value)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    claim: Mapped[Claim] = relationship(back_populates="evidence")
    source: Mapped[Source] = relationship()


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("run_id", "version", name="uq_reports_run_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    markdown: Mapped[str] = mapped_column(Text)
    sections: Mapped[list] = mapped_column(JSONType, default=list)   # [{heading, kind, sentences:[{idx,text,claim_ids}]}]
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)       # citation-check results
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportCitation(Base):
    __tablename__ = "report_citations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    sentence_idx: Mapped[int] = mapped_column(Integer)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)


class RunEvent(Base):
    """Append-only progress log; the SSE endpoint tails it, so progress survives reconnects and restarts."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelCall(Base):
    """The table all five roadmap builds share: one row per model-call attempt."""

    __tablename__ = "model_calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    build: Mapped[str] = mapped_column(String(32), default="deep-research-agent")
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    feature: Mapped[str] = mapped_column(String(32))          # plan | research | verify | write
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))           # success | error
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thinking_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ToolCall(Base):
    """Every search and fetch, so a run's external traffic is as auditable as its model calls."""

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    tool: Mapped[str] = mapped_column(String(16))              # search | fetch
    provider: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(Text)                  # query or URL
    status: Mapped[str] = mapped_column(String(16))
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
