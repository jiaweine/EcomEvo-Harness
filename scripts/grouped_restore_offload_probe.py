from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import statistics
import tempfile
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_event_store import BundledEventStore


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


def seed_store(path: Path) -> None:
    store = BundledEventStore(path)
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


def normalize(value: dict[str, Any] | None) -> Any:
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


@dataclass(slots=True)
class _RestoreRequest:
    session_id: str
    seq: int
    future: asyncio.Future[dict[str, Any] | None]


@dataclass(slots=True)
class _LoopRestoreGroup:
    queue: list[_RestoreRequest] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class PrototypeGroupedRestoreStore(BundledEventStore):
    def __init__(self, path: Path):
        self._restore_group_lock = threading.RLock()
        self._restore_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopRestoreGroup
        ] = weakref.WeakKeyDictionary()
        self.restore_batches = 0
        self.restore_connections = 0
        self.restore_batch_sizes: list[int] = []
        self._restore_trace_lock = threading.Lock()
        super().__init__(path)

    @staticmethod
    def _restore_from_connection(connection, session_id: str, seq: int):
        q = (
            "SELECT seq,snapshot_blob,state_hash,event_hash FROM snapshots "
            "WHERE session_id=? AND seq<=? ORDER BY seq DESC LIMIT 1"
        )
        row = connection.execute(q, (session_id, int(seq))).fetchone()
        if not row:
            return None
        checkpoint_seq = int(row["seq"])
        event_hash = "GENESIS"
        if checkpoint_seq:
            event = connection.execute(
                "SELECT hash FROM events WHERE session_id=? AND seq=?",
                (session_id, checkpoint_seq),
            ).fetchone()
            if event is None:
                return None
            event_hash = str(event["hash"])

        blob = str(row["snapshot_blob"])
        if not blob.startswith("json:"):
            return None
        state = json.loads(blob[5:])
        body = PrototypeGroupedRestoreStore._state_body(state)
        expected_state = str(row["state_hash"] or hashlib.sha256(body.encode()).hexdigest())
        expected_event = str(row["event_hash"] or event_hash)
        if hashlib.sha256(body.encode()).hexdigest() != expected_state or event_hash != expected_event:
            return None
        return {
            **state,
            "_checkpoint": {
                "session_id": session_id,
                "seq": checkpoint_seq,
                "state_hash": expected_state,
                "event_hash": expected_event,
            },
        }

    def _restore_batch(self, batch: list[_RestoreRequest]):
        with self._restore_trace_lock:
            self.restore_batches += 1
            self.restore_batch_sizes.append(len(batch))
        with self._conn() as connection:
            with self._restore_trace_lock:
                self.restore_connections += 1
            return [
                self._restore_from_connection(connection, request.session_id, request.seq)
                for request in batch
            ]

    async def restore_checkpoint_grouped(self, session_id: str, seq: int):
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        request = _RestoreRequest(str(session_id), int(seq), future)
        with self._restore_group_lock:
            group = self._restore_groups.get(loop)
            if group is None:
                group = _LoopRestoreGroup()
                self._restore_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_restore_group(group))
        return await future

    async def _flush_restore_group(self, group: _LoopRestoreGroup) -> None:
        try:
            while True:
                await asyncio.sleep(0)
                with self._restore_group_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return
                    batch = list(group.queue[:GROUP_LIMIT])
                    del group.queue[: len(batch)]
                try:
                    values = await asyncio.to_thread(self._restore_batch, batch)
                except Exception as exc:
                    for request in batch:
                        if not request.future.done():
                            request.future.set_exception(exc)
                    continue
                for request, value in zip(batch, values):
                    if not request.future.done():
                        request.future.set_result(value)
        except asyncio.CancelledError:
            with self._restore_group_lock:
                queued = list(group.queue)
                group.queue.clear()
                group.scheduled = False
                group.worker = None
            for request in queued:
                if not request.future.done():
                    request.future.set_exception(
                        RuntimeError("restore group worker cancelled before read")
                    )
            raise


async def semantic_checks(template: Path, root: Path) -> list[str]:
    failures: list[str] = []
    path = root / "semantic.db"
    shutil.copy2(template, path)
    store = PrototypeGroupedRestoreStore(path)
    cases = [
        ("restore-0", 3),
        ("restore-0", 4),
        ("restore-0", 5),
        ("restore-31", 4),
        ("restore-does-not-exist", 5),
    ]
    for sid, seq in cases:
        baseline = normalize(store.restore_checkpoint(sid, seq))
        grouped = normalize(await store.restore_checkpoint_grouped(sid, seq))
        if grouped != baseline:
            failures.append(f"semantic mismatch for {sid} seq={seq}")
    return failures


async def measure_sync(template: Path, root: Path, experiment: int) -> dict[str, Any]:
    path = root / f"sync-{experiment}.db"
    shutil.copy2(template, path)
    store = BundledEventStore(path)
    return await _measure(store, "sync", experiment)


async def measure_grouped(template: Path, root: Path, experiment: int) -> dict[str, Any]:
    path = root / f"grouped-{experiment}.db"
    shutil.copy2(template, path)
    store = PrototypeGroupedRestoreStore(path)
    row = await _measure(store, "grouped", experiment)
    row["restore_batches"] = store.restore_batches
    row["restore_connections"] = store.restore_connections
    row["restore_batch_sizes"] = list(store.restore_batch_sizes)
    return row


async def _measure(store, mode: str, experiment: int) -> dict[str, Any]:
    lags_ms: list[float] = []
    call_ms: list[float] = []
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
        started = time.perf_counter()
        if mode == "sync":
            value = store.restore_checkpoint(sid, seq)
        else:
            value = await store.restore_checkpoint_grouped(sid, seq)
        call_ms.append((time.perf_counter() - started) * 1000.0)
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

    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": wall,
        "call_ms": {
            "p50": percentile(call_ms, 0.50),
            "p95": percentile(call_ms, 0.95),
            "p99": percentile(call_ms, 0.99),
            "mean": statistics.fmean(call_ms) if call_ms else 0.0,
        },
        "heartbeat_ms": {
            "samples": len(lags_ms),
            "p50": percentile(lags_ms, 0.50),
            "p95": percentile(lags_ms, 0.95),
            "p99": percentile(lags_ms, 0.99),
            "max": max(lags_ms) if lags_ms else 0.0,
            "mean": statistics.fmean(lags_ms) if lags_ms else 0.0,
        },
        "failures": failures[:10],
    }


def rounded(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["wall_seconds"] = round(float(row["wall_seconds"]), 4)
    result["call_ms"] = {key: round(float(value), 3) for key, value in row["call_ms"].items()}
    result["heartbeat_ms"] = {
        key: (int(value) if key == "samples" else round(float(value), 3))
        for key, value in row["heartbeat_ms"].items()
    }
    return result


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-grouped-restore-") as tmp:
        root = Path(tmp)
        template = root / "template.db"
        seed_store(template)
        failures.extend(await semantic_checks(template, root))

        rows: list[dict[str, Any]] = []
        for experiment in range(EXPERIMENTS):
            order = ("sync", "grouped") if experiment % 2 == 0 else ("grouped", "sync")
            for mode in order:
                row = (
                    await measure_sync(template, root, experiment)
                    if mode == "sync"
                    else await measure_grouped(template, root, experiment)
                )
                rows.append(row)
                failures.extend(row["failures"])
                if mode == "grouped":
                    expected_batches = (REQUESTS + GROUP_LIMIT - 1) // GROUP_LIMIT
                    if int(row["restore_batches"]) != expected_batches:
                        failures.append(
                            f"group batch count changed: {row['restore_batches']} != {expected_batches}"
                        )
                    if int(row["restore_connections"]) != expected_batches:
                        failures.append(
                            "group connection count changed: "
                            f"{row['restore_connections']} != {expected_batches}"
                        )
                    if any(int(size) > GROUP_LIMIT for size in row["restore_batch_sizes"]):
                        failures.append("restore group exceeded bounded batch size")

    sync_rows = [row for row in rows if row["mode"] == "sync"]
    grouped_rows = [row for row in rows if row["mode"] == "grouped"]
    sync_wall = statistics.median(float(row["wall_seconds"]) for row in sync_rows)
    grouped_wall = statistics.median(float(row["wall_seconds"]) for row in grouped_rows)
    sync_max = statistics.median(float(row["heartbeat_ms"]["max"]) for row in sync_rows)
    grouped_max = statistics.median(float(row["heartbeat_ms"]["max"]) for row in grouped_rows)
    wall_ratio = grouped_wall / max(sync_wall, 1e-9)
    lag_ratio = grouped_max / max(sync_max, 1e-6)

    if wall_ratio > WALL_RATIO_LIMIT:
        failures.append(
            f"grouped restore wall ratio too high: {wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
        )
    if lag_ratio > MAX_LAG_RATIO_LIMIT:
        failures.append(
            f"grouped restore max-lag ratio too high: {lag_ratio:.4f} > {MAX_LAG_RATIO_LIMIT:.2f}"
        )

    result = {
        "ok": not failures,
        "positive": not failures,
        "sessions": SESSIONS,
        "requests_per_arm": REQUESTS,
        "experiments": EXPERIMENTS,
        "group_limit": GROUP_LIMIT,
        "expected_batches_per_grouped_run": (REQUESTS + GROUP_LIMIT - 1) // GROUP_LIMIT,
        "thresholds": {
            "wall_ratio_max": WALL_RATIO_LIMIT,
            "max_lag_ratio_max": MAX_LAG_RATIO_LIMIT,
        },
        "medians": {
            "sync_wall_seconds": round(sync_wall, 4),
            "grouped_wall_seconds": round(grouped_wall, 4),
            "grouped_to_sync_wall_ratio": round(wall_ratio, 4),
            "sync_heartbeat_max_ms": round(sync_max, 4),
            "grouped_heartbeat_max_ms": round(grouped_max, 4),
            "grouped_to_sync_max_lag_ratio": round(lag_ratio, 4),
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
