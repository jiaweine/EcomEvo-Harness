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
EXPERIMENTS = 3
TRANSACTION_RATIO_LIMIT = 0.10
WALL_RATIO_LIMIT = 0.90


class TracingStore(BundledEventStore):
    def __init__(self, path: Path):
        self.immediate_begins = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._trace_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection


class LegacyAsyncBootstrapStore(TracingStore):
    async def create_session_events_checkpoint_async(
        self,
        session_id: str,
        events: list[tuple[str, dict[str, Any]]],
        snapshot: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
    ):
        return await self._run_io(
            self.create_session_events_checkpoint,
            session_id,
            events,
            snapshot,
            meta=meta,
            parent_session_id=parent_session_id,
            parent_seq=parent_seq,
        )


def payloads(index: int) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    events = [
        ("goal.parsed", {"goal": f"merchant-review-{index}"}),
        ("belief.updated", {"confidence": 0.2, "facts": {"index": index}}),
        ("harness.profile.bound", {"component_ids": ["prompt", "tool", "memory"]}),
    ]
    snapshot = {
        "stage": "initial",
        "goal": {"primary": f"merchant-review-{index}"},
        "belief": {"confidence": 0.2, "facts": {"index": index}},
    }
    meta = {"domain": "merchant_review", "goal": f"merchant-review-{index}"}
    return events, snapshot, meta


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return float(ordered[index])


async def measure(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    store_type = TracingStore if mode == "grouped" else LegacyAsyncBootstrapStore
    store = store_type(root / f"bootstrap-{mode}-{experiment}.db")
    failures: list[str] = []
    latencies_ms: list[float] = []

    async def one(index: int) -> None:
        events, snapshot, meta = payloads(index)
        session_id = f"{mode}-{experiment}-{index}"
        started = time.perf_counter()
        persisted, reference = await store.create_session_events_checkpoint_async(
            session_id,
            events,
            snapshot,
            meta=meta,
        )
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        if [event.seq for event in persisted] != [1, 2, 3]:
            failures.append(f"{session_id}: invalid bootstrap event sequence")
        if int(reference.get("seq", -1)) != 3:
            failures.append(f"{session_id}: checkpoint not bound to bootstrap tail")
        if reference.get("event_hash") != persisted[-1].hash:
            failures.append(f"{session_id}: checkpoint event hash mismatch")

    started = time.perf_counter()
    await asyncio.gather(*(one(index) for index in range(SESSIONS)))
    wall = time.perf_counter() - started

    for index in range(SESSIONS):
        session_id = f"{mode}-{experiment}-{index}"
        if not store.verify_chain(session_id):
            failures.append(f"{session_id}: invalid chain")
            break
        restored = store.restore_checkpoint(session_id)
        if not restored or restored.get("_checkpoint", {}).get("seq") != 3:
            failures.append(f"{session_id}: invalid restored checkpoint")
            break

    return {
        "mode": mode,
        "sessions": SESSIONS,
        "writer_transactions": store.immediate_begins,
        "transactions_per_session": round(store.immediate_begins / SESSIONS, 4),
        "wall_seconds": round(wall, 4),
        "throughput_sessions_per_second": round(SESSIONS / wall, 1) if wall else 0.0,
        "completion_ms": {
            "p50": round(percentile(latencies_ms, 0.50), 3),
            "p95": round(percentile(latencies_ms, 0.95), 3),
            "p99": round(percentile(latencies_ms, 0.99), 3),
        },
        "failures": failures[:20],
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    results: dict[str, list[dict[str, Any]]] = {"legacy_async": [], "grouped": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-bootstrap-group-gate-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = (
                ("legacy_async", "grouped")
                if experiment % 2 == 0
                else ("grouped", "legacy_async")
            )
            for mode in order:
                row = await measure(root, mode, experiment)
                results[mode].append(row)
                failures.extend(f"{mode}[{experiment}]: {item}" for item in row["failures"])

    legacy_tx = statistics.median(row["writer_transactions"] for row in results["legacy_async"])
    grouped_tx = statistics.median(row["writer_transactions"] for row in results["grouped"])
    legacy_wall = statistics.median(row["wall_seconds"] for row in results["legacy_async"])
    grouped_wall = statistics.median(row["wall_seconds"] for row in results["grouped"])
    legacy_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["legacy_async"])
    grouped_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["grouped"])

    tx_ratio = grouped_tx / max(1, legacy_tx)
    wall_ratio = grouped_wall / max(0.0001, legacy_wall)
    p99_ratio = grouped_p99 / max(0.001, legacy_p99)

    if tx_ratio > TRANSACTION_RATIO_LIMIT:
        failures.append(
            f"bootstrap group writer reduction too small: ratio={tx_ratio:.4f} > {TRANSACTION_RATIO_LIMIT:.2f}"
        )
    if wall_ratio > WALL_RATIO_LIMIT:
        failures.append(
            f"bootstrap group wall reduction too small: ratio={wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
        )

    return {
        "ok": not failures,
        "sessions": SESSIONS,
        "experiments": EXPERIMENTS,
        "results": results,
        "comparison": {
            "legacy_median_writer_transactions": legacy_tx,
            "grouped_median_writer_transactions": grouped_tx,
            "grouped_to_legacy_transaction_ratio": round(tx_ratio, 4),
            "legacy_median_wall_seconds": round(legacy_wall, 4),
            "grouped_median_wall_seconds": round(grouped_wall, 4),
            "grouped_to_legacy_wall_ratio": round(wall_ratio, 4),
            "legacy_median_p99_ms": round(legacy_p99, 3),
            "grouped_median_p99_ms": round(grouped_p99, 3),
            "grouped_to_legacy_p99_ratio": round(p99_ratio, 4),
        },
        "limits": {
            "transaction_ratio": TRANSACTION_RATIO_LIMIT,
            "wall_ratio": WALL_RATIO_LIMIT,
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
