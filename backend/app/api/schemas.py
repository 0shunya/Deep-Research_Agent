"""Request/response models for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.llm.schemas import Plan


class CreateRun(BaseModel):
    question: str = Field(min_length=10, max_length=1000)
    depth: Literal["quick", "standard", "deep"] = "standard"
    budget_tokens: int | None = Field(default=None, ge=5_000)
    max_workers: int | None = Field(default=None, ge=1, le=8)


class ApprovePlan(BaseModel):
    plan: Plan | None = None  # omitted -> approve the proposed plan unchanged


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    question: str
    why: str | None
    depends_on: list[str]
    status: str
    attempts: int
    steps_used: int
    summary: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class RunSummary(BaseModel):
    id: uuid.UUID
    question: str
    depth: str
    status: str
    budget_tokens: int
    spent_tokens: int
    cost_usd: Decimal
    created_at: datetime
    finished_at: datetime | None
    wall_seconds: float | None
    stop_reason: str | None


class RunDetail(RunSummary):
    max_workers: int
    search_provider: str
    search_calls: int
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    plan: dict | None
    approved_plan: dict | None
    tasks: list[TaskOut]
    counts: dict[str, int]


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    domain: str
    title: str | None
    published_at: date | None
    fetched_at: datetime
    content_hash: str
    search_rank: int | None


class EvidenceOut(BaseModel):
    id: uuid.UUID
    quote: str
    quote_found: bool
    origin: str
    stance: str
    score: float | None
    rationale: str | None
    context: str | None
    source: SourceOut


class ClaimOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    text: str
    verdict: str
    verdict_reason: str | None
    support_domains: int
    group_key: str


class ClaimDetail(ClaimOut):
    run_id: uuid.UUID
    task_question: str | None
    evidence: list[EvidenceOut]


class ReportOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    version: int
    title: str
    markdown: str
    sections: list[dict]
    stats: dict
    created_at: datetime
    claims: dict[str, ClaimOut]
    sources: dict[str, list[SourceOut]]  # claim id -> sources with verbatim evidence
