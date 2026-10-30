"""Writer + citation check. The writer drafts only from checked claims and cites claim ids per sentence;
a final deterministic pass maps each sentence to claims and each claim to stored sources, and drops any
sentence that cites nothing real.

If the writer cannot run (budget spent, model failure), ``fallback_report`` builds a plain report from
the claim rows, so partial results are never lost.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.db import session_scope
from app.llm.gateway import CallContext
from app.llm.prompts import WRITE_SYSTEM, WRITE_USER, WRITE_VERSION, Prompt
from app.llm.schemas import WriterOutput, WrittenSection, WrittenSentence
from app.models import Claim, Evidence, Report, ReportCitation, Run, Source, Stance, Verdict
from app.research.env import ResearchEnv
from app.research.planning import depth_profile
from app.services.observability import emit

MAX_CLAIMS_FOR_WRITER = 45
_VERDICT_ORDER = {Verdict.supported.value: 0, Verdict.disputed.value: 1, Verdict.unverified.value: 2}
DISPUTED_HEADING = "disputed or uncertain"


def _representatives(run_id: uuid.UUID) -> list[Claim]:
    """One claim per group_key, best-supported first, capped for the writer's context."""
    with session_scope() as db:
        claims = list(db.scalars(select(Claim).where(Claim.run_id == run_id, Claim.verdict != Verdict.pending.value)
                                 .order_by(Claim.created_at)))
        db.expunge_all()
    seen: set[str] = set()
    reps = []
    for claim in sorted(claims, key=lambda c: (_VERDICT_ORDER.get(c.verdict, 3), -c.support_domains)):
        if claim.group_key in seen:
            continue
        seen.add(claim.group_key)
        reps.append(claim)
    return reps[:MAX_CLAIMS_FOR_WRITER]


def citation_check(output: WriterOutput, short_ids: dict[str, Claim]) -> tuple[list[dict], dict]:
    """Keep only sentences that cite at least one known claim; record what the check found."""
    stats = defaultdict(int)
    sections = []
    idx = 0
    for section in output.sections:
        in_disputed = DISPUTED_HEADING in section.heading.lower() or "disputed" in section.heading.lower()
        kept = []
        for sentence in section.sentences:
            stats["sentences_total"] += 1
            valid = [cid for cid in dict.fromkeys(sentence.claim_ids) if cid in short_ids]
            stats["invalid_claim_ids"] += len(set(sentence.claim_ids)) - len(valid)
            if not valid:
                stats["sentences_dropped_uncited"] += 1
                continue
            verdicts = {short_ids[c].verdict for c in valid}
            if Verdict.disputed.value in verdicts and not in_disputed:
                stats["disputed_cited_outside_disputed_section"] += 1
            if verdicts == {Verdict.unverified.value}:
                stats["sentences_on_unverified_only"] += 1
            kept.append({"idx": idx, "text": sentence.text.strip(), "claim_ids": [str(short_ids[c].id) for c in valid]})
            idx += 1
        if kept:
            sections.append({"heading": section.heading.strip(), "sentences": kept})
    stats["sentences_kept"] = idx
    return sections, dict(stats)


def fallback_output(claims: list[Claim], reason: str) -> tuple[WriterOutput, dict[str, Claim]]:
    """A no-model report: claims listed by verdict. Used when the writer cannot run."""
    short_ids = {f"C{i}": c for i, c in enumerate(claims, 1)}
    groups = [("Supported findings", Verdict.supported.value), ("Disputed or uncertain", Verdict.disputed.value),
              ("Single-source findings (unverified)", Verdict.unverified.value)]
    sections = []
    for heading, verdict in groups:
        sentences = [WrittenSentence(text=c.text, claim_ids=[sid]) for sid, c in short_ids.items() if c.verdict == verdict]
        if sentences:
            sections.append(WrittenSection(heading=heading, sentences=sentences[:40]))
    if not sections:
        sections = [WrittenSection(heading="No findings", sentences=[WrittenSentence(text="No claims were extracted.")])]
    return WriterOutput(title=f"Findings ({reason})", sections=sections), short_ids


def render_markdown(title: str, sections: list[dict], sources_by_claim: dict[str, list[Source]]) -> str:
    """Markdown export: each sentence ends with [n] markers pointing at numbered sources."""
    numbers: dict[uuid.UUID, int] = {}
    ordered: list[Source] = []
    lines = [f"# {title}", ""]
    for section in sections:
        lines += [f"## {section['heading']}", ""]
        sentences = []
        for sentence in section["sentences"]:
            marks = []
            for cid in sentence["claim_ids"]:
                for src in sources_by_claim.get(cid, []):
                    if src.id not in numbers:
                        numbers[src.id] = len(ordered) + 1
                        ordered.append(src)
                    marks.append(numbers[src.id])
            tags = "".join(f"[{n}]" for n in sorted(set(marks)))
            sentences.append(f"{sentence['text']} {tags}".rstrip())
        lines += [" ".join(sentences), ""]
    if ordered:
        lines += ["## Sources", ""]
        for n, src in enumerate(ordered, 1):
            published = f", published {src.published_at}" if src.published_at else ""
            lines.append(f"{n}. [{src.title or src.url}]({src.url}) — {src.domain}{published}, "
                         f"fetched {src.fetched_at:%Y-%m-%d}")
    return "\n".join(lines).strip() + "\n"


def _supporting_sources(claim_ids: list[uuid.UUID]) -> dict[str, list[Source]]:
    with session_scope() as db:
        rows = db.execute(select(Evidence.claim_id, Source).join(Source, Source.id == Evidence.source_id)
                          .where(Evidence.claim_id.in_(claim_ids), Evidence.quote_found.is_(True),
                                 Evidence.stance.in_([Stance.supports.value, Stance.contradicts.value]))).all()
        db.expunge_all()
    out: dict[str, list[Source]] = defaultdict(list)
    for claim_id, source in rows:
        if source.id not in {s.id for s in out[str(claim_id)]}:
            out[str(claim_id)].append(source)
    return out


def save_report(run_id: uuid.UUID, title: str, sections: list[dict], stats: dict, claims: list[Claim]) -> uuid.UUID:
    sources = _supporting_sources([c.id for c in claims])
    markdown = render_markdown(title, sections, sources)
    with session_scope() as db:
        version = (db.scalar(select(func.max(Report.version)).where(Report.run_id == run_id)) or 0) + 1
        report = Report(run_id=run_id, version=version, markdown=markdown,
                        sections=sections,
                        stats={**stats, "title": title})
        db.add(report)
        db.flush()
        for section in sections:
            for sentence in section["sentences"]:
                for cid in sentence["claim_ids"]:
                    db.add(ReportCitation(report_id=report.id, sentence_idx=sentence["idx"], claim_id=uuid.UUID(cid)))
        return report.id


async def write_report(env: ResearchEnv, run_id: uuid.UUID, *, use_model: bool = True, reason: str = "") -> uuid.UUID:
    with session_scope() as db:
        run = db.get(Run, run_id)
        question, depth = run.question, run.depth
        plan = run.approved_plan or run.plan or {}
    claims = await asyncio.to_thread(_representatives, run_id)
    stats: dict = {}
    output: WriterOutput | None = None
    short_ids: dict[str, Claim] = {}
    usable = [c for c in claims if c.verdict in (Verdict.supported.value, Verdict.disputed.value, Verdict.unverified.value)]
    if use_model and usable:
        short_ids = {f"C{i}": c for i, c in enumerate(usable, 1)}
        lines = "\n".join(f"{sid} [{c.verdict.upper()}, {c.support_domains} independent origin(s)]: {c.text}"
                          for sid, c in short_ids.items())
        max_words = min(plan.get("report", {}).get("max_words", 900), depth_profile(depth).max_words)
        prompt = Prompt("write", WRITE_VERSION, WRITE_SYSTEM.format(max_words=max_words),
                        WRITE_USER.format(question=question, brief=plan.get("brief", question), claims=lines))
        try:
            output = (await env.gateway.generate(prompt, WriterOutput, CallContext(str(run_id)))).output
            stats["writer"] = "model"
        except Exception as exc:  # the writer failing must not lose the research
            reason = f"writer failed: {getattr(exc, 'category', type(exc).__name__)}"
    if output is None:
        output, short_ids = fallback_output(usable, reason or "no model")
        stats["writer"] = "fallback"
        stats["fallback_reason"] = reason or "no model"
    sections, check = citation_check(output, short_ids)
    stats.update(check)
    cited = {cid for s in sections for sent in s["sentences"] for cid in sent["claim_ids"]}
    by_id = {str(c.id): c for c in usable}
    stats["cited_claims"] = len(cited)
    for verdict in ("supported", "disputed", "unverified"):
        stats[f"cited_{verdict}"] = sum(1 for cid in cited if by_id[cid].verdict == verdict)
    report_id = await asyncio.to_thread(save_report, run_id, output.title, sections, stats, usable)
    emit(run_id, "report_ready", report_id=str(report_id), sentences=stats.get("sentences_kept", 0),
         dropped=stats.get("sentences_dropped_uncited", 0), writer=stats["writer"])
    return report_id


def latest_report(db, run_id: uuid.UUID) -> Report | None:
    return db.scalar(select(Report).where(Report.run_id == run_id).order_by(Report.version.desc()).limit(1))


def load_claim(db, claim_id: uuid.UUID) -> Claim | None:
    return db.scalar(select(Claim).where(Claim.id == claim_id)
                     .options(selectinload(Claim.evidence).selectinload(Evidence.source)))
