from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer


TASKS = 64
HEARTBEAT_INTERVAL = 0.002
DOMAIN = "merchant_review"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class TracingHarness(BundledHarnessEvolutionOptimizer):
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


def project(snapshot: dict[str, Any]) -> dict[str, Any]:
    components = list(snapshot.get("components") or [])
    return {
        "active": {
            str(row["kind"]): int(row["generation"])
            for row in components
            if row.get("status") == "active"
        },
        "shadow": [
            {"kind": str(row["kind"]), "generation": int(row["generation"])}
            for row in components
            if row.get("status") == "shadow"
        ],
    }


async def measure(mode: str, root: Path) -> dict[str, Any]:
    harness = TracingHarness(root / f"{mode}.db")
    profile = harness.profile(DOMAIN, session_key=f"{mode}-seed")
    component_ids = list(profile.get("component_ids") or [])
    if len(component_ids) != len(harness.KINDS):
        raise AssertionError("failed to initialize full harness profile")
    harness.immediate_begins = 0

    lags_ms: list[float] = []
    states: list[dict[str, Any]] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        target = time.perf_counter() + HEARTBEAT_INTERVAL
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            now = time.perf_counter()
            lags_ms.append(max(0.0, now - target) * 1000.0)
            target = now + HEARTBEAT_INTERVAL

    async def one(index: int) -> None:
        kwargs = {
            "verifier_score": 0.82,
            "evidence_complete": True,
            "session_id": f"{mode}-{index}",
            "meta": {"probe": "harness-finalization"},
        }
        if mode == "sync":
            transitions = harness.record_outcome(DOMAIN, component_ids, **kwargs)
            state = project(harness.snapshot(DOMAIN))
        else:
            transitions = await harness.record_outcome_async(DOMAIN, component_ids, **kwargs)
            state = await harness.state_summary_async(DOMAIN)
        if transitions:
            raise AssertionError("unexpected transition without a shadow component")
        states.append(state)

    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(HEARTBEAT_INTERVAL * 2)
    started = time.perf_counter()
    try:
        await asyncio.gather(*(one(index) for index in range(TASKS)))
    finally:
        wall = time.perf_counter() - started
        stop.set()
        await heartbeat_task

    failures: list[str] = []
    if harness.immediate_begins != TASKS:
        failures.append(f"writer transactions changed: {harness.immediate_begins} != {TASKS}")
    final_snapshot = harness.snapshot(DOMAIN)
    expected_state = project(final_snapshot)
    if any(state != expected_state for state in states[-1:]):
        failures.append("final compact state differs from full snapshot projection")
    selected = set(component_ids)
    selected_rows = [
        row for row in final_snapshot.get("components", []) if row.get("component_id") in selected
    ]
    if len(selected_rows) != len(component_ids):
        failures.append("selected harness components disappeared")
    elif any(int(row.get("uses", 0)) != TASKS for row in selected_rows):
        failures.append("outcome uses do not match completed task count")

    return {
        "mode": mode,
        "tasks": TASKS,
        "writer_transactions": harness.immediate_begins,
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
    with tempfile.TemporaryDirectory(prefix="ecomevo-harness-finalization-") as tmp:
        root = Path(tmp)
        sync = await measure("sync", root)
        async_result = await measure("async", root)

    failures.extend(sync["failures"])
    failures.extend(async_result["failures"])
    if sync["writer_transactions"] != async_result["writer_transactions"]:
        failures.append("harness offload changed writer transaction count")

    sync_max = float(sync["heartbeat_ms"]["max"])
    async_max = float(async_result["heartbeat_ms"]["max"])
    lag_ratio = async_max / max(0.001, sync_max)
    wall_ratio = float(async_result["wall_seconds"]) / max(0.0001, float(sync["wall_seconds"]))

    # Fixed before CI: the finalization fast path must materially remove the phase-aligned
    # loop stall while preserving exactly one learning writer transaction per task. Two
    # short executor handoffs are allowed a wider wall budget than EventStore's bundled I/O
    # gate; full-runtime pressure remains the end-to-end non-regression check.
    if lag_ratio > 0.35:
        failures.append(f"harness max lag reduction too small: ratio={lag_ratio:.4f} > 0.35")
    if wall_ratio > 1.75:
        failures.append(f"harness offload wall regression too large: ratio={wall_ratio:.4f} > 1.75")

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
