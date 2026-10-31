"""Verifier accuracy on hand-labelled claim-quote pairs (supports / contradicts / insufficient).

One batched model call over evaluation/dataset/verifier_pairs.jsonl, using the production verifier
prompt and schema. Includes a prompt-injection pair (the quote tells the judge to answer "supports").
The raw judgements are saved to evaluation/runs/<name>/verifier.json; rerun with --score-only.

    python evaluation/eval_verifier.py --name verifier-live
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.llm.gateway import CallContext, ModelGateway, build_providers  # noqa: E402
from app.llm.prompts import VERIFY_SYSTEM, VERIFY_USER, VERIFY_VERSION, Prompt  # noqa: E402
from app.llm.schemas import VerifierOutput  # noqa: E402

PAIRS = ROOT / "evaluation" / "dataset" / "verifier_pairs.jsonl"
LABELS = ("supports", "contradicts", "insufficient")


async def judge(pairs: list[dict]) -> tuple[dict, list]:
    records = []
    gateway = ModelGateway(build_providers(), records.append)
    block = "\n\n".join(f'Pair {n}:\nCLAIM: {p["claim"]}\n<source domain="example.org" published="unknown">\n'
                        f'QUOTE: {p["quote"]}\n</source>' for n, p in enumerate(pairs))
    prompt = Prompt("verify", VERIFY_VERSION, VERIFY_SYSTEM, VERIFY_USER.format(pairs=block))
    result = await gateway.generate(prompt, VerifierOutput, CallContext())
    return result.output.model_dump(), [r.__dict__ | {"cost_usd": float(r.cost_usd)} for r in records]


def score(pairs: list[dict], output: dict) -> dict:
    got = {j["pair"]: j["stance"] for j in output["judgements"]}
    confusion = Counter()
    rows = []
    for n, p in enumerate(pairs):
        pred = got.get(n, "missing")
        confusion[(p["gold"], pred)] += 1
        rows.append({"id": p["id"], "gold": p["gold"], "pred": pred})
    correct = sum(r["gold"] == r["pred"] for r in rows)
    false_support = sum(r["pred"] == "supports" and r["gold"] != "supports" for r in rows)
    per_label = {lab: {"n": sum(r["gold"] == lab for r in rows),
                       "correct": sum(r["gold"] == lab == r["pred"] for r in rows)} for lab in LABELS}
    return {"pairs": len(rows), "accuracy": round(correct / len(rows), 3), "false_supports": false_support,
            "per_label": per_label, "confusion": {f"{g}->{p}": c for (g, p), c in sorted(confusion.items())},
            "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="verifier-live")
    parser.add_argument("--score-only", action="store_true")
    args = parser.parse_args()
    pairs = [json.loads(line) for line in PAIRS.read_text(encoding="utf-8").splitlines() if line]
    folder = ROOT / "evaluation" / "runs" / args.name
    folder.mkdir(parents=True, exist_ok=True)
    raw_path = folder / "verifier.json"
    if not args.score_only and not raw_path.exists():
        output, calls = asyncio.run(judge(pairs))
        raw_path.write_text(json.dumps({"output": output, "model_calls": calls}, indent=2, default=str), encoding="utf-8")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    summary = score(pairs, raw["output"])
    summary["model_calls"] = [{k: c[k] for k in ("model", "status", "input_tokens", "output_tokens", "latency_ms",
                                                   "cost_usd")} for c in raw["model_calls"]]
    results = ROOT / "evaluation" / "results"
    results.mkdir(exist_ok=True)
    (results / f"{args.name}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
