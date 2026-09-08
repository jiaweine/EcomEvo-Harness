from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_event_store import BundledEventStore


SESSIONS = 64
RESTORES_PER_SESSION = 8
HEARTBEAT_INTERVAL = 0.001
LAG_RATIO_TARGET = 0.35
WALL_RATIO_LIMIT = 1.50


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class TracingStore(BundledEventStore):
    def __init__(self, path: Path):
        self.restore_threads: list[int] = []
        self._restore_trace_lock = threading.Lock()
        super().__init__(path)

    def restore_checkpoint(self, session_id: str, seq: int | None = None):
        with self._restore_trace_lock:
            self.restore_threads.append(threading.get_ident())
        return super().restore_checkpoint(session_id, seq)


def seed_store(path: Path) -> TracingStore:
    store = TracingStore(path)
    for index in range(SESSIONS):
        sid = f"session-{index}"
        store.create_session_and_append(sid, "seed.one", {"index": index, "seq": 1})
        store.save_checkpoint(sid, {"index": index, "stage": "one"}, seq=1)
        store.append(sid, "seed.two", {"index": index, "seq": 2})
        store.save_checkpoint(sid, {"index": index, "stage": "two"}, seq=2)
        store.append(sid, "seed.three", {"index": index, "seq": 3})
        store.save_checkpoint(sid, {"index": index, "stage": "three"}, seq=3)
    store.restore_threads.clear()
    return store


def restore_projection(value: dict[str, Any] | None) -> Any:
    if value is None:
        return None
    checkpoint = value.get("_checkpoint") or {}
    return {
        "index": value.get("index"),
        "stage": value.get("stage"),
        "seq": checkpoint.get("seq"),
        "state_hash": checkpoint.get("state_hash"),
        "event_hash": checkpoint.get("event_hash"),
    }


def calls() -> list[tuple[str, int | None]]:
    result: list[tuple[str, int | None]] = []
    for repeat in range(RESTORES_PER_SESSION):
        seq: int | None = None if repeat % 2 == 0 else 2
        for index in range(SESSIONS):
            result.append((f"session-{index}", seq))
    return result


async def semantic_cases(root: Path) -> list[str]:
    failures: list[str] = []
    store = seed_store(root / "semantics.db")
    loop_thread = threading.get_ident()

    expected_latest = restore_projection(store.restore_checkpoint("session-0"))
    expected_bounded = restore_projection(store.restore_checkpoint("session-0", 2))
    expected_missing = store.restore_checkpoint("missing")
    store.restore_threads.clear()

    async def invoke(mode: str, sid: str, seq: int | None):
        if mode == "gated":
            return await store._run_io(store.restore_checkpoint, sid, seq)
        return await asyncio.to_thread(store.restore_checkpoint, sid, seq)

    for mode in ("gated", "parallel"):
        latest = restore_projection(await invoke(mode, "session-0", None))
        bounded = restore_projection(await invoke(mode, "session-0", 2))
        missing = await invoke(mode, "missing", None)
        if latest != expected_latest:
            failures.append(f"{mode}: latest restore changed")
        if bounded != expected_bounded:
            failures.append(f"{mode}: bounded restore changed")
        if missing != expected_missing:
            failures.append(f"{mode}: missing restore changed")

    # Tampered snapshot state hash must remain unusable in every shape.
    with store._conn() as connection:
        connection.execute(
            "UPDATE snapshots SET state_hash='tampered' WHERE session_id=? AND seq=?",
            ("session-1", 3),
        )
    if store.restore_checkpoint("session-1") is not None:
        failures.append("sync: tampered state hash restored")
    for mode in ("gated", "parallel"):
        if await invoke(mode, "session-1", None) is not None:
            failures.append(f"{mode}: tampered state hash restored")

    # Tampered bound event hash must also remain unusable.
    with store._conn() as connection:
        connection.execute(
            "UPDATE snapshots SET event_hash='tampered' WHERE session_id=? AND seq=?",
            ("session-2", 3),
        )
    if store.restore_checkpoint("session-2") is not None:
        failures.append("sync: tampered event hash restored")
    for mode in ("gated", "parallel"):
        if await invoke(mode, "session-2", None) is not None:
            failures.append(f"{mode}: tampered event hash restored")

    worker_threads = [thread_id for thread_id in store.restore_threads if thread_id != loop_thread]
    if not worker_threads:
        failures.append("offload modes never left the event-loop thread")
    return failures


async def measure(mode: str, path: Path) -> dict[str, Any]:
    store = seed_store(path)
    loop_thread = threading.get_ident()
    workload = calls()
    expected = [restore_projection(store.restore_checkpoint(sid, seq)) for sid, seq in workload]
    store.restore_threads.clear()

    lags_ms: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        target = time.perf_counter() + HEARTBEAT_INTERVAL
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            now = time.perf_counter()
            lags_ms.append(max(0.0, now - target) * 1000.0)
            target = now + HEARTBEAT_INTERVAL

    async def one(sid: str, seq: int | None):
        if mode == "sync":
            return store.restore_checkpoint(sid, seq)
        if mode == "gated":
            return await store._run_io(store.restore_checkpoint, sid, seq)
        return await asyncio.to_thread(store.restore_checkpoint, sid, seq)

    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(HEARTBEAT_INTERVAL * 3)
    started = time.perf_counter()
    try:
        values = await asyncio.gather(*(one(sid, seq) for sid, seq in workload))
    finally:
        wall = time.perf_counter() - started
        stop.set()
        await heartbeat_task

    actual = [restore_projection(value) for value in values]
    failures: list[str] = []
    if actual != expected:
        failures.append("restore projections differ from synchronous baseline")

    unique_threads = set(store.restore_threads)
    off_loop_threads = {thread_id for thread_id in unique_threads if thread_id != loop_thread}
    if mode == "sync" and off_loop_threads:
        failures.append("sync restore unexpectedly left event-loop thread")
    if mode != "sync" and not off_loop_threads:
        failures.append(f"{mode} restore did not leave event-loop thread")

    return {
        "mode": mode,
        "calls": len(workload),
        "wall_seconds": round(wall, 6),
        "throughput_per_second": round(len(workload) / wall, 2) if wall else 0.0,
        "threads": len(unique_threads),
        "off_loop_threads": len(off_loop_threads),
        "heartbeat_ms": {
            "samples": len(lags_ms),
            "p50": round(percentile(lags_ms, 0.50), 4),
            "p95": round(percentile(lags_ms, 0.95), 4),
            "p99": round(percentile(lags_ms, 0.99), 4),
            "max": round(max(lags_ms), 4) if lags_ms else 0.0,
            "mean": round(statistics.fmean(lags_ms), 4) if lags_ms else 0.0,
        },
        "failures": failures,
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-restore-offload-") as tmp:
        root = Path(tmp)
        failures.extend(await semantic_cases(root))
        sync = await measure("sync", root / "sync.db")
        gated = await measure("gated", root / "gated.db")
        parallel = await measure("parallel", root / "parallel.db")

    for result in (sync, gated, parallel):
        failures.extend(result["failures"])

    sync_max = max(0.001, float(sync["heartbeat_ms"]["max"]))
    sync_wall = max(0.000001, float(sync["wall_seconds"]))
    comparisons: dict[str, Any] = {}
    recommended: str | None = None
    for result in (gated, parallel):
        lag_ratio = float(result["heartbeat_ms"]["max"]) / sync_max
        wall_ratio = float(result["wall_seconds"]) / sync_wall
        qualifies = lag_ratio <= LAG_RATIO_TARGET and wall_ratio <= WALL_RATIO_LIMIT
        comparisons[result["mode"]] = {
            "max_lag_ratio": round(lag_ratio, 4),
            "wall_ratio": round(wall_ratio, 4),
            "qualifies": qualifies,
        }
        if qualifies and (
            recommended is None
            or wall_ratio < float(comparisons[recommended]["wall_ratio"])
        ):
            recommended = result["mode"]

    return {
        "ok": not failures,
        "sessions": SESSIONS,
        "restores_per_session": RESTORES_PER_SESSION,
        "sync": sync,
        "gated": gated,
        "parallel": parallel,
        "comparison": comparisons,
        "targets": {
            "max_lag_ratio_lte": LAG_RATIO_TARGET,
            "wall_ratio_lte": WALL_RATIO_LIMIT,
        },
        "recommended": recommended,
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
