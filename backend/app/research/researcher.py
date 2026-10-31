"""A researcher agent: one sub-question, its own context window, a bounded search -> read -> extract loop.

It returns compressed findings (claims with exact quotes plus a short summary), never raw pages, to the
orchestrator. Every page it reads is stored with URL, dates and a content hash before any claim cites it.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.db import session_scope
from app.llm.gateway import CallContext
from app.llm.prompts import RESEARCH_SYSTEM, RESEARCH_USER, RESEARCH_VERSION, Prompt
from app.llm.schemas import ResearchStep
from app.models import Claim, Evidence, Run, Source, Task, utcnow
from app.research.env import ResearchEnv
from app.research.planning import Depth
from app.research.web import FetchedPage, SearchHit, ToolError, page_from_html, quote_in_text
from app.services.observability import emit, record_tool_call

_STOPWORDS = set("a an the of in on at to for and or is are was were be been by with as from that this it its "
                 "which who has have had than then into about over per".split())
MAX_SOURCES_IN_CONTEXT = 6


class BudgetExhausted(Exception):
    pass


def claim_group_key(text: str) -> str:
    """Claims with the same content words (any order, any casing) line up under one key."""
    tokens = sorted({t.strip(".") for t in re.findall(r"[a-z0-9.%$]+", text.lower())} - _STOPWORDS - {""})
    return hashlib.sha1(" ".join(tokens).encode()).hexdigest()[:16]


def budget_left(run_id: uuid.UUID, share: float = 1.0) -> bool:
    with session_scope() as db:
        run = db.get(Run, run_id)
        return run is not None and run.spent_tokens < run.budget_tokens * share


@dataclass
class _Gathered:
    source_id: uuid.UUID
    label: str
    url: str
    domain: str
    title: str | None
    published: str | None
    text: str


@dataclass
class ResearchOutcome:
    claims: int = 0
    quotes_found: int = 0
    quotes_missing: int = 0
    sources: int = 0
    steps: int = 0
    summary: str = ""
    stopped_for_budget: bool = False
    errors: list[str] = field(default_factory=list)


def _store_source(run_id: uuid.UUID, task_id: uuid.UUID, page: FetchedPage, provider: str, rank: int) -> uuid.UUID:
    """Store a page once per run. Parallel workers often find the same URL: the unique constraint on
    (run_id, url) decides the winner and the loser reuses the stored row."""
    find = select(Source.id).where(Source.run_id == run_id, Source.url == page.url)
    with session_scope() as db:
        existing = db.scalar(find)
        if existing:
            return existing
    try:
        with session_scope() as db:
            source = Source(run_id=run_id, task_id=task_id, url=page.url, domain=page.domain, title=page.title,
                            published_at=page.published_at, content_hash=page.content_hash,
                            shingle_hash=page.shingle_hash, raw_content=page.raw, text=page.text,
                            content_type=page.content_type, search_provider=provider, search_rank=rank)
            db.add(source)
            db.flush()
            return source.id
    except IntegrityError:
        with session_scope() as db:
            return db.scalar(find)


async def _search(env: ResearchEnv, run_id: uuid.UUID, task_id: uuid.UUID, query: str) -> list[SearchHit]:
    started = time.perf_counter()
    try:
        hits = await env.search.search(query, env.settings.search_results_per_query)
    except ToolError as exc:
        record_tool_call(run_id, task_id, "search", env.search.name, query, "error",
                         round((time.perf_counter() - started) * 1000), error=exc.message)
        raise
    record_tool_call(run_id, task_id, "search", env.search.name, query, "success",
                     round((time.perf_counter() - started) * 1000), result_count=len(hits))
    emit(run_id, "search_done", task_id=str(task_id), query=query, results=len(hits))
    return hits


async def _read(env: ResearchEnv, run_id: uuid.UUID, task_id: uuid.UUID, hit: SearchHit, rank: int) -> _Gathered | None:
    started = time.perf_counter()
    try:
        page = await env.fetcher.fetch(hit.url)
        if len(page.text) < 200 and hit.content:
            raise ToolError("too_short", "Extracted text is too short.")
    except ToolError as exc:
        record_tool_call(run_id, task_id, "fetch", "http", hit.url, "error",
                         round((time.perf_counter() - started) * 1000), error=exc.message)
        if not hit.content or len(hit.content) < 200:
            emit(run_id, "source_failed", task_id=str(task_id), url=hit.url, reason=exc.message)
            return None
        # The search API already extracted this page's text: keep that as the stored copy instead.
        page = page_from_html(hit.url, hit.url, hit.content, "text/plain; source=search-api", len(hit.content))
    else:
        record_tool_call(run_id, task_id, "fetch", "http", hit.url, "success",
                         round((time.perf_counter() - started) * 1000), result_count=len(page.text))
    if len(page.text) < 100:
        emit(run_id, "source_failed", task_id=str(task_id), url=hit.url, reason="no readable text")
        return None
    source_id = await asyncio.to_thread(_store_source, run_id, task_id, page, env.search.name, rank)
    emit(run_id, "source_read", task_id=str(task_id), source_id=str(source_id), url=page.url, domain=page.domain,
         title=page.title or hit.title, published_at=page.published_at.isoformat() if page.published_at else None)
    return _Gathered(source_id, "", page.url, page.domain, page.title or hit.title,
                     page.published_at.isoformat() if page.published_at else hit.published, page.text)


def _sources_block(gathered: list[_Gathered], chars: int) -> str:
    blocks = []
    for g in gathered:
        blocks.append(f'<source id="{g.label}" url="{g.url}" domain="{g.domain}" '
                      f'published="{g.published or "unknown"}">\nTitle: {g.title or ""}\n{g.text[:chars]}\n</source>')
    return "\n\n".join(blocks)


def _save_claims(run_id: uuid.UUID, task_id: uuid.UUID, step: ResearchStep, gathered: list[_Gathered],
                 outcome: ResearchOutcome) -> None:
    by_label = {g.label: g for g in gathered}
    with session_scope() as db:
        for extracted in step.claims:
            quotes = [(by_label[q.source], q.quote) for q in extracted.quotes if q.source in by_label]
            if not quotes:
                continue  # cites only sources it was never shown: discard
            claim = Claim(run_id=run_id, task_id=task_id, text=extracted.text.strip(),
                          group_key=claim_group_key(extracted.text))
            for g, quote in quotes:
                found = quote_in_text(quote, g.text)
                outcome.quotes_found += found
                outcome.quotes_missing += not found
                claim.evidence.append(Evidence(source_id=g.source_id, quote=quote.strip(), quote_found=found,
                                               origin="extracted"))
            db.add(claim)
            outcome.claims += 1


async def run_research_task(env: ResearchEnv, run_id: uuid.UUID, task_id: uuid.UUID, brief: str,
                            depth: Depth) -> ResearchOutcome:
    settings = env.settings
    with session_scope() as db:
        task = db.get(Task, task_id)
        question, key = task.question, task.key
        deps = [db.get(Task, uuid.UUID(d)) for d in task.depends_on]
        context = "\n".join(f"Findings from {d.key} ({d.question}): {d.summary}" for d in deps if d and d.summary)
        # Idempotent retry: a previous attempt's partial claims are replaced, never duplicated.
        db.execute(delete(Claim).where(Claim.task_id == task_id))
    outcome = ResearchOutcome()
    gathered: list[_Gathered] = []
    query = question
    for step_no in range(1, depth.max_steps + 1):
        hits = await _search(env, run_id, task_id, query)
        seen = {g.url for g in gathered}
        fresh = [h for h in hits if h.url not in seen][: depth.sources_per_task]
        reads = await asyncio.gather(*[_read(env, run_id, task_id, h, rank) for rank, h in enumerate(fresh, 1)])
        gathered.extend(r for r in reads if r is not None)
        gathered = gathered[-MAX_SOURCES_IN_CONTEXT:]
        for i, g in enumerate(gathered, 1):
            g.label = f"S{i}"
        outcome.sources = len(gathered)
        if not gathered:
            if step_no == depth.max_steps:
                break
            query = f"{question} overview"
            continue
        if not budget_left(run_id, settings.research_budget_share):
            outcome.stopped_for_budget = True
            break
        prompt = Prompt("research", RESEARCH_VERSION, RESEARCH_SYSTEM, RESEARCH_USER.format(
            brief=brief, key=key, question=question,
            context=f"Context from earlier sub-questions:\n{context}\n" if context else "",
            sources=_sources_block(gathered, settings.page_chars_for_model)))
        result = await env.gateway.generate(prompt, ResearchStep, CallContext(str(run_id), str(task_id)))
        outcome.steps = step_no
        await asyncio.to_thread(_save_claims, run_id, task_id, result.output, gathered, outcome)
        outcome.summary = result.output.summary or outcome.summary
        emit(run_id, "claims_found", task_id=str(task_id), step=step_no, claims=outcome.claims,
             quotes_found=outcome.quotes_found, quotes_missing=outcome.quotes_missing)
        next_query = result.output.next_query.strip()
        if not next_query or step_no == depth.max_steps:
            break
        query = next_query  # only ever sent to the search API, never fetched as a URL
    with session_scope() as db:
        task = db.get(Task, task_id)
        task.steps_used = outcome.steps
        task.summary = outcome.summary[:1500] or None
        task.finished_at = utcnow()
    return outcome
