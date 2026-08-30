from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary


TASKS = 64
HEARTBEAT_INTERVAL = 0.002
DOMAIN = "merchant_review"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class TracingSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path: Path):
        self.immediate_begins = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str):
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._trace_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection


async def measure(mode: str, root: Path) -> dict[str, Any]:
    skills = TracingSkills(root / f"{mode}.db")
    before = skills.policy(DOMAIN)
    skills.immediate_begins = 0

    lags_ms: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        target = time.perf_counter() + HEARTBEAT_INTERVAL
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            now = time.perf_counter()
            lags_ms.append(max(0.0, now - target) * 1000.0)
            target = now + HEARTBEAT_INTERVAL

    async def one() -> None:
        if mode == "sync":
            skills.note_run(DOMAIN, success=False, skill_used=False)
        else:
            await skills.note_run_async(DOMAIN, success=False, skill_used=False)

    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(HEARTBEAT_INTERVAL * 2)
    started = time.perf_counter()
    try:
        await asyncio.gather(*(one() for _ in range(TASKS)))
    finally:
        wall = time.perf_counter() - started
        stop.set()
        await heartbeat_task

    after = skills.policy(DOMAIN)
    failures: list[str] = []
    if skills.immediate_begins != TASKS:
        failures.append(f"writer transactions changed: {skills.immediate_begins} != {TASKS}")
    if int(after["updates"]) != int(before["updates"]) + TASKS:
        failures.append("policy update count does not match completed finalizations")

    return {
        "mode": mode,
        "tasks": TASKS,
        "writer_transactions": skills.immediate_begins,
        "policy_updates": int(after["updates"]) - int(before["updates"]),
        "wall_seconds": round(wall, 4),
        "throughput_tasks_per_second": round(TASKS / wall, 3) if wall else 0.0,
        "heartbeat_ms": {
            "samples": len(lags_ms),
            "p50": round(percentile(lags_ms, 0.50), 3),
            "p95": round(percentile(lags_ms, 0.95), 3),
            "p99": round(percentile(lags_ms, 0.99), 3),
            "max": round(max(lags_ms), 3) if lags_ms else 0.0,
            "mean": round(statistics.fmean(lags_ms), 3) if lags_ms else 0.0,
        },
        "failures": failures,
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-skill-finalization-") as tmp:
        root = Path(tmp)
        sync = await measure("sync", root)
        async_result = await measure("async", root)

    failures.extend(sync["failures"])
    failures.extend(async_result["failures"])
    if sync["writer_transactions"] != async_result["writer_transactions"]:
        failures.append("skill offload changed writer transaction count")

    sync_max = float(sync["heartbeat_ms"]["max"])
    async_max = float(async_result["heartbeat_ms"]["max"])
    lag_ratio = async_max / max(0.001, sync_max)
    wall_ratio = float(async_result["wall_seconds"]) / max(0.0001, float(sync["wall_seconds"]))

    # Fixed before CI: preserve one policy writer per finalization and remove most of the
    # phase-aligned loop stall. This tiny SQLite operation may pay noticeable thread-handoff
    # overhead, so the same-process wall guard is deliberately generous; full runtime pressure
    # remains the end-to-end non-regression check.
    if lag_ratio > 0.35:
        failures.append(f"skill max lag reduction too small: ratio={lag_ratio:.4f} > 0.35")
    if wall_ratio > 1.75:
        failures.append(f"skill offload wall regression too large: ratio={wall_ratio:.4f} > 1.75")

    return {
        "ok": not failures,
        "sync": sync,
        "async": async_result,
        "comparison": {
            "async_to_sync_max_lag_ratio": round(lag_ratio, 4),
            "async_to_sync_wall_ratio": round(wall_ratio, 4),
            "writer_transactions_equal": sync["writer_transactions"] == async_result["writer_transactions"],
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
