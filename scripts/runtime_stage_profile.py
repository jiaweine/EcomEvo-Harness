from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import statistics
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class MethodProfiler:
    """Pressure-only method timer; never changes production runtime behavior."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, list[float]] = defaultdict(list)

    def record(self, label: str, elapsed_ms: float) -> None:
        with self._lock:
            self._samples[label].append(elapsed_ms)

    def wrap(self, obj: Any, method_name: str, label: str) -> None:
        original = getattr(obj, method_name, None)
        if not callable(original):
            return
        if inspect.iscoroutinefunction(original):
            async def async_wrapped(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return await original(*args, **kwargs)
                finally:
                    self.record(label, (time.perf_counter() - started) * 1000.0)

            setattr(obj, method_name, async_wrapped)
            return

        def sync_wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.record(label, (time.perf_counter() - started) * 1000.0)

        setattr(obj, method_name, sync_wrapped)

    def report(self, *, tasks: int, wall_seconds: float) -> list[dict[str, Any]]:
        rows = []
        for label, values in self._samples.items():
            total_ms = sum(values)
            rows.append(
                {
                    "method": label,
                    "calls": len(values),
                    "calls_per_task": round(len(values) / max(1, tasks), 3),
                    "total_ms": round(total_ms, 3),
                    "ms_per_task": round(total_ms / max(1, tasks), 3),
                    "latency_ms": {
                        "p50": round(percentile(values, 0.50), 3),
                        "p95": round(percentile(values, 0.95), 3),
                        "p99": round(percentile(values, 0.99), 3),
                        "mean": round(statistics.fmean(values), 3),
                        "max": round(max(values), 3),
                    },
                    "wall_equivalent_pct": round(
                        100.0 * total_ms / max(1.0, wall_seconds * 1000.0), 2
                    ),
                }
            )
        rows.sort(key=lambda row: row["total_ms"], reverse=True)
        return rows


def attach_profiler(engine: EcomEvoEngine, profiler: MethodProfiler) -> None:
    targets: list[tuple[Any, str, str]] = []

    for name in (
        "create_session_events_checkpoint",
        "create_session_and_append",
        "append",
        "save_checkpoint",
        "save_checkpoint_and_append",
        "verify_chain",
    ):
        targets.append((engine.events, name, f"events.{name}"))

    for name in ("policy", "relevant", "record_outcome", "note_run"):
        targets.append((engine.skills, name, f"skills.{name}"))

    for name in ("profile", "record_outcome", "propose", "snapshot"):
        targets.append((engine.harness, name, f"harness.{name}"))

    for name in ("relevant", "add"):
        targets.append((engine.memory, name, f"memory.{name}"))

    for name in ("parse_goal", "initial_belief", "plan"):
        targets.append((engine.planner, name, f"planner.{name}"))

    for name in ("verify",):
        targets.append((engine.verifier, name, f"verifier.{name}"))

    for name in ("run",):
        targets.append((engine.autonomy, name, f"autonomy.{name}"))

    for name in ("execute",):
        targets.append((engine.ptc, name, f"ptc.{name}"))

    for name in ("evolve", "ingest", "distill_success"):
        targets.append((engine.evolver, name, f"evolver.{name}"))

    for obj, method_name, label in targets:
        profiler.wrap(obj, method_name, label)


async def run_profile(tasks: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ecomevo-runtime-profile-") as tmp:
        root = Path(tmp)
        db = root / "runtime-profile.db"
        engine = EcomEvoEngine(db)
        profiler = MethodProfiler()
        attach_profiler(engine, profiler)

        failures: list[str] = []
        latencies: list[float] = []

        async def one(index: int) -> None:
            started = time.perf_counter()
            summary = await engine.run(
                f"审核商家并核对主体、授权和历史风险。阶段画像任务 {index}。",
                [],
                domain_hint="merchant_review",
            )
            latencies.append(time.perf_counter() - started)
            if not summary.event_chain_valid:
                failures.append(f"{index}: invalid event chain")
            if not summary.stop_reason:
                failures.append(f"{index}: empty stop reason")

        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(tasks)))
        wall = time.perf_counter() - started
        rows = profiler.report(tasks=tasks, wall_seconds=wall)

        # These are overlapping method timers, so wall_equivalent_pct is diagnostic,
        # not an additive percentage. The ranking and per-call tails are the signal.
        return {
            "ok": not failures,
            "tasks": tasks,
            "wall_seconds": round(wall, 4),
            "throughput_tasks_per_second": round(tasks / wall, 3) if wall else 0.0,
            "task_latency_seconds": {
                "p50": round(percentile(latencies, 0.50), 4),
                "p95": round(percentile(latencies, 0.95), 4),
                "p99": round(percentile(latencies, 0.99), 4),
                "mean": round(statistics.fmean(latencies), 4) if latencies else 0.0,
            },
            "top_methods": rows,
            "failures": failures[:20],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile runtime method hotspots under concurrent load")
    parser.add_argument("--tasks", type=int, default=120)
    args = parser.parse_args()
    if args.tasks < 1 or args.tasks > 512:
        raise SystemExit("tasks must be between 1 and 512")
    result = asyncio.run(run_profile(args.tasks))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
