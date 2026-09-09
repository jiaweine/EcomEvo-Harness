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


TASKS = 256
PAIR_ORDERS = (("sync", "async"), ("async", "sync")) * 3
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
        "wall_seconds": wall,
        "throughput_tasks_per_second": round(TASKS / wall, 3) if wall else 0.0,
        "heartbeat_ms": {
            "samples": len(lags_ms),
            "p50": round(percentile(lags_ms, 0.50), 3),
            "p95": round(percentile(lags_ms, 0.95), 3),
            "p99": round(percentile(lags_ms, 0.99), 3),
            "max": max(lags_ms) if lags_ms else 0.0,
            "mean": round(statistics.fmean(lags_ms), 3) if lags_ms else 0.0,
        },
        "failures": failures,
    }


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Use every predeclared pair; correctness failures never get averaged away."""
    failures: list[str] = []
    if len(samples) != len(PAIR_ORDERS):
        failures.append(f"sample count changed: {len(samples)} != {len(PAIR_ORDERS)}")
    wall_ratios: list[float] = []
    lag_ratios: list[float] = []
    writers_equal = True
    for index, sample in enumerate(samples):
        if index < len(PAIR_ORDERS) and tuple(sample["order"]) != PAIR_ORDERS[index]:
            failures.append(f"sample {index + 1}: fixed arm order changed")
        sync = sample["sync"]
        async_result = sample["async"]
        for arm in (sync, async_result):
            failures.extend(f"sample {index + 1}: {failure}" for failure in arm["failures"])
            for key in ("tasks", "writer_transactions", "policy_updates"):
                if arm[key] != TASKS:
                    failures.append(f"sample {index + 1} {arm['mode']}: {key} != {TASKS}")
        equal = sync["writer_transactions"] == async_result["writer_transactions"]
        writers_equal = writers_equal and equal
        if not equal:
            failures.append(f"sample {index + 1}: skill offload changed writer transaction count")
        lag = float(async_result["heartbeat_ms"]["max"]) / max(0.001, float(sync["heartbeat_ms"]["max"]))
        wall = float(async_result["wall_seconds"]) / max(0.0001, float(sync["wall_seconds"]))
        wall_ratios.append(wall)
        lag_ratios.append(lag)
        sample["comparison"] = {
            "async_to_sync_max_lag_ratio": lag,
            "async_to_sync_wall_ratio": wall,
            "writer_transactions_equal": equal,
        }
    lag_ratio = statistics.median(lag_ratios) if lag_ratios else 0.0
    wall_ratio = statistics.median(wall_ratios) if wall_ratios else 0.0

    # Fixed before CI: preserve one policy writer per finalization and remove most of the
    # phase-aligned loop stall. Keep the original numerical limits. #110's first
    # post-merge 64-call pair failed, and #112's fixed six-order attribution found
    # current and legacy dispatch comparable at both 64 and 256 calls. Use six
    # balanced 256-call pairs, without retries, early success, or outlier exclusion.
    if lag_ratio > 0.35:
        failures.append(f"skill max lag reduction too small: ratio={lag_ratio:.6f} > 0.35")
    if wall_ratio > 1.75:
        failures.append(f"skill offload wall regression too large: ratio={wall_ratio:.6f} > 1.75")

    return {
        "ok": not failures,
        "sampling": {
            "pairs": len(PAIR_ORDERS),
            "tasks_per_arm": TASKS,
            "aggregation": "median of all paired ratios",
            "executor_warmup": "one untimed handoff before all pairs",
        },
        "samples": samples,
        "comparison": {
            "async_to_sync_max_lag_ratio": lag_ratio,
            "async_to_sync_wall_ratio": wall_ratio,
            "writer_transactions_equal": writers_equal,
        },
        "failures": failures,
    }


async def main_async() -> dict[str, Any]:
    # This gate measures finalization after runtime startup, like #112. Keep
    # executor creation outside timing, just as policy bootstrap already is.
    await asyncio.to_thread(lambda: None)
    samples: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-skill-finalization-") as tmp:
        root = Path(tmp)
        for index, order in enumerate(PAIR_ORDERS):
            sample_root = root / str(index)
            sample_root.mkdir()
            sample: dict[str, Any] = {"order": list(order)}
            for mode in order:
                sample[mode] = await measure(mode, sample_root)
            samples.append(sample)
    return summarize_samples(samples)


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
