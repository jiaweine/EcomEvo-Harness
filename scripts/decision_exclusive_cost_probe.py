from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections import Counter, defaultdict
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import ecomevo.runtime.control_policy as control_policy_module
from ecomevo.runtime.engine import EcomEvoEngine


TASKS = 32
DOMAIN = "merchant_review"


def _ms() -> float:
    return time.perf_counter() * 1000.0


def _install_probe(policy: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "sanitize_rows": [],
        "fallback_rows": [],
        "rank_rows": [],
    }
    active_sanitize: ContextVar[dict[str, Any] | None] = ContextVar(
        "decision-exclusive-active-sanitize", default=None
    )
    active_fallback: ContextVar[dict[str, Any] | None] = ContextVar(
        "decision-exclusive-active-fallback", default=None
    )
    active_rank: ContextVar[bool] = ContextVar(
        "decision-exclusive-active-rank", default=False
    )

    original_sanitize = policy.sanitize
    original_fallback = policy.fallback_calls
    original_rank = policy._rank_candidates
    original_skill_policy = policy.skills.policy
    original_validate = policy.sandbox.validate_tool
    original_signature = policy.call_signature
    original_plan = policy.planner.plan
    original_planned_calls = policy.registry.planned_calls
    original_harness_profile = control_policy_module.current_harness_profile
    original_query_terms = control_policy_module._query_terms

    def rank_candidates(candidates, *args, **kwargs):
        sanitize_row = active_sanitize.get()
        token = active_rank.set(True)
        started = _ms()
        try:
            selected, trace = original_rank(candidates, *args, **kwargs)
        finally:
            elapsed = _ms() - started
            active_rank.reset(token)
        row = {
            "phase": str((sanitize_row or {}).get("phase") or "unknown"),
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "trace_count": len(trace),
            "total_ms": elapsed,
        }
        state["rank_rows"].append(row)
        if sanitize_row is not None:
            sanitize_row["rank_calls"] += 1
            sanitize_row["rank_ms"] += elapsed
            sanitize_row["candidate_count"] = len(candidates)
            sanitize_row["selected_count"] = len(selected)
        return selected, trace

    def skill_policy(*args, **kwargs):
        started = _ms()
        try:
            return original_skill_policy(*args, **kwargs)
        finally:
            row = active_sanitize.get()
            if row is not None and not active_rank.get():
                row["skill_policy_calls"] += 1
                row["skill_policy_ms"] += _ms() - started

    def validate_tool(*args, **kwargs):
        started = _ms()
        try:
            return original_validate(*args, **kwargs)
        finally:
            row = active_sanitize.get()
            if row is not None and not active_rank.get():
                row["validate_calls"] += 1
                row["validate_ms"] += _ms() - started

    def call_signature(*args, **kwargs):
        started = _ms()
        try:
            return original_signature(*args, **kwargs)
        finally:
            row = active_sanitize.get()
            if row is not None and not active_rank.get():
                row["signature_calls"] += 1
                row["signature_ms"] += _ms() - started

    def current_harness_profile(*args, **kwargs):
        started = _ms()
        try:
            return original_harness_profile(*args, **kwargs)
        finally:
            elapsed = _ms() - started
            sanitize_row = active_sanitize.get()
            fallback_row = active_fallback.get()
            if sanitize_row is not None and not active_rank.get():
                sanitize_row["harness_profile_calls"] += 1
                sanitize_row["harness_profile_ms"] += elapsed
            elif fallback_row is not None:
                fallback_row["harness_profile_calls"] += 1
                fallback_row["harness_profile_ms"] += elapsed

    def planner_plan(*args, **kwargs):
        started = _ms()
        try:
            return original_plan(*args, **kwargs)
        finally:
            fallback_row = active_fallback.get()
            if fallback_row is not None and active_sanitize.get() is None:
                fallback_row["planner_calls"] += 1
                fallback_row["planner_ms"] += _ms() - started

    def planned_calls(*args, **kwargs):
        started = _ms()
        try:
            return original_planned_calls(*args, **kwargs)
        finally:
            fallback_row = active_fallback.get()
            if fallback_row is not None and active_sanitize.get() is None:
                fallback_row["registry_calls"] += 1
                fallback_row["registry_ms"] += _ms() - started

    def query_terms(*args, **kwargs):
        started = _ms()
        try:
            return original_query_terms(*args, **kwargs)
        finally:
            fallback_row = active_fallback.get()
            if fallback_row is not None and active_sanitize.get() is None:
                fallback_row["query_terms_calls"] += 1
                fallback_row["query_terms_ms"] += _ms() - started

    def sanitize(*args, **kwargs):
        phase = str(kwargs.get("phase") or "unknown")
        row = {
            "phase": phase,
            "total_ms": 0.0,
            "rank_calls": 0,
            "rank_ms": 0.0,
            "candidate_count": None,
            "selected_count": None,
            "skill_policy_calls": 0,
            "skill_policy_ms": 0.0,
            "validate_calls": 0,
            "validate_ms": 0.0,
            "signature_calls": 0,
            "signature_ms": 0.0,
            "harness_profile_calls": 0,
            "harness_profile_ms": 0.0,
        }
        token = active_sanitize.set(row)
        started = _ms()
        try:
            return original_sanitize(*args, **kwargs)
        finally:
            row["total_ms"] = _ms() - started
            row["exclusive_ms"] = max(0.0, row["total_ms"] - row["rank_ms"])
            known = (
                row["skill_policy_ms"]
                + row["validate_ms"]
                + row["signature_ms"]
                + row["harness_profile_ms"]
            )
            row["known_exclusive_child_ms"] = known
            row["exclusive_residual_ms"] = max(0.0, row["exclusive_ms"] - known)
            fallback_row = active_fallback.get()
            if fallback_row is not None and phase == "fallback":
                fallback_row["sanitize_calls"] += 1
                fallback_row["sanitize_ms"] += row["total_ms"]
            state["sanitize_rows"].append(row)
            active_sanitize.reset(token)

    def fallback_calls(*args, **kwargs):
        row = {
            "total_ms": 0.0,
            "sanitize_calls": 0,
            "sanitize_ms": 0.0,
            "planner_calls": 0,
            "planner_ms": 0.0,
            "registry_calls": 0,
            "registry_ms": 0.0,
            "query_terms_calls": 0,
            "query_terms_ms": 0.0,
            "harness_profile_calls": 0,
            "harness_profile_ms": 0.0,
        }
        token = active_fallback.set(row)
        started = _ms()
        try:
            return original_fallback(*args, **kwargs)
        finally:
            row["total_ms"] = _ms() - started
            row["exclusive_ms"] = max(0.0, row["total_ms"] - row["sanitize_ms"])
            known = (
                row["planner_ms"]
                + row["registry_ms"]
                + row["query_terms_ms"]
                + row["harness_profile_ms"]
            )
            row["known_exclusive_child_ms"] = known
            row["exclusive_residual_ms"] = max(0.0, row["exclusive_ms"] - known)
            state["fallback_rows"].append(row)
            active_fallback.reset(token)

    policy._rank_candidates = rank_candidates
    policy.skills.policy = skill_policy
    policy.sandbox.validate_tool = validate_tool
    policy.call_signature = call_signature
    policy.planner.plan = planner_plan
    policy.registry.planned_calls = planned_calls
    control_policy_module.current_harness_profile = current_harness_profile
    control_policy_module._query_terms = query_terms
    policy.sanitize = sanitize
    policy.fallback_calls = fallback_calls
    return state


def _sum(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row.get(key, 0.0) or 0.0) for row in rows)


def _count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(row.get(key, 0) or 0) for row in rows)


def _summarize_sanitize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_ms = _sum(rows, "total_ms")
    rank_ms = _sum(rows, "rank_ms")
    exclusive_ms = _sum(rows, "exclusive_ms")
    known_ms = _sum(rows, "known_exclusive_child_ms")
    return {
        "calls": len(rows),
        "total_ms": round(total_ms, 3),
        "rank_child_ms": round(rank_ms, 3),
        "rank_share": round(rank_ms / total_ms, 4) if total_ms else 0.0,
        "exclusive_ms": round(exclusive_ms, 3),
        "exclusive_share": round(exclusive_ms / total_ms, 4) if total_ms else 0.0,
        "known_exclusive_child_ms": round(known_ms, 3),
        "exclusive_residual_ms": round(_sum(rows, "exclusive_residual_ms"), 3),
        "children": {
            "skills_policy": {
                "calls": _count(rows, "skill_policy_calls"),
                "ms": round(_sum(rows, "skill_policy_ms"), 3),
            },
            "sandbox_validate": {
                "calls": _count(rows, "validate_calls"),
                "ms": round(_sum(rows, "validate_ms"), 3),
            },
            "call_signature": {
                "calls": _count(rows, "signature_calls"),
                "ms": round(_sum(rows, "signature_ms"), 3),
            },
            "harness_profile": {
                "calls": _count(rows, "harness_profile_calls"),
                "ms": round(_sum(rows, "harness_profile_ms"), 3),
            },
        },
        "candidate_count_distribution": dict(
            Counter(str(row.get("candidate_count")) for row in rows)
        ),
    }


def _summarize_fallback(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_ms = _sum(rows, "total_ms")
    sanitize_ms = _sum(rows, "sanitize_ms")
    exclusive_ms = _sum(rows, "exclusive_ms")
    return {
        "calls": len(rows),
        "total_ms": round(total_ms, 3),
        "nested_sanitize_ms": round(sanitize_ms, 3),
        "nested_sanitize_share": round(sanitize_ms / total_ms, 4) if total_ms else 0.0,
        "exclusive_ms": round(exclusive_ms, 3),
        "exclusive_share": round(exclusive_ms / total_ms, 4) if total_ms else 0.0,
        "known_exclusive_child_ms": round(_sum(rows, "known_exclusive_child_ms"), 3),
        "exclusive_residual_ms": round(_sum(rows, "exclusive_residual_ms"), 3),
        "children": {
            "planner_plan": {
                "calls": _count(rows, "planner_calls"),
                "ms": round(_sum(rows, "planner_ms"), 3),
            },
            "registry_planned_calls": {
                "calls": _count(rows, "registry_calls"),
                "ms": round(_sum(rows, "registry_ms"), 3),
            },
            "query_terms_pre_sanitize": {
                "calls": _count(rows, "query_terms_calls"),
                "ms": round(_sum(rows, "query_terms_ms"), 3),
            },
            "harness_profile_pre_sanitize": {
                "calls": _count(rows, "harness_profile_calls"),
                "ms": round(_sum(rows, "harness_profile_ms"), 3),
            },
        },
    }


def _summarize_ranks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_phase[str(row["phase"])].append(row)
        by_candidates[int(row["candidate_count"])].append(row)
    return {
        "calls": len(rows),
        "total_ms": round(_sum(rows, "total_ms"), 3),
        "by_phase": {
            phase: {
                "calls": len(items),
                "total_ms": round(_sum(items, "total_ms"), 3),
            }
            for phase, items in sorted(by_phase.items())
        },
        "by_candidate_count": {
            str(count): {
                "calls": len(items),
                "total_ms": round(_sum(items, "total_ms"), 3),
                "selected_total": sum(int(row["selected_count"]) for row in items),
            }
            for count, items in sorted(by_candidates.items())
        },
    }


async def _run_batch(engine: EcomEvoEngine, tasks: int) -> list[Any]:
    async def one(index: int):
        return await engine.run(
            f"审核商家并核对主体、授权和历史风险。decision exclusive 任务 {index}。",
            [],
            domain_hint=DOMAIN,
        )

    return await asyncio.gather(*(one(index) for index in range(tasks)))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-decision-exclusive-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "runtime.db")
        warm = await _run_batch(engine, 1)
        if not warm[0].event_chain_valid:
            failures.append("warm-up event chain invalid")

        state = _install_probe(engine.autonomy.policy)
        started = time.perf_counter()
        summaries = await _run_batch(engine, TASKS)
        runtime_wall = time.perf_counter() - started
        if any(not summary.event_chain_valid for summary in summaries):
            failures.append("measured event chain invalid")

        sanitize_rows = list(state["sanitize_rows"])
        fallback_rows = list(state["fallback_rows"])
        rank_rows = list(state["rank_rows"])
        sanitize_by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in sanitize_rows:
            sanitize_by_phase[str(row["phase"])].append(row)

        if not fallback_rows:
            failures.append("workload did not exercise fallback_calls")
        if not sanitize_rows:
            failures.append("workload did not exercise sanitize")
        if any(int(row["rank_calls"]) != 1 for row in sanitize_rows):
            failures.append("a sanitize call did not contain exactly one rank call")
        if any(int(row["sanitize_calls"]) != 1 for row in fallback_rows):
            failures.append("a fallback call did not contain exactly one fallback sanitize")
        if len(rank_rows) != len(sanitize_rows):
            failures.append(
                f"rank/sanitize call mismatch: {len(rank_rows)} != {len(sanitize_rows)}"
            )

        return {
            "ok": not failures,
            "tasks": TASKS,
            "runtime_wall_seconds": round(runtime_wall, 4),
            "policy_class": type(engine.autonomy.policy).__name__,
            "fallback": _summarize_fallback(fallback_rows),
            "sanitize": {
                "all": _summarize_sanitize(sanitize_rows),
                "by_phase": {
                    phase: _summarize_sanitize(items)
                    for phase, items in sorted(sanitize_by_phase.items())
                },
            },
            "rank": _summarize_ranks(rank_rows),
            "failures": failures,
        }


def main() -> int:
    report = asyncio.run(main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
