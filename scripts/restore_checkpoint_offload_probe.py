from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_event_store import BundledEventStore


SESSIONS = 128
REQUESTS = 512
EXPERIMENTS = 5
HEARTBEAT_INTERVAL = 0.001
PAYLOAD_WORDS = 192
MODES = ("sync", "serialized_gate", "parallel_thread")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def normalize(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    # round-trip through JSON so equality is independent of incidental model/container types.
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def snapshot(index: int, stage: str) -> dict[str, Any]:
    # Keep this payload large enough to exercise the production restore path's JSON decode
    # and SHA-256 integrity check without turning the diagnostic into a synthetic stress test.
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


def seed_store(path: Path) -> list[dict[str, Any]]:
    store = BundledEventStore(path)
    expected: list[dict[str, Any]] = []
    for index in range(SESSIONS):
        sid = f"restore-{index}"
        events, first_ref = store.create_session_events_checkpoint(
            sid,
            [
                ("goal.parsed", {"goal": f"review-{index}"}),
                ("belief.updated", {"confidence": 0.2, "index": index}),
                ("harness.profile.bound", {"component_ids": ["active"]}),
            ],
            snapshot(index, "initial"),
        )
        if [event.seq for event in events] != [1, 2, 3] or int(first_ref["seq"]) != 3:
            raise AssertionError("seed bootstrap sequence changed")

        event4 = store.append(sid, "tool.executed", {"tool": "evidence.search", "index": index})
        ref4 = store.save_checkpoint(sid, snapshot(index, "recovery-1"), seq=event4.seq)
        event5 = store.append(sid, "verification.completed", {"score": 0.4, "index": index})
        ref5 = store.save_checkpoint(sid, snapshot(index, "recovery-2"), seq=event5.seq)
        if int(ref4["seq"]) != 4 or int(ref5["seq"]) != 5:
            raise AssertionError("seed checkpoint sequence changed")
        if not store.verify_chain(sid):
            raise AssertionError(f"invalid seed event chain: {sid}")
        expected.append(
            {
                "session_id": sid,
                "latest": normalize(store.restore_checkpoint(sid)),
                "seq4": normalize(store.restore_checkpoint(sid, 4)),
                "seq3": normalize(store.restore_checkpoint(sid, 3)),
            }
        )

    # Put the immutable read template into the main database file before copying it.
    with store._conn() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return expected


async def restore(store: BundledEventStore, mode: str, sid: str, seq: int | None):
    if mode == "sync":
        return store.restore_checkpoint(sid, seq)
    if mode == "serialized_gate":
        return await store._run_io(store.restore_checkpoint, sid, seq)
    if mode == "parallel_thread":
        return await asyncio.to_thread(store.restore_checkpoint, sid, seq)
    raise AssertionError(f"unknown mode: {mode}")


async def semantic_checks(template: Path, expected: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    semantic_path = template.parent / "semantic.db"
    shutil.copy2(template, semantic_path)
    store = BundledEventStore(semantic_path)

    # Create two dedicated corruption cases without touching the performance corpus.
    corrupt_state_sid = "restore-corrupt-state"
    corrupt_event_sid = "restore-corrupt-event"
    for sid, index in ((corrupt_state_sid, 9001), (corrupt_event_sid, 9002)):
        store.create_session_events_checkpoint(
            sid,
            [("goal.parsed", {"goal": sid})],
            snapshot(index, "corrupt-check"),
        )
    with store._conn() as connection:
        connection.execute(
            "UPDATE snapshots SET state_hash='corrupt-state-hash' WHERE session_id=?",
            (corrupt_state_sid,),
        )
        connection.execute(
            "UPDATE snapshots SET event_hash='corrupt-event-hash' WHERE session_id=?",
            (corrupt_event_sid,),
        )

    baseline_cases = {
        "latest": expected[0]["latest"],
        "seq4": expected[0]["seq4"],
        "seq3": expected[0]["seq3"],
        "missing_session": None,
        "corrupt_state_hash": None,
        "corrupt_event_hash": None,
    }
    case_args = {
        "latest": ("restore-0", None),
        "seq4": ("restore-0", 4),
        "seq3": ("restore-0", 3),
        "missing_session": ("restore-does-not-exist", None),
        "corrupt_state_hash": (corrupt_state_sid, None),
        "corrupt_event_hash": (corrupt_event_sid, None),
    }

    for mode in MODES:
        for name, (sid, seq) in case_args.items():
            actual = normalize(await restore(store, mode, sid, seq))
            if actual != baseline_cases[name]:
                failures.append(f"{mode}:{name} restore semantics changed")

    # A valid earlier checkpoint must never be replaced by the later snapshot when seq is bounded.
    if baseline_cases["seq3"] == baseline_cases["seq4"] or baseline_cases["seq4"] == baseline_cases["latest"]:
        failures.append("seed snapshots do not distinguish bounded restore semantics")
    return failures


async def measure_mode(template: Path, root: Path, mode: str, experiment: int) -> dict[str, Any]:
    path = root / f"{mode}-{experiment}.db"
    shutil.copy2(template, path)
    store = BundledEventStore(path)
    lags_ms: list[float] = []
    call_ms: list[float] = []
    stop = asyncio.Event()
    failures: list[str] = []

    async def heartbeat() -> None:
        target = time.perf_counter() + HEARTBEAT_INTERVAL
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            now = time.perf_counter()
            lags_ms.append(max(0.0, now - target) * 1000.0)
            target = now + HEARTBEAT_INTERVAL

    async def one(request_index: int) -> None:
        sid_index = request_index % SESSIONS
        sid = f"restore-{sid_index}"
        selector = request_index % 3
        seq = None if selector == 0 else (4 if selector == 1 else 3)
        started = time.perf_counter()
        value = await restore(store, mode, sid, seq)
        call_ms.append((time.perf_counter() - started) * 1000.0)
        if value is None:
            failures.append(f"{mode}: unexpected missing valid restore for {sid} seq={seq}")
            return
        checkpoint = value.get("_checkpoint") if isinstance(value, dict) else None
        expected_seq = 5 if seq is None else seq
        if not isinstance(checkpoint, dict) or int(checkpoint.get("seq", -1)) != expected_seq:
            failures.append(
                f"{mode}: wrong checkpoint for {sid}: {checkpoint!r}, expected seq={expected_seq}"
            )

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
        "requests": REQUESTS,
        "wall_seconds": wall,
        "throughput_per_second": REQUESTS / wall if wall else 0.0,
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


def rounded_measurement(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "wall_seconds": round(float(row["wall_seconds"]), 4),
        "throughput_per_second": round(float(row["throughput_per_second"]), 2),
        "call_ms": {key: round(float(value), 3) for key, value in row["call_ms"].items()},
        "heartbeat_ms": {
            key: (int(value) if key == "samples" else round(float(value), 3))
            for key, value in row["heartbeat_ms"].items()
        },
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-restore-offload-") as tmp:
        root = Path(tmp)
        template = root / "template.db"
        expected = seed_store(template)
        failures.extend(await semantic_checks(template, expected))

        rows: list[dict[str, Any]] = []
        # Rotate arm order so filesystem/page-cache and runner drift do not systematically
        # favor either offload strategy.
        for experiment in range(EXPERIMENTS):
            start = experiment % len(MODES)
            order = MODES[start:] + MODES[:start]
            for mode in order:
                row = await measure_mode(template, root, mode, experiment)
                rows.append(row)
                failures.extend(row["failures"])

    by_mode = {mode: [row for row in rows if row["mode"] == mode] for mode in MODES}
    medians: dict[str, dict[str, float]] = {}
    for mode, mode_rows in by_mode.items():
        medians[mode] = {
            "wall_seconds": statistics.median(float(row["wall_seconds"]) for row in mode_rows),
            "heartbeat_max_ms": statistics.median(
                float(row["heartbeat_ms"]["max"]) for row in mode_rows
            ),
            "heartbeat_p99_ms": statistics.median(
                float(row["heartbeat_ms"]["p99"]) for row in mode_rows
            ),
        }

    sync_wall = medians["sync"]["wall_seconds"]
    sync_max_lag = medians["sync"]["heartbeat_max_ms"]
    comparison = {}
    for mode in ("serialized_gate", "parallel_thread"):
        comparison[mode] = {
            "wall_ratio_vs_sync": medians[mode]["wall_seconds"] / max(sync_wall, 1e-9),
            "max_lag_ratio_vs_sync": medians[mode]["heartbeat_max_ms"] / max(sync_max_lag, 1e-6),
        }

    result = {
        "ok": not failures,
        "sessions": SESSIONS,
        "requests_per_arm": REQUESTS,
        "experiments": EXPERIMENTS,
        "payload_words": PAYLOAD_WORDS,
        "existing_restore_checkpoint_async": hasattr(BundledEventStore, "restore_checkpoint_async"),
        "semantic_cases": [
            "latest",
            "bounded_seq4",
            "bounded_seq3",
            "missing_session",
            "corrupt_state_hash",
            "corrupt_event_hash",
        ],
        "medians": {
            mode: {key: round(value, 4) for key, value in values.items()}
            for mode, values in medians.items()
        },
        "comparison": {
            mode: {key: round(value, 4) for key, value in values.items()}
            for mode, values in comparison.items()
        },
        "runs": [rounded_measurement(row) for row in rows],
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    result = asyncio.run(main_async())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
