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
from scripts.runtime_stage_profile import MethodProfiler, attach_profiler, percentile


async def run_profile(tasks: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ecomevo-decision-leaf-") as tmp:
        root = Path(tmp)
        engine = EcomEvoEngine(root / "decision-leaf.db")
        profiler = MethodProfiler()
        attach_profiler(engine, profiler)

        policy = engine.autonomy.policy
        for name in ("_rank_candidates", "_base_features", "_tool_meta", "_terms"):
            profiler.wrap(policy, name, f"decision.{name.lstrip('_')}")

        routing = getattr(policy, "routing", None)
        if routing is not None:
            for name in ("_posterior_from_row", "_inverse", "_reliability_map"):
                profiler.wrap(routing, name, f"routing.{name.lstrip('_')}")

        failures: list[str] = []
        latencies: list[float] = []
        loop_lag_ms: list[float] = []
        loop_lag_windows: list[tuple[float, float]] = []
        stop = asyncio.Event()

        async def heartbeat() -> None:
            interval = 0.01
            target = time.perf_counter() + interval
            while not stop.is_set():
                await asyncio.sleep(max(0.0, target - time.perf_counter()))
                now = time.perf_counter()
                loop_lag_windows.append((target, now))
                loop_lag_ms.append(max(0.0, now - target) * 1000.0)
                target = now + interval

        async def one(index: int) -> None:
            started = time.perf_counter()
            summary = await engine.run(
                f"审核商家并核对主体、授权和历史风险。决策叶子画像 {index}。",
                [],
                domain_hint="merchant_review",
            )
            latencies.append(time.perf_counter() - started)
            if not summary.event_chain_valid:
                failures.append(f"{index}: invalid event chain")

        heartbeat_task = asyncio.create_task(heartbeat())
        started = time.perf_counter()
        try:
            await asyncio.gather(*(one(index) for index in range(tasks)))
        finally:
            stop.set()
            await heartbeat_task
        wall = time.perf_counter() - started
        rows = profiler.report(tasks=tasks, wall_seconds=wall)
        decision_rows = [
            row for row in rows
            if row["method"].startswith(("decision.", "routing.", "tools.describe", "skills."))
        ]
        return {
            "ok": not failures,
            "tasks": tasks,
            "wall_seconds": round(wall, 4),
            "throughput_tasks_per_second": round(tasks / wall, 3),
            "task_latency_seconds": {
                "p50": round(percentile(latencies, 0.50), 4),
                "p95": round(percentile(latencies, 0.95), 4),
                "p99": round(percentile(latencies, 0.99), 4),
                "mean": round(statistics.fmean(latencies), 4),
            },
            "event_loop_lag_ms": {
                "p95": round(percentile(loop_lag_ms, 0.95), 3),
                "p99": round(percentile(loop_lag_ms, 0.99), 3),
                "max": round(max(loop_lag_ms), 3) if loop_lag_ms else 0.0,
            },
            "event_loop_stall_attribution": profiler.stall_report(loop_lag_windows),
            "decision_leaf_methods": decision_rows,
            "failures": failures,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile decision-ranking leaf work")
    parser.add_argument("--tasks", type=int, default=120)
    args = parser.parse_args()
    if args.tasks < 1 or args.tasks > 512:
        raise SystemExit("tasks must be between 1 and 512")
    result = asyncio.run(run_profile(args.tasks))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
