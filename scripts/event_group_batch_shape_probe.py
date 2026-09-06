from __future__ import annotations

import asyncio
import json
import math
import statistics
import tempfile
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import writer_profile_gate as writer_profile


CONCURRENCY_LEVELS = (32, 64, 120)


class ShapeEventStore(writer_profile.ProfiledEventStore):
    """Observe successful built-in append-group batches without changing scheduling."""

    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._shape_lock = threading.RLock()
        self._batches: list[list[tuple[str, str]]] = []
        super().__init__(path, profile)

    def reset_shape(self) -> None:
        with self._shape_lock:
            self._batches.clear()

    def _persist_append_group(self, batch):
        persisted = super()._persist_append_group(batch)
        snapshot = [
            (str(request.event_type), str(request.session_id))
            for request in batch
        ]
        with self._shape_lock:
            self._batches.append(snapshot)
        return persisted

    @staticmethod
    def _percentile(values: list[int], q: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
        return int(ordered[index])

    def shape_report(self) -> dict[str, Any]:
        with self._shape_lock:
            batches = [list(batch) for batch in self._batches]

        sizes = [len(batch) for batch in batches]
        event_requests: Counter[str] = Counter()
        event_batch_counts: Counter[str] = Counter()
        event_batch_sizes: dict[str, list[int]] = defaultdict(list)
        composition_counts: Counter[str] = Counter()
        pure_batches = 0
        singleton_batches = 0
        distinct_sessions: list[int] = []

        for batch in batches:
            if len(batch) == 1:
                singleton_batches += 1
            kinds = Counter(event_type for event_type, _session_id in batch)
            event_requests.update(kinds)
            if len(kinds) == 1:
                pure_batches += 1
            for event_type, count in kinds.items():
                event_batch_counts[event_type] += 1
                event_batch_sizes[event_type].append(int(count))
            signature = " + ".join(
                f"{event_type}:{count}"
                for event_type, count in sorted(kinds.items())
            )
            composition_counts[signature] += 1
            distinct_sessions.append(len({session_id for _event_type, session_id in batch}))

        per_event_type = []
        for event_type, requests in event_requests.most_common():
            containing = int(event_batch_counts[event_type])
            counts = event_batch_sizes[event_type]
            per_event_type.append(
                {
                    "event_type": event_type,
                    "requests": int(requests),
                    "containing_batches": containing,
                    "requests_per_containing_batch": round(requests / max(1, containing), 3),
                    "median_requests_per_batch": round(float(statistics.median(counts)), 3),
                    "max_requests_in_batch": max(counts, default=0),
                }
            )

        total_requests = sum(sizes)
        total_batches = len(batches)
        return {
            "total_requests": total_requests,
            "total_batches": total_batches,
            "requests_per_batch": round(total_requests / max(1, total_batches), 3),
            "batch_size_median": round(float(statistics.median(sizes)), 3) if sizes else 0.0,
            "batch_size_p95": self._percentile(sizes, 0.95),
            "batch_size_max": max(sizes, default=0),
            "singleton_batches": singleton_batches,
            "singleton_batch_ratio": round(singleton_batches / max(1, total_batches), 4),
            "pure_event_type_batches": pure_batches,
            "pure_event_type_batch_ratio": round(pure_batches / max(1, total_batches), 4),
            "distinct_sessions_per_batch_median": (
                round(float(statistics.median(distinct_sessions)), 3)
                if distinct_sessions
                else 0.0
            ),
            "theoretical_limit64_batches": math.ceil(total_requests / 64) if total_requests else 0,
            "per_event_type": per_event_type,
            "top_compositions": [
                {"composition": signature, "batches": int(count)}
                for signature, count in composition_counts.most_common(12)
            ],
        }


def build_engine(db: Path, profile: writer_profile.WriterProfile):
    sandbox = writer_profile.ActionSandbox()
    events = ShapeEventStore(db, profile)
    skills = writer_profile.ProfiledSkills(db, profile)
    harness = writer_profile.ProfiledHarness(db, profile, sandbox=sandbox)
    engine = writer_profile.EcomEvoEngine(
        db,
        plugin_overrides={
            "event.store": events,
            "memory.skills": skills,
            "evolver.harness": harness,
            "sandbox.action": sandbox,
        },
    )
    engine.autonomy.policy.routing = writer_profile.ProfiledRouting(db, profile)
    return engine, events


async def one_level(root: Path, tasks: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, events = build_engine(root / f"shape-{tasks}.db", profile)

    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"warm-up chain invalid at c{tasks}")

    profile.reset()
    events.reset_shape()
    summaries = await writer_profile._run_batch(engine, tasks)
    if any(not summary.event_chain_valid for summary in summaries):
        raise AssertionError(f"runtime chain invalid at c{tasks}")

    shape = events.shape_report()
    writer = profile.report(tasks)
    group_stage = next(
        (row for row in writer["stages"] if row["stage"] == "event.group_commit"),
        None,
    )
    if group_stage is None:
        raise AssertionError(f"no event.group_commit stage at c{tasks}")
    if int(group_stage["transactions"]) != int(shape["total_batches"]):
        raise AssertionError(
            f"batch/transaction mismatch at c{tasks}: "
            f"{shape['total_batches']} batches != {group_stage['transactions']} tx"
        )
    return {
        "tasks": tasks,
        "event_group_transactions": int(group_stage["transactions"]),
        "event_group_transactions_per_task": float(group_stage["transactions_per_task"]),
        "writer_hold_ms_total": float(group_stage["writer_hold_ms_total"]),
        "operation_ms_total": float(group_stage["operation_ms_total"]),
        "shape": shape,
        "unattributed_transactions": int(writer["unattributed_transactions"]),
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    levels: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-event-group-shape-") as tmp:
        root = Path(tmp)
        for tasks in CONCURRENCY_LEVELS:
            try:
                row = await one_level(root, tasks)
            except Exception as exc:
                failures.append(f"c{tasks}: {type(exc).__name__}: {exc}")
                continue
            if row["unattributed_transactions"]:
                failures.append(
                    f"c{tasks}: lost writer attribution: {row['unattributed_transactions']}"
                )
            levels.append(row)

    return {
        "ok": not failures,
        "concurrency_levels": list(CONCURRENCY_LEVELS),
        "levels": levels,
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
