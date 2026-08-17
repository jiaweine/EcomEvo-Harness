from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


async def run_level(level: int, root: Path) -> dict[str, Any]:
    db = root / f"runtime-{level}.db"
    engine = EcomEvoEngine(db)
    started = time.perf_counter()

    async def one(index: int):
        t0 = time.perf_counter()
        summary = await engine.run(
            f"审核商家并核对主体、授权和历史风险。压力任务编号 {index}。",
            [],
            domain_hint="merchant_review",
        )
        return summary, time.perf_counter() - t0

    rows = await asyncio.gather(*(one(i) for i in range(level)))
    wall = time.perf_counter() - started
    latencies = [elapsed for _, elapsed in rows]
    summaries = [summary for summary, _ in rows]
    failures: list[str] = []
    session_ids = [summary.session_id for summary in summaries]
    if len(set(session_ids)) != len(session_ids):
        failures.append("duplicate session id")
    for index, summary in enumerate(summaries):
        if not summary.event_chain_valid:
            failures.append(f"{index}: invalid event chain")
        if not summary.stop_reason:
            failures.append(f"{index}: empty stop reason")
        if summary.tool_cost_used > summary.tool_cost_budget + 1e-9:
            failures.append(f"{index}: tool budget exceeded")
        if summary.status not in {"completed", "needs_evidence"}:
            failures.append(f"{index}: invalid status {summary.status}")
        if summary.status != "completed" and summary.proposed_actions:
            failures.append(f"{index}: incomplete evidence produced actions")
        for action in summary.proposed_actions:
            if action.status != "proposed":
                failures.append(f"{index}: autonomous action status {action.status}")
            if action.side_effect and not action.requires_confirmation:
                failures.append(f"{index}: side effect without confirmation")
    return {
        "concurrency": level,
        "tasks": level,
        "wall_seconds": round(wall, 4),
        "throughput_tasks_per_second": round(level / wall, 3) if wall else 0.0,
        "latency_seconds": {
            "p50": round(percentile(latencies, 0.50), 4),
            "p95": round(percentile(latencies, 0.95), 4),
            "p99": round(percentile(latencies, 0.99), 4),
            "mean": round(statistics.fmean(latencies), 4) if latencies else 0.0,
        },
        "failures": failures,
    }


async def main_async(levels: list[int]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ecomevo-pressure-") as tmp:
        root = Path(tmp)
        results = []
        for level in levels:
            results.append(await run_level(level, root))
        failures = [f"c={row['concurrency']}: {failure}" for row in results for failure in row["failures"]]
        return {"ok": not failures, "levels": levels, "results": results, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description="Current-head EcomEvo runtime pressure/safety gate")
    parser.add_argument("--levels", default="1,8,32,64,120,240")
    args = parser.parse_args()
    levels = [int(value) for value in args.levels.split(",") if value.strip()]
    if not levels or any(level < 1 or level > 512 for level in levels):
        raise SystemExit("levels must be between 1 and 512")
    result = asyncio.run(main_async(levels))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
