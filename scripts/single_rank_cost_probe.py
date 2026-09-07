from __future__ import annotations

import asyncio
import json
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine


TASKS = 32
DOMAIN = "merchant_review"


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _install_probe(policy: Any) -> dict[str, Any]:
    state: dict[str, Any] = {"single_rows": [], "all_rank_counts": {}}
    active_single: ContextVar[dict[str, Any] | None] = ContextVar(
        "single-rank-cost-active", default=None
    )
    active_features: ContextVar[bool] = ContextVar(
        "single-rank-cost-features", default=False
    )

    original_rank = policy._rank_candidates
    original_skill_policy = policy.skills.policy
    original_prepare = policy.routing.prepare_context
    original_base_features = policy._base_features
    original_abstain = policy.routing.abstain_vector
    original_score = policy.routing.score_prepared
    original_vector = policy._vector
    original_tool_meta = policy._tool_meta
    original_terms = policy._terms

    def _timed_child(name: str, fn, *args, **kwargs):
        started = _now_ms()
        try:
            return fn(*args, **kwargs)
        finally:
            row = active_single.get()
            if row is not None:
                row[f"{name}_calls"] += 1
                row[f"{name}_ms"] += _now_ms() - started

    def skill_policy(*args, **kwargs):
        return _timed_child("skills_policy", original_skill_policy, *args, **kwargs)

    def prepare_context(*args, **kwargs):
        return _timed_child("prepare", original_prepare, *args, **kwargs)

    def base_features(*args, **kwargs):
        row = active_single.get()
        token = active_features.set(row is not None)
        started = _now_ms()
        try:
            return original_base_features(*args, **kwargs)
        finally:
            if row is not None:
                row["base_features_calls"] += 1
                row["base_features_ms"] += _now_ms() - started
            active_features.reset(token)

    def abstain_vector(*args, **kwargs):
        return _timed_child("abstain", original_abstain, *args, **kwargs)

    def score_prepared(*args, **kwargs):
        return _timed_child("score", original_score, *args, **kwargs)

    def vector(*args, **kwargs):
        return _timed_child("vector", original_vector, *args, **kwargs)

    def tool_meta(*args, **kwargs):
        started = _now_ms()
        try:
            return original_tool_meta(*args, **kwargs)
        finally:
            row = active_single.get()
            if row is not None and active_features.get():
                row["feature_tool_meta_calls"] += 1
                row["feature_tool_meta_ms"] += _now_ms() - started

    def terms(*args, **kwargs):
        started = _now_ms()
        try:
            return original_terms(*args, **kwargs)
        finally:
            row = active_single.get()
            if row is not None and active_features.get():
                row["feature_terms_calls"] += 1
                row["feature_terms_ms"] += _now_ms() - started

    def rank_candidates(candidates, *args, **kwargs):
        count = len(candidates)
        state["all_rank_counts"][str(count)] = int(state["all_rank_counts"].get(str(count), 0)) + 1
        if count != 1:
            return original_rank(candidates, *args, **kwargs)

        row = {
            "total_ms": 0.0,
            "skills_policy_calls": 0,
            "skills_policy_ms": 0.0,
            "prepare_calls": 0,
            "prepare_ms": 0.0,
            "base_features_calls": 0,
            "base_features_ms": 0.0,
            "abstain_calls": 0,
            "abstain_ms": 0.0,
            "score_calls": 0,
            "score_ms": 0.0,
            "vector_calls": 0,
            "vector_ms": 0.0,
            "feature_tool_meta_calls": 0,
            "feature_tool_meta_ms": 0.0,
            "feature_terms_calls": 0,
            "feature_terms_ms": 0.0,
            "selected_count": 0,
            "trace_count": 0,
        }
        token = active_single.set(row)
        started = _now_ms()
        try:
            selected, trace = original_rank(candidates, *args, **kwargs)
            row["selected_count"] = len(selected)
            row["trace_count"] = len(trace)
            return selected, trace
        finally:
            row["total_ms"] = _now_ms() - started
            child_ms = (
                row["skills_policy_ms"]
                + row["prepare_ms"]
                + row["base_features_ms"]
                + row["abstain_ms"]
                + row["score_ms"]
                + row["vector_ms"]
            )
            row["known_child_ms"] = child_ms
            row["orchestration_residual_ms"] = max(0.0, row["total_ms"] - child_ms)
            row["base_features_residual_ms"] = max(
                0.0,
                row["base_features_ms"]
                - row["feature_tool_meta_ms"]
                - row["feature_terms_ms"],
            )
            state["single_rows"].append(row)
            active_single.reset(token)

    policy._rank_candidates = rank_candidates
    policy.skills.policy = skill_policy
    policy.routing.prepare_context = prepare_context
    policy._base_features = base_features
    policy.routing.abstain_vector = abstain_vector
    policy.routing.score_prepared = score_prepared
    policy._vector = vector
    policy._tool_meta = tool_meta
    policy._terms = terms
    return state


def _sum(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row.get(key, 0.0) or 0.0) for row in rows)


def _count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(row.get(key, 0) or 0) for row in rows)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_ms = _sum(rows, "total_ms")
    known_ms = _sum(rows, "known_child_ms")
    return {
        "calls": len(rows),
        "total_ms": round(total_ms, 3),
        "ms_per_call": round(total_ms / max(1, len(rows)), 4),
        "selected_total": _count(rows, "selected_count"),
        "trace_total": _count(rows, "trace_count"),
        "known_child_ms": round(known_ms, 3),
        "known_child_share": round(known_ms / total_ms, 4) if total_ms else 0.0,
        "orchestration_residual_ms": round(_sum(rows, "orchestration_residual_ms"), 3),
        "children": {
            "skills_policy": {
                "calls": _count(rows, "skills_policy_calls"),
                "ms": round(_sum(rows, "skills_policy_ms"), 3),
            },
            "prepare_context": {
                "calls": _count(rows, "prepare_calls"),
                "ms": round(_sum(rows, "prepare_ms"), 3),
            },
            "base_features": {
                "calls": _count(rows, "base_features_calls"),
                "ms": round(_sum(rows, "base_features_ms"), 3),
                "subchildren": {
                    "tool_meta": {
                        "calls": _count(rows, "feature_tool_meta_calls"),
                        "ms": round(_sum(rows, "feature_tool_meta_ms"), 3),
                    },
                    "terms": {
                        "calls": _count(rows, "feature_terms_calls"),
                        "ms": round(_sum(rows, "feature_terms_ms"), 3),
                    },
                    "residual_ms": round(_sum(rows, "base_features_residual_ms"), 3),
                },
            },
            "abstain_vector": {
                "calls": _count(rows, "abstain_calls"),
                "ms": round(_sum(rows, "abstain_ms"), 3),
            },
            "score_prepared": {
                "calls": _count(rows, "score_calls"),
                "ms": round(_sum(rows, "score_ms"), 3),
            },
            "candidate_vector": {
                "calls": _count(rows, "vector_calls"),
                "ms": round(_sum(rows, "vector_ms"), 3),
            },
        },
    }


async def _run_batch(engine: EcomEvoEngine, tasks: int) -> list[Any]:
    async def one(index: int):
        return await engine.run(
            f"审核商家并核对主体、授权和历史风险。single-rank cost 任务 {index}。",
            [],
            domain_hint=DOMAIN,
        )

    return await asyncio.gather(*(one(index) for index in range(tasks)))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-single-rank-cost-") as tmp:
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

        rows = list(state["single_rows"])
        if not rows:
            failures.append("workload did not exercise a single-candidate rank")
        if any(int(row["base_features_calls"]) != 1 for row in rows):
            failures.append("single-candidate rank did not call base_features exactly once")
        if any(int(row["score_calls"]) != 2 for row in rows):
            failures.append("single-candidate rank did not call score_prepared exactly twice")
        if any(int(row["prepare_calls"]) != 1 for row in rows):
            failures.append("single-candidate rank did not call prepare_context exactly once")
        if any(int(row["selected_count"]) != 1 for row in rows):
            failures.append("single-candidate rank did not select exactly one candidate")

        result = {
            "ok": not failures,
            "tasks": TASKS,
            "runtime_wall_seconds": round(runtime_wall, 4),
            "policy_class": type(engine.autonomy.policy).__name__,
            "rank_candidate_distribution": state["all_rank_counts"],
            "single_candidate": _summary(rows),
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main() -> int:
    report = asyncio.run(main_async())
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
