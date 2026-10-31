"""Verifier: every claim is checked against the quotes behind it, from every page the run read.

Three layers, cheapest first:
1. Deterministic: a quote must occur verbatim in the stored page, or it counts for nothing.
2. Cross-check: for each claim, the best-matching sentence from every *other* stored page is added as
   candidate evidence, so a claim can gain independent support (or meet a contradiction) beyond the one
   page the researcher happened to cite.
3. An LLM judge labels each claim-quote pair supports / contradicts / insufficient, in batches.

The verdict rule is plain code over those rows (see ``decide_verdict``).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import session_scope
from app.llm.gateway import CallContext
from app.llm.prompts import VERIFY_SYSTEM, VERIFY_USER, VERIFY_VERSION, Prompt
from app.llm.schemas import VerifierOutput
from app.models import Claim, Evidence, Source, Stance, Verdict
from app.research.env import ResearchEnv
from app.research.web import normalize_text
from app.services.observability import emit

_STOPWORDS = set("a an the of in on at to for and or is are was were be been by with as from that this it its which "
                 "who has have had than then into about over per also more most but".split())
MAX_CROSS_CHECKS_PER_CLAIM = 3
CROSS_CHECK_MIN_OVERLAP = 0.6          # wording match alone
CROSS_CHECK_MIN_OVERLAP_WITH_NUMBER = 0.3  # looser when the sentence repeats one of the claim's numbers
SUPPORT_MIN_SCORE = 0.5


@dataclass(frozen=True)
class EvidenceView:
    stance: str
    score: float | None
    quote_found: bool
    origin: str


def decide_verdict(evidence: list[EvidenceView], min_domains: int, strong: float) -> tuple[str, int, str]:
    """supported = verbatim supporting quotes from ``min_domains``+ independent origins and no strong
    contradiction; disputed = a strong contradiction; otherwise unverified."""
    supporting = {e.origin for e in evidence
                  if e.quote_found and e.stance == Stance.supports.value and (e.score or 0) >= SUPPORT_MIN_SCORE}
    contradicting = {e.origin for e in evidence
                     if e.quote_found and e.stance == Stance.contradicts.value and (e.score or 0) >= strong}
    if contradicting:
        return (Verdict.disputed.value, len(supporting),
                f"Contradicted by {len(contradicting)} origin(s); supported by {len(supporting)}.")
    if len(supporting) >= min_domains:
        return Verdict.supported.value, len(supporting), f"Supported by {len(supporting)} independent origin(s)."
    if supporting:
        return (Verdict.unverified.value, len(supporting),
                f"Supported by only {len(supporting)} independent origin(s); {min_domains} required.")
    return Verdict.unverified.value, 0, "No verbatim quote supports this claim."


def origin_map(sources: list[Source]) -> dict[uuid.UUID, str]:
    """Group sources into independent origins: same domain, or same/near-duplicate content (citation
    laundering — ten sites copying one press release are one source). Union-find over both keys."""
    parent: dict[uuid.UUID, uuid.UUID] = {s.id: s.id for s in sources}

    def find(x: uuid.UUID) -> uuid.UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first_by_key: dict[str, uuid.UUID] = {}
    for s in sources:
        for key in (f"d:{s.domain}", f"h:{s.content_hash}", f"s:{s.shingle_hash}" if s.shingle_hash else None):
            if key is None:
                continue
            if key in first_by_key:
                parent[find(s.id)] = find(first_by_key[key])
            else:
                first_by_key[key] = s.id
    by_id = {s.id: s for s in sources}
    return {s.id: by_id[find(s.id)].domain for s in sources}


_UNITS = {"meters": "m", "metres": "m", "meter": "m", "metre": "m", "feet": "ft", "foot": "ft",
          "kilometres": "km", "kilometers": "km", "kilometre": "km", "kilometer": "km", "miles": "mi", "mile": "mi",
          "percent": "%"}


def _keywords(text: str) -> set[str]:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", normalize_text(text))  # 2,717 -> 2717
    tokens = (_UNITS.get(t, t) for t in (t.strip(".") for t in re.findall(r"[a-z0-9.%$]+", text)))
    return {t for t in tokens if t not in _STOPWORDS and (len(t) > 1 or t in ("m", "%"))}


def _numbers(keys: set[str]) -> set[str]:
    return {k for k in keys if re.fullmatch(r"\d+(\.\d+)?%?", k) and k not in {"1", "2", "3"}}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if 30 <= len(s.strip()) <= 600]


def best_sentence(claim_text: str, source_text: str) -> tuple[str, float] | None:
    """The sentence in another page most likely to confirm or contradict the claim.

    Real pages phrase one fact differently ("828 m tall" vs "architectural height … 828 m (2,717 ft)"), so
    word overlap alone misses most matches. A sentence also qualifies when it repeats one of the claim's
    numbers and shares a smaller fraction of its words. The LLM judge then decides what it actually says.
    """
    keys = _keywords(claim_text)
    if len(keys) < 3:
        return None
    numbers = _numbers(keys)
    best: tuple[str, float, int] | None = None
    for sentence in _sentences(source_text):
        words = _keywords(sentence)
        overlap = len(keys & words) / len(keys)
        shared_numbers = len(numbers & words)
        if overlap < CROSS_CHECK_MIN_OVERLAP and not (shared_numbers and overlap >= CROSS_CHECK_MIN_OVERLAP_WITH_NUMBER):
            continue
        if best is None or (shared_numbers, overlap) > (best[2], best[1]):
            best = (sentence, overlap, shared_numbers)
    return (best[0], best[1]) if best else None


def add_cross_checks(run_id: uuid.UUID) -> int:
    """Attach the best-matching sentence from other origins as candidate evidence. Idempotent."""
    added = 0
    with session_scope() as db:
        sources = list(db.scalars(select(Source).where(Source.run_id == run_id)))
        origins = origin_map(sources)
        claims = list(db.scalars(select(Claim).where(Claim.run_id == run_id).options(selectinload(Claim.evidence))))
        for claim in claims:
            used = {origins.get(e.source_id) for e in claim.evidence}
            used_sources = {e.source_id for e in claim.evidence}
            candidates = []
            for source in sources:
                if source.id in used_sources or origins[source.id] in used:
                    continue
                match = best_sentence(claim.text, source.text)
                if match:
                    candidates.append((match[1], source, match[0]))
            candidates.sort(key=lambda c: -c[0])
            for _, source, sentence in candidates:
                if len([e for e in claim.evidence if e.origin == "cross_check"]) >= MAX_CROSS_CHECKS_PER_CLAIM:
                    break
                if origins[source.id] in used:
                    continue
                used.add(origins[source.id])
                claim.evidence.append(Evidence(source_id=source.id, quote=sentence, quote_found=True,
                                               origin="cross_check"))
                added += 1
    return added


async def _judge_batch(env: ResearchEnv, run_id: uuid.UUID, batch: list[tuple[uuid.UUID, str, str, str, str]]) -> dict:
    """batch rows: (evidence_id, claim_text, quote, domain, published). Returns evidence_id -> judgement."""
    lines = "\n\n".join(
        f'Pair {n}:\nCLAIM: {claim}\n<source domain="{domain}" published="{published}">\nQUOTE: {quote}\n</source>'
        for n, (_, claim, quote, domain, published) in enumerate(batch))
    prompt = Prompt("verify", VERIFY_VERSION, VERIFY_SYSTEM, VERIFY_USER.format(pairs=lines))
    result = await env.gateway.generate(prompt, VerifierOutput, CallContext(str(run_id)))
    judged = {}
    for j in result.output.judgements:
        if 0 <= j.pair < len(batch):
            judged[batch[j.pair][0]] = j
    return judged


async def verify_run(env: ResearchEnv, run_id: uuid.UUID, *, use_model: bool = True) -> dict:
    settings = env.settings
    cross = await asyncio.to_thread(add_cross_checks, run_id)
    with session_scope() as db:
        rows = db.execute(
            select(Evidence.id, Claim.text, Evidence.quote, Source.domain, Source.published_at, Evidence.quote_found)
            .join(Claim, Claim.id == Evidence.claim_id).join(Source, Source.id == Evidence.source_id)
            .where(Claim.run_id == run_id, Evidence.stance == Stance.pending.value)).all()
        # Layer 1: a quote that is not in the stored page is worth nothing; no model call needed.
        for row in rows:
            if not row.quote_found:
                ev = db.get(Evidence, row.id)
                ev.stance, ev.score = Stance.insufficient.value, 0.0
                ev.rationale = "Quote not found verbatim in the stored page."
    pending = [(r.id, r.text, r.quote, r.domain, str(r.published_at or "unknown")) for r in rows if r.quote_found]
    judged: dict = {}
    if pending and use_model:
        size = settings.verifier_batch_size
        batches = [pending[i:i + size] for i in range(0, len(pending), size)]
        limiter = asyncio.Semaphore(settings.max_parallel_workers)

        async def run_batch(batch):
            async with limiter:
                return await _judge_batch(env, run_id, batch)

        for result in await asyncio.gather(*[run_batch(b) for b in batches]):
            judged.update(result)
    with session_scope() as db:
        for ev_id, *_ in pending:
            ev = db.get(Evidence, ev_id)
            j = judged.get(ev_id)
            if j is None:
                ev.stance, ev.score = Stance.insufficient.value, 0.0
                ev.rationale = "Not judged (verifier skipped or returned no judgement)."
            else:
                ev.stance, ev.score, ev.rationale = j.stance, j.score, j.rationale[:300] or None
    return await asyncio.to_thread(apply_verdicts, run_id, settings.min_independent_domains,
                                   settings.strong_contradiction_score, cross)


def apply_verdicts(run_id: uuid.UUID, min_domains: int, strong: float, cross_checks: int = 0) -> dict:
    """Pool evidence across claims that share a group_key, then apply the verdict rule to each group."""
    counts: dict[str, int] = defaultdict(int)
    with session_scope() as db:
        sources = list(db.scalars(select(Source).where(Source.run_id == run_id)))
        origins = origin_map(sources)
        claims = list(db.scalars(select(Claim).where(Claim.run_id == run_id).options(selectinload(Claim.evidence))))
        groups: dict[str, list[Claim]] = defaultdict(list)
        for claim in claims:
            groups[claim.group_key].append(claim)
        for members in groups.values():
            pooled = [EvidenceView(e.stance, e.score, e.quote_found, origins.get(e.source_id, "?"))
                      for c in members for e in c.evidence]
            verdict, support, reason = decide_verdict(pooled, min_domains, strong)
            for claim in members:
                claim.verdict, claim.support_domains, claim.verdict_reason = verdict, support, reason
                counts[verdict] += 1
    summary = {"claims": len(claims), "supported": counts["supported"], "disputed": counts["disputed"],
               "unverified": counts["unverified"], "cross_checks_added": cross_checks}
    emit(run_id, "claims_checked", **summary)
    return summary
