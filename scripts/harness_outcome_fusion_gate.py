from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer
from ecomevo.runtime.harness_optimizer import HarnessEvolutionOptimizer


TASKS = 64
EXPERIMENTS = 3
DOMAIN = "merchant_review"
WALL_RATIO_LIMIT = 1.10
P99_RATIO_LIMIT = 1.10


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return float(ordered[index])


class TraceMixin:
    def __init__(self, path):
        self.immediate_begins = 0
        self.component_updates = 0
        self.outcome_inserts = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            normalized = statement.strip().upper()
            with self._trace_lock:
                if normalized.startswith("BEGIN IMMEDIATE"):
                    self.immediate_begins += 1
                if normalized.startswith("UPDATE HARNESS_COMPONENTS SET ALPHA="):
                    self.component_updates += 1
                if "INSERT INTO HARNESS_COMPONENT_OUTCOMES" in normalized:
                    self.outcome_inserts += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_trace(self) -> None:
        with self._trace_lock:
            self.immediate_begins = 0
            self.component_updates = 0
            self.outcome_inserts = 0


class LegacyHarness(TraceMixin, BundledHarnessEvolutionOptimizer):
    def record_outcome(self, domain, component_ids, **kwargs):
        return HarnessEvolutionOptimizer.record_outcome(
            self,
            domain,
            component_ids,
            **kwargs,
        )


class ProductionHarness(TraceMixin, BundledHarnessEvolutionOptimizer):
    pass


def projection(harness, component_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(component_ids)
    rows = [
        row
        for row in harness.snapshot(DOMAIN).get("components", [])
        if row.get("component_id") in selected
    ]
    rows.sort(key=lambda row: str(row["kind"]))
    return [
        {
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "uses": int(row["uses"]),
            "alpha": round(float(row["alpha"]), 8),
            "beta": round(float(row["beta"]), 8),
            "generation": int(row["generation"]),
        }
        for row in rows
    ]


async def measure(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    harness_type = LegacyHarness if mode == "legacy" else ProductionHarness
    harness = harness_type(root / f"{mode}-{experiment}.db")
    profile = harness.profile(DOMAIN, session_key=f"{mode}-{experiment}-seed")
    component_ids = list(profile.get("component_ids") or [])
    if len(component_ids) != len(harness.KINDS):
        raise AssertionError("failed to initialize Harness profile")
    harness.reset_trace()

    latencies_ms: list[float] = []
    transitions: list[dict[str, Any]] = []

    async def one(index: int) -> None:
        started = time.perf_counter()
        result = await harness.record_outcome_async(
            DOMAIN,
            component_ids,
            verifier_score=0.82,
            evidence_complete=True,
            session_id=f"{mode}-{experiment}-{index}",
            meta={"gate": "harness-outcome-fusion"},
        )
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        transitions.extend(result)

    started = time.perf_counter()
    await asyncio.gather(*(one(index) for index in range(TASKS)))
    wall = time.perf_counter() - started

    failures: list[str] = []
    if transitions:
        failures.append("unexpected transition without shadow")
    if harness.immediate_begins != TASKS:
        failures.append(
            f"writer transactions changed: {harness.immediate_begins} != {TASKS}"
        )

    return {
        "mode": mode,
        "writer_transactions": harness.immediate_begins,
        "component_update_statements": harness.component_updates,
        "outcome_insert_statements": harness.outcome_inserts,
        "wall_seconds": round(wall, 4),
        "completion_p99_ms": round(percentile(latencies_ms, 0.99), 3),
        "projection": projection(harness, component_ids),
        "failures": failures,
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    results: dict[str, list[dict[str, Any]]] = {"legacy": [], "production": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-harness-outcome-fusion-gate-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = ("legacy", "production") if experiment % 2 == 0 else ("production", "legacy")
            for mode in order:
                row = await measure(root, mode, experiment)
                results[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    for experiment in range(EXPERIMENTS):
        if results["legacy"][experiment]["projection"] != results["production"][experiment]["projection"]:
            failures.append(f"component projection diverged in experiment {experiment}")

    legacy_updates = statistics.median(
        row["component_update_statements"] for row in results["legacy"]
    )
    production_updates = statistics.median(
        row["component_update_statements"] for row in results["production"]
    )
    legacy_inserts = statistics.median(
        row["outcome_insert_statements"] for row in results["legacy"]
    )
    production_inserts = statistics.median(
        row["outcome_insert_statements"] for row in results["production"]
    )
    legacy_wall = statistics.median(row["wall_seconds"] for row in results["legacy"])
    production_wall = statistics.median(row["wall_seconds"] for row in results["production"])
    legacy_p99 = statistics.median(row["completion_p99_ms"] for row in results["legacy"])
    production_p99 = statistics.median(row["completion_p99_ms"] for row in results["production"])

    update_ratio = production_updates / max(1.0, legacy_updates)
    insert_ratio = production_inserts / max(1.0, legacy_inserts)
    wall_ratio = production_wall / max(0.0001, legacy_wall)
    p99_ratio = production_p99 / max(0.001, legacy_p99)

    if update_ratio > 0.25:
        failures.append(f"component UPDATE fusion too small: ratio={update_ratio:.4f} > 0.25")
    if insert_ratio > 0.25:
        failures.append(f"outcome INSERT fusion too small: ratio={insert_ratio:.4f} > 0.25")
    if wall_ratio > WALL_RATIO_LIMIT:
        failures.append(
            f"outcome fusion wall regression: ratio={wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
        )
    if p99_ratio > P99_RATIO_LIMIT:
        failures.append(
            f"outcome fusion p99 regression: ratio={p99_ratio:.4f} > {P99_RATIO_LIMIT:.2f}"
        )

    return {
        "ok": not failures,
        "tasks": TASKS,
        "experiments": EXPERIMENTS,
        "results": results,
        "comparison": {
            "writer_transactions_equal": all(
                row["writer_transactions"] == TASKS
                for rows in results.values()
                for row in rows
            ),
            "component_update_statement_ratio": round(update_ratio, 4),
            "outcome_insert_statement_ratio": round(insert_ratio, 4),
            "median_wall_ratio": round(wall_ratio, 4),
            "median_p99_ratio": round(p99_ratio, 4),
        },
        "limits": {
            "wall_ratio": WALL_RATIO_LIMIT,
            "p99_ratio": P99_RATIO_LIMIT,
        },
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
