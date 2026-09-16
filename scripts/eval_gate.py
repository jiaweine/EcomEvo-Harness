from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ecomevo.evaluation import asset_for, evaluate, load_cases, validate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "evals" / "gold_set.jsonl"

# Keep the historical helper names importable from scripts.eval_gate while the
# implementation lives in the packaged evaluation core used by the Test Center.
__all__ = ["asset_for", "evaluate", "load_cases", "validate", "DEFAULT_GOLD"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the EcomEvo business Gold Set promotion gate")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.gold))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
