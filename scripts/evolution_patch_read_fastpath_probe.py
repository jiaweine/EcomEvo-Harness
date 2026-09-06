from __future__ import annotations

import asyncio
import json
import sqlite3
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import writer_profile_gate as writer_profile
from ecomevo.models import EvolutionPatch


TASKS = 32
EXPERIMENTS = 3


def patch(*, patch_id: str = "patch-a", value: str = "inspect") -> EvolutionPatch:
    return EvolutionPatch(
        patch_id=patch_id,
        created_at=time.time(),
        reason="diagnostic",
        target="tool",
        patch={"preferred_tools": [value]},
        replay_cases=1,
        regression_before=0.5,
        regression_after=0.4,
        accepted=True,
    )


class CountingBaselineEventStore(writer_profile.ProfiledEventStore):
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self.fast_hits = 0
        self.writer_rechecks = 0
        self.novel_inserts = 0
        self.calls = 0
        self._counter_lock = threading.Lock()
        super().__init__(path, profile)

    def reset_patch_counters(self) -> None:
        with self._counter_lock:
            self.fast_hits = 0
            self.writer_rechecks = 0
            self.novel_inserts = 0
            self.calls = 0

    def save_patch_if_novel(self, patch_value):
        result = super().save_patch_if_novel(patch_value)
        with self._counter_lock:
            self.calls += 1
            if result is None:
                self.novel_inserts += 1
            else:
                self.writer_rechecks += 1
        return result

    def counters(self) -> dict[str, int]:
        with self._counter_lock:
            return {
                "calls": self.calls,
                "fast_hits": self.fast_hits,
                "writer_rechecks": self.writer_rechecks,
                "novel_inserts": self.novel_inserts,
            }


class FastPathEventStore(writer_profile.ProfiledEventStore):
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self.fast_hits = 0
        self.writer_rechecks = 0
        self.novel_inserts = 0
        self.calls = 0
        self._counter_lock = threading.Lock()
        super().__init__(path, profile)

    @staticmethod
    def _existing_payload(row, patch_value: EvolutionPatch) -> dict[str, Any]:
        try:
            return json.loads(row["payload_json"])
        except Exception:
            return {
                "patch_id": "existing",
                "accepted": patch_value.accepted,
                "target": patch_value.target,
                "patch": patch_value.patch,
            }

    def reset_patch_counters(self) -> None:
        with self._counter_lock:
            self.fast_hits = 0
            self.writer_rechecks = 0
            self.novel_inserts = 0
            self.calls = 0

    def _save_patch_read_fast(self, patch_value: EvolutionPatch) -> dict[str, Any] | None:
        fp = self._patch_fingerprint(patch_value)
        with self._conn() as connection:
            row = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fp,),
            ).fetchone()
        if row:
            with self._counter_lock:
                self.fast_hits += 1
            return self._existing_payload(row, patch_value)

        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fp,),
            ).fetchone()
            if row:
                with self._counter_lock:
                    self.writer_rechecks += 1
                return self._existing_payload(row, patch_value)
            connection.execute(
                "INSERT INTO evolution_patches(patch_id,created_at,payload_json,fingerprint) "
                "VALUES(?,?,?,?)",
                (
                    patch_value.patch_id,
                    patch_value.created_at,
                    patch_value.model_dump_json(),
                    fp,
                ),
            )
            with self._counter_lock:
                self.novel_inserts += 1
        return None

    def save_patch_if_novel(self, patch_value):
        with self._counter_lock:
            self.calls += 1
        return writer_profile._timed(
            self._writer_profile,
            "event.evolution_patch",
            lambda: self._save_patch_read_fast(patch_value),
        )

    def counters(self) -> dict[str, int]:
        with self._counter_lock:
            return {
                "calls": self.calls,
                "fast_hits": self.fast_hits,
                "writer_rechecks": self.writer_rechecks,
                "novel_inserts": self.novel_inserts,
            }


def build_engine(db: Path, profile: writer_profile.WriterProfile, mode: str):
    sandbox = writer_profile.ActionSandbox()
    events = (
        FastPathEventStore(db, profile)
        if mode == "fast"
        else CountingBaselineEventStore(db, profile)
    )
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


def stage_from(report: dict[str, Any], stage_name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == stage_name), None)


def semantic_probe(root: Path) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for mode, store_type in (
        ("baseline", CountingBaselineEventStore),
        ("fast", FastPathEventStore),
    ):
        profile = writer_profile.WriterProfile()
        store = store_type(root / f"semantic-{mode}.db", profile)
        first = patch(patch_id=f"{mode}-one", value="inspect")
        duplicate = patch(patch_id=f"{mode}-two", value="inspect")
        assert store.save_patch_if_novel(first) is None
        existing = store.save_patch_if_novel(duplicate)
        assert existing is not None
        assert existing["patch_id"] == first.patch_id

        collision = patch(patch_id=first.patch_id, value="different")
        collision_error = None
        try:
            store.save_patch_if_novel(collision)
        except sqlite3.IntegrityError as exc:
            collision_error = type(exc).__name__
        output[mode] = {
            "duplicate_returns_original": existing["patch_id"] == first.patch_id,
            "patch_id_collision_error": collision_error,
        }
    return output


async def measure(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, events = build_engine(root / f"runtime-{mode}-{experiment}.db", profile, mode)

    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    events.reset_patch_counters()
    profile.reset()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, TASKS)
    wall = time.perf_counter() - started
    report = profile.report(TASKS)
    stage = stage_from(report, "event.evolution_patch")
    counters = events.counters()

    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")
    if counters["calls"] != TASKS:
        failures.append(f"patch call count changed: {counters['calls']} != {TASKS}")
    if counters["novel_inserts"] != 0:
        failures.append(
            f"steady-state runtime unexpectedly inserted novel patches: {counters['novel_inserts']}"
        )

    return {
        "mode": mode,
        "experiment": experiment,
        "tasks": TASKS,
        "wall_seconds": round(wall, 4),
        "throughput_tasks_per_second": round(TASKS / wall, 3) if wall else 0.0,
        "total_writer_transactions": int(report["total_transactions"]),
        "transactions_per_task": float(report["transactions_per_task"]),
        "evolution_patch": {
            "transactions": int(stage["transactions"]) if stage else 0,
            "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
            "writer_hold_ms_p95": float(stage["writer_hold_ms_p95"]) if stage else 0.0,
            "writer_hold_ms_p99": float(stage["writer_hold_ms_p99"]) if stage else 0.0,
            "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        },
        "counters": counters,
        "unattributed_transactions": int(report["unattributed_transactions"]),
        "failures": failures,
    }


def median_metric(rows: list[dict[str, Any]], *path: str) -> float:
    values: list[float] = []
    for row in rows:
        value: Any = row
        for key in path:
            value = value[key]
        values.append(float(value))
    return float(statistics.median(values))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    results: dict[str, list[dict[str, Any]]] = {"baseline": [], "fast": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-evolution-patch-fastpath-") as tmp:
        root = Path(tmp)
        semantics = semantic_probe(root)
        if semantics["baseline"] != semantics["fast"]:
            failures.append("read fast path changed duplicate/collision semantics")

        for experiment in range(EXPERIMENTS):
            order = ("baseline", "fast") if experiment % 2 == 0 else ("fast", "baseline")
            for mode in order:
                row = await measure(root, mode, experiment)
                results[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    baseline_tx = median_metric(results["baseline"], "evolution_patch", "transactions")
    fast_tx = median_metric(results["fast"], "evolution_patch", "transactions")
    baseline_hold = median_metric(results["baseline"], "evolution_patch", "writer_hold_ms_total")
    fast_hold = median_metric(results["fast"], "evolution_patch", "writer_hold_ms_total")
    baseline_op = median_metric(results["baseline"], "evolution_patch", "operation_ms_total")
    fast_op = median_metric(results["fast"], "evolution_patch", "operation_ms_total")
    baseline_wall = median_metric(results["baseline"], "wall_seconds")
    fast_wall = median_metric(results["fast"], "wall_seconds")
    baseline_total_tx = median_metric(results["baseline"], "total_writer_transactions")
    fast_total_tx = median_metric(results["fast"], "total_writer_transactions")

    return {
        "ok": not failures,
        "tasks": TASKS,
        "experiments": EXPERIMENTS,
        "semantics": semantics,
        "results": results,
        "comparison": {
            "evolution_patch_writer_transaction_ratio": round(fast_tx / max(1.0, baseline_tx), 4),
            "evolution_patch_writer_hold_total_ratio": round(fast_hold / max(0.001, baseline_hold), 4),
            "evolution_patch_operation_total_ratio": round(fast_op / max(0.001, baseline_op), 4),
            "runtime_wall_ratio": round(fast_wall / max(0.0001, baseline_wall), 4),
            "total_writer_transaction_ratio": round(fast_total_tx / max(1.0, baseline_total_tx), 4),
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
