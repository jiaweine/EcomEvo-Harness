from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.grouped_restore_event_store import GroupedRestoreBundledEventStore


SESSIONS = 128
REQUESTS = 512
EXPERIMENTS = 5
GROUP_LIMIT = 64
HEARTBEAT_INTERVAL = 0.001
PAYLOAD_WORDS = 192
WALL_RATIO_LIMIT = 1.15
MAX_LAG_RATIO_LIMIT = 0.25


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def snapshot(index: int, stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "index": index,
        "belief": {
            "confidence": round(0.2 + (index % 7) * 0.03, 4),
            "facts": {
                "merchant": f"merchant-{index}",
                "notes": [f"evidence-{index}-{part}" for part in range(24)],
            },
            "missing_evidence": ["identity", "authorization"],
        },
        "task_graph": {
            "nodes": [
                {
                    "id": f"node-{part}",
                    "kind": "evidence.search",
                    "goal": f"checkpoint restore payload {index} {part}",
                }
                for part in range(12)
            ]
        },
        "padding": [f"payload-{index}-{part:03d}" for part in range(PAYLOAD_WORDS)],
    }


class TracingProductionStore(GroupedRestoreBundledEventStore):
    def __init__(self, path: Path):
        self.restore_batches: list[int] = []
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _restore_checkpoint_group(self, batch):
        with self._trace_lock:
            self.restore_batches.append(len(batch))
        return super()._restore_checkpoint_group(batch)


def seed_store(path: Path) -> None:
    store = TracingProductionStore(path)
    for index in range(SESSIONS):
        sid = f"restore-{index}"
        store.create_session_events_checkpoint(
            sid,
            [
                ("goal.parsed", {"goal": f"review-{index}"}),
                ("belief.updated", {"confidence": 0.2, "index": index}),
                ("harness.profile.bound", {"component_ids": ["active"]}),
            ],
            snapshot(index, "initial"),
        )
        event4 = store.append(sid, "tool.executed", {"tool": "evidence.search", "index": index})
        store.save_checkpoint(sid, snapshot(index, "recovery-1"), seq=event4.seq)
        event5 = store.append(sid, "verification.completed", {"score": 0.4, "index": index})
        store.save_checkpoint(sid, snapshot(index, "recovery-2"), seq=event5.seq)
        if not store.verify_chain(sid):
            raise AssertionError(f"invalid seed event chain: {sid}")
    with store._conn() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


async def measure(template: Path, root: Path, mode: str, experiment: int) -> dict[str, Any]:
    path = root / f"{mode}-{experiment}.db"
    shutil.copy2(template, path)
    store = TracingProductionStore(path)
    lags_ms: list[float] = []
    failures: list[str] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        target = time.perf_counter() + HEARTBEAT_INTERVAL
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            now = time.perf_counter()
            lags_ms.append(max(0.0, now - target) * 1000.0)
            target = now + HEARTBEAT_INTERVAL

    async def one(index: int) -> None:
        sid = f"restore-{index % SESSIONS}"
        seq = 3 + (index % 3)
        if mode == "baseline":
            value = store.restore_checkpoint(sid, seq)
        else:
            value = await store.restore_checkpoint_async(sid, seq)
        if value is None:
            failures.append(f"{mode}: missing {sid} seq={seq}")
            return
        checkpoint = value.get("_checkpoint") if isinstance(value, dict) else None
        if not isinstance(checkpoint, dict) or int(checkpoint.get("seq", -1)) != seq:
            failures.append(f"{mode}: wrong checkpoint {sid} seq={seq}: {checkpoint!r}")

    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(HEARTBEAT_INTERVAL * 4)
    started = time.perf_counter()
    try:
        await asyncio.gather(*(one(index) for index in range(REQUESTS)))
    finally:
        wall = time.perf_counter() - started
        stop.set()
        await heartbeat_task

    if mode == "production":
        expected = (REQUESTS + GROUP_LIMIT - 1) // GROUP_LIMIT
        if len(store.restore_batches) != expected:
            failures.append(f"production restore batches {len(store.restore_batches)} != {expected}")
        if any(size > GROUP_LIMIT for size in store.restore_batches):
            failures.append(f"production restore batch exceeded {GROUP_LIMIT}: {store.restore_batches}")
        if sum(store.restore_batches) != REQUESTS:
            failures.append(
                f"production restore requests attributed {sum(store.restore_batches)} != {REQUESTS}"
            )
    elif store.restore_batches:
        failures.append("baseline unexpectedly entered grouped restore")

    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": wall,
        "heartbeat_ms": {
            "samples": len(lags_ms),
            "p50": percentile(lags_ms, 0.50),
            "p95": percentile(lags_ms, 0.95),
            "p99": percentile(lags_ms, 0.99),
            "max": max(lags_ms) if lags_ms else 0.0,
            "mean": statistics.fmean(lags_ms) if lags_ms else 0.0,
        },
        "restore_batches": list(store.restore_batches),
        "failures": failures[:10],
    }


def rounded(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "wall_seconds": round(float(row["wall_seconds"]), 4),
        "heartbeat_ms": {
            key: (int(value) if key == "samples" else round(float(value), 3))
            for key, value in row["heartbeat_ms"].items()
        },
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-grouped-restore-gate-") as tmp:
        root = Path(tmp)
        template = root / "template.db"
        seed_store(template)

        rows: list[dict[str, Any]] = []
        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "production")
                if experiment % 2 == 0
                else ("production", "baseline")
            )
            for mode in order:
                row = await measure(template, root, mode, experiment)
                rows.append(row)
                failures.extend(row["failures"])

    baseline = [row for row in rows if row["mode"] == "baseline"]
    production = [row for row in rows if row["mode"] == "production"]
    baseline_wall = statistics.median(float(row["wall_seconds"]) for row in baseline)
    production_wall = statistics.median(float(row["wall_seconds"]) for row in production)
    baseline_max = statistics.median(float(row["heartbeat_ms"]["max"]) for row in baseline)
    production_max = statistics.median(float(row["heartbeat_ms"]["max"]) for row in production)
    wall_ratio = production_wall / max(baseline_wall, 1e-9)
    max_lag_ratio = production_max / max(baseline_max, 1e-6)

    if wall_ratio > WALL_RATIO_LIMIT:
        failures.append(
            f"production grouped restore wall ratio {wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
        )
    if max_lag_ratio > MAX_LAG_RATIO_LIMIT:
        failures.append(
            "production grouped restore max-lag ratio "
            f"{max_lag_ratio:.4f} > {MAX_LAG_RATIO_LIMIT:.2f}"
        )

    result = {
        "ok": not failures,
        "sessions": SESSIONS,
        "requests_per_arm": REQUESTS,
        "experiments": EXPERIMENTS,
        "group_limit": GROUP_LIMIT,
        "thresholds": {
            "wall_ratio_max": WALL_RATIO_LIMIT,
            "max_lag_ratio_max": MAX_LAG_RATIO_LIMIT,
        },
        "medians": {
            "baseline_wall_seconds": round(baseline_wall, 4),
            "production_wall_seconds": round(production_wall, 4),
            "production_to_baseline_wall_ratio": round(wall_ratio, 4),
            "baseline_heartbeat_max_ms": round(baseline_max, 4),
            "production_heartbeat_max_ms": round(production_max, 4),
            "production_to_baseline_max_lag_ratio": round(max_lag_ratio, 4),
        },
        "runs": [rounded(row) for row in rows],
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    result = asyncio.run(main_async())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
