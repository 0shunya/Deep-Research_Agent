"""Resumable benchmark runner.

    # live: real Gemini + real search; few questions at a time (the free quota is ~20 calls/day/model)
    python evaluation/run_eval.py --name live-wiki-quick --ids q01,q09 --depth quick
    # score everything saved under evaluation/runs/<name>/
    python evaluation/run_eval.py --name live-wiki-quick --score-only

Each question's raw record (plan, tasks, claims with quotes, report, model calls) is saved to
evaluation/runs/<name>/<id>.json before scoring, and questions that already have a record are skipped,
so a run interrupted by a quota error resumes the next day. The plan is auto-approved unedited.
Runs use the database in backend/.env (the local PostgreSQL by default).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.evaluation import aggregate, export_run, score_record  # noqa: E402
from app.llm.schemas import Plan  # noqa: E402
from app.models import Run  # noqa: E402
from app.research.env import build_default_env  # noqa: E402
from app.research.orchestrator import approve_plan, execute_run, plan_run  # noqa: E402

DATASET = ROOT / "evaluation" / "dataset" / "questions.jsonl"
RUNS = ROOT / "evaluation" / "runs"
RESULTS = ROOT / "evaluation" / "results"
STOP_CATEGORIES = {"quota_exhausted", "daily_limit", "auth_error", "config_missing"}


def load_labels() -> dict[str, dict]:
    return {row["id"]: row for row in map(json.loads, DATASET.read_text(encoding="utf-8").splitlines()) if row}


async def run_one(env, label: dict, depth: str, workers: int) -> dict:
    settings = get_settings()
    with session_scope() as db:
        run = Run(question=label["question"], depth=depth, budget_tokens=settings.default_budget_tokens,
                  max_workers=workers, search_provider=settings.resolved_search_provider)
        db.add(run)
        db.flush()
        run_id = run.id
    await plan_run(env, run_id)
    with session_scope() as db:
        run = db.get(Run, run_id)
        if run.status == "awaiting_approval":
            approve_plan(db, run, Plan.model_validate(run.plan))
    with session_scope() as db:
        approved = db.get(Run, run_id).status == "researching"
    if approved:
        await execute_run(env, run_id)
    return export_run(run_id)


async def run_all(folder: Path, labels: dict, ids: list[str], depth: str, workers: int) -> None:
    # One event loop and one environment for the whole batch: the Gemini async client is bound to the loop
    # it was first used on, so it must not be shared across separate asyncio.run() calls.
    env = build_default_env()
    for qid in ids:
        path = folder / f"{qid}.json"
        if path.exists():
            print(f"{qid}: already recorded, skipping")
            continue
        record = await run_one(env, labels[qid], depth, workers)
        run = record["run"]
        if run["status"] == "failed" and run["error_code"] in STOP_CATEGORIES:
            print(f"{qid}: stopped — {run['error_code']}: {run['error_message']} (not saved; resume later)")
            break
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{qid}: {run['status']} in {run['research_wall_seconds']}s, {len(record['model_calls'])} model calls, "
              f"{run['spent_tokens']} tokens")


def score(name: str) -> dict:
    labels = load_labels()
    folder = RUNS / name
    scores = []
    for path in sorted(folder.glob("q*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        scores.append(score_record(record, labels[path.stem]))
    summary = {"name": name, "aggregate": aggregate(scores), "per_question": scores,
               "config": json.loads((folder / "config.json").read_text()) if (folder / "config.json").exists() else {}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--ids", default="", help="comma-separated question ids (default: all)")
    parser.add_argument("--depth", default="quick", choices=["quick", "standard", "deep"])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--score-only", action="store_true")
    args = parser.parse_args()

    folder = RUNS / args.name
    folder.mkdir(parents=True, exist_ok=True)
    if not args.score_only:
        settings = get_settings()
        config = {"depth": args.depth, "workers": args.workers, "models": settings.model_list,
                  "search_provider": settings.resolved_search_provider,
                  "min_independent_domains": settings.min_independent_domains,
                  "price_per_mtok": [settings.price_input_per_mtok, settings.price_output_per_mtok]}
        (folder / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        labels = load_labels()
        ids = [i for i in args.ids.split(",") if i] or list(labels)
        asyncio.run(run_all(folder, labels, ids, args.depth, args.workers))
    summary = score(args.name)
    print(json.dumps(summary["aggregate"], indent=2))


if __name__ == "__main__":
    main()
