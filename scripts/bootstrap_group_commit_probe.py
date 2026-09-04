from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecomevo.models import RuntimeEvent
from ecomevo.runtime.bundled_event_store import BundledEventStore


SESSIONS = 64
GROUP_LIMIT = 64
EXPERIMENTS = 3
TRANSACTION_RATIO_LIMIT = 0.10
WALL_RATIO_LIMIT = 0.90


@dataclass(slots=True)
class _BootstrapRequest:
    session_id: str
    events: list[tuple[str, dict[str, Any]]]
    snapshot: dict[str, Any]
    meta: dict[str, Any] | None
    parent_session_id: str | None
    parent_seq: int | None
    future: asyncio.Future[tuple[list[RuntimeEvent], dict[str, Any]]]


@dataclass(slots=True)
class _BootstrapGroup:
    queue: list[_BootstrapRequest] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class TracingBundledStore(BundledEventStore):
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


class GroupedBootstrapProbeStore(TracingBundledStore):
    def __init__(self, path: Path):
        self._bootstrap_lock = threading.RLock()
        self._bootstrap_group: _BootstrapGroup | None = None
        super().__init__(path)

    async def create_session_events_checkpoint_async(
        self,
        session_id: str,
        events: list[tuple[str, dict[str, Any]]],
        snapshot: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
    ) -> tuple[list[RuntimeEvent], dict[str, Any]]:
        if not events:
            raise ValueError("bootstrap bundle requires at least one event")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[list[RuntimeEvent], dict[str, Any]]] = loop.create_future()
        request = _BootstrapRequest(
            session_id=str(session_id),
            events=list(events),
            snapshot=snapshot,
            meta=dict(meta) if meta is not None else None,
            parent_session_id=parent_session_id,
            parent_seq=parent_seq,
            future=future,
        )
        with self._bootstrap_lock:
            group = self._bootstrap_group
            if group is None:
                group = _BootstrapGroup()
                self._bootstrap_group = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_bootstrap_group(group))

        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            try:
                await asyncio.shield(future)
            except Exception:
                raise
            raise cancelled

    async def _flush_bootstrap_group(self, group: _BootstrapGroup) -> None:
        while True:
            await asyncio.sleep(0)
            with self._bootstrap_lock:
                if not group.queue:
                    group.scheduled = False
                    group.worker = None
                    return
                batch = list(group.queue[:GROUP_LIMIT])
                del group.queue[: len(batch)]

            try:
                persisted = await self._run_io(self._persist_bootstrap_group, batch)
            except Exception:
                for request in batch:
                    try:
                        result = await self._run_io(
                            self.create_session_events_checkpoint,
                            request.session_id,
                            request.events,
                            request.snapshot,
                            meta=request.meta,
                            parent_session_id=request.parent_session_id,
                            parent_seq=request.parent_seq,
                        )
                    except Exception as exc:
                        if not request.future.done():
                            request.future.set_exception(exc)
                    else:
                        if not request.future.done():
                            request.future.set_result(result)
                continue

            for request, result in zip(batch, persisted):
                if not request.future.done():
                    request.future.set_result(result)

    def _persist_bootstrap_group(
        self, batch: list[_BootstrapRequest]
    ) -> list[tuple[list[RuntimeEvent], dict[str, Any]]]:
        persisted_groups: list[tuple[list[RuntimeEvent], dict[str, Any]]] = []
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for request in batch:
                connection.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?,?)",
                    (
                        request.session_id,
                        request.parent_session_id,
                        request.parent_seq,
                        time.time(),
                        json.dumps(request.meta or {}, ensure_ascii=False, default=str),
                    ),
                )
                tail: dict[str, Any] = {"seq": None, "hash": None}
                events: list[RuntimeEvent] = []
                for event_type, payload in request.events:
                    event = self._append_in_transaction(
                        connection,
                        request.session_id,
                        str(event_type),
                        payload,
                        tail=tail,
                    )
                    events.append(event)
                    tail = {"seq": event.seq, "hash": event.hash}
                reference = self._save_checkpoint_in_transaction(
                    connection,
                    request.session_id,
                    request.snapshot,
                    tail=tail,
                )
                persisted_groups.append((events, reference))
        return persisted_groups


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
    store_type = GroupedBootstrapProbeStore if mode == "grouped" else TracingBundledStore
    store = store_type(root / f"{mode}-{experiment}.db")
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


async def isolation_probe(root: Path) -> dict[str, Any]:
    store = GroupedBootstrapProbeStore(root / "isolation.db")
    events, snapshot, meta = payloads(999)
    store.create_session_events_checkpoint("duplicate", events, snapshot, meta=meta)
    before = store.immediate_begins

    async def create(session_id: str):
        return await store.create_session_events_checkpoint_async(
            session_id,
            events,
            snapshot,
            meta=meta,
        )

    results = await asyncio.gather(
        create("good-a"),
        create("duplicate"),
        create("good-b"),
        return_exceptions=True,
    )
    failures: list[str] = []
    if isinstance(results[0], Exception) or isinstance(results[2], Exception):
        failures.append("valid peers failed after shared bootstrap rollback")
    if not isinstance(results[1], Exception):
        failures.append("duplicate bootstrap unexpectedly succeeded")
    for session_id in ("good-a", "good-b"):
        if not store.verify_chain(session_id):
            failures.append(f"{session_id}: invalid isolated chain")
        restored = store.restore_checkpoint(session_id)
        if not restored or restored.get("_checkpoint", {}).get("seq") != 3:
            failures.append(f"{session_id}: invalid isolated checkpoint")

    return {
        "writer_attempts_after_seed": store.immediate_begins - before,
        "good_a_succeeded": not isinstance(results[0], Exception),
        "duplicate_failed": isinstance(results[1], Exception),
        "good_b_succeeded": not isinstance(results[2], Exception),
        "failures": failures,
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    results: dict[str, list[dict[str, Any]]] = {"baseline": [], "grouped": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-bootstrap-group-probe-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = ("baseline", "grouped") if experiment % 2 == 0 else ("grouped", "baseline")
            for mode in order:
                row = await measure(root, mode, experiment)
                results[mode].append(row)
                failures.extend(f"{mode}[{experiment}]: {item}" for item in row["failures"])
        isolation = await isolation_probe(root)
        failures.extend(f"isolation: {item}" for item in isolation["failures"])

    baseline_tx = statistics.median(row["writer_transactions"] for row in results["baseline"])
    grouped_tx = statistics.median(row["writer_transactions"] for row in results["grouped"])
    baseline_wall = statistics.median(row["wall_seconds"] for row in results["baseline"])
    grouped_wall = statistics.median(row["wall_seconds"] for row in results["grouped"])
    baseline_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["baseline"])
    grouped_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["grouped"])
    tx_ratio = grouped_tx / max(1, baseline_tx)
    wall_ratio = grouped_wall / max(0.0001, baseline_wall)
    p99_ratio = grouped_p99 / max(0.001, baseline_p99)

    if tx_ratio > TRANSACTION_RATIO_LIMIT:
        failures.append(
            f"grouped bootstrap transaction ratio too high: {tx_ratio:.4f} > {TRANSACTION_RATIO_LIMIT:.2f}"
        )
    if wall_ratio > WALL_RATIO_LIMIT:
        failures.append(
            f"grouped bootstrap wall ratio too high: {wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
        )

    return {
        "ok": not failures,
        "sessions": SESSIONS,
        "group_limit": GROUP_LIMIT,
        "experiments": EXPERIMENTS,
        "results": results,
        "comparison": {
            "baseline_median_writer_transactions": baseline_tx,
            "grouped_median_writer_transactions": grouped_tx,
            "grouped_to_baseline_transaction_ratio": round(tx_ratio, 4),
            "baseline_median_wall_seconds": round(baseline_wall, 4),
            "grouped_median_wall_seconds": round(grouped_wall, 4),
            "grouped_to_baseline_wall_ratio": round(wall_ratio, 4),
            "baseline_median_p99_ms": round(baseline_p99, 3),
            "grouped_median_p99_ms": round(grouped_p99, 3),
            "grouped_to_baseline_p99_ratio": round(p99_ratio, 4),
        },
        "isolation": isolation,
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
