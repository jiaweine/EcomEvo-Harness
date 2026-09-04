from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import writer_profile_gate as writer_profile
from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer


DOMAIN = "merchant_review"
TASKS = 64
RUNTIME_TASKS = 32
EXPERIMENTS = 3


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return float(ordered[index])


class TracingHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path: Path):
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
                elif normalized.startswith("UPDATE HARNESS_COMPONENTS SET ALPHA="):
                    self.component_updates += 1
                elif normalized.startswith("INSERT INTO HARNESS_COMPONENT_OUTCOMES"):
                    self.outcome_inserts += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_trace(self) -> None:
        with self._trace_lock:
            self.immediate_begins = 0
            self.component_updates = 0
            self.outcome_inserts = 0


class SetBasedOutcomeHarness(TracingHarness):
    def record_outcome(
        self,
        domain: str,
        component_ids: Iterable[str],
        *,
        verifier_score: float,
        evidence_complete: bool,
        session_id: str | None,
        meta: dict[str, Any] | None = None,
        evidence_completeness: float | None = None,
    ) -> list[dict[str, Any]]:
        q = max(0.0, min(1.0, float(verifier_score)))
        completeness = (
            max(0.0, min(1.0, float(evidence_completeness)))
            if evidence_completeness is not None
            else (1.0 if evidence_complete else q)
        )
        reward = self.verifier_potential(q, completeness)
        now = time.time()
        ids = list(dict.fromkeys(str(value) for value in component_ids if str(value)))
        outcome_meta = {
            **(meta or {}),
            "raw_verifier_score": q,
            "evidence_completeness": completeness,
            "reward": reward,
            "reward_method": "verifier_harmonic_potential",
        }
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    "UPDATE harness_components "
                    "SET alpha=alpha+?,beta=beta+?,uses=uses+1,updated_at=? "
                    f"WHERE domain=? AND component_id IN ({placeholders})",
                    (reward, 1.0 - reward, now, domain, *ids),
                )
                connection.execute(
                    "INSERT INTO harness_component_outcomes("
                    "component_id,session_id,verifier_score,evidence_complete,meta_json,created_at) "
                    "SELECT component_id,?,?,?,?,? FROM harness_components "
                    f"WHERE domain=? AND component_id IN ({placeholders})",
                    (
                        session_id,
                        reward,
                        int(bool(evidence_complete)),
                        json.dumps(outcome_meta, ensure_ascii=False, default=str),
                        now,
                        domain,
                        *ids,
                    ),
                )
            return self._transition_shadows(connection, domain, now)


def _selected_projection(harness: BundledHarnessEvolutionOptimizer, ids: list[str]) -> list[dict[str, Any]]:
    selected = set(ids)
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


async def measure_no_shadow(
    root: Path,
    mode: str,
    experiment: int,
) -> dict[str, Any]:
    store_type = SetBasedOutcomeHarness if mode == "set_based" else TracingHarness
    harness = store_type(root / f"{mode}-{experiment}.db")
    profile = harness.profile(DOMAIN, session_key=f"{mode}-{experiment}-seed")
    component_ids = list(profile.get("component_ids") or [])
    if len(component_ids) != len(harness.KINDS):
        raise AssertionError("failed to initialize full harness profile")
    harness.reset_trace()

    latencies_ms: list[float] = []
    transitions_seen: list[dict[str, Any]] = []

    async def one(index: int) -> None:
        started = time.perf_counter()
        transitions = await harness.record_outcome_async(
            DOMAIN,
            component_ids,
            verifier_score=0.82,
            evidence_complete=True,
            session_id=f"{mode}-{experiment}-{index}",
            meta={"probe": "harness-outcome-fusion"},
        )
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        transitions_seen.extend(transitions)

    started = time.perf_counter()
    await asyncio.gather(*(one(index) for index in range(TASKS)))
    wall = time.perf_counter() - started
    projection = _selected_projection(harness, component_ids)

    failures: list[str] = []
    if transitions_seen:
        failures.append("unexpected transition in no-shadow A/B")
    if harness.immediate_begins != TASKS:
        failures.append(f"writer transactions changed: {harness.immediate_begins} != {TASKS}")
    if any(row["uses"] != TASKS for row in projection):
        failures.append("selected component uses do not equal completed outcome count")

    return {
        "mode": mode,
        "tasks": TASKS,
        "writer_transactions": harness.immediate_begins,
        "component_update_statements": harness.component_updates,
        "outcome_insert_statements": harness.outcome_inserts,
        "wall_seconds": round(wall, 4),
        "completion_ms": {
            "p50": round(percentile(latencies_ms, 0.50), 3),
            "p95": round(percentile(latencies_ms, 0.95), 3),
            "p99": round(percentile(latencies_ms, 0.99), 3),
        },
        "projection": projection,
        "failures": failures,
    }


def _catalog() -> list[dict[str, Any]]:
    return [
        {
            "tool": "merchant.inspect",
            "mode": "read-only",
            "purpose": "read merchant identity authorization evidence",
            "evidence_tags": ["merchant_identity", "authorization"],
            "cost": 1.0,
        },
        {
            "tool": "evidence.search",
            "mode": "read-only",
            "purpose": "search supplied evidence",
            "evidence_tags": ["merchant_identity"],
            "cost": 0.6,
        },
    ]


def _transition_signature(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "kind": row.get("kind"),
            "transition": row.get("transition"),
            "probability_superior": row.get("probability_superior"),
            "candidate_exposures": row.get("candidate_exposures"),
            "incumbent_exposures": row.get("incumbent_exposures"),
        }
        for row in rows
    ]


async def shadow_semantics(root: Path, mode: str) -> dict[str, Any]:
    store_type = SetBasedOutcomeHarness if mode == "set_based" else TracingHarness
    harness = store_type(root / f"shadow-{mode}.db")
    candidate = await harness.propose(
        DOMAIN,
        trajectory={
            "goal": "review merchant identity and authorization",
            "missing": ["merchant identity", "authorization evidence"],
        },
        tool_catalog=_catalog(),
        reasoner=None,
    )
    if candidate is None:
        raise AssertionError("failed to seed shadow candidate")

    promotion_round: int | None = None
    transition_signature: list[dict[str, Any]] = []
    for index in range(40):
        await harness.record_outcome_async(
            DOMAIN,
            [candidate["parent_id"]],
            verifier_score=0.05,
            evidence_complete=False,
            session_id=f"{mode}-parent-{index}",
        )
        transitions = await harness.record_outcome_async(
            DOMAIN,
            [candidate["component_id"]],
            verifier_score=0.95,
            evidence_complete=True,
            session_id=f"{mode}-candidate-{index}",
        )
        if transitions:
            transition_signature = _transition_signature(transitions)
        if any(row.get("transition") == "promoted" for row in transitions):
            promotion_round = index
            break

    snapshot = harness.snapshot(DOMAIN)
    statuses = Counter(
        (str(row["kind"]), str(row["status"]))
        for row in snapshot.get("components", [])
    )
    return {
        "mode": mode,
        "promotion_round": promotion_round,
        "transition_signature": transition_signature,
        "status_counts": {
            f"{kind}:{status}": count
            for (kind, status), count in sorted(statuses.items())
        },
    }


class RuntimeDiagnosticHarness(writer_profile.ProfiledHarness):
    def __init__(self, path: Path, profile: writer_profile.WriterProfile, *, sandbox=None):
        self._diagnostic_lock = threading.RLock()
        self._outcome_calls = 0
        self._shadow_before = 0
        self._selected_shadow_calls = 0
        self._selected_shadow_ids = 0
        self._transition_counts: Counter[str] = Counter()
        super().__init__(path, profile, sandbox=sandbox)

    def reset_diagnostics(self) -> None:
        with self._diagnostic_lock:
            self._outcome_calls = 0
            self._shadow_before = 0
            self._selected_shadow_calls = 0
            self._selected_shadow_ids = 0
            self._transition_counts.clear()

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
        transitions = super().record_outcome(domain, ids, **kwargs)
        with self._diagnostic_lock:
            self._outcome_calls += 1
            self._shadow_before += int(has_shadow)
            self._selected_shadow_calls += int(selected_shadow > 0)
            self._selected_shadow_ids += selected_shadow
            self._transition_counts.update(
                str(row.get("transition") or "unknown") for row in transitions
            )
        return transitions

    def diagnostics(self) -> dict[str, Any]:
        with self._diagnostic_lock:
            return {
                "outcome_calls": self._outcome_calls,
                "calls_with_shadow_before": self._shadow_before,
                "calls_selecting_shadow": self._selected_shadow_calls,
                "selected_shadow_ids": self._selected_shadow_ids,
                "transition_counts": dict(sorted(self._transition_counts.items())),
            }


def _build_runtime_engine(db: Path, profile: writer_profile.WriterProfile):
    sandbox = writer_profile.ActionSandbox()
    events = writer_profile.ProfiledEventStore(db, profile)
    skills = writer_profile.ProfiledSkills(db, profile)
    harness = RuntimeDiagnosticHarness(db, profile, sandbox=sandbox)
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


async def runtime_shadow_probe(root: Path) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, harness = _build_runtime_engine(root / "runtime-shadow.db", profile)
    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError("warm-up runtime chain invalid")
    harness.reset_diagnostics()
    profile.reset()
    summaries = await writer_profile._run_batch(engine, RUNTIME_TASKS)
    report = profile.report(RUNTIME_TASKS)
    harness_stage = next(
        (row for row in report["stages"] if row["stage"] == "harness.outcome"),
        None,
    )
    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime shadow probe produced an invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime shadow probe lost writer attribution")
    return {
        "tasks": RUNTIME_TASKS,
        "harness_outcome_writer_transactions": int(harness_stage["transactions"])
        if harness_stage
        else 0,
        "harness_outcome_writer_hold_ms_total": float(harness_stage["writer_hold_ms_total"])
        if harness_stage
        else 0.0,
        "harness_outcome_writer_hold_ms_p99": float(harness_stage["writer_hold_ms_p99"])
        if harness_stage
        else 0.0,
        "diagnostics": harness.diagnostics(),
        "unattributed_transactions": int(report["unattributed_transactions"]),
        "failures": failures,
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    results: dict[str, list[dict[str, Any]]] = {"baseline": [], "set_based": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-harness-outcome-fusion-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = ("baseline", "set_based") if experiment % 2 == 0 else ("set_based", "baseline")
            for mode in order:
                row = await measure_no_shadow(root, mode, experiment)
                results[mode].append(row)
                failures.extend(f"{mode}[{experiment}]: {item}" for item in row["failures"])

        baseline_shadow = await shadow_semantics(root, "baseline")
        set_based_shadow = await shadow_semantics(root, "set_based")
        runtime = await runtime_shadow_probe(root)
        failures.extend(f"runtime: {item}" for item in runtime["failures"])

    for experiment in range(EXPERIMENTS):
        if results["baseline"][experiment]["projection"] != results["set_based"][experiment]["projection"]:
            failures.append(f"no-shadow component projection diverged in experiment {experiment}")
    if baseline_shadow["promotion_round"] != set_based_shadow["promotion_round"]:
        failures.append("shadow promotion round changed under set-based outcome persistence")
    if baseline_shadow["transition_signature"] != set_based_shadow["transition_signature"]:
        failures.append("shadow transition evidence changed under set-based outcome persistence")
    if baseline_shadow["status_counts"] != set_based_shadow["status_counts"]:
        failures.append("shadow final status projection changed under set-based outcome persistence")

    baseline_wall = statistics.median(row["wall_seconds"] for row in results["baseline"])
    set_based_wall = statistics.median(row["wall_seconds"] for row in results["set_based"])
    baseline_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["baseline"])
    set_based_p99 = statistics.median(row["completion_ms"]["p99"] for row in results["set_based"])
    baseline_updates = statistics.median(row["component_update_statements"] for row in results["baseline"])
    set_based_updates = statistics.median(row["component_update_statements"] for row in results["set_based"])
    baseline_inserts = statistics.median(row["outcome_insert_statements"] for row in results["baseline"])
    set_based_inserts = statistics.median(row["outcome_insert_statements"] for row in results["set_based"])

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
            "component_update_statement_ratio": round(set_based_updates / max(1, baseline_updates), 4),
            "outcome_insert_statement_ratio": round(set_based_inserts / max(1, baseline_inserts), 4),
            "median_wall_ratio": round(set_based_wall / max(0.0001, baseline_wall), 4),
            "median_p99_ratio": round(set_based_p99 / max(0.001, baseline_p99), 4),
        },
        "shadow_semantics": {
            "baseline": baseline_shadow,
            "set_based": set_based_shadow,
        },
        "runtime_shadow_probe": runtime,
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
