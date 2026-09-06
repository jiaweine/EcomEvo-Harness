from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import harness_outcome_fusion_probe as fusion
import writer_profile_gate as writer_profile


EXPERIMENTS = 3
TASKS = 32


class SetBasedRuntimeDiagnosticHarness(fusion.RuntimeDiagnosticHarness):
    def record_outcome(self, domain, component_ids, **kwargs):
        ids = list(component_ids)
        selected_shadow = 0
        with self._conn() as connection:
            has_shadow = self._has_shadow(connection, str(domain))
            if ids:
                placeholders = ",".join("?" for _ in ids)
                rows = connection.execute(
                    "SELECT status FROM harness_components "
                    f"WHERE component_id IN ({placeholders})",
                    ids,
                ).fetchall()
                selected_shadow = sum(1 for row in rows if str(row["status"]) == "shadow")

        transitions = writer_profile._timed(
            self._writer_profile,
            "harness.outcome",
            lambda: fusion.SetBasedOutcomeHarness.record_outcome(
                self,
                domain,
                ids,
                **kwargs,
            ),
        )
        with self._diagnostic_lock:
            self._outcome_calls += 1
            self._shadow_before += int(has_shadow)
            self._selected_shadow_calls += int(selected_shadow > 0)
            self._selected_shadow_ids += selected_shadow
            self._transition_counts.update(
                str(row.get("transition") or "unknown") for row in transitions
            )
        return transitions


def build_engine(db: Path, profile: writer_profile.WriterProfile, mode: str):
    sandbox = writer_profile.ActionSandbox()
    events = writer_profile.ProfiledEventStore(db, profile)
    skills = writer_profile.ProfiledSkills(db, profile)
    harness_type = (
        SetBasedRuntimeDiagnosticHarness
        if mode == "set_based"
        else fusion.RuntimeDiagnosticHarness
    )
    harness = harness_type(db, profile, sandbox=sandbox)
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
    return engine, harness


async def measure(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, harness = build_engine(root / f"runtime-{mode}-{experiment}.db", profile, mode)
    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    harness.reset_diagnostics()
    profile.reset()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, TASKS)
    wall = time.perf_counter() - started
    report = profile.report(TASKS)
    stage = next(
        (row for row in report["stages"] if row["stage"] == "harness.outcome"),
        None,
    )

    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")
    if stage is None:
        failures.append("runtime lost harness.outcome stage")
    elif int(stage["transactions"]) != TASKS:
        failures.append(
            f"harness.outcome transactions changed: {stage['transactions']} != {TASKS}"
        )

    return {
        "mode": mode,
        "experiment": experiment,
        "tasks": TASKS,
        "wall_seconds": round(wall, 4),
        "throughput_tasks_per_second": round(TASKS / wall, 3) if wall else 0.0,
        "total_writer_transactions": int(report["total_transactions"]),
        "transactions_per_task": float(report["transactions_per_task"]),
        "harness_outcome": {
            "transactions": int(stage["transactions"]) if stage else 0,
            "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
            "writer_hold_ms_p50": float(stage["writer_hold_ms_p50"]) if stage else 0.0,
            "writer_hold_ms_p95": float(stage["writer_hold_ms_p95"]) if stage else 0.0,
            "writer_hold_ms_p99": float(stage["writer_hold_ms_p99"]) if stage else 0.0,
            "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
            "operation_ms_p95": float(stage["operation_ms_p95"]) if stage else 0.0,
        },
        "diagnostics": harness.diagnostics(),
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
    results: dict[str, list[dict[str, Any]]] = {"baseline": [], "set_based": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-harness-runtime-fusion-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = ("baseline", "set_based") if experiment % 2 == 0 else ("set_based", "baseline")
            for mode in order:
                row = await measure(root, mode, experiment)
                results[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    baseline_hold = median_metric(results["baseline"], "harness_outcome", "writer_hold_ms_total")
    set_based_hold = median_metric(results["set_based"], "harness_outcome", "writer_hold_ms_total")
    baseline_p99 = median_metric(results["baseline"], "harness_outcome", "writer_hold_ms_p99")
    set_based_p99 = median_metric(results["set_based"], "harness_outcome", "writer_hold_ms_p99")
    baseline_op = median_metric(results["baseline"], "harness_outcome", "operation_ms_total")
    set_based_op = median_metric(results["set_based"], "harness_outcome", "operation_ms_total")
    baseline_wall = median_metric(results["baseline"], "wall_seconds")
    set_based_wall = median_metric(results["set_based"], "wall_seconds")
    baseline_tx = median_metric(results["baseline"], "total_writer_transactions")
    set_based_tx = median_metric(results["set_based"], "total_writer_transactions")

    return {
        "ok": not failures,
        "tasks": TASKS,
        "experiments": EXPERIMENTS,
        "results": results,
        "comparison": {
            "harness_writer_hold_total_ratio": round(set_based_hold / max(0.001, baseline_hold), 4),
            "harness_writer_hold_p99_ratio": round(set_based_p99 / max(0.001, baseline_p99), 4),
            "harness_operation_total_ratio": round(set_based_op / max(0.001, baseline_op), 4),
            "runtime_wall_ratio": round(set_based_wall / max(0.0001, baseline_wall), 4),
            "total_writer_transaction_ratio": round(set_based_tx / max(1.0, baseline_tx), 4),
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
