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


class CountingStore(BundledEventStore):
    def __init__(self, path: Path):
        self._count_lock = threading.Lock()
        self.immediate_begins = 0
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str):
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._count_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_count(self) -> None:
        with self._count_lock:
            self.immediate_begins = 0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


async def run_mode(
    root: Path,
    mode: str,
    experiment: int,
    sessions: int,
    event_rounds: int,
) -> dict[str, Any]:
    store = CountingStore(root / f"group-{mode}-{experiment}.db")
    session_ids = [f"s-{index}" for index in range(sessions)]
    for sid in session_ids:
        store.create_session(sid)
    store.reset_count()

    completion_ms: list[float] = []
    failures: list[str] = []
    started = time.perf_counter()

    for event_round in range(event_rounds):
        release = asyncio.Event()
        round_started = 0.0

        async def one(index: int) -> None:
            nonlocal round_started
            await release.wait()
            sid = session_ids[index]
            try:
                if mode == "grouped":
                    await store.append_grouped(
                        sid,
                        "pressure.event",
                        {"round": event_round, "index": index},
                    )
                else:
                    store.append(
                        sid,
                        "pressure.event",
                        {"round": event_round, "index": index},
                    )
                completion_ms.append((time.perf_counter() - round_started) * 1000.0)
            except Exception as exc:
                failures.append(f"{sid}/round-{event_round}: {exc!r}")

        tasks = [asyncio.create_task(one(index)) for index in range(sessions)]
        await asyncio.sleep(0)
        round_started = time.perf_counter()
        release.set()
        await asyncio.gather(*tasks)

    wall = time.perf_counter() - started
    writer_transactions = store.immediate_begins
    expected_legacy = sessions * event_rounds
    if mode == "legacy" and writer_transactions != expected_legacy:
        failures.append(
            f"legacy transaction count changed: {writer_transactions} != {expected_legacy}"
        )
    if mode == "grouped" and writer_transactions > event_rounds * 2:
        failures.append(
            f"grouped transaction count too high: {writer_transactions} > {event_rounds * 2}"
        )

    for sid in session_ids:
        events = store.list_events(sid)
        if len(events) != event_rounds or [event.seq for event in events] != list(
            range(1, event_rounds + 1)
        ):
            failures.append(f"invalid event sequence: {sid}")
            break
        if not store.verify_chain(sid):
            failures.append(f"invalid hash chain: {sid}")
            break

    return {
        "mode": mode,
        "sessions": sessions,
        "event_rounds": event_rounds,
        "events": sessions * event_rounds,
        "writer_transactions": writer_transactions,
        "transactions_per_event": round(writer_transactions / max(1, sessions * event_rounds), 4),
        "wall_seconds": round(wall, 4),
        "throughput_events_per_second": round((sessions * event_rounds) / wall, 1) if wall else 0.0,
        "completion_ms": {
            "p50": round(percentile(completion_ms, 0.50), 3),
            "p95": round(percentile(completion_ms, 0.95), 3),
            "p99": round(percentile(completion_ms, 0.99), 3),
        },
        "failures": failures[:20],
    }


async def main_async() -> dict[str, Any]:
    sessions = 64
    event_rounds = 4
    experiments = 3
    results: dict[str, list[dict[str, Any]]] = {"legacy": [], "grouped": []}
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ecomevo-group-commit-ab-") as tmp:
        root = Path(tmp)
        for experiment in range(experiments):
            order = ("legacy", "grouped") if experiment % 2 == 0 else ("grouped", "legacy")
            for mode in order:
                row = await run_mode(root, mode, experiment, sessions, event_rounds)
                results[mode].append(row)
                failures.extend(f"{mode}[{experiment}]: {item}" for item in row["failures"])

    legacy_wall = statistics.median(row["wall_seconds"] for row in results["legacy"])
    grouped_wall = statistics.median(row["wall_seconds"] for row in results["grouped"])
    legacy_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["legacy"])
    grouped_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["grouped"])
    legacy_tx = statistics.median(row["writer_transactions"] for row in results["legacy"])
    grouped_tx = statistics.median(row["writer_transactions"] for row in results["grouped"])

    wall_ratio = grouped_wall / legacy_wall if legacy_wall else 1.0
    p99_ratio = grouped_p99 / legacy_p99 if legacy_p99 else 1.0
    tx_ratio = grouped_tx / legacy_tx if legacy_tx else 1.0

    if tx_ratio > 0.10:
        failures.append(f"group commit did not cut writer transactions by 90%: ratio={tx_ratio:.3f}")
    if wall_ratio > 0.80:
        failures.append(f"group commit did not reduce same-run wall by 20%: ratio={wall_ratio:.3f}")
    if p99_ratio > 0.90:
        failures.append(f"group commit did not reduce synchronized p99 by 10%: ratio={p99_ratio:.3f}")

    return {
        "ok": not failures,
        "sessions": sessions,
        "event_rounds": event_rounds,
        "experiments": experiments,
        "results": results,
        "comparison": {
            "legacy_median_writer_transactions": legacy_tx,
            "grouped_median_writer_transactions": grouped_tx,
            "grouped_to_legacy_transaction_ratio": round(tx_ratio, 4),
            "legacy_median_wall_seconds": round(legacy_wall, 4),
            "grouped_median_wall_seconds": round(grouped_wall, 4),
            "grouped_to_legacy_wall_ratio": round(wall_ratio, 4),
            "legacy_median_completion_p99_ms": round(legacy_p99, 3),
            "grouped_median_completion_p99_ms": round(grouped_p99, 3),
            "grouped_to_legacy_p99_ratio": round(p99_ratio, 4),
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
