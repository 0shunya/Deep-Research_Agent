"""Evaluation: export a finished run as a self-contained record, and score records against labels.

Metrics (roadmap Build 04, "Evaluate it"):
- answer accuracy: an accepted answer string appears in the report text;
- required-fact recall: share of required facts whose accepted strings appear in the report text;
- claim precision ("cited claims supported by quotes"): share of claims cited in the report that have at
  least one verbatim quote the verifier judged as supporting; also the stricter share with the
  "supported" verdict (2+ independent origins);
- source diversity: distinct registrable domains read; cost (list price from real token counts),
  tokens, model calls and wall time per run.

String matching is deliberately simple and deterministic: it costs no model calls and cannot be argued
with, but it can miss a correct answer phrased differently (e.g. "UK" vs "England"), so accuracy is a
lower bound. Every record keeps the raw report so it can be re-scored.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import session_scope
from app.models import Claim, Evidence, ModelCall, Report, Run, Source, ToolCall


def export_run(run_id: uuid.UUID) -> dict:
    with session_scope() as db:
        run = db.scalar(select(Run).where(Run.id == run_id).options(selectinload(Run.tasks)))
        report = db.scalar(select(Report).where(Report.run_id == run_id).order_by(Report.version.desc()).limit(1))
        claims = list(db.scalars(select(Claim).where(Claim.run_id == run_id).options(
            selectinload(Claim.evidence).selectinload(Evidence.source))))
        sources = list(db.scalars(select(Source).where(Source.run_id == run_id)))
        calls = list(db.scalars(select(ModelCall).where(ModelCall.run_id == run_id).order_by(ModelCall.created_at)))
        tools = list(db.scalars(select(ToolCall).where(ToolCall.run_id == run_id)))
        start, end = run.research_started_at or run.created_at, run.finished_at
        if start and start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return {
            "run": {"id": str(run.id), "question": run.question, "depth": run.depth, "status": run.status,
                    "stop_reason": run.stop_reason, "error_code": run.error_code, "error_message": run.error_message,
                    "max_workers": run.max_workers, "search_provider": run.search_provider,
                    "budget_tokens": run.budget_tokens, "spent_tokens": run.spent_tokens,
                    "cost_usd": float(run.cost_usd), "search_calls": run.search_calls,
                    "research_wall_seconds": round((end - start).total_seconds(), 1) if start and end else None},
            "plan": run.approved_plan or run.plan,
            "tasks": [{"key": t.key, "question": t.question, "status": t.status, "attempts": t.attempts,
                       "steps": t.steps_used, "error": t.error} for t in run.tasks],
            "report": None if report is None else {"markdown": report.markdown, "sections": report.sections,
                                                   "stats": report.stats},
            "claims": [{"id": str(c.id), "text": c.text, "verdict": c.verdict, "support_domains": c.support_domains,
                        "reason": c.verdict_reason,
                        "evidence": [{"domain": e.source.domain, "url": e.source.url, "quote": e.quote,
                                      "quote_found": e.quote_found, "origin": e.origin, "stance": e.stance,
                                      "score": e.score} for e in c.evidence]} for c in claims],
            "sources": [{"url": s.url, "domain": s.domain, "published_at": str(s.published_at or ""),
                         "content_hash": s.content_hash} for s in sources],
            "model_calls": [{"feature": c.feature, "model": c.model, "status": c.status, "error": c.error_category,
                             "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                             "thinking_tokens": c.thinking_tokens, "cost_usd": float(c.cost_usd),
                             "latency_ms": c.latency_ms} for c in calls],
            "tool_calls": {"search": sum(t.tool == "search" for t in tools),
                           "fetch_ok": sum(t.tool == "fetch" and t.status == "success" for t in tools),
                           "fetch_failed": sum(t.tool == "fetch" and t.status != "success" for t in tools)},
        }


def _contains(text: str, options: list[str]) -> bool:
    lowered = text.lower()
    return any(option.lower() in lowered for option in options)


def report_text(record: dict) -> str:
    report = record.get("report") or {}
    return " ".join(s["text"] for sec in report.get("sections", []) for s in sec["sentences"])


def score_record(record: dict, label: dict) -> dict:
    text = report_text(record)
    cited_ids = {cid for sec in (record.get("report") or {}).get("sections", [])
                 for s in sec["sentences"] for cid in s["claim_ids"]}
    claims = {c["id"]: c for c in record["claims"]}
    cited = [claims[i] for i in cited_ids if i in claims]
    quote_backed = [c for c in cited if any(e["quote_found"] and e["stance"] == "supports" and (e["score"] or 0) >= 0.5
                                            for e in c["evidence"])]
    extracted = [e for c in record["claims"] for e in c["evidence"] if e["origin"] == "extracted"]
    facts = label.get("required_facts", [])
    ok_calls = [c for c in record["model_calls"] if c["status"] == "success"]
    return {
        "id": label["id"], "category": label.get("category"), "status": record["run"]["status"],
        "has_report": bool(text),
        "answer_correct": _contains(text, label["answer_any_of"]) if text else False,
        "fact_recall": (sum(_contains(text, f["any_of"]) for f in facts) / len(facts)) if facts else None,
        "cited_claims": len(cited),
        "cited_quote_backed": len(quote_backed),
        "cited_supported_verdict": sum(c["verdict"] == "supported" for c in cited),
        "claims": len(record["claims"]),
        "extracted_quotes": len(extracted),
        "extracted_quotes_verbatim": sum(e["quote_found"] for e in extracted),
        "domains": len({s["domain"] for s in record["sources"]}),
        "sources": len(record["sources"]),
        "model_calls": len(record["model_calls"]),
        "model_call_errors": len(record["model_calls"]) - len(ok_calls),
        "tokens": record["run"]["spent_tokens"],
        "cost_usd": record["run"]["cost_usd"],
        "wall_seconds": record["run"]["research_wall_seconds"],
        "sentences_dropped_uncited": ((record.get("report") or {}).get("stats") or {}).get("sentences_dropped_uncited", 0),
    }


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def aggregate(scores: list[dict]) -> dict:
    done = [s for s in scores if s["has_report"]]
    cited = sum(s["cited_claims"] for s in done)
    extracted = sum(s["extracted_quotes"] for s in scores)
    walls = sorted(s["wall_seconds"] for s in done if s["wall_seconds"] is not None)
    recalls = [s["fact_recall"] for s in done if s["fact_recall"] is not None]
    return {
        "questions": len(scores), "with_report": len(done),
        "answer_accuracy": _ratio(sum(s["answer_correct"] for s in done), len(done)),
        "fact_recall_mean": round(statistics.mean(recalls), 3) if recalls else None,
        "cited_claims": cited,
        "cited_quote_backed_share": _ratio(sum(s["cited_quote_backed"] for s in done), cited),
        "cited_supported_verdict_share": _ratio(sum(s["cited_supported_verdict"] for s in done), cited),
        "extracted_quote_verbatim_rate": _ratio(sum(s["extracted_quotes_verbatim"] for s in scores), extracted),
        "domains_median": _median([s["domains"] for s in done]),
        "cost_usd_mean": round(statistics.mean([s["cost_usd"] for s in done]), 6) if done else None,
        "tokens_mean": round(statistics.mean([s["tokens"] for s in done])) if done else None,
        "model_calls_mean": round(statistics.mean([s["model_calls"] for s in done]), 1) if done else None,
        "wall_seconds_p50": _median(walls),
        "wall_seconds_max": walls[-1] if walls else None,
    }
