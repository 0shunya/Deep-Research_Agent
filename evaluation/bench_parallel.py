"""SIMULATED benchmark: how much do parallel researchers cut wall time?

Runs the real orchestrator (plan -> DAG -> verify -> write, real database writes) against the scripted
model and the offline web, with each model call delayed by the p50 latency measured for that stage in
the live runs, and each search/fetch delayed by a fixed amount. Only the orchestration is real; the
latencies are injected, so this measures the scheduler, not Gemini or the web.

    python evaluation/bench_parallel.py --sub-questions 4 --workers 1,2,4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
db_file = Path(tempfile.mkdtemp()) / "bench.db"
os.environ.update({"DATABASE_URL": f"sqlite:///{db_file}", "GEMINI_API_KEY": "", "DAILY_MODEL_CALL_LIMIT": "0",
                   "APP_ENV": "test"})

from app.config import get_settings  # noqa: E402
from app.db import Base, get_engine, session_scope  # noqa: E402
from app.llm.gateway import ModelGateway  # noqa: E402
from app.llm.providers import ScriptedProvider  # noqa: E402
from app.llm.schemas import Plan  # noqa: E402
from app.models import Run  # noqa: E402
from app.research.env import ResearchEnv  # noqa: E402
from app.research.orchestrator import approve_plan, execute_run, plan_run  # noqa: E402
from app.research.simulated import scripted_responder, static_web  # noqa: E402
from app.services.observability import record_call_to_db  # noqa: E402

# p50 latencies per stage (seconds) from the live runs' model_calls (25 successful live calls on 2026-10-31).
DEFAULT_LATENCY = {"plan": 2.0, "research": 2.16, "verify": 1.33, "write": 2.23}


class StageLatencyProvider(ScriptedProvider):
    async def generate(self, request):
        await asyncio.sleep(self.stage_latency[request.feature])
        return await super().generate(request)


async def one_run(sub_questions: int, workers: int, web_latency: float, latency: dict) -> float:
    questions = [f"Eiffel Tower fact number {i}?" for i in range(1, sub_questions + 1)]
    provider = StageLatencyProvider(scripted_responder(sub_questions=questions))
    provider.stage_latency = latency
    web = static_web(latency=web_latency)
    env = ResearchEnv(ModelGateway([provider], record_call_to_db), web, web, get_settings())
    with session_scope() as db:
        run = Run(question="benchmark", depth="deep", budget_tokens=400_000, max_workers=workers,
                  search_provider="static")
        db.add(run)
        db.flush()
        run_id = run.id
    await plan_run(env, run_id)
    with session_scope() as db:
        run = db.get(Run, run_id)
        plan = Plan.model_validate(run.plan)
        for q in plan.sub_questions:
            q.depends_on = []  # all independent: the case parallelism is for
        approve_plan(db, run, plan)
    started = time.perf_counter()
    await execute_run(env, run_id)
    elapsed = time.perf_counter() - started
    with session_scope() as db:
        assert db.get(Run, run_id).status == "completed"
    return round(elapsed, 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sub-questions", type=int, default=4)
    parser.add_argument("--workers", default="1,2,4")
    parser.add_argument("--web-latency", type=float, default=0.4)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    os.environ["MAX_SUB_QUESTIONS"] = str(max(5, args.sub_questions))
    get_settings.cache_clear()
    Base.metadata.create_all(get_engine())
    results = {}
    for w in [int(x) for x in args.workers.split(",")]:
        times = [asyncio.run(one_run(args.sub_questions, w, args.web_latency, DEFAULT_LATENCY)) for _ in range(args.repeats)]
        results[w] = {"runs_seconds": times, "best_seconds": min(times)}
        print(f"workers={w}: {times}")
    base = results[min(results)]["best_seconds"]
    summary = {"kind": "SIMULATED (scripted model with injected stage latencies; offline web)",
               "sub_questions": args.sub_questions, "web_latency_s": args.web_latency,
               "model_latency_s": DEFAULT_LATENCY, "results": results,
               "speedup_vs_1_worker": {w: round(base / r["best_seconds"], 2) for w, r in results.items()}}
    out = ROOT / "evaluation" / "results" / "bench-parallel-simulated.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["speedup_vs_1_worker"]))


if __name__ == "__main__":
    main()
