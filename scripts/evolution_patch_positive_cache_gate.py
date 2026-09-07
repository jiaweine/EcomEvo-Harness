from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import writer_profile_gate as writer_profile
from ecomevo.models import EvolutionPatch
from ecomevo.runtime.event_store import EventStore


DUPLICATE_CALLS = 512
DIRECT_EXPERIMENTS = 5
RUNTIME_TASKS = 32
RUNTIME_EXPERIMENTS = 3
WALL_RATIO_LIMIT = 0.35


def patch(*, patch_id: str, value: str = "inspect") -> EvolutionPatch:
    return EvolutionPatch(
        patch_id=patch_id,
        created_at=time.time(),
        reason="positive-cache-gate",
        target="tool",
        patch={"preferred_tools": [value]},
        replay_cases=1,
        regression_before=0.5,
        regression_after=0.4,
        accepted=True,
    )


def _is_patch_lookup(statement: str) -> bool:
    normalized = " ".join(statement.strip().upper().split())
    return normalized.startswith(
        "SELECT PAYLOAD_JSON FROM EVOLUTION_PATCHES WHERE FINGERPRINT="
    )


class CountingProfiledEventStore(writer_profile.ProfiledEventStore):
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._patch_trace_lock = threading.RLock()
        self.patch_lookup_selects = 0
        super().__init__(path, profile)

    def _conn(self):
        connection = EventStore._conn(self)
        connection_id = id(connection)

        def trace(statement: str) -> None:
            self._writer_profile.trace(connection_id, statement)
            if _is_patch_lookup(statement):
                with self._patch_trace_lock:
                    self.patch_lookup_selects += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_patch_metrics(self) -> None:
        with self._patch_trace_lock:
            self.patch_lookup_selects = 0


class BaselineReadFastPathStore(CountingProfiledEventStore):
    """Exact #74 behavior before the positive cache is applied."""

    def save_patch_if_novel(self, patch_value: EvolutionPatch) -> dict[str, Any] | None:
        def operation() -> dict[str, Any] | None:
            fingerprint = self._patch_fingerprint(patch_value)
            with self._conn() as connection:
                existing = connection.execute(
                    "SELECT payload_json FROM evolution_patches "
                    "WHERE fingerprint=? LIMIT 1",
                    (fingerprint,),
                ).fetchone()
            if existing is not None:
                return self._existing_patch_payload(existing, patch_value)

            with self._lock, self._conn() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT payload_json FROM evolution_patches "
                    "WHERE fingerprint=? LIMIT 1",
                    (fingerprint,),
                ).fetchone()
                if existing is not None:
                    return self._existing_patch_payload(existing, patch_value)
                connection.execute(
                    "INSERT INTO evolution_patches"
                    "(patch_id,created_at,payload_json,fingerprint) VALUES(?,?,?,?)",
                    (
                        patch_value.patch_id,
                        patch_value.created_at,
                        patch_value.model_dump_json(),
                        fingerprint,
                    ),
                )
            return None

        return writer_profile._timed(
            self._writer_profile,
            "event.evolution_patch",
            operation,
        )


class ProductionPositiveCacheStore(CountingProfiledEventStore):
    pass


def _stage(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == name), None)


def direct_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    store_type = (
        BaselineReadFastPathStore
        if mode == "baseline"
        else ProductionPositiveCacheStore
    )
    store = store_type(root / f"direct-{experiment}-{mode}.db", profile)
    seed = patch(patch_id=f"{mode}-{experiment}-seed")
    assert store.save_patch_if_novel(seed) is None

    profile.reset()
    store.reset_patch_metrics()
    started = time.perf_counter()
    for index in range(DUPLICATE_CALLS):
        existing = store.save_patch_if_novel(
            patch(patch_id=f"{mode}-{experiment}-duplicate-{index}")
        )
        if existing is None or existing["patch_id"] != seed.patch_id:
            raise AssertionError("duplicate lookup stopped returning original payload")
    wall = time.perf_counter() - started
    report = profile.report(DUPLICATE_CALLS)
    stage = _stage(report, "event.evolution_patch")
    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": wall,
        "fingerprint_selects": store.patch_lookup_selects,
        "writer_transactions": int(stage["transactions"]) if stage else 0,
        "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        "unattributed_transactions": int(report["unattributed_transactions"]),
    }


def build_engine(
    db: Path,
    profile: writer_profile.WriterProfile,
    mode: str,
):
    sandbox = writer_profile.ActionSandbox()
    event_store = (
        BaselineReadFastPathStore(db, profile)
        if mode == "baseline"
        else ProductionPositiveCacheStore(db, profile)
    )
    skills = writer_profile.ProfiledSkills(db, profile)
    harness = writer_profile.ProfiledHarness(db, profile, sandbox=sandbox)
    engine = writer_profile.EcomEvoEngine(
        db,
        plugin_overrides={
            "event.store": event_store,
            "memory.skills": skills,
            "evolver.harness": harness,
            "sandbox.action": sandbox,
        },
    )
    engine.autonomy.policy.routing = writer_profile.ProfiledRouting(db, profile)
    return engine, event_store


async def runtime_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, event_store = build_engine(
        root / f"runtime-{experiment}-{mode}.db",
        profile,
        mode,
    )
    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    profile.reset()
    event_store.reset_patch_metrics()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, RUNTIME_TASKS)
    wall = time.perf_counter() - started
    report = profile.report(RUNTIME_TASKS)
    stage = _stage(report, "event.evolution_patch")
    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")

    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": wall,
        "fingerprint_selects": event_store.patch_lookup_selects,
        "evolution_patch_transactions": int(stage["transactions"]) if stage else 0,
        "evolution_patch_operation_ms_total": (
            float(stage["operation_ms_total"]) if stage else 0.0
        ),
        "total_writer_transactions": int(report["total_transactions"]),
        "failures": failures,
    }


def _median(rows: list[dict[str, Any]], key: str) -> float:
    return float(statistics.median(float(row[key]) for row in rows))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    direct: dict[str, list[dict[str, Any]]] = {"baseline": [], "production": []}
    runtime: dict[str, list[dict[str, Any]]] = {"baseline": [], "production": []}

    with tempfile.TemporaryDirectory(prefix="ecomevo-patch-positive-cache-gate-") as tmp:
        root = Path(tmp)
        for experiment in range(DIRECT_EXPERIMENTS):
            order = (
                ("baseline", "production")
                if experiment % 2 == 0
                else ("production", "baseline")
            )
            for mode in order:
                direct[mode].append(direct_probe(root, mode, experiment))

        for experiment in range(RUNTIME_EXPERIMENTS):
            order = (
                ("baseline", "production")
                if experiment % 2 == 0
                else ("production", "baseline")
            )
            for mode in order:
                row = await runtime_probe(root, mode, experiment)
                runtime[mode].append(row)
                failures.extend(
                    f"runtime {mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    wall_ratios: list[float] = []
    for experiment in range(DIRECT_EXPERIMENTS):
        baseline = direct["baseline"][experiment]
        production = direct["production"][experiment]
        wall_ratios.append(
            production["wall_seconds"] / max(1e-9, baseline["wall_seconds"])
        )
        if baseline["fingerprint_selects"] != DUPLICATE_CALLS:
            failures.append(
                f"direct baseline[{experiment}] SELECT count "
                f"{baseline['fingerprint_selects']} != {DUPLICATE_CALLS}"
            )
        if production["fingerprint_selects"] != 0:
            failures.append(
                f"direct production[{experiment}] still queried SQLite "
                f"{production['fingerprint_selects']} times"
            )
        if baseline["writer_transactions"] or production["writer_transactions"]:
            failures.append(f"direct experiment {experiment} reintroduced patch writers")
        if baseline["unattributed_transactions"] or production["unattributed_transactions"]:
            failures.append(f"direct experiment {experiment} lost writer attribution")

    median_wall_ratio = float(statistics.median(wall_ratios))
    if median_wall_ratio > WALL_RATIO_LIMIT:
        failures.append(
            f"direct median wall ratio {median_wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
        )

    for experiment in range(RUNTIME_EXPERIMENTS):
        baseline = runtime["baseline"][experiment]
        production = runtime["production"][experiment]
        if baseline["fingerprint_selects"] != RUNTIME_TASKS:
            failures.append(
                f"runtime baseline[{experiment}] SELECT count "
                f"{baseline['fingerprint_selects']} != {RUNTIME_TASKS}"
            )
        if production["fingerprint_selects"] != 0:
            failures.append(
                f"runtime production[{experiment}] still queried SQLite "
                f"{production['fingerprint_selects']} times"
            )
        if baseline["evolution_patch_transactions"] != 0:
            failures.append(f"runtime baseline[{experiment}] unexpectedly used patch writer")
        if production["evolution_patch_transactions"] != 0:
            failures.append(f"runtime production[{experiment}] unexpectedly used patch writer")

    baseline_runtime_wall = _median(runtime["baseline"], "wall_seconds")
    production_runtime_wall = _median(runtime["production"], "wall_seconds")
    baseline_runtime_op = _median(runtime["baseline"], "evolution_patch_operation_ms_total")
    production_runtime_op = _median(runtime["production"], "evolution_patch_operation_ms_total")

    return {
        "ok": not failures,
        "duplicate_calls": DUPLICATE_CALLS,
        "direct_experiments": DIRECT_EXPERIMENTS,
        "runtime_tasks": RUNTIME_TASKS,
        "runtime_experiments": RUNTIME_EXPERIMENTS,
        "wall_ratio_limit": WALL_RATIO_LIMIT,
        "direct": direct,
        "direct_wall_ratios": [round(value, 4) for value in wall_ratios],
        "median_direct_wall_ratio": round(median_wall_ratio, 4),
        "runtime": runtime,
        "runtime_comparison": {
            "median_wall_ratio": round(
                production_runtime_wall / max(1e-9, baseline_runtime_wall), 4
            ),
            "median_patch_operation_ratio": round(
                production_runtime_op / max(0.001, baseline_runtime_op), 4
            ),
            "baseline_median_patch_selects": _median(
                runtime["baseline"], "fingerprint_selects"
            ),
            "production_median_patch_selects": _median(
                runtime["production"], "fingerprint_selects"
            ),
        },
        "failures": failures,
    }


def main() -> int:
    report = asyncio.run(main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
