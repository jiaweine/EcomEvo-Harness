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
from ecomevo.runtime.adaptive_routing import AdaptiveRoutingStore


DOMAIN = "merchant_review"
RUNTIME_TASKS = 32
CAUSAL_ROUNDS = 64
EXPERIMENTS = 3


class RoutingMetricsMixin:
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._writer_profile = profile
        self._metrics_lock = threading.Lock()
        self._batch_sizes: list[int] = []
        self._reliability_selects = 0
        self._reliability_writes = 0
        super().__init__(path)
        self.reset_metrics()

    def reset_metrics(self) -> None:
        with self._metrics_lock:
            self._batch_sizes.clear()
            self._reliability_selects = 0
            self._reliability_writes = 0

    def _conn(self):
        connection = super()._conn()
        connection_id = id(connection)

        def trace(statement: str) -> None:
            self._writer_profile.trace(connection_id, statement)
            normalized = statement.strip().upper()
            if "ROUTING_TOOL_STATS" not in normalized:
                return
            with self._metrics_lock:
                if normalized.startswith("SELECT"):
                    self._reliability_selects += 1
                elif normalized.startswith(("INSERT", "UPDATE", "REPLACE")):
                    self._reliability_writes += 1

        connection.set_trace_callback(trace)
        return connection

    def apply_batch(self, domain: str, *, phase: str, rows: list[dict[str, Any]]):
        with self._metrics_lock:
            self._batch_sizes.append(len(rows))
        return writer_profile._timed(
            self._writer_profile,
            "routing.outcome",
            lambda: AdaptiveRoutingStore.apply_batch(
                self, domain, phase=phase, rows=rows
            ),
        )

    def metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            sizes = list(self._batch_sizes)
            return {
                "apply_calls": len(sizes),
                "batch_sizes": sizes,
                "rows": sum(sizes),
                "max_batch": max(sizes, default=0),
                "multirow_batches": sum(size > 1 for size in sizes),
                "reliability_selects": self._reliability_selects,
                "reliability_writes": self._reliability_writes,
            }


class BaselineRouting(RoutingMetricsMixin, AdaptiveRoutingStore):
    pass


class FusedRouting(RoutingMetricsMixin, AdaptiveRoutingStore):
    def _update_reliability_batch(
        self,
        c,
        domain: str,
        rows: list[dict[str, Any]],
        now: float,
    ):
        tools = list(
            dict.fromkeys(
                str(item["tool"])
                for item in rows
                if str(item.get("tool") or "")
            )
        )
        if not tools:
            return
        placeholders = ",".join("?" for _ in tools)
        existing_rows = c.execute(
            f"SELECT * FROM routing_tool_stats "
            f"WHERE domain IN (?,?) AND tool IN ({placeholders})",  # nosec B608 -- placeholder count only
            ["*", domain, *tools],
        ).fetchall()
        state = {
            (str(row["domain"]), str(row["tool"])): dict(row)
            for row in existing_rows
        }
        touched: list[tuple[str, str]] = []
        touched_set: set[tuple[str, str]] = set()

        # Preserve historical input order; only final SQL writes are fused.
        for item in rows:
            tool = str(item["tool"])
            ok = bool(item.get("ok", False))
            reward = float(item["reward"])
            for scope_domain in ("*", domain):
                key = (scope_domain, tool)
                if key not in touched_set:
                    touched.append(key)
                    touched_set.add(key)
                current = state.get(key)
                if current:
                    alpha = float(current["alpha"]) + (1.0 if ok else 0.0)
                    beta = float(current["beta"]) + (0.0 if ok else 1.0)
                    uses = int(current["uses"]) + 1
                    reward_ewma = 0.92 * float(current["reward_ewma"]) + 0.08 * reward
                else:
                    alpha = 2.0 + (1.0 if ok else 0.0)
                    beta = 2.0 + (0.0 if ok else 1.0)
                    uses = 1
                    reward_ewma = reward
                state[key] = {
                    "domain": scope_domain,
                    "tool": tool,
                    "alpha": alpha,
                    "beta": beta,
                    "uses": uses,
                    "reward_ewma": reward_ewma,
                }

        values = [
            (
                scope_domain,
                tool,
                float(state[(scope_domain, tool)]["alpha"]),
                float(state[(scope_domain, tool)]["beta"]),
                int(state[(scope_domain, tool)]["uses"]),
                float(state[(scope_domain, tool)]["reward_ewma"]),
                now,
            )
            for scope_domain, tool in touched
        ]
        if not values:
            return
        row_sql = ",".join("(?,?,?,?,?,?,?)" for _ in values)
        parameters: list[Any] = []
        for value in values:
            parameters.extend(value)
        c.execute(
            "INSERT INTO routing_tool_stats("
            "domain,tool,alpha,beta,uses,reward_ewma,updated_at"
            f") VALUES {row_sql} "  # nosec B608 -- bounded local placeholder count only
            "ON CONFLICT(domain,tool) DO UPDATE SET "
            "alpha=excluded.alpha,beta=excluded.beta,uses=excluded.uses,"
            "reward_ewma=excluded.reward_ewma,updated_at=excluded.updated_at",
            parameters,
        )


def projection(store: AdaptiveRoutingStore) -> dict[str, Any]:
    with store._conn() as c:
        policy = [
            {key: value for key, value in dict(row).items() if key != "updated_at"}
            for row in c.execute("SELECT * FROM routing_policy ORDER BY policy_key").fetchall()
        ]
        stats = [
            {key: value for key, value in dict(row).items() if key != "updated_at"}
            for row in c.execute(
                "SELECT * FROM routing_tool_stats ORDER BY domain,tool"
            ).fetchall()
        ]
        outcomes = [
            {
                key: value
                for key, value in dict(row).items()
                if key not in {"id", "created_at"}
            }
            for row in c.execute("SELECT * FROM routing_outcomes ORDER BY id").fetchall()
        ]
    return {"policy": policy, "stats": stats, "outcomes": outcomes}


def sample_row(tool: str, reward: float, ok: bool, seed: float) -> dict[str, Any]:
    vector = [0.0] * 12
    vector[0] = 1.0
    vector[1] = seed
    vector[4] = 1.0 - seed / 2.0
    vector[7] = 0.5 + seed / 4.0
    return {
        "tool": tool,
        "vector": vector,
        "reward": reward,
        "ok": ok,
        "meta": {"seed": seed},
    }


def semantic_probe(root: Path) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    baseline = BaselineRouting(root / "semantic-baseline.db", profile)
    fused = FusedRouting(root / "semantic-fused.db", profile)
    batches = [
        [sample_row("catalog", 0.4, True, 0.2), sample_row("risk", -0.2, False, 0.7)],
        [
            sample_row("catalog", 0.8, True, 0.4),
            sample_row("catalog", 0.1, True, 0.6),
            sample_row("risk", 0.3, True, 0.3),
        ],
        [sample_row("entity", -0.5, False, 0.8)],
        [
            sample_row("risk", 0.9, True, 0.5),
            sample_row("entity", 0.2, True, 0.1),
            sample_row("catalog", -0.1, False, 0.9),
        ],
    ]
    equal = True
    for index, rows in enumerate(batches):
        baseline.apply_batch(DOMAIN, phase=f"round-{index}", rows=rows)
        fused.apply_batch(DOMAIN, phase=f"round-{index}", rows=rows)
        if projection(baseline) != projection(fused):
            equal = False
            break
    return {"rounds": len(batches), "states_equal_each_round": equal}


def stage_from(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == name), None)


def causal_rows(index: int) -> list[dict[str, Any]]:
    seed = ((index % 7) + 1) / 10.0
    return [
        sample_row("catalog", 0.6 if index % 3 else -0.1, index % 5 != 0, seed),
        sample_row("risk", -0.2 if index % 4 else 0.7, index % 4 == 0, 1.0 - seed / 2.0),
    ]


def causal_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    routing_type = BaselineRouting if mode == "baseline" else FusedRouting
    routing = routing_type(root / f"causal-{mode}-{experiment}.db", profile)

    # Align both arms on identical warm state, then measure only the steady two-row path.
    routing.apply_batch(DOMAIN, phase="warm", rows=causal_rows(-1))
    profile.reset()
    routing.reset_metrics()
    started = time.perf_counter()
    for index in range(CAUSAL_ROUNDS):
        routing.apply_batch(DOMAIN, phase=f"causal-{index}", rows=causal_rows(index))
    wall = time.perf_counter() - started
    report = profile.report(CAUSAL_ROUNDS)
    stage = stage_from(report, "routing.outcome")
    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": round(wall, 4),
        "routing": {
            "transactions": int(stage["transactions"]) if stage else 0,
            "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
            "writer_hold_ms_p95": float(stage["writer_hold_ms_p95"]) if stage else 0.0,
            "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        },
        "metrics": routing.metrics(),
        "projection": projection(routing),
    }


def build_engine(db: Path, profile: writer_profile.WriterProfile, mode: str):
    sandbox = writer_profile.ActionSandbox()
    events = writer_profile.ProfiledEventStore(db, profile)
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
    routing_type = BaselineRouting if mode == "baseline" else FusedRouting
    routing = routing_type(db, profile)
    engine.autonomy.policy.routing = routing
    return engine, routing


async def runtime_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, routing = build_engine(root / f"runtime-{mode}-{experiment}.db", profile, mode)
    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    profile.reset()
    routing.reset_metrics()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, RUNTIME_TASKS)
    wall = time.perf_counter() - started
    report = profile.report(RUNTIME_TASKS)
    stage = stage_from(report, "routing.outcome")
    metrics = routing.metrics()
    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")
    if stage and int(stage["transactions"]) != metrics["apply_calls"]:
        failures.append(
            f"routing transaction/apply mismatch: {stage['transactions']} != {metrics['apply_calls']}"
        )
    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": round(wall, 4),
        "routing": {
            "transactions": int(stage["transactions"]) if stage else 0,
            "writer_hold_ms_total": float(stage["writer_hold_ms_total"]) if stage else 0.0,
            "writer_hold_ms_p95": float(stage["writer_hold_ms_p95"]) if stage else 0.0,
            "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        },
        "metrics": metrics,
        "failures": failures,
    }


def median_path(rows: list[dict[str, Any]], *path: str) -> float:
    values = []
    for row in rows:
        value: Any = row
        for key in path:
            value = value[key]
        values.append(float(value))
    return float(statistics.median(values))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    causal: dict[str, list[dict[str, Any]]] = {"baseline": [], "fused": []}
    runtime: dict[str, list[dict[str, Any]]] = {"baseline": [], "fused": []}
    with tempfile.TemporaryDirectory(prefix="ecomevo-routing-reliability-fusion-") as tmp:
        root = Path(tmp)
        semantics = semantic_probe(root)
        if not semantics["states_equal_each_round"]:
            failures.append("fused reliability writes changed routing state")

        for experiment in range(EXPERIMENTS):
            order = ("baseline", "fused") if experiment % 2 == 0 else ("fused", "baseline")
            causal_rows_by_mode = {
                mode: causal_probe(root, mode, experiment)
                for mode in order
            }
            for mode in order:
                causal[mode].append(causal_rows_by_mode[mode])
            if causal_rows_by_mode["baseline"]["projection"] != causal_rows_by_mode["fused"]["projection"]:
                failures.append(f"causal state diverged in experiment {experiment}")
            for mode in order:
                if causal_rows_by_mode[mode]["routing"]["transactions"] != CAUSAL_ROUNDS:
                    failures.append(
                        f"{mode}[{experiment}] causal tx changed: "
                        f"{causal_rows_by_mode[mode]['routing']['transactions']} != {CAUSAL_ROUNDS}"
                    )

            for mode in order:
                row = await runtime_probe(root, mode, experiment)
                runtime[mode].append(row)
                failures.extend(
                    f"{mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    baseline_causal_hold = median_path(causal["baseline"], "routing", "writer_hold_ms_total")
    fused_causal_hold = median_path(causal["fused"], "routing", "writer_hold_ms_total")
    baseline_causal_op = median_path(causal["baseline"], "routing", "operation_ms_total")
    fused_causal_op = median_path(causal["fused"], "routing", "operation_ms_total")
    baseline_causal_writes = median_path(causal["baseline"], "metrics", "reliability_writes")
    fused_causal_writes = median_path(causal["fused"], "metrics", "reliability_writes")
    baseline_causal_wall = median_path(causal["baseline"], "wall_seconds")
    fused_causal_wall = median_path(causal["fused"], "wall_seconds")

    baseline_runtime_hold_per_call = statistics.median(
        row["routing"]["writer_hold_ms_total"] / max(1, row["metrics"]["apply_calls"])
        for row in runtime["baseline"]
    )
    fused_runtime_hold_per_call = statistics.median(
        row["routing"]["writer_hold_ms_total"] / max(1, row["metrics"]["apply_calls"])
        for row in runtime["fused"]
    )
    baseline_runtime_op_per_call = statistics.median(
        row["routing"]["operation_ms_total"] / max(1, row["metrics"]["apply_calls"])
        for row in runtime["baseline"]
    )
    fused_runtime_op_per_call = statistics.median(
        row["routing"]["operation_ms_total"] / max(1, row["metrics"]["apply_calls"])
        for row in runtime["fused"]
    )
    baseline_runtime_writes_per_call = statistics.median(
        row["metrics"]["reliability_writes"] / max(1, row["metrics"]["apply_calls"])
        for row in runtime["baseline"]
    )
    fused_runtime_writes_per_call = statistics.median(
        row["metrics"]["reliability_writes"] / max(1, row["metrics"]["apply_calls"])
        for row in runtime["fused"]
    )

    return {
        "ok": not failures,
        "tasks": RUNTIME_TASKS,
        "causal_rounds": CAUSAL_ROUNDS,
        "experiments": EXPERIMENTS,
        "semantics": semantics,
        "causal": causal,
        "runtime": runtime,
        "comparison": {
            "causal_reliability_write_statement_ratio": round(
                fused_causal_writes / max(1.0, baseline_causal_writes), 4
            ),
            "causal_writer_hold_total_ratio": round(
                fused_causal_hold / max(0.001, baseline_causal_hold), 4
            ),
            "causal_operation_total_ratio": round(
                fused_causal_op / max(0.001, baseline_causal_op), 4
            ),
            "causal_wall_ratio": round(
                fused_causal_wall / max(0.0001, baseline_causal_wall), 4
            ),
            "runtime_writer_hold_per_apply_ratio": round(
                fused_runtime_hold_per_call / max(0.001, baseline_runtime_hold_per_call), 4
            ),
            "runtime_operation_per_apply_ratio": round(
                fused_runtime_op_per_call / max(0.001, baseline_runtime_op_per_call), 4
            ),
            "runtime_reliability_writes_per_apply_ratio": round(
                fused_runtime_writes_per_call / max(0.001, baseline_runtime_writes_per_call), 4
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
