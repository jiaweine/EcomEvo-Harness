from __future__ import annotations

import json
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.event_store import EventStore


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class _TxnCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.value = 0

    def increment(self) -> None:
        with self._lock:
            self.value += 1

    def reset(self) -> None:
        with self._lock:
            self.value = 0


class _CountingEventStore(EventStore):
    def __init__(self, path: Path, counter: _TxnCounter):
        self._pressure_counter = counter
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                self._pressure_counter.increment()

        connection.set_trace_callback(trace)
        return connection


class _CountingBundledEventStore(BundledEventStore):
    def __init__(self, path: Path, counter: _TxnCounter):
        self._pressure_counter = counter
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                self._pressure_counter.increment()

        connection.set_trace_callback(trace)
        return connection


def _payloads(index: int) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any], dict[str, Any]]:
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


def _run_mode(root: Path, mode: str, run_index: int, sessions: int, workers: int) -> dict[str, Any]:
    db = root / f"bootstrap-{mode}-{run_index}.db"
    counter = _TxnCounter()
    store_type = _CountingBundledEventStore if mode == "bundle" else _CountingEventStore
    stores = [store_type(db, counter) for _ in range(min(8, workers))]
    counter.reset()
    latencies_ms: list[float] = []
    failures: list[str] = []

    def one(index: int) -> None:
        store = stores[index % len(stores)]
        sid = f"{mode}-{run_index}-{index}"
        events, snapshot, meta = _payloads(index)
        started = time.perf_counter()
        if mode == "bundle":
            store.create_session_events_checkpoint(sid, events, snapshot, meta=meta)
        else:
            first_type, first_payload = events[0]
            store.create_session_and_append(sid, first_type, first_payload, meta=meta)
            for event_type, payload in events[1:]:
                store.append(sid, event_type, payload)
            store.save_checkpoint(sid, snapshot)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, index) for index in range(sessions)]
        for future in futures:
            try:
                future.result(timeout=30)
            except Exception as exc:
                failures.append(f"bootstrap worker failed: {exc!r}")
    wall = time.perf_counter() - started

    expected_begins = sessions if mode == "bundle" else sessions * 4
    if counter.value != expected_begins:
        failures.append(
            f"writer transaction count changed: {counter.value} != {expected_begins}"
        )

    verifier = stores[0]
    for index in range(sessions):
        sid = f"{mode}-{run_index}-{index}"
        if not verifier.verify_chain(sid):
            failures.append(f"invalid chain: {sid}")
            break
        restored = verifier.restore_checkpoint(sid)
        if not restored or restored.get("_checkpoint", {}).get("seq") != 3:
            failures.append(f"invalid bootstrap checkpoint: {sid}")
            break

    return {
        "mode": mode,
        "sessions": sessions,
        "workers": workers,
        "writer_transactions": counter.value,
        "wall_seconds": round(wall, 4),
        "throughput_sessions_per_second": round(sessions / wall, 1) if wall else 0.0,
        "latency_ms": {
            "p50": round(percentile(latencies_ms, 0.50), 3),
            "p95": round(percentile(latencies_ms, 0.95), 3),
            "p99": round(percentile(latencies_ms, 0.99), 3),
        },
        "failures": failures[:20],
    }


def main() -> int:
    sessions = 128
    workers = 16
    rounds = 3
    results: dict[str, list[dict[str, Any]]] = {"legacy": [], "bundle": []}
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ecomevo-bootstrap-ab-") as tmp:
        root = Path(tmp)
        for run_index in range(rounds):
            order = ("legacy", "bundle") if run_index % 2 == 0 else ("bundle", "legacy")
            for mode in order:
                row = _run_mode(root, mode, run_index, sessions, workers)
                results[mode].append(row)
                failures.extend(f"{mode}[{run_index}]: {item}" for item in row["failures"])

    legacy_wall = statistics.median(row["wall_seconds"] for row in results["legacy"])
    bundle_wall = statistics.median(row["wall_seconds"] for row in results["bundle"])
    legacy_p99 = statistics.median(row["latency_ms"]["p99"] for row in results["legacy"])
    bundle_p99 = statistics.median(row["latency_ms"]["p99"] for row in results["bundle"])
    wall_ratio = bundle_wall / legacy_wall if legacy_wall else 1.0
    p99_ratio = bundle_p99 / legacy_p99 if legacy_p99 else 1.0

    if wall_ratio > 0.90:
        failures.append(
            f"bundle did not reduce same-run bootstrap wall time by at least 10%: ratio={wall_ratio:.3f}"
        )
    if p99_ratio > 1.15:
        failures.append(
            f"bundle increased same-run bootstrap p99 by more than 15%: ratio={p99_ratio:.3f}"
        )

    report = {
        "ok": not failures,
        "sessions_per_round": sessions,
        "workers": workers,
        "rounds": rounds,
        "results": results,
        "comparison": {
            "legacy_median_wall_seconds": round(legacy_wall, 4),
            "bundle_median_wall_seconds": round(bundle_wall, 4),
            "bundle_to_legacy_wall_ratio": round(wall_ratio, 4),
            "legacy_median_p99_ms": round(legacy_p99, 3),
            "bundle_median_p99_ms": round(bundle_p99, 3),
            "bundle_to_legacy_p99_ratio": round(p99_ratio, 4),
            "writer_transactions_per_session": {"legacy": 4, "bundle": 1},
        },
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
