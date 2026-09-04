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
CHECKPOINT_ROUNDS = 3
HEARTBEAT_INTERVAL = 0.002
CHECKPOINT_TRANSACTION_RATIO_LIMIT = 0.10


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class TracingStore(BundledEventStore):
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


def bootstrap_events(index: int) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("goal.parsed", {"goal": f"review-{index}"}),
        ("belief.updated", {"confidence": 0.2, "index": index}),
        ("harness.profile.bound", {"component_ids": ["active"]}),
    ]


async def measure(mode: str, root: Path) -> dict[str, Any]:
    store = TracingStore(root / f"{mode}.db")
    lags_ms: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        target = time.perf_counter() + HEARTBEAT_INTERVAL
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            now = time.perf_counter()
            lags_ms.append(max(0.0, now - target) * 1000.0)
            target = now + HEARTBEAT_INTERVAL

    async def one(index: int) -> None:
        sid = f"{mode}-{index}"
        events = bootstrap_events(index)
        if mode == "sync":
            store.create_session_events_checkpoint(
                sid,
                events,
                {"stage": "initial", "index": index},
            )
            for checkpoint in range(CHECKPOINT_ROUNDS):
                store.save_checkpoint_and_append(
                    sid,
                    {"stage": f"checkpoint-{checkpoint}", "index": index},
                    "runtime.checkpointed",
                    {"stage": f"checkpoint-{checkpoint}"},
                )
            valid = store.verify_chain(sid)
        else:
            await store.create_session_events_checkpoint_async(
                sid,
                events,
                {"stage": "initial", "index": index},
            )
            for checkpoint in range(CHECKPOINT_ROUNDS):
                await store.save_checkpoint_and_append_async(
                    sid,
                    {"stage": f"checkpoint-{checkpoint}", "index": index},
                    "runtime.checkpointed",
                    {"stage": f"checkpoint-{checkpoint}"},
                )
            valid = await store.verify_chain_async(sid)
        if not valid:
            raise AssertionError(f"invalid chain: {sid}")

    heartbeat_task = asyncio.create_task(heartbeat())
    # Let the heartbeat establish a deadline before the intentionally synchronous arm
    # blocks the loop, so the first long stall is measured rather than hidden at startup.
    await asyncio.sleep(HEARTBEAT_INTERVAL * 2)
    started = time.perf_counter()
    try:
        await asyncio.gather(*(one(index) for index in range(SESSIONS)))
    finally:
        wall = time.perf_counter() - started
        stop.set()
        await heartbeat_task

    failures: list[str] = []
    if mode == "sync":
        expected_transactions = SESSIONS * (1 + CHECKPOINT_ROUNDS)
        if store.immediate_begins != expected_transactions:
            failures.append(
                f"writer transactions changed: {store.immediate_begins} != {expected_transactions}"
            )
    expected_events = 3 + CHECKPOINT_ROUNDS
    for index in range(SESSIONS):
        sid = f"{mode}-{index}"
        events = store.list_events(sid)
        if len(events) != expected_events:
            failures.append(f"{sid}: event count {len(events)} != {expected_events}")
            break
        if [event.seq for event in events] != list(range(1, expected_events + 1)):
            failures.append(f"{sid}: non-contiguous event sequence")
            break
        if not store.verify_chain(sid):
            failures.append(f"{sid}: invalid final chain")
            break

    return {
        "mode": mode,
        "sessions": SESSIONS,
        "checkpoint_rounds": CHECKPOINT_ROUNDS,
        "writer_transactions": store.immediate_begins,
        "wall_seconds": round(wall, 4),
        "throughput_sessions_per_second": round(SESSIONS / wall, 3) if wall else 0.0,
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
    with tempfile.TemporaryDirectory(prefix="ecomevo-event-io-offload-") as tmp:
        root = Path(tmp)
        sync = await measure("sync", root)
        async_result = await measure("async", root)

    failures.extend(sync["failures"])
    failures.extend(async_result["failures"])

    bootstrap_transactions = SESSIONS
    sync_checkpoint_transactions = int(sync["writer_transactions"]) - bootstrap_transactions
    async_checkpoint_transactions = int(async_result["writer_transactions"]) - bootstrap_transactions
    if async_checkpoint_transactions < 0:
        failures.append(
            "async writer transactions dropped below the one-transaction-per-bootstrap floor"
        )
    checkpoint_transaction_ratio = async_checkpoint_transactions / max(
        1, sync_checkpoint_transactions
    )
    if checkpoint_transaction_ratio > CHECKPOINT_TRANSACTION_RATIO_LIMIT:
        failures.append(
            "async checkpoint writer reduction too small: "
            f"ratio={checkpoint_transaction_ratio:.4f} > {CHECKPOINT_TRANSACTION_RATIO_LIMIT:.2f}"
        )

    sync_max = float(sync["heartbeat_ms"]["max"])
    async_max = float(async_result["heartbeat_ms"]["max"])
    lag_ratio = async_max / max(0.001, sync_max)
    wall_ratio = float(async_result["wall_seconds"]) / max(0.0001, float(sync["wall_seconds"]))

    # The async arm now combines the original I/O offload with checkpoint group commit.
    # Preserve the original loop-lag and wall guards, while requiring the grouped checkpoint
    # writer amplification to stay within the same <=10% gate predeclared by the checkpoint
    # A/B probe. Bootstrap creation remains one transaction per session in both arms.
    if lag_ratio > 0.40:
        failures.append(f"event-loop max lag reduction too small: ratio={lag_ratio:.4f} > 0.40")
    if wall_ratio > 1.50:
        failures.append(f"offload wall regression too large: ratio={wall_ratio:.4f} > 1.50")

    return {
        "ok": not failures,
        "sync": sync,
        "async": async_result,
        "comparison": {
            "async_to_sync_max_lag_ratio": round(lag_ratio, 4),
            "async_to_sync_wall_ratio": round(wall_ratio, 4),
            "bootstrap_writer_transactions": bootstrap_transactions,
            "sync_checkpoint_writer_transactions": sync_checkpoint_transactions,
            "async_checkpoint_writer_transactions": async_checkpoint_transactions,
            "async_to_sync_checkpoint_transaction_ratio": round(
                checkpoint_transaction_ratio, 4
            ),
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
