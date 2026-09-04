from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import Any

import writer_profile_gate as writer_profile


TASKS = 32


class FragmentationEventStore(writer_profile.ProfiledEventStore):
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._fragment_lock = threading.RLock()
        self._append_batches: list[dict[str, Any]] = []
        super().__init__(path, profile)

    def reset_append_batches(self) -> None:
        with self._fragment_lock:
            self._append_batches.clear()

    def append_batches(self) -> list[dict[str, Any]]:
        with self._fragment_lock:
            return [dict(batch) for batch in self._append_batches]

    def _persist_append_group(self, batch):
        event_types = Counter(str(request.event_type) for request in batch)
        session_counts = Counter(str(request.session_id) for request in batch)
        snapshot = {
            "size": len(batch),
            "event_types": dict(sorted(event_types.items())),
            "unique_sessions": len(session_counts),
            "max_requests_per_session": max(session_counts.values(), default=0),
        }
        with self._fragment_lock:
            self._append_batches.append(snapshot)
        return super()._persist_append_group(batch)


def percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return int(ordered[index])


def summarize_batches(batches: list[dict[str, Any]]) -> dict[str, Any]:
    sizes = [int(batch["size"]) for batch in batches]
    event_type_totals: Counter[str] = Counter()
    shape_counts: Counter[str] = Counter()
    for batch in batches:
        event_types = dict(batch["event_types"])
        event_type_totals.update({str(key): int(value) for key, value in event_types.items()})
        shape = " | ".join(f"{key}={value}" for key, value in sorted(event_types.items()))
        shape_counts[shape] += 1

    return {
        "batches": len(batches),
        "requests": sum(sizes),
        "average_batch_size": round(statistics.fmean(sizes), 3) if sizes else 0.0,
        "batch_size_p50": percentile(sizes, 0.50),
        "batch_size_p95": percentile(sizes, 0.95),
        "batch_size_max": max(sizes, default=0),
        "single_request_batches": sum(1 for size in sizes if size == 1),
        "full_64_batches": sum(1 for size in sizes if size == 64),
        "homogeneous_batches": sum(1 for batch in batches if len(batch["event_types"]) == 1),
        "mixed_event_type_batches": sum(1 for batch in batches if len(batch["event_types"]) > 1),
        "multi_request_same_session_batches": sum(
            1 for batch in batches if int(batch["max_requests_per_session"]) > 1
        ),
        "event_type_requests": dict(sorted(event_type_totals.items())),
        "batch_shape_counts": dict(sorted(shape_counts.items())),
        "batch_sizes": sizes,
    }


async def main_async() -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    failures: list[str] = []
    original_event_store = writer_profile.ProfiledEventStore
    writer_profile.ProfiledEventStore = FragmentationEventStore
    try:
        with tempfile.TemporaryDirectory(prefix="ecomevo-event-fragmentation-") as tmp:
            db = Path(tmp) / "event-fragmentation.db"
            engine = writer_profile._build_engine(db, profile)
            if not isinstance(engine.events, FragmentationEventStore):
                raise AssertionError("diagnostic engine did not bind FragmentationEventStore")

            warm = await writer_profile._run_batch(engine, 1)
            if not warm[0].event_chain_valid:
                failures.append("warm-up run produced an invalid event chain")

            profile.reset()
            engine.events.reset_append_batches()
            summaries = await writer_profile._run_batch(engine, TASKS)
            if any(not summary.event_chain_valid for summary in summaries):
                failures.append("profiled run produced an invalid event chain")

            report = profile.report(TASKS)
            batches = engine.events.append_batches()
            fragmentation = summarize_batches(batches)
            group_stage = next(
                (row for row in report["stages"] if row["stage"] == "event.group_commit"),
                None,
            )
            writer_transactions = int(group_stage["transactions"]) if group_stage else 0

            if report["unattributed_transactions"]:
                failures.append(
                    "event fragmentation probe lost writer attribution: "
                    f"{report['unattributed_transactions']} transactions"
                )
            if fragmentation["batches"] != writer_transactions:
                failures.append(
                    "append persistence batch count did not match grouped writer transactions: "
                    f"{fragmentation['batches']} != {writer_transactions}"
                )
            if fragmentation["requests"] < TASKS:
                failures.append(
                    "event fragmentation probe observed implausibly few grouped requests: "
                    f"{fragmentation['requests']} < {TASKS}"
                )

            return {
                "ok": not failures,
                "tasks": TASKS,
                "group_writer_transactions": writer_transactions,
                "group_transactions_per_task": round(writer_transactions / TASKS, 3),
                "fragmentation": fragmentation,
                "unattributed_transactions": int(report["unattributed_transactions"]),
                "failures": failures,
            }
    finally:
        writer_profile.ProfiledEventStore = original_event_store


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
