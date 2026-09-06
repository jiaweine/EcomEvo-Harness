from __future__ import annotations

import asyncio
import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

import writer_profile_gate as writer_profile
from ecomevo.models import EvolutionPatch
from ecomevo.runtime.event_store import EventStore


ISOLATED_DUPLICATES = 64
RUNTIME_TASKS = 32
EXPERIMENTS = 3


def patch(*, patch_id: str, value: str = "inspect") -> EvolutionPatch:
    return EvolutionPatch(
        patch_id=patch_id,
        created_at=time.time(),
        reason="gate",
        target="tool",
        patch={"preferred_tools": [value]},
        replay_cases=1,
        regression_before=0.5,
        regression_after=0.4,
        accepted=True,
    )


class BaselineProfiledEventStore(writer_profile.ProfiledEventStore):
    """Profile the unchanged base EventStore novelty path for the A/B control arm."""

    def save_patch_if_novel(self, *args, **kwargs):
        return writer_profile._timed(
            self._writer_profile,
            "event.evolution_patch",
            lambda: EventStore.save_patch_if_novel(self, *args, **kwargs),
        )


def stage_from(report: dict[str, Any], stage_name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == stage_name), None)


def semantic_probe(root: Path) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for mode, store_type in (
        ("baseline", BaselineProfiledEventStore),
        ("production", writer_profile.ProfiledEventStore),
    ):
        profile = writer_profile.WriterProfile()
        store = store_type(root / f"semantic-{mode}.db", profile)
        first = patch(patch_id=f"{mode}-one")
        duplicate = patch(patch_id=f"{mode}-two")
        assert store.save_patch_if_novel(first) is None
        existing = store.save_patch_if_novel(duplicate)
        assert existing is not None

        collision_error = None
        try:
            store.save_patch_if_novel(patch(patch_id=first.patch_id, value="different"))
        except sqlite3.IntegrityError as exc:
            collision_error = type(exc).__name__

        output[mode] = {
            "duplicate_returns_original": existing["patch_id"] == first.patch_id,
            "patch_id_collision_error": collision_error,
        }
    return output


def isolated_duplicate_probe(root: Path, mode: str) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    store_type = (
        BaselineProfiledEventStore
        if mode == "baseline"
        else writer_profile.ProfiledEventStore
    )
    store = store_type(root / f"isolated-{mode}.db", profile)
    first = patch(patch_id=f"{mode}-seed")
    assert store.save_patch_if_novel(first) is None

    profile.reset()
    started = time.perf_counter()
    for index in range(ISOLATED_DUPLICATES):
        existing = store.save_patch_if_novel(
            patch(patch_id=f"{mode}-duplicate-{index}")
        )
        if existing is None or existing["patch_id"] != first.patch_id:
            raise AssertionError("duplicate lookup stopped returning the stored patch")
    wall = time.perf_counter() - started
    report = profile.report(ISOLATED_DUPLICATES)
    stage = stage_from(report, "event.evolution_patch")
    return {
        "mode": mode,
        "writer_transactions": int(stage["transactions"]) if stage else 0,
        "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
        "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        "wall_seconds": round(wall, 4),
        "unattributed_transactions": int(report["unattributed_transactions"]),
    }


def build_engine(db: Path, profile: writer_profile.WriterProfile, mode: str):
    sandbox = writer_profile.ActionSandbox()
    event_store = (
        BaselineProfiledEventStore(db, profile)
        if mode == "baseline"
        else writer_profile.ProfiledEventStore(db, profile)
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
    return engine


async def runtime_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine = build_engine(root / f"runtime-{mode}-{experiment}.db", profile, mode)

    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    profile.reset()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, RUNTIME_TASKS)
    wall = time.perf_counter() - started
    report = profile.report(RUNTIME_TASKS)
    stage = stage_from(report, "event.evolution_patch")
    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")

    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": round(wall, 4),
        "total_writer_transactions": int(report["total_transactions"]),
        "evolution_patch_transactions": int(stage["transactions"]) if stage else 0,
        "evolution_patch_writer_hold_ms_total": (
            float(stage["writer_hold_ms_total"]) if stage else 0.0
        ),
        "evolution_patch_operation_ms_total": (
            float(stage["operation_ms_total"]) if stage else 0.0
        ),
        "failures": failures,
    }


def median(rows: list[dict[str, Any]], key: str) -> float:
    return float(statistics.median(float(row[key]) for row in rows))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    runtime: dict[str, list[dict[str, Any]]] = {"baseline": [], "production": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-evolution-patch-gate-") as tmp:
        root = Path(tmp)
        semantics = semantic_probe(root)
        if semantics["baseline"] != semantics["production"]:
            failures.append("production changed duplicate/collision semantics")

        isolated = {
            mode: isolated_duplicate_probe(root, mode)
            for mode in ("baseline", "production")
        }
        if isolated["baseline"]["writer_transactions"] != ISOLATED_DUPLICATES:
            failures.append(
                "baseline duplicate transaction count changed: "
                f"{isolated['baseline']['writer_transactions']} != {ISOLATED_DUPLICATES}"
            )
        if isolated["production"]["writer_transactions"] != 0:
            failures.append(
                "production duplicate fast path reserved writer transactions: "
                f"{isolated['production']['writer_transactions']} != 0"
            )
        if any(row["unattributed_transactions"] for row in isolated.values()):
            failures.append("isolated probe lost writer attribution")

        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "production")
                if experiment % 2 == 0
                else ("production", "baseline")
            )
            for mode in order:
                row = await runtime_probe(root, mode, experiment)
                runtime[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    for experiment in range(EXPERIMENTS):
        baseline_tx = runtime["baseline"][experiment]["evolution_patch_transactions"]
        production_tx = runtime["production"][experiment]["evolution_patch_transactions"]
        if baseline_tx != RUNTIME_TASKS:
            failures.append(
                f"baseline runtime patch tx changed in experiment {experiment}: "
                f"{baseline_tx} != {RUNTIME_TASKS}"
            )
        if production_tx != 0:
            failures.append(
                f"production runtime duplicate patch tx remained in experiment {experiment}: "
                f"{production_tx} != 0"
            )

    baseline_wall = median(runtime["baseline"], "wall_seconds")
    production_wall = median(runtime["production"], "wall_seconds")
    baseline_total_tx = median(runtime["baseline"], "total_writer_transactions")
    production_total_tx = median(runtime["production"], "total_writer_transactions")
    baseline_hold = median(
        runtime["baseline"], "evolution_patch_writer_hold_ms_total"
    )
    production_hold = median(
        runtime["production"], "evolution_patch_writer_hold_ms_total"
    )

    return {
        "ok": not failures,
        "isolated_duplicates": ISOLATED_DUPLICATES,
        "runtime_tasks": RUNTIME_TASKS,
        "experiments": EXPERIMENTS,
        "semantics": semantics,
        "isolated": isolated,
        "runtime": runtime,
        "comparison": {
            "isolated_writer_transaction_ratio": round(
                isolated["production"]["writer_transactions"]
                / max(1, isolated["baseline"]["writer_transactions"]),
                4,
            ),
            "runtime_evolution_patch_writer_hold_ratio": round(
                production_hold / max(0.001, baseline_hold), 4
            ),
            "runtime_total_writer_transaction_ratio": round(
                production_total_tx / max(1.0, baseline_total_tx), 4
            ),
            "runtime_wall_ratio": round(
                production_wall / max(0.0001, baseline_wall), 4
            ),
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
