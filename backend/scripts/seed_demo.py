"""Seed one finished research run into the configured database without any API key or network.

It uses the scripted model and the offline web from app/research/simulated.py, so a reviewer can open the
UI and click through a report, claim audits and the research trail. The run is labelled "[offline demo]";
its content and cost are simulated, not model output.

    python -m scripts.seed_demo        (from backend/)
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db import session_scope
from app.llm.gateway import ModelGateway
from app.llm.schemas import Plan
from app.models import Run
from app.research.env import ResearchEnv
from app.research.orchestrator import approve_plan, execute_run, plan_run
from app.research.simulated import scripted_provider, static_web
from app.services.observability import record_call_to_db


async def main() -> None:
    web = static_web(include_mirror=True)
    env = ResearchEnv(ModelGateway([scripted_provider()], record_call_to_db), web, web, get_settings())
    with session_scope() as db:
        run = Run(question="[offline demo] When was the Eiffel Tower completed, and how tall is it?", depth="standard",
                  budget_tokens=50_000, max_workers=2, search_provider="static")
        db.add(run)
        db.flush()
        run_id = run.id
    await plan_run(env, run_id)
    with session_scope() as db:
        run = db.get(Run, run_id)
        approve_plan(db, run, Plan.model_validate(run.plan))
    await execute_run(env, run_id)
    print(f"Seeded offline demo run {run_id}: open http://localhost:5183/runs/{run_id}")


if __name__ == "__main__":
    asyncio.run(main())
