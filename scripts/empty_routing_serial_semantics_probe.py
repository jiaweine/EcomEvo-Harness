from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ecomevo.runtime.engine import EcomEvoEngine


TASKS = 16
DOMAIN = "merchant_review"


def _copy_database(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _canonical_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ignored = {"routing_ms", "posterior_prepare_ms"}
    return [
        {key: value for key, value in row.items() if key not in ignored}
        for row in trace
    ]


def _summary_projection(summary: Any) -> dict[str, Any]:
    return {
        "domain": str(summary.domain.value),
        "status": str(summary.status),
        "tool_calls": int(summary.tool_calls),
        "subagents": int(summary.subagents),
        "recovery_events": int(summary.recovery_events),
        "verifier_score": float(summary.verifier_score),
        "evolved": bool(summary.evolved),
        "event_chain_valid": bool(summary.event_chain_valid),
        "autonomy_steps": int(summary.autonomy_steps),
        "delegations": int(summary.delegations),
        "evolution_events": int(summary.evolution_events),
        "skills_used": list(summary.skills_used),
        "findings": list(summary.findings),
        "risks": list(summary.risks),
        "evidence_complete": bool(summary.evidence_complete),
        "missing_evidence": list(summary.missing_evidence),
        "tool_cost_used": float(summary.tool_cost_used),
        "tool_cost_budget": float(summary.tool_cost_budget),
        "tool_cost_remaining": float(summary.tool_cost_remaining),
        "stop_reason": str(summary.stop_reason),
        "stop_detail": str(summary.stop_detail),
        "stagnated": bool(summary.stagnated),
        "autonomy_mode": str(summary.autonomy_mode),
        "belief": summary.belief.model_dump(mode="json"),
        "actions": [
            {
                "kind": str(action.kind),
                "title": str(action.title),
                "description": str(action.description),
                "side_effect": bool(action.side_effect),
                "risk_level": str(action.risk_level),
                "requires_confirmation": bool(action.requires_confirmation),
                "payload": action.payload,
                "status": str(action.status),
            }
            for action in summary.proposed_actions
        ],
    }


def _routing_projection(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        policy = [
            dict(row)
            for row in connection.execute(
                "SELECT policy_key,domain,scope,a_json,b_json,samples,reward_ewma,residual_ewma "
                "FROM routing_policy ORDER BY policy_key"
            ).fetchall()
        ]
        outcomes = [
            dict(row)
            for row in connection.execute(
                "SELECT domain,phase,tool,reward,feature_json,meta_json "
                "FROM routing_outcomes ORDER BY id"
            ).fetchall()
        ]
        stats = [
            dict(row)
            for row in connection.execute(
                "SELECT domain,tool,alpha,beta,uses,reward_ewma "
                "FROM routing_tool_stats ORDER BY domain,tool"
            ).fetchall()
        ]
    return {"policy": policy, "outcomes": outcomes, "tool_stats": stats}


def _install_probe(policy: Any, *, short_circuit_empty: bool) -> dict[str, Any]:
    state: dict[str, Any] = {
        "prepare_calls": 0,
        "score_calls": 0,
        "current_rows": None,
        "all_rows": [],
    }
    original_prepare = policy.routing.prepare_context
    original_score = policy.routing.score_prepared
    original_rank = policy._rank_candidates

    def prepare_context(*args, **kwargs):
        state["prepare_calls"] += 1
        return original_prepare(*args, **kwargs)

    def score_prepared(*args, **kwargs):
        state["score_calls"] += 1
        return original_score(*args, **kwargs)

    def rank_candidates(candidates, *args, **kwargs):
        before_prepare = int(state["prepare_calls"])
        before_score = int(state["score_calls"])
        if short_circuit_empty and not candidates:
            selected, trace = [], []
        else:
            selected, trace = original_rank(candidates, *args, **kwargs)
        row = {
            "candidate_count": len(candidates),
            "selected_tools": [str(item.get("tool") or "") for item in selected],
            "trace": _canonical_trace(trace),
            "prepare_calls": int(state["prepare_calls"]) - before_prepare,
            "score_calls": int(state["score_calls"]) - before_score,
        }
        state["all_rows"].append(row)
        current = state.get("current_rows")
        if isinstance(current, list):
            current.append(row)
        return selected, trace

    policy.routing.prepare_context = prepare_context
    policy.routing.score_prepared = score_prepared
    policy._rank_candidates = rank_candidates
    return state


async def _run_arm(path: Path, *, short_circuit_empty: bool) -> dict[str, Any]:
    engine = EcomEvoEngine(path)
    policy = engine.autonomy.policy
    if type(policy).__name__ != "CounterfactualAdaptiveDecisionPolicy":
        raise AssertionError(f"unexpected production policy class: {type(policy).__name__}")

    state = _install_probe(policy, short_circuit_empty=short_circuit_empty)
    tasks: list[dict[str, Any]] = []
    routing_snapshots: list[dict[str, Any]] = []
    for index in range(TASKS):
        current_rows: list[dict[str, Any]] = []
        state["current_rows"] = current_rows
        summary = await engine.run(
            f"审核商家并核对主体、授权和历史风险。serial semantics 任务 {index}。",
            [],
            domain_hint=DOMAIN,
        )
        state["current_rows"] = None
        if not summary.event_chain_valid:
            raise AssertionError(f"task {index} produced an invalid event chain")
        tasks.append(
            {
                "summary": _summary_projection(summary),
                "ranks": current_rows,
            }
        )
        routing_snapshots.append(policy._routing_source.snapshot(DOMAIN))

    empty_rows = [row for row in state["all_rows"] if row["candidate_count"] == 0]
    nonempty_rows = [row for row in state["all_rows"] if row["candidate_count"] > 0]
    return {
        "tasks": tasks,
        "routing_snapshots": routing_snapshots,
        "routing_tables": _routing_projection(path),
        "empty": {
            "calls": len(empty_rows),
            "prepare_calls": sum(int(row["prepare_calls"]) for row in empty_rows),
            "score_calls": sum(int(row["score_calls"]) for row in empty_rows),
        },
        "nonempty": {
            "calls": len(nonempty_rows),
            "prepare_calls": sum(int(row["prepare_calls"]) for row in nonempty_rows),
            "score_calls": sum(int(row["score_calls"]) for row in nonempty_rows),
        },
    }


def _rank_semantics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_count": int(row["candidate_count"]),
            "selected_tools": list(row["selected_tools"]),
            "trace": row["trace"],
        }
        for row in rows
    ]


def _nonempty_operation_shape(rows: list[dict[str, Any]]) -> list[dict[str, int]]:
    return [
        {
            "candidate_count": int(row["candidate_count"]),
            "prepare_calls": int(row["prepare_calls"]),
            "score_calls": int(row["score_calls"]),
        }
        for row in rows
        if int(row["candidate_count"]) > 0
    ]


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-empty-serial-semantics-") as tmp:
        root = Path(tmp)
        seed = root / "seed.db"
        seed_engine = EcomEvoEngine(seed)
        warm = await seed_engine.run(
            "审核商家并核对主体、授权和历史风险。serial semantics warm。",
            [],
            domain_hint=DOMAIN,
        )
        if not warm.event_chain_valid:
            failures.append("seed warm-up produced an invalid event chain")

        baseline_db = root / "baseline.db"
        candidate_db = root / "candidate.db"
        _copy_database(seed, baseline_db)
        _copy_database(seed, candidate_db)

        baseline = await _run_arm(baseline_db, short_circuit_empty=False)
        candidate = await _run_arm(candidate_db, short_circuit_empty=True)

        if baseline["empty"]["calls"] <= 0:
            failures.append("serial workload did not exercise an empty rank")
        if baseline["empty"]["calls"] != candidate["empty"]["calls"]:
            failures.append(
                "empty rank count changed: "
                f"{baseline['empty']['calls']} -> {candidate['empty']['calls']}"
            )
        if baseline["empty"]["prepare_calls"] != baseline["empty"]["calls"]:
            failures.append("baseline empty ranks did not each call prepare_context once")
        if baseline["empty"]["score_calls"] != baseline["empty"]["calls"]:
            failures.append("baseline empty ranks did not each call score_prepared once")
        if candidate["empty"]["prepare_calls"] != 0:
            failures.append("candidate empty ranks still called prepare_context")
        if candidate["empty"]["score_calls"] != 0:
            failures.append("candidate empty ranks still called score_prepared")

        if len(baseline["tasks"]) != len(candidate["tasks"]):
            failures.append("task count changed between serial arms")
        else:
            for index, (left, right) in enumerate(zip(baseline["tasks"], candidate["tasks"])):
                if left["summary"] != right["summary"]:
                    failures.append(f"task {index}: runtime summary projection changed")
                if _rank_semantics(left["ranks"]) != _rank_semantics(right["ranks"]):
                    failures.append(f"task {index}: rank selection/trace semantics changed")
                if _nonempty_operation_shape(left["ranks"]) != _nonempty_operation_shape(right["ranks"]):
                    failures.append(f"task {index}: nonempty rank operation shape changed")

        if baseline["routing_snapshots"] != candidate["routing_snapshots"]:
            failures.append("per-task routing learning snapshots changed")
        if baseline["routing_tables"] != candidate["routing_tables"]:
            failures.append("final routing learning tables changed")
        if baseline["nonempty"] != candidate["nonempty"]:
            failures.append(
                "aggregate nonempty rank operation counts changed: "
                f"{baseline['nonempty']} -> {candidate['nonempty']}"
            )

        return {
            "ok": not failures,
            "tasks": TASKS,
            "seed_event_chain_valid": bool(warm.event_chain_valid),
            "baseline": {
                "empty": baseline["empty"],
                "nonempty": baseline["nonempty"],
                "routing_samples": baseline["routing_snapshots"][-1] if baseline["routing_snapshots"] else {},
                "routing_outcomes": len(baseline["routing_tables"]["outcomes"]),
            },
            "candidate": {
                "empty": candidate["empty"],
                "nonempty": candidate["nonempty"],
                "routing_samples": candidate["routing_snapshots"][-1] if candidate["routing_snapshots"] else {},
                "routing_outcomes": len(candidate["routing_tables"]["outcomes"]),
            },
            "summary_projection_equal": [row["summary"] for row in baseline["tasks"]]
            == [row["summary"] for row in candidate["tasks"]],
            "rank_semantics_equal": [
                _rank_semantics(row["ranks"]) for row in baseline["tasks"]
            ]
            == [
                _rank_semantics(row["ranks"]) for row in candidate["tasks"]
            ],
            "nonempty_operation_shape_equal": [
                _nonempty_operation_shape(row["ranks"]) for row in baseline["tasks"]
            ]
            == [
                _nonempty_operation_shape(row["ranks"]) for row in candidate["tasks"]
            ],
            "routing_snapshots_equal": baseline["routing_snapshots"] == candidate["routing_snapshots"],
            "routing_tables_equal": baseline["routing_tables"] == candidate["routing_tables"],
            "failures": failures,
        }


def main() -> int:
    report = asyncio.run(main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
