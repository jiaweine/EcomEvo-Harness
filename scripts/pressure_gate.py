from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ecomevo.models import ToolCall
from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.resilient_executor import ResilientPTCExecutor
from ecomevo.runtime.skills import AdaptiveSkillLibrary


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class _AllowReadSandbox:
    def validate_tool(self, _tool):
        return SimpleNamespace(allowed=True, reason="", requires_confirmation=False)


class _PressureRegistry:
    def __init__(self, tool):
        self.tools = {tool.key: tool}


class _SlowPressureTool:
    key = "pressure.read"
    cost = 0.1

    def __init__(self):
        self.active = 0
        self.peak = 0

    async def execute(self, _ctx, _args):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(1)
            return {"ok": True}
        finally:
            self.active -= 1


async def run_backpressure_probe(tasks: int = 64) -> dict[str, Any]:
    tool = _SlowPressureTool()
    executor = ResilientPTCExecutor(_PressureRegistry(tool), _AllowReadSandbox())
    executor.timeout_s = 0.05
    executor.max_inflight = 2
    executor._slots = asyncio.Semaphore(2)
    calls = [
        ToolCall(
            call_id=f"pressure-{index}",
            tool=tool.key,
            purpose="overload deadline probe",
            parallel_group="overload",
        )
        for index in range(tasks)
    ]
    started = time.perf_counter()
    results = await executor.execute(calls, {})
    wall = time.perf_counter() - started
    failures: list[str] = []
    expected_error = "tool_timeout:0.05s"
    if any(result.error != expected_error for result in results):
        failures.append("overloaded calls did not converge to the bounded timeout result")
    # A generous 15x scheduling envelope avoids runner-load flakes while still
    # distinguishing the legacy per-queue-slot timeout (~1.6s for this probe).
    if wall >= 0.75:
        failures.append(f"backpressure queue exceeded total deadline envelope: {wall:.4f}s")
    if tool.peak > 2:
        failures.append(f"tool fan-out exceeded semaphore bound: {tool.peak}")
    if tool.active:
        failures.append(f"cancelled tools remained active: {tool.active}")
    durations = [result.duration_ms for result in results]
    return {
        "name": "ptc_backpressure_deadline",
        "tasks": tasks,
        "max_inflight": 2,
        "deadline_seconds": 0.05,
        "wall_seconds": round(wall, 4),
        "duration_ms_p99": round(percentile(durations, 0.99), 3),
        "peak_active": tool.peak,
        "failures": failures,
    }


class _CoordinatedReadLibrary(AdaptiveSkillLibrary):
    def __init__(self, db_path: Path, barrier: threading.Barrier):
        self._pressure_barrier = barrier
        super().__init__(db_path)

    def policy(self, domain: str):
        value = super().policy(domain)
        self._pressure_barrier.wait(timeout=5)
        return value


def run_policy_contention_probe(root: Path, workers: int = 8) -> dict[str, Any]:
    db = root / "adaptive-policy-contention.db"
    barrier = threading.Barrier(workers)
    libraries = [_CoordinatedReadLibrary(db, barrier) for _ in range(workers)]
    started = time.perf_counter()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                library.note_run,
                "merchant_review",
                success=False,
                skill_used=False,
            )
            for library in libraries
        ]
        for future in futures:
            try:
                future.result(timeout=10)
            except Exception as exc:
                failures.append(f"policy worker failed: {exc!r}")
    policy = AdaptiveSkillLibrary(db).policy("merchant_review")
    expected_exploration = 0.60 + workers * 0.025
    if policy["updates"] != workers:
        failures.append(f"policy updates lost: {policy['updates']} != {workers}")
    if abs(policy["exploration"] - expected_exploration) > 1e-9:
        failures.append(
            f"policy value lost an update: {policy['exploration']:.6f} != {expected_exploration:.6f}"
        )
    return {
        "name": "adaptive_policy_contention",
        "workers": workers,
        "wall_seconds": round(time.perf_counter() - started, 4),
        "updates": policy["updates"],
        "exploration": round(policy["exploration"], 6),
        "failures": failures,
    }


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
        probes = [
            await run_backpressure_probe(),
            await asyncio.to_thread(run_policy_contention_probe, root),
        ]
        results = []
        for level in levels:
            results.append(await run_level(level, root))
        failures = [
            f"probe={row['name']}: {failure}"
            for row in probes
            for failure in row["failures"]
        ]
        failures.extend(
            f"c={row['concurrency']}: {failure}"
            for row in results
            for failure in row["failures"]
        )
        return {
            "ok": not failures,
            "levels": levels,
            "probes": probes,
            "results": results,
            "failures": failures,
        }


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
