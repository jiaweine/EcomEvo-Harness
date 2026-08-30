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


_GROUP_LIMIT = 64


@dataclass(slots=True)
class _CheckpointRequest:
    session_id: str
    snapshot: dict[str, Any]
    event_type: str
    event_payload: dict[str, Any]
    seq: int | None
    future: asyncio.Future[tuple[dict[str, Any], RuntimeEvent]]


@dataclass(slots=True)
class _CheckpointGroup:
    queue: list[_CheckpointRequest] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class ProbeStore(BundledEventStore):
    """Diagnostic-only checkpoint group commit prototype.

    Production code is intentionally untouched. This class exists only to establish
    whether cross-session checkpoint+audit coalescing is worth productionizing while
    preserving the existing pre-audit checkpoint binding contract.
    """

    def __init__(self, path: Path):
        self._count_lock = threading.Lock()
        self.immediate_begins = 0
        self._checkpoint_group_lock = threading.RLock()
        self._checkpoint_groups: dict[asyncio.AbstractEventLoop, _CheckpointGroup] = {}
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._count_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_count(self) -> None:
        with self._count_lock:
            self.immediate_begins = 0

    async def checkpoint_grouped(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        event_type: str,
        event_payload: dict[str, Any] | None = None,
        *,
        seq: int | None = None,
    ) -> tuple[dict[str, Any], RuntimeEvent]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[dict[str, Any], RuntimeEvent]] = loop.create_future()
        request = _CheckpointRequest(
            session_id=str(session_id),
            snapshot=snapshot,
            event_type=str(event_type),
            event_payload=dict(event_payload or {}),
            seq=seq,
            future=future,
        )
        with self._checkpoint_group_lock:
            group = self._checkpoint_groups.get(loop)
            if group is None:
                group = _CheckpointGroup()
                self._checkpoint_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_checkpoint_group(group))
        return await asyncio.shield(future)

    async def _flush_checkpoint_group(self, group: _CheckpointGroup) -> None:
        while True:
            await asyncio.sleep(0)
            with self._checkpoint_group_lock:
                if not group.queue:
                    group.scheduled = False
                    group.worker = None
                    return
                batch = list(group.queue[:_GROUP_LIMIT])
                del group.queue[: len(batch)]

            try:
                persisted = await self._run_io(self._persist_checkpoint_group, batch)
            except Exception:
                # Shared transaction has rolled back. Isolate one malformed session or
                # snapshot without failing unrelated requests.
                for request in batch:
                    if request.future.done():
                        continue
                    try:
                        result = await self.save_checkpoint_and_append_async(
                            request.session_id,
                            request.snapshot,
                            request.event_type,
                            request.event_payload,
                            seq=request.seq,
                        )
                    except Exception as exc:
                        request.future.set_exception(exc)
                    else:
                        request.future.set_result(result)
                continue

            for request, result in zip(batch, persisted):
                if not request.future.done():
                    request.future.set_result(result)

    def _persist_checkpoint_group(
        self,
        batch: list[_CheckpointRequest],
    ) -> list[tuple[dict[str, Any], RuntimeEvent]]:
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            tails: dict[str, Any] = {}
            persisted: list[tuple[dict[str, Any], RuntimeEvent]] = []
            for request in batch:
                tail = tails.get(request.session_id)
                if tail is None:
                    tail = self._session_tail(connection, request.session_id)
                    if tail is None:
                        raise KeyError(f"unknown session: {request.session_id}")
                reference = self._save_checkpoint_in_transaction(
                    connection,
                    request.session_id,
                    request.snapshot,
                    seq=request.seq,
                    tail=tail,
                )
                payload = {**request.event_payload, **reference}
                event = self._append_in_transaction(
                    connection,
                    request.session_id,
                    request.event_type,
                    payload,
                    tail=tail,
                )
                persisted.append((reference, event))
                tails[request.session_id] = {"seq": event.seq, "hash": event.hash}
            return persisted


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


async def semantic_probe(root: Path) -> list[str]:
    failures: list[str] = []
    store = ProbeStore(root / "semantics.db")
    store.create_session("same")
    seed = store.append("same", "seed", {"value": 1})
    if seed.seq != 1:
        failures.append("seed sequence is not 1")

    same_results = await asyncio.gather(
        *[
            store.checkpoint_grouped(
                "same",
                {"round": index, "state": f"s-{index}"},
                "runtime.checkpointed",
                {"stage": f"same-{index}"},
            )
            for index in range(4)
        ]
    )
    expected_refs = [1, 2, 3, 4]
    actual_refs = [int(reference["seq"]) for reference, _ in same_results]
    if actual_refs != expected_refs:
        failures.append(f"same-session checkpoint refs changed: {actual_refs}")
    events = store.list_events("same")
    if [event.seq for event in events] != [1, 2, 3, 4, 5]:
        failures.append("same-session event sequence invalid")
    for reference, event in same_results:
        ref_seq = int(reference["seq"])
        expected_hash = events[ref_seq - 1].hash if ref_seq else "GENESIS"
        if reference["event_hash"] != expected_hash:
            failures.append(f"checkpoint event_hash mismatch at seq {ref_seq}")
            break
        if int(event.payload.get("seq", -1)) != ref_seq:
            failures.append(f"audit payload reference mismatch at seq {ref_seq}")
            break
    restored = store.restore_checkpoint("same")
    if not restored or int(restored.get("round", -1)) != 3:
        failures.append("latest same-session checkpoint did not restore")
    if not store.verify_chain("same"):
        failures.append("same-session hash chain invalid")

    for sid in ("good-a", "good-b"):
        store.create_session(sid)
        store.append(sid, "seed", {"sid": sid})
    isolated = await asyncio.gather(
        store.checkpoint_grouped("good-a", {"ok": "a"}, "runtime.checkpointed", {"stage": "a"}),
        store.checkpoint_grouped("missing", {"bad": True}, "runtime.checkpointed", {"stage": "bad"}),
        store.checkpoint_grouped("good-b", {"ok": "b"}, "runtime.checkpointed", {"stage": "b"}),
        return_exceptions=True,
    )
    if isinstance(isolated[0], Exception) or isinstance(isolated[2], Exception):
        failures.append("valid peers failed during batch isolation")
    if not isinstance(isolated[1], KeyError):
        failures.append(f"missing session did not isolate as KeyError: {isolated[1]!r}")
    for sid in ("good-a", "good-b"):
        if len(store.list_events(sid)) != 2 or not store.verify_chain(sid):
            failures.append(f"isolated valid chain invalid: {sid}")
    return failures


async def run_mode(
    root: Path,
    mode: str,
    experiment: int,
    sessions: int,
    checkpoint_rounds: int,
) -> dict[str, Any]:
    store = ProbeStore(root / f"checkpoint-{mode}-{experiment}.db")
    session_ids = [f"s-{index}" for index in range(sessions)]
    for sid in session_ids:
        store.create_session(sid)
        store.append(sid, "seed", {"sid": sid})
    store.reset_count()

    completion_ms: list[float] = []
    failures: list[str] = []
    started = time.perf_counter()
    for checkpoint_round in range(checkpoint_rounds):
        release = asyncio.Event()
        round_started = 0.0

        async def one(index: int) -> None:
            nonlocal round_started
            await release.wait()
            sid = session_ids[index]
            snapshot = {
                "round": checkpoint_round,
                "index": index,
                "belief": {"facts": index + checkpoint_round, "stage": "recovery"},
            }
            try:
                if mode == "grouped":
                    reference, event = await store.checkpoint_grouped(
                        sid,
                        snapshot,
                        "runtime.checkpointed",
                        {"stage": f"round-{checkpoint_round}"},
                    )
                else:
                    reference, event = await store.save_checkpoint_and_append_async(
                        sid,
                        snapshot,
                        "runtime.checkpointed",
                        {"stage": f"round-{checkpoint_round}"},
                    )
                expected_ref = checkpoint_round + 1
                if int(reference["seq"]) != expected_ref:
                    failures.append(
                        f"{sid}/round-{checkpoint_round}: ref {reference['seq']} != {expected_ref}"
                    )
                if int(event.payload.get("seq", -1)) != expected_ref:
                    failures.append(f"{sid}/round-{checkpoint_round}: audit payload mismatch")
                completion_ms.append((time.perf_counter() - round_started) * 1000.0)
            except Exception as exc:
                failures.append(f"{sid}/round-{checkpoint_round}: {exc!r}")

        tasks = [asyncio.create_task(one(index)) for index in range(sessions)]
        await asyncio.sleep(0)
        round_started = time.perf_counter()
        release.set()
        await asyncio.gather(*tasks)

    wall = time.perf_counter() - started
    writer_transactions = store.immediate_begins
    expected_legacy = sessions * checkpoint_rounds
    if mode == "legacy" and writer_transactions != expected_legacy:
        failures.append(
            f"legacy transaction count changed: {writer_transactions} != {expected_legacy}"
        )
    if mode == "grouped" and writer_transactions > checkpoint_rounds * 2:
        failures.append(
            f"grouped transaction count too high: {writer_transactions} > {checkpoint_rounds * 2}"
        )

    for sid in session_ids:
        events = store.list_events(sid)
        if len(events) != checkpoint_rounds + 1:
            failures.append(f"event count invalid: {sid}")
            break
        restored = store.restore_checkpoint(sid)
        if not restored or int(restored.get("round", -1)) != checkpoint_rounds - 1:
            failures.append(f"checkpoint restore invalid: {sid}")
            break
        checkpoint = restored.get("_checkpoint") or {}
        if int(checkpoint.get("seq", -1)) != checkpoint_rounds:
            failures.append(f"checkpoint tail binding invalid: {sid}")
            break
        if not store.verify_chain(sid):
            failures.append(f"hash chain invalid: {sid}")
            break

    return {
        "mode": mode,
        "sessions": sessions,
        "checkpoint_rounds": checkpoint_rounds,
        "writer_transactions": writer_transactions,
        "transactions_per_checkpoint": round(
            writer_transactions / max(1, sessions * checkpoint_rounds), 4
        ),
        "wall_seconds": round(wall, 4),
        "throughput_checkpoints_per_second": round(
            (sessions * checkpoint_rounds) / wall, 1
        ) if wall else 0.0,
        "completion_ms": {
            "p50": round(percentile(completion_ms, 0.50), 3),
            "p95": round(percentile(completion_ms, 0.95), 3),
            "p99": round(percentile(completion_ms, 0.99), 3),
        },
        "failures": failures[:20],
    }


async def main_async() -> dict[str, Any]:
    sessions = 64
    checkpoint_rounds = 4
    experiments = 3
    results: dict[str, list[dict[str, Any]]] = {"legacy": [], "grouped": []}
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ecomevo-checkpoint-group-") as tmp:
        root = Path(tmp)
        failures.extend(await semantic_probe(root))
        for experiment in range(experiments):
            order = ("legacy", "grouped") if experiment % 2 == 0 else ("grouped", "legacy")
            for mode in order:
                row = await run_mode(
                    root,
                    mode,
                    experiment,
                    sessions,
                    checkpoint_rounds,
                )
                results[mode].append(row)
                failures.extend(f"{mode}[{experiment}]: {item}" for item in row["failures"])

    legacy_tx = statistics.median(row["writer_transactions"] for row in results["legacy"])
    grouped_tx = statistics.median(row["writer_transactions"] for row in results["grouped"])
    legacy_wall = statistics.median(row["wall_seconds"] for row in results["legacy"])
    grouped_wall = statistics.median(row["wall_seconds"] for row in results["grouped"])
    legacy_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["legacy"])
    grouped_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["grouped"])

    tx_ratio = grouped_tx / legacy_tx if legacy_tx else 1.0
    wall_ratio = grouped_wall / legacy_wall if legacy_wall else 1.0
    p99_ratio = grouped_p99 / legacy_p99 if legacy_p99 else 1.0

    # Pre-declared diagnostic thresholds. Productionization is only justified if the
    # structural transaction cut is large and synchronized completion improves too.
    if tx_ratio > 0.10:
        failures.append(f"checkpoint grouping writer ratio too high: {tx_ratio:.4f} > 0.10")
    if wall_ratio > 0.85:
        failures.append(f"checkpoint grouping wall ratio too high: {wall_ratio:.4f} > 0.85")
    if p99_ratio > 0.90:
        failures.append(f"checkpoint grouping p99 ratio too high: {p99_ratio:.4f} > 0.90")

    return {
        "ok": not failures,
        "sessions": sessions,
        "checkpoint_rounds": checkpoint_rounds,
        "experiments": experiments,
        "semantics": {
            "checkpoint_binds_pre_audit_tail": True,
            "same_session_order_checked": True,
            "shared_failure_isolated": True,
        },
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
        "failures": failures[:30],
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
