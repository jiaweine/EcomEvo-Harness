from __future__ import annotations

import asyncio
import contextvars
import json
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ecomevo.runtime.adaptive_routing import AdaptiveRoutingStore
from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer
from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary
from ecomevo.runtime.engine import EcomEvoEngine
from ecomevo.runtime.sandbox import ActionSandbox


_stage: contextvars.ContextVar[str] = contextvars.ContextVar("writer_profile_stage", default="unattributed")
_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE")


class WriterProfile:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # connection_id -> (stage, writer-slot-start, is_writer)
        self._active: dict[int, tuple[str, float, bool]] = {}
        self._transactions: dict[str, int] = defaultdict(int)
        self._hold_ms: dict[str, list[float]] = defaultdict(list)
        self._operation_ms: dict[str, list[float]] = defaultdict(list)

    def reset(self) -> None:
        with self._lock:
            self._active.clear()
            self._transactions.clear()
            self._hold_ms.clear()
            self._operation_ms.clear()

    def trace(self, connection_id: int, statement: str) -> None:
        normalized = statement.strip().upper()
        now = time.perf_counter()
        with self._lock:
            if normalized.startswith("BEGIN IMMEDIATE"):
                stage = _stage.get()
                self._transactions[stage] += 1
                self._active[connection_id] = (stage, now, True)
                return
            if normalized.startswith("BEGIN"):
                # Deferred BEGIN is often a deliberate WAL read snapshot. Do not call it
                # a writer until SQLite actually executes DML in that transaction.
                self._active[connection_id] = (_stage.get(), now, False)
                return
            if normalized.startswith(_WRITE_PREFIXES):
                active = self._active.get(connection_id)
                if active is None:
                    stage = _stage.get()
                    self._transactions[stage] += 1
                    self._active[connection_id] = (stage, now, True)
                elif not active[2]:
                    stage = _stage.get()
                    self._transactions[stage] += 1
                    self._active[connection_id] = (stage, now, True)
                return
            if normalized.startswith("COMMIT") or normalized.startswith("ROLLBACK"):
                active = self._active.pop(connection_id, None)
                if active and active[2]:
                    stage, started, _ = active
                    self._hold_ms[stage].append((now - started) * 1000.0)

    def operation(self, stage: str, elapsed_ms: float) -> None:
        with self._lock:
            self._operation_ms[stage].append(float(elapsed_ms))

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
        return ordered[index]

    def report(self, tasks: int) -> dict[str, Any]:
        with self._lock:
            stages = sorted(set(self._transactions) | set(self._hold_ms) | set(self._operation_ms))
            rows = []
            for stage in stages:
                hold = list(self._hold_ms.get(stage, ()))
                op = list(self._operation_ms.get(stage, ()))
                transactions = int(self._transactions.get(stage, 0))
                rows.append(
                    {
                        "stage": stage,
                        "transactions": transactions,
                        "transactions_per_task": round(transactions / max(1, tasks), 3),
                        "writer_hold_ms_total": round(sum(hold), 3),
                        "writer_hold_ms_p50": round(self._percentile(hold, 0.50), 3),
                        "writer_hold_ms_p95": round(self._percentile(hold, 0.95), 3),
                        "writer_hold_ms_p99": round(self._percentile(hold, 0.99), 3),
                        "operation_ms_total": round(sum(op), 3),
                        "operation_ms_p95": round(self._percentile(op, 0.95), 3),
                    }
                )
            rows.sort(key=lambda row: (row["transactions"], row["writer_hold_ms_total"]), reverse=True)
            return {
                "tasks": tasks,
                "total_transactions": sum(row["transactions"] for row in rows),
                "transactions_per_task": round(
                    sum(row["transactions"] for row in rows) / max(1, tasks), 3
                ),
                "stages": rows,
                "unattributed_transactions": int(self._transactions.get("unattributed", 0)),
            }


def _timed(profile: WriterProfile, stage: str, call: Callable[[], Any]) -> Any:
    token = _stage.set(stage)
    started = time.perf_counter()
    try:
        return call()
    finally:
        profile.operation(stage, (time.perf_counter() - started) * 1000.0)
        _stage.reset(token)


class ProfiledEventStore(BundledEventStore):
    def __init__(self, path: Path, profile: WriterProfile):
        self._writer_profile = profile
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()
        connection_id = id(connection)
        connection.set_trace_callback(
            lambda statement: self._writer_profile.trace(connection_id, statement)
        )
        return connection

    def create_session_events_checkpoint(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "event.bootstrap",
            lambda: super(ProfiledEventStore, self).create_session_events_checkpoint(*args, **kwargs),
        )

    def create_session_and_append(self, session_id, event_type, payload, **kwargs):
        return _timed(
            self._writer_profile,
            "event.session_first",
            lambda: super(ProfiledEventStore, self).create_session_and_append(
                session_id, event_type, payload, **kwargs
            ),
        )

    def create_session(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "event.session",
            lambda: super(ProfiledEventStore, self).create_session(*args, **kwargs),
        )

    def append(self, session_id, event_type, payload):
        stage = f"event.append:{str(event_type)[:64]}"
        return _timed(
            self._writer_profile,
            stage,
            lambda: super(ProfiledEventStore, self).append(session_id, event_type, payload),
        )

    async def append_grouped(self, session_id, event_type, payload):
        stage = "event.group_commit"
        token = _stage.set(stage)
        started = time.perf_counter()
        try:
            return await super().append_grouped(session_id, event_type, payload)
        finally:
            self._writer_profile.operation(stage, (time.perf_counter() - started) * 1000.0)
            _stage.reset(token)

    def save_checkpoint(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "event.checkpoint_snapshot",
            lambda: super(ProfiledEventStore, self).save_checkpoint(*args, **kwargs),
        )

    def save_checkpoint_and_append(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "event.checkpoint_audit",
            lambda: super(ProfiledEventStore, self).save_checkpoint_and_append(*args, **kwargs),
        )

    async def save_checkpoint_and_append_grouped(self, *args, **kwargs):
        stage = "event.checkpoint_audit"
        token = _stage.set(stage)
        started = time.perf_counter()
        try:
            return await super().save_checkpoint_and_append_grouped(*args, **kwargs)
        finally:
            self._writer_profile.operation(
                stage, (time.perf_counter() - started) * 1000.0
            )
            _stage.reset(token)

    def save_patch_if_novel(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "event.evolution_patch",
            lambda: super(ProfiledEventStore, self).save_patch_if_novel(*args, **kwargs),
        )


class ProfiledSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path: Path, profile: WriterProfile):
        self._writer_profile = profile
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()
        connection_id = id(connection)
        connection.set_trace_callback(
            lambda statement: self._writer_profile.trace(connection_id, statement)
        )
        return connection

    def policy(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "skills.policy",
            lambda: super(ProfiledSkills, self).policy(*args, **kwargs),
        )

    def _persist_note_run_group(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "skills.note_run",
            lambda: super(ProfiledSkills, self)._persist_note_run_group(*args, **kwargs),
        )

    def record_outcome(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "skills.outcome",
            lambda: super(ProfiledSkills, self).record_outcome(*args, **kwargs),
        )

    def upsert_candidate(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "skills.candidate",
            lambda: super(ProfiledSkills, self).upsert_candidate(*args, **kwargs),
        )


class ProfiledHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path: Path, profile: WriterProfile, *, sandbox=None):
        self._writer_profile = profile
        super().__init__(path, sandbox=sandbox)

    def _conn(self):
        connection = super()._conn()
        connection_id = id(connection)
        connection.set_trace_callback(
            lambda statement: self._writer_profile.trace(connection_id, statement)
        )
        return connection

    def profile(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "harness.profile",
            lambda: super(ProfiledHarness, self).profile(*args, **kwargs),
        )

    def record_outcome(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "harness.outcome",
            lambda: super(ProfiledHarness, self).record_outcome(*args, **kwargs),
        )

    def _record_replay_case(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "harness.replay_case",
            lambda: super(ProfiledHarness, self)._record_replay_case(*args, **kwargs),
        )

    def _persist_replay_group(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "harness.replay_case",
            lambda: super(ProfiledHarness, self)._persist_replay_group(*args, **kwargs),
        )

    def _record_rejection(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "harness.rejection",
            lambda: super(ProfiledHarness, self)._record_rejection(*args, **kwargs),
        )

    async def propose(self, *args, **kwargs):
        token = _stage.set("harness.propose")
        started = time.perf_counter()
        try:
            return await super().propose(*args, **kwargs)
        finally:
            self._writer_profile.operation(
                "harness.propose", (time.perf_counter() - started) * 1000.0
            )
            _stage.reset(token)


class ProfiledRouting(AdaptiveRoutingStore):
    def __init__(self, path: Path, profile: WriterProfile):
        self._writer_profile = profile
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()
        connection_id = id(connection)
        connection.set_trace_callback(
            lambda statement: self._writer_profile.trace(connection_id, statement)
        )
        return connection

    def apply_batch(self, *args, **kwargs):
        return _timed(
            self._writer_profile,
            "routing.outcome",
            lambda: super(ProfiledRouting, self).apply_batch(*args, **kwargs),
        )


def _build_engine(db: Path, profile: WriterProfile) -> EcomEvoEngine:
    sandbox = ActionSandbox()
    events = ProfiledEventStore(db, profile)
    skills = ProfiledSkills(db, profile)
    harness = ProfiledHarness(db, profile, sandbox=sandbox)
    engine = EcomEvoEngine(
        db,
        plugin_overrides={
            "event.store": events,
            "memory.skills": skills,
            "evolver.harness": harness,
            "sandbox.action": sandbox,
        },
    )
    engine.autonomy.policy.routing = ProfiledRouting(db, profile)
    return engine


async def _run_batch(engine: EcomEvoEngine, tasks: int) -> list[Any]:
    async def one(index: int):
        return await engine.run(
            f"审核商家并核对主体、授权和历史风险。writer profile 任务 {index}。",
            [],
            domain_hint="merchant_review",
        )

    return await asyncio.gather(*(one(index) for index in range(tasks)))


async def _profile_metadata_note_runs(
    skills: ProfiledSkills,
    profile: WriterProfile,
    *,
    tasks: int = 32,
    domain: str = "merchant_review",
) -> dict[str, Any]:
    before = skills.policy(domain)
    profile.reset()
    started = time.perf_counter()
    await asyncio.gather(
        *(
            skills.note_run_async(domain, success=True, skill_used=False)
            for _ in range(tasks)
        )
    )
    wall = time.perf_counter() - started
    after = skills.policy(domain)
    report = profile.report(tasks)
    stage = next(
        (row for row in report["stages"] if row["stage"] == "skills.note_run"),
        None,
    )
    strategy_keys = ("promotion_threshold", "retirement_threshold", "exploration")
    return {
        "tasks": tasks,
        "writer_transactions": int(stage["transactions"]) if stage else 0,
        "transactions_per_task": round(
            (int(stage["transactions"]) if stage else 0) / max(1, tasks), 3
        ),
        "updates_delta": int(after["updates"]) - int(before["updates"]),
        "strategy_parameters_unchanged": all(
            float(after[key]) == float(before[key]) for key in strategy_keys
        ),
        "wall_seconds": round(wall, 4),
        "unattributed_transactions": int(report["unattributed_transactions"]),
    }


async def main_async() -> dict[str, Any]:
    tasks = 32
    profile = WriterProfile()
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-writer-profile-") as tmp:
        db = Path(tmp) / "writer-profile.db"
        engine = _build_engine(db, profile)

        # Warm one run so domain/bootstrap table creation and first-use policy seeding do
        # not distort the steady-state writer distribution we want to optimize next.
        warm = await _run_batch(engine, 1)
        if not warm[0].event_chain_valid:
            failures.append("warm-up run produced an invalid event chain")

        profile.reset()
        started = time.perf_counter()
        summaries = await _run_batch(engine, tasks)
        wall = time.perf_counter() - started
        if any(not summary.event_chain_valid for summary in summaries):
            failures.append("profiled run produced an invalid event chain")

        report = profile.report(tasks)
        if report["unattributed_transactions"]:
            failures.append(
                f"writer profiling lost stage attribution: {report['unattributed_transactions']} transactions"
            )
        if report["total_transactions"] < tasks:
            failures.append(
                f"writer profiling observed too few transactions: {report['total_transactions']} < {tasks}"
            )

        component_totals: dict[str, dict[str, float]] = {}
        for row in report["stages"]:
            component = str(row["stage"]).split(".", 1)[0]
            target = component_totals.setdefault(
                component,
                {"transactions": 0.0, "writer_hold_ms_total": 0.0, "operation_ms_total": 0.0},
            )
            target["transactions"] += float(row["transactions"])
            target["writer_hold_ms_total"] += float(row["writer_hold_ms_total"])
            target["operation_ms_total"] += float(row["operation_ms_total"])
        components = [
            {
                "component": component,
                "transactions": int(values["transactions"]),
                "transactions_per_task": round(values["transactions"] / tasks, 3),
                "writer_hold_ms_total": round(values["writer_hold_ms_total"], 3),
                "operation_ms_total": round(values["operation_ms_total"], 3),
            }
            for component, values in component_totals.items()
        ]
        components.sort(key=lambda row: (row["transactions"], row["writer_hold_ms_total"]), reverse=True)

        metadata_note_run_probe = await _profile_metadata_note_runs(
            engine.skills,
            profile,
            tasks=tasks,
        )
        if metadata_note_run_probe["updates_delta"] != tasks:
            failures.append(
                "metadata-only skill note-run probe lost policy updates: "
                f"{metadata_note_run_probe['updates_delta']} != {tasks}"
            )
        if not metadata_note_run_probe["strategy_parameters_unchanged"]:
            failures.append("metadata-only skill note-run probe changed strategy parameters")
        if metadata_note_run_probe["unattributed_transactions"]:
            failures.append(
                "metadata-only skill note-run probe lost writer attribution: "
                f"{metadata_note_run_probe['unattributed_transactions']} transactions"
            )
        if metadata_note_run_probe["writer_transactions"] >= tasks:
            failures.append(
                "metadata-only skill note-run grouping was not observed: "
                f"{metadata_note_run_probe['writer_transactions']} >= {tasks} transactions"
            )

        return {
            "ok": not failures,
            "tasks": tasks,
            "wall_seconds": round(wall, 4),
            "throughput_tasks_per_second": round(tasks / wall, 3) if wall else 0.0,
            "profile": report,
            "components": components,
            "metadata_note_run_probe": metadata_note_run_probe,
            "failures": failures,
        }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())