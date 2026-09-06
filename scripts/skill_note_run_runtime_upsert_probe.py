from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

import skill_note_run_upsert_probe as upsert_probe
import writer_profile_gate as writer_profile


TASKS = 32
EXPERIMENTS = 3


class UpsertProfiledSkills(writer_profile.ProfiledSkills):
    def _persist_learning_upsert(self, batch) -> None:
        if len(batch) != 1:
            raise RuntimeError("learning-bearing skill note runs must not be grouped")
        request = batch[0]
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            upsert_probe.UpsertSkills._adapt_policy_upsert(
                connection,
                request,
                now=time.time(),
            )

    def _persist_note_run_group(self, batch) -> None:
        if all(request.metadata_only for request in batch):
            return super()._persist_note_run_group(batch)
        return writer_profile._timed(
            self._writer_profile,
            "skills.note_run",
            lambda: self._persist_learning_upsert(batch),
        )


def build_engine(db: Path, profile: writer_profile.WriterProfile, mode: str):
    sandbox = writer_profile.ActionSandbox()
    events = writer_profile.ProfiledEventStore(db, profile)
    skills = (
        UpsertProfiledSkills(db, profile)
        if mode == "upsert"
        else writer_profile.ProfiledSkills(db, profile)
    )
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
    return engine, skills


def stage_from(report: dict[str, Any], stage_name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == stage_name), None)


async def measure(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, skills = build_engine(root / f"runtime-{mode}-{experiment}.db", profile, mode)

    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    before_policy = skills.policy("merchant_review")
    profile.reset()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, TASKS)
    wall = time.perf_counter() - started
    after_policy = skills.policy("merchant_review")
    report = profile.report(TASKS)
    stage = stage_from(report, "skills.note_run")

    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")
    if stage is None:
        failures.append("runtime lost skills.note_run stage")
    elif int(stage["transactions"]) != TASKS:
        failures.append(
            f"skills.note_run transactions changed: {stage['transactions']} != {TASKS}"
        )

    policy = {
        "promotion_threshold": round(float(after_policy["promotion_threshold"]), 12),
        "retirement_threshold": round(float(after_policy["retirement_threshold"]), 12),
        "exploration": round(float(after_policy["exploration"]), 12),
        "updates_delta": int(after_policy["updates"]) - int(before_policy["updates"]),
    }
    if policy["updates_delta"] != TASKS:
        failures.append(
            f"runtime policy update count changed: {policy['updates_delta']} != {TASKS}"
        )

    return {
        "mode": mode,
        "experiment": experiment,
        "tasks": TASKS,
        "wall_seconds": round(wall, 4),
        "throughput_tasks_per_second": round(TASKS / wall, 3) if wall else 0.0,
        "total_writer_transactions": int(report["total_transactions"]),
        "transactions_per_task": float(report["transactions_per_task"]),
        "skills_note_run": {
            "transactions": int(stage["transactions"]) if stage else 0,
            "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
            "writer_hold_ms_p50": float(stage["writer_hold_ms_p50"]) if stage else 0.0,
            "writer_hold_ms_p95": float(stage["writer_hold_ms_p95"]) if stage else 0.0,
            "writer_hold_ms_p99": float(stage["writer_hold_ms_p99"]) if stage else 0.0,
            "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
            "operation_ms_p95": float(stage["operation_ms_p95"]) if stage else 0.0,
        },
        "policy": policy,
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
    results: dict[str, list[dict[str, Any]]] = {"baseline": [], "upsert": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-skill-runtime-upsert-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = ("baseline", "upsert") if experiment % 2 == 0 else ("upsert", "baseline")
            for mode in order:
                row = await measure(root, mode, experiment)
                results[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    for experiment in range(EXPERIMENTS):
        if results["baseline"][experiment]["policy"] != results["upsert"][experiment]["policy"]:
            failures.append(f"runtime policy diverged in experiment {experiment}")

    baseline_hold = median_metric(results["baseline"], "skills_note_run", "writer_hold_ms_total")
    upsert_hold = median_metric(results["upsert"], "skills_note_run", "writer_hold_ms_total")
    baseline_p95 = median_metric(results["baseline"], "skills_note_run", "writer_hold_ms_p95")
    upsert_p95 = median_metric(results["upsert"], "skills_note_run", "writer_hold_ms_p95")
    baseline_p99 = median_metric(results["baseline"], "skills_note_run", "writer_hold_ms_p99")
    upsert_p99 = median_metric(results["upsert"], "skills_note_run", "writer_hold_ms_p99")
    baseline_op = median_metric(results["baseline"], "skills_note_run", "operation_ms_total")
    upsert_op = median_metric(results["upsert"], "skills_note_run", "operation_ms_total")
    baseline_wall = median_metric(results["baseline"], "wall_seconds")
    upsert_wall = median_metric(results["upsert"], "wall_seconds")
    baseline_tx = median_metric(results["baseline"], "total_writer_transactions")
    upsert_tx = median_metric(results["upsert"], "total_writer_transactions")

    return {
        "ok": not failures,
        "tasks": TASKS,
        "experiments": EXPERIMENTS,
        "results": results,
        "comparison": {
            "skills_writer_hold_total_ratio": round(upsert_hold / max(0.001, baseline_hold), 4),
            "skills_writer_hold_p95_ratio": round(upsert_p95 / max(0.001, baseline_p95), 4),
            "skills_writer_hold_p99_ratio": round(upsert_p99 / max(0.001, baseline_p99), 4),
            "skills_operation_total_ratio": round(upsert_op / max(0.001, baseline_op), 4),
            "runtime_wall_ratio": round(upsert_wall / max(0.0001, baseline_wall), 4),
            "total_writer_transaction_ratio": round(upsert_tx / max(1.0, baseline_tx), 4),
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
