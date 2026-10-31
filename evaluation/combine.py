"""Combine eval folders into one scored set without double-counting cost.

    python evaluation/combine.py --name live-tavily-22 \
        --reverified live-tavily-quick-reverified:live-tavily-quick --fresh live-tavily-v2

For re-verified records, claims/verdicts/report come from the re-verified copy (current verifier), while
the run's cost, tokens, wall time and model calls come from the original record: that is what one pass of
the pipeline cost (re-verification added a second verifier call that a normal run never makes).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "evaluation"))

from run_eval import RUNS, score  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--reverified", action="append", default=[], help="reverified_folder:original_folder")
    parser.add_argument("--fresh", action="append", default=[])
    args = parser.parse_args()
    dst = RUNS / args.name
    dst.mkdir(parents=True, exist_ok=True)
    sources = []
    for pair in args.reverified:
        rev, orig = pair.split(":")
        for path in sorted((RUNS / rev).glob("q*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            original = json.loads((RUNS / orig / path.name).read_text(encoding="utf-8"))
            record["run"] = original["run"]
            record["model_calls"] = original["model_calls"]
            record["notes"] = f"verdicts from {rev}; run cost/time/model calls from {orig} (one pipeline pass)"
            (dst / path.name).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        sources.append(pair)
    for folder in args.fresh:
        for path in sorted((RUNS / folder).glob("q*.json")):
            (dst / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        sources.append(folder)
    base = json.loads((RUNS / args.fresh[0] / "config.json").read_text()) if args.fresh else {}
    (dst / "config.json").write_text(json.dumps({**base, "combined_from": sources}, indent=2), encoding="utf-8")
    print(json.dumps(score(args.name)["aggregate"], indent=2))


if __name__ == "__main__":
    main()
