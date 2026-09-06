from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import writer_profile_gate as writer_profile
from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary
from ecomevo.runtime.skills import AdaptiveSkillLibrary


DOMAIN = "merchant_review"
CAUSAL_ROUNDS = 64
RUNTIME_TASKS = 32
EXPERIMENTS = 3


class SkillMetricsMixin:
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._writer_profile = profile
        self._metric_lock = threading.Lock()
        self._policy_selects = 0
        self._policy_writes = 0
        self._note_run_calls = 0
        self._learning_calls = 0
        super().__init__(path)

    def reset_metrics(self) -> None:
        with self._metric_lock:
            self._policy_selects = 0
            self._policy_writes = 0
            self._note_run_calls = 0
            self._learning_calls = 0

    def _conn(self):
        connection = super()._conn()
        connection_id = id(connection)

        def trace(statement: str) -> None:
            self._writer_profile.trace(connection_id, statement)
            normalized = statement.strip().upper()
            if "EVOLUTION_POLICY" not in normalized:
                return
            with self._metric_lock:
                if normalized.startswith("SELECT"):
                    self._policy_selects += 1
                elif normalized.startswith(("INSERT", "UPDATE", "REPLACE", "DELETE")):
                    self._policy_writes += 1

        connection.set_trace_callback(trace)
        return connection

    async def note_run_async(
        self,
        domain: str,
        *,
        success: bool,
        skill_used: bool = False,
    ) -> None:
        with self._metric_lock:
            self._note_run_calls += 1
            if not (success and not skill_used):
                self._learning_calls += 1
        await super().note_run_async(
            domain,
            success=success,
            skill_used=skill_used,
        )

    def metrics(self) -> dict[str, int]:
        with self._metric_lock:
            return {
                "policy_selects": self._policy_selects,
                "policy_writes": self._policy_writes,
                "note_run_calls": self._note_run_calls,
                "learning_calls": self._learning_calls,
            }


class BaselineSkills(SkillMetricsMixin, BundledAdaptiveSkillLibrary):
    def _persist_note_run_group(self, batch) -> None:
        return writer_profile._timed(
            self._writer_profile,
            "skills.note_run",
            lambda: super(BaselineSkills, self)._persist_note_run_group(batch),
        )


class CasSkills(SkillMetricsMixin, BundledAdaptiveSkillLibrary):
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._cas_lock = threading.Lock()
        self._cas_attempts = 0
        self._cas_successes = 0
        self._cas_fallbacks = 0
        self._before_cas: Callable[[], None] | None = None
        super().__init__(path, profile)

    def reset_metrics(self) -> None:
        super().reset_metrics()
        with self._cas_lock:
            self._cas_attempts = 0
            self._cas_successes = 0
            self._cas_fallbacks = 0

    def metrics(self) -> dict[str, int]:
        result = super().metrics()
        with self._cas_lock:
            result.update(
                {
                    "cas_attempts": self._cas_attempts,
                    "cas_successes": self._cas_successes,
                    "cas_fallbacks": self._cas_fallbacks,
                }
            )
        return result

    @staticmethod
    def _next_values(row, *, success: bool, skill_used: bool) -> tuple[float, float, float]:
        promotion = float(row["promotion_threshold"])
        retirement = float(row["retirement_threshold"])
        exploration = float(row["exploration"])
        if skill_used and not success:
            promotion = min(0.98, promotion + 0.008)
            exploration = min(0.90, exploration + 0.04)
            retirement = min(0.55, retirement + 0.005)
        elif skill_used and success:
            promotion = max(0.90, promotion - 0.003)
            exploration = max(0.25, exploration - 0.02)
            retirement = max(0.40, retirement - 0.002)
        elif not skill_used and not success:
            exploration = min(0.90, exploration + 0.025)
        return promotion, retirement, exploration

    def _persist_note_run_group_cas(self, batch) -> None:
        # Keep the existing proven group path for state-independent metadata-only writes.
        if len(batch) != 1 or all(request.metadata_only for request in batch):
            return super(CasSkills, self)._persist_note_run_group(batch)

        request = batch[0]
        with self._conn() as read_connection:
            observed = read_connection.execute(
                "SELECT promotion_threshold,retirement_threshold,exploration,updates "
                "FROM evolution_policy WHERE domain=?",
                (request.domain,),
            ).fetchone()

        with self._cas_lock:
            self._cas_attempts += 1

        if observed is None:
            with self._cas_lock:
                self._cas_fallbacks += 1
            return super(CasSkills, self)._persist_note_run_group(batch)

        hook = self._before_cas
        self._before_cas = None
        if hook is not None:
            hook()

        promotion, retirement, exploration = self._next_values(
            observed,
            success=request.success,
            skill_used=request.skill_used,
        )
        now = time.time()
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE evolution_policy SET promotion_threshold=?,retirement_threshold=?,"
                "exploration=?,updates=updates+1,updated_at=? "
                "WHERE domain=? AND updates=? AND promotion_threshold=? "
                "AND retirement_threshold=? AND exploration=?",
                (
                    promotion,
                    retirement,
                    exploration,
                    now,
                    request.domain,
                    int(observed["updates"]),
                    float(observed["promotion_threshold"]),
                    float(observed["retirement_threshold"]),
                    float(observed["exploration"]),
                ),
            )
            if cursor.rowcount == 1:
                with self._cas_lock:
                    self._cas_successes += 1
                return

            # A stale snapshot or unexpected policy mutation is never overwritten.
            # Recompute from the latest row while retaining this writer transaction.
            with self._cas_lock:
                self._cas_fallbacks += 1
            self._adapt_policy_in_transaction(
                connection,
                request.domain,
                success=request.success,
                skill_used=request.skill_used,
                now=time.time(),
            )

    def _persist_note_run_group(self, batch) -> None:
        return writer_profile._timed(
            self._writer_profile,
            "skills.note_run",
            lambda: self._persist_note_run_group_cas(batch),
        )


def policy_projection(skills: AdaptiveSkillLibrary) -> dict[str, Any]:
    with skills._conn() as connection:
        row = connection.execute(
            "SELECT promotion_threshold,retirement_threshold,exploration,updates "
            "FROM evolution_policy WHERE domain=?",
            (DOMAIN,),
        ).fetchone()
    if row is None:
        return {}
    return {
        "promotion_threshold": float(row["promotion_threshold"]),
        "retirement_threshold": float(row["retirement_threshold"]),
        "exploration": float(row["exploration"]),
        "updates": int(row["updates"]),
    }


def stage_from(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == name), None)


async def semantic_probe(root: Path) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    baseline = BaselineSkills(root / "semantic-baseline.db", profile)
    cas = CasSkills(root / "semantic-cas.db", profile)
    baseline.policy(DOMAIN)
    cas.policy(DOMAIN)
    sequence = [
        (False, False),
        (False, True),
        (True, True),
        (True, False),
    ] * 16
    equal_each_step = True
    for success, skill_used in sequence:
        await baseline.note_run_async(DOMAIN, success=success, skill_used=skill_used)
        await cas.note_run_async(DOMAIN, success=success, skill_used=skill_used)
        if policy_projection(baseline) != policy_projection(cas):
            equal_each_step = False
            break
    return {
        "rounds": len(sequence),
        "states_equal_each_step": equal_each_step,
        "final": policy_projection(cas),
    }


async def stale_fallback_probe(root: Path) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    cas_path = root / "stale-cas.db"
    expected_path = root / "stale-expected.db"
    cas = CasSkills(cas_path, profile)
    external = AdaptiveSkillLibrary(cas_path)
    expected = AdaptiveSkillLibrary(expected_path)
    cas.policy(DOMAIN)
    expected.policy(DOMAIN)

    # Force the optimistic row stale between read and writer reservation.
    cas._before_cas = lambda: external.note_run(
        DOMAIN,
        success=True,
        skill_used=True,
    )
    await cas.note_run_async(DOMAIN, success=False, skill_used=False)

    expected.note_run(DOMAIN, success=True, skill_used=True)
    expected.note_run(DOMAIN, success=False, skill_used=False)
    return {
        "state_equal": policy_projection(cas) == policy_projection(expected),
        "metrics": cas.metrics(),
        "final": policy_projection(cas),
    }


async def causal_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    skills_type = BaselineSkills if mode == "baseline" else CasSkills
    skills = skills_type(root / f"causal-{mode}-{experiment}.db", profile)
    skills.policy(DOMAIN)
    profile.reset()
    skills.reset_metrics()

    started = time.perf_counter()
    for _ in range(CAUSAL_ROUNDS):
        await skills.note_run_async(DOMAIN, success=False, skill_used=False)
    wall = time.perf_counter() - started
    report = profile.report(CAUSAL_ROUNDS)
    stage = stage_from(report, "skills.note_run")
    metrics = skills.metrics()
    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": round(wall, 4),
        "transactions": int(stage["transactions"]) if stage else 0,
        "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
        "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        "policy_writes": int(metrics["policy_writes"]),
        "policy_selects": int(metrics["policy_selects"]),
        "cas_successes": int(metrics.get("cas_successes", 0)),
        "cas_fallbacks": int(metrics.get("cas_fallbacks", 0)),
        "final": policy_projection(skills),
    }


def build_engine(db: Path, profile: writer_profile.WriterProfile, mode: str):
    sandbox = writer_profile.ActionSandbox()
    events = writer_profile.ProfiledEventStore(db, profile)
    skills_type = BaselineSkills if mode == "baseline" else CasSkills
    skills = skills_type(db, profile)
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


async def runtime_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, skills = build_engine(root / f"runtime-{mode}-{experiment}.db", profile, mode)
    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    profile.reset()
    skills.reset_metrics()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, RUNTIME_TASKS)
    wall = time.perf_counter() - started
    report = profile.report(RUNTIME_TASKS)
    stage = stage_from(report, "skills.note_run")
    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")
    metrics = skills.metrics()
    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": round(wall, 4),
        "transactions": int(stage["transactions"]) if stage else 0,
        "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
        "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        "note_run_calls": int(metrics["note_run_calls"]),
        "learning_calls": int(metrics["learning_calls"]),
        "policy_writes": int(metrics["policy_writes"]),
        "cas_successes": int(metrics.get("cas_successes", 0)),
        "cas_fallbacks": int(metrics.get("cas_fallbacks", 0)),
        "failures": failures,
    }


def median(rows: list[dict[str, Any]], key: str) -> float:
    return float(statistics.median(float(row[key]) for row in rows))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    causal: dict[str, list[dict[str, Any]]] = {"baseline": [], "cas": []}
    runtime: dict[str, list[dict[str, Any]]] = {"baseline": [], "cas": []}

    with tempfile.TemporaryDirectory(prefix="ecomevo-skill-policy-cas-") as tmp:
        root = Path(tmp)
        semantics = await semantic_probe(root)
        if not semantics["states_equal_each_step"]:
            failures.append("CAS changed policy state in the mixed semantic sequence")

        stale = await stale_fallback_probe(root)
        if not stale["state_equal"]:
            failures.append("CAS stale-read fallback lost or reordered a policy update")
        if int(stale["metrics"].get("cas_fallbacks", 0)) < 1:
            failures.append("stale-read probe did not exercise the CAS fallback")

        for experiment in range(EXPERIMENTS):
            order = ("baseline", "cas") if experiment % 2 == 0 else ("cas", "baseline")
            causal_by_mode = {
                mode: await causal_probe(root, mode, experiment)
                for mode in order
            }
            for mode in order:
                causal[mode].append(causal_by_mode[mode])
            if causal_by_mode["baseline"]["final"] != causal_by_mode["cas"]["final"]:
                failures.append(f"causal policy state diverged in experiment {experiment}")
            for mode in order:
                if causal_by_mode[mode]["transactions"] != CAUSAL_ROUNDS:
                    failures.append(
                        f"{mode}[{experiment}] causal tx changed: "
                        f"{causal_by_mode[mode]['transactions']} != {CAUSAL_ROUNDS}"
                    )
            if causal_by_mode["cas"]["cas_successes"] != CAUSAL_ROUNDS:
                failures.append(
                    f"cas[{experiment}] did not stay on the steady CAS path: "
                    f"{causal_by_mode['cas']['cas_successes']} != {CAUSAL_ROUNDS}"
                )
            if causal_by_mode["cas"]["policy_writes"] >= causal_by_mode["baseline"]["policy_writes"]:
                failures.append(
                    f"cas[{experiment}] did not reduce policy write statements: "
                    f"{causal_by_mode['cas']['policy_writes']} >= "
                    f"{causal_by_mode['baseline']['policy_writes']}"
                )

            for mode in order:
                row = await runtime_probe(root, mode, experiment)
                runtime[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    baseline_hold = median(causal["baseline"], "writer_hold_ms_total")
    cas_hold = median(causal["cas"], "writer_hold_ms_total")
    baseline_op = median(causal["baseline"], "operation_ms_total")
    cas_op = median(causal["cas"], "operation_ms_total")
    baseline_wall = median(causal["baseline"], "wall_seconds")
    cas_wall = median(causal["cas"], "wall_seconds")
    baseline_writes = median(causal["baseline"], "policy_writes")
    cas_writes = median(causal["cas"], "policy_writes")

    def runtime_per_call(rows: list[dict[str, Any]], key: str) -> float:
        return float(
            statistics.median(
                float(row[key]) / max(1, int(row["note_run_calls"]))
                for row in rows
            )
        )

    comparison = {
        "causal_policy_write_statement_ratio": round(cas_writes / max(1.0, baseline_writes), 4),
        "causal_writer_hold_total_ratio": round(cas_hold / max(0.001, baseline_hold), 4),
        "causal_operation_total_ratio": round(cas_op / max(0.001, baseline_op), 4),
        "causal_wall_ratio": round(cas_wall / max(0.0001, baseline_wall), 4),
        "runtime_writer_hold_per_call_ratio": round(
            runtime_per_call(runtime["cas"], "writer_hold_ms_total")
            / max(0.001, runtime_per_call(runtime["baseline"], "writer_hold_ms_total")),
            4,
        ),
        "runtime_operation_per_call_ratio": round(
            runtime_per_call(runtime["cas"], "operation_ms_total")
            / max(0.001, runtime_per_call(runtime["baseline"], "operation_ms_total")),
            4,
        ),
        "runtime_policy_writes_per_call_ratio": round(
            runtime_per_call(runtime["cas"], "policy_writes")
            / max(0.001, runtime_per_call(runtime["baseline"], "policy_writes")),
            4,
        ),
    }

    return {
        "ok": not failures,
        "causal_rounds": CAUSAL_ROUNDS,
        "runtime_tasks": RUNTIME_TASKS,
        "experiments": EXPERIMENTS,
        "semantics": semantics,
        "stale_fallback": stale,
        "causal": {
            mode: [
                {
                    key: row[key]
                    for key in (
                        "experiment",
                        "transactions",
                        "writer_hold_ms_total",
                        "operation_ms_total",
                        "wall_seconds",
                        "policy_writes",
                        "policy_selects",
                        "cas_successes",
                        "cas_fallbacks",
                    )
                }
                for row in rows
            ]
            for mode, rows in causal.items()
        },
        "runtime": {
            mode: [
                {
                    key: row[key]
                    for key in (
                        "experiment",
                        "transactions",
                        "writer_hold_ms_total",
                        "operation_ms_total",
                        "wall_seconds",
                        "note_run_calls",
                        "learning_calls",
                        "policy_writes",
                        "cas_successes",
                        "cas_fallbacks",
                    )
                }
                for row in rows
            ]
            for mode, rows in runtime.items()
        },
        "comparison": comparison,
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
