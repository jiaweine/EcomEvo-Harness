from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary, _QueuedNoteRun


DOMAIN = "merchant_review"
TASKS = 64
EXPERIMENTS = 3


class TraceMixin:
    def __init__(self, path):
        self.immediate_begins = 0
        self.policy_inserts = 0
        self.policy_selects = 0
        self.policy_updates = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)
        self.reset_trace()

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            normalized = statement.strip().upper()
            with self._trace_lock:
                if normalized.startswith("BEGIN IMMEDIATE"):
                    self.immediate_begins += 1
                if normalized.startswith("INSERT") and "EVOLUTION_POLICY" in normalized:
                    self.policy_inserts += 1
                if normalized.startswith("SELECT") and "EVOLUTION_POLICY" in normalized:
                    self.policy_selects += 1
                if normalized.startswith("UPDATE EVOLUTION_POLICY"):
                    self.policy_updates += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_trace(self) -> None:
        with self._trace_lock:
            self.immediate_begins = 0
            self.policy_inserts = 0
            self.policy_selects = 0
            self.policy_updates = 0

    def trace_snapshot(self) -> dict[str, int]:
        with self._trace_lock:
            return {
                "writer_transactions": self.immediate_begins,
                "policy_inserts": self.policy_inserts,
                "policy_selects": self.policy_selects,
                "policy_updates": self.policy_updates,
            }


class LegacySkills(TraceMixin, BundledAdaptiveSkillLibrary):
    pass


class UpsertSkills(TraceMixin, BundledAdaptiveSkillLibrary):
    @staticmethod
    def _adapt_policy_upsert(connection, request: _QueuedNoteRun, *, now: float) -> None:
        success = bool(request.success)
        skill_used = bool(request.skill_used)

        if skill_used and not success:
            initial_promotion = 0.928
            initial_retirement = 0.455
            initial_exploration = 0.64
        elif skill_used and success:
            initial_promotion = 0.917
            initial_retirement = 0.448
            initial_exploration = 0.58
        elif not skill_used and not success:
            initial_promotion = 0.92
            initial_retirement = 0.45
            initial_exploration = 0.625
        else:  # pragma: no cover - metadata-only requests delegate to the existing path
            initial_promotion = 0.92
            initial_retirement = 0.45
            initial_exploration = 0.60

        connection.execute(
            "INSERT INTO evolution_policy("
            "domain,promotion_threshold,retirement_threshold,exploration,updates,updated_at"
            ") VALUES(?,?,?,?,1,?) "
            "ON CONFLICT(domain) DO UPDATE SET "
            "promotion_threshold=CASE "
            "WHEN ? AND NOT ? THEN MIN(0.98,evolution_policy.promotion_threshold+0.008) "
            "WHEN ? AND ? THEN MAX(0.90,evolution_policy.promotion_threshold-0.003) "
            "ELSE evolution_policy.promotion_threshold END,"
            "retirement_threshold=CASE "
            "WHEN ? AND NOT ? THEN MIN(0.55,evolution_policy.retirement_threshold+0.005) "
            "WHEN ? AND ? THEN MAX(0.40,evolution_policy.retirement_threshold-0.002) "
            "ELSE evolution_policy.retirement_threshold END,"
            "exploration=CASE "
            "WHEN ? AND NOT ? THEN MIN(0.90,evolution_policy.exploration+0.04) "
            "WHEN ? AND ? THEN MAX(0.25,evolution_policy.exploration-0.02) "
            "WHEN NOT ? AND NOT ? THEN MIN(0.90,evolution_policy.exploration+0.025) "
            "ELSE evolution_policy.exploration END,"
            "updates=evolution_policy.updates+1,updated_at=excluded.updated_at",
            (
                request.domain,
                initial_promotion,
                initial_retirement,
                initial_exploration,
                now,
                int(skill_used),
                int(success),
                int(skill_used),
                int(success),
                int(skill_used),
                int(success),
                int(skill_used),
                int(success),
                int(skill_used),
                int(success),
                int(skill_used),
                int(success),
                int(skill_used),
                int(success),
            ),
        )

    def _persist_note_run_group(self, batch: list[_QueuedNoteRun]) -> None:
        if all(request.metadata_only for request in batch):
            return super()._persist_note_run_group(batch)
        if len(batch) != 1:
            raise RuntimeError("learning-bearing skill note runs must not be grouped")
        request = batch[0]
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._adapt_policy_upsert(connection, request, now=time.time())


def policy_projection(skills: BundledAdaptiveSkillLibrary) -> dict[str, Any]:
    row = skills.policy(DOMAIN)
    return {
        "promotion_threshold": round(float(row["promotion_threshold"]), 12),
        "retirement_threshold": round(float(row["retirement_threshold"]), 12),
        "exploration": round(float(row["exploration"]), 12),
        "updates": int(row["updates"]),
    }


async def semantic_sequence(root: Path, mode: str) -> dict[str, Any]:
    skills_type = LegacySkills if mode == "legacy" else UpsertSkills
    skills = skills_type(root / f"semantic-{mode}.db")
    sequence = [
        (False, False),
        (False, True),
        (True, True),
        (False, False),
        (True, True),
        (False, True),
        *[(False, False)] * 16,
        *[(False, True)] * 20,
        *[(True, True)] * 40,
    ]
    states: list[dict[str, Any]] = []
    updated_at_values: list[float] = []
    for success, skill_used in sequence:
        await skills.note_run_async(
            DOMAIN,
            success=success,
            skill_used=skill_used,
        )
        state = policy_projection(skills)
        states.append(state)
        updated_at_values.append(float(skills.policy(DOMAIN)["updated_at"]))

    monotonic = all(
        later >= earlier
        for earlier, later in zip(updated_at_values, updated_at_values[1:])
    )
    return {
        "mode": mode,
        "steps": len(sequence),
        "states": states,
        "updated_at_monotonic": monotonic,
        "final": states[-1],
    }


async def measure(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    skills_type = LegacySkills if mode == "legacy" else UpsertSkills
    skills = skills_type(root / f"perf-{mode}-{experiment}.db")
    # Pre-initialize policy so this measures the steady-state learning path used by runtime.
    skills.policy(DOMAIN)
    skills.reset_trace()

    latencies_ms: list[float] = []

    async def one() -> None:
        started = time.perf_counter()
        await skills.note_run_async(DOMAIN, success=False, skill_used=False)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(TASKS)))
    wall = time.perf_counter() - started
    trace = skills.trace_snapshot()
    final = policy_projection(skills)

    failures: list[str] = []
    if trace["writer_transactions"] != TASKS:
        failures.append(
            f"writer transactions changed: {trace['writer_transactions']} != {TASKS}"
        )
    if final["updates"] != TASKS:
        failures.append(f"policy updates changed: {final['updates']} != {TASKS}")
    expected_exploration = min(0.90, 0.60 + TASKS * 0.025)
    if abs(final["exploration"] - expected_exploration) > 1e-12:
        failures.append(
            f"exploration mismatch: {final['exploration']} != {expected_exploration}"
        )

    ordered = sorted(latencies_ms)
    p99 = ordered[max(0, min(len(ordered) - 1, round((len(ordered) - 1) * 0.99)))]
    return {
        "mode": mode,
        "experiment": experiment,
        **trace,
        "wall_seconds": round(wall, 4),
        "completion_p99_ms": round(float(p99), 3),
        "final": final,
        "failures": failures,
    }


def median(rows: list[dict[str, Any]], key: str) -> float:
    return float(statistics.median(float(row[key]) for row in rows))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    results: dict[str, list[dict[str, Any]]] = {"legacy": [], "upsert": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-skill-note-run-upsert-") as tmp:
        root = Path(tmp)
        legacy_semantics = await semantic_sequence(root, "legacy")
        upsert_semantics = await semantic_sequence(root, "upsert")
        if legacy_semantics["states"] != upsert_semantics["states"]:
            failures.append("policy state diverged during mixed learning sequence")
        if not legacy_semantics["updated_at_monotonic"] or not upsert_semantics["updated_at_monotonic"]:
            failures.append("updated_at stopped being monotonic")

        for experiment in range(EXPERIMENTS):
            order = ("legacy", "upsert") if experiment % 2 == 0 else ("upsert", "legacy")
            for mode in order:
                row = await measure(root, mode, experiment)
                results[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    for experiment in range(EXPERIMENTS):
        if results["legacy"][experiment]["final"] != results["upsert"][experiment]["final"]:
            failures.append(f"final policy diverged in experiment {experiment}")

    legacy_wall = median(results["legacy"], "wall_seconds")
    upsert_wall = median(results["upsert"], "wall_seconds")
    legacy_p99 = median(results["legacy"], "completion_p99_ms")
    upsert_p99 = median(results["upsert"], "completion_p99_ms")
    legacy_statements = statistics.median(
        row["policy_inserts"] + row["policy_selects"] + row["policy_updates"]
        for row in results["legacy"]
    )
    upsert_statements = statistics.median(
        row["policy_inserts"] + row["policy_selects"] + row["policy_updates"]
        for row in results["upsert"]
    )

    return {
        "ok": not failures,
        "tasks": TASKS,
        "experiments": EXPERIMENTS,
        "semantic_sequence": {
            "legacy_final": legacy_semantics["final"],
            "upsert_final": upsert_semantics["final"],
            "states_equal": legacy_semantics["states"] == upsert_semantics["states"],
            "steps": legacy_semantics["steps"],
        },
        "results": results,
        "comparison": {
            "writer_transactions_equal": all(
                row["writer_transactions"] == TASKS
                for rows in results.values()
                for row in rows
            ),
            "policy_statement_ratio": round(float(upsert_statements) / max(1.0, float(legacy_statements)), 4),
            "median_wall_ratio": round(upsert_wall / max(0.0001, legacy_wall), 4),
            "median_p99_ratio": round(upsert_p99 / max(0.001, legacy_p99), 4),
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
