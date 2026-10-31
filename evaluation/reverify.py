"""Re-run only the verifier on stored live runs, to measure a verifier change on identical research.

For each record in evaluation/runs/<source>/, the run's claims and the pages it read stay exactly as they
were. Cross-check evidence is removed, every stance and verdict is reset, and verify_run() runs again with
the current code: one batched judge call per run. The original report is kept, so the cited claims are the
same and only their verdicts can change. Records are saved to evaluation/runs/<name>/ (resumable).

    python evaluation/reverify.py --source live-tavily-quick --name live-tavily-quick-reverified
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "evaluation"))

from sqlalchemy import delete, select, update  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.evaluation import export_run  # noqa: E402
from app.models import Claim, Evidence, Verdict  # noqa: E402
from app.research.env import build_default_env  # noqa: E402
from app.research.verifier import verify_run  # noqa: E402
from run_eval import RUNS, score  # noqa: E402


def reset_verification(run_id: uuid.UUID) -> None:
    with session_scope() as db:
        claim_ids = select(Claim.id).where(Claim.run_id == run_id)
        db.execute(delete(Evidence).where(Evidence.claim_id.in_(claim_ids), Evidence.origin == "cross_check"))
        db.execute(update(Evidence).where(Evidence.claim_id.in_(claim_ids))
                   .values(stance="pending", score=None, rationale=None))
        db.execute(update(Claim).where(Claim.run_id == run_id)
                   .values(verdict=Verdict.pending.value, support_domains=0, verdict_reason=None))


async def reverify_all(src: Path, dst: Path, source_name: str) -> None:
    # One event loop for every run: the Gemini async client is bound to the loop it was first used on.
    env = build_default_env()
    for path in sorted(src.glob("q*.json")):
        if (dst / path.name).exists():
            print(f"{path.stem}: already re-verified, skipping")
            continue
        original = json.loads(path.read_text(encoding="utf-8"))
        run_id = uuid.UUID(original["run"]["id"])
        reset_verification(run_id)
        summary = await verify_run(env, run_id)
        record = export_run(run_id)
        record["run"]["research_wall_seconds"] = original["run"]["research_wall_seconds"]
        record["notes"] = (f"Verifier re-run on the stored research of {source_name}/{path.name}; the report is "
                           "unchanged. model_calls includes the original calls plus the new verify call.")
        (dst / path.name).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{path.stem}: {summary}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    src, dst = RUNS / args.source, RUNS / args.name
    dst.mkdir(parents=True, exist_ok=True)
    config = json.loads((src / "config.json").read_text())
    config["reverified_from"] = args.source
    (dst / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    asyncio.run(reverify_all(src, dst, args.source))
    print(json.dumps(score(args.name)["aggregate"], indent=2))


if __name__ == "__main__":
    main()
