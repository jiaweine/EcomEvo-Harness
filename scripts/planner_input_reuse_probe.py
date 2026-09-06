from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import ecomevo.runtime.planner as planner_module
from ecomevo.runtime import EcomEvoEngine


TASKS = 120


def _digest(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _normalize_calls(calls: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for item in calls:
        data = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        rows.append(
            {
                "tool": data.get("tool"),
                "purpose": data.get("purpose"),
                "args": data.get("args") or {},
                "estimated_cost": data.get("estimated_cost", data.get("cost")),
                "parallel_group": data.get("parallel_group"),
            }
        )
    return rows


def _semantic_input(planner: Any, goal: Any, belief: Any, recovery: bool) -> dict[str, Any]:
    facts = belief.facts if isinstance(getattr(belief, "facts", None), dict) else {}
    domain = str(goal.domain.value)
    return {
        # Only values actually read by AdaptivePlanner.plan are fingerprinted. Assets are
        # deliberately excluded because production plan() does not inspect that argument.
        "goal_primary_digest": _digest(str(goal.primary)),
        "domain": domain,
        "max_tool_cost": float(goal.max_tool_cost),
        "memory_watch_terms": [
            str(value)
            for value in (facts.get("memory_watch_terms") or [])
            if str(value).strip()
        ],
        "conversation_context_terms": [
            str(value)
            for value in (facts.get("conversation_context_terms") or [])
            if str(value).strip()
        ],
        "missing_evidence": list(belief.missing_evidence),
        "learned_checks": list(planner.learned_checks.get(domain, [])),
        "recovery": bool(recovery),
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-planner-input-reuse-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "probe.db")
        planner = engine.planner

        task_var: ContextVar[int | None] = ContextVar("planner-probe-task", default=None)
        in_plan_var: ContextVar[bool] = ContextVar("planner-probe-in-plan", default=False)
        per_task_ordinal: Counter[int] = Counter()
        rows: list[dict[str, Any]] = []
        term_rows: list[dict[str, Any]] = []

        original_plan = planner.plan
        original_query_terms = planner_module._query_terms

        def wrapped_query_terms(query: str, limit: int = 40):
            if not in_plan_var.get():
                return original_query_terms(query, limit=limit)
            started = time.perf_counter()
            result = original_query_terms(query, limit=limit)
            term_rows.append(
                {
                    "task": task_var.get(),
                    "query_digest": _digest(str(query)),
                    "limit": int(limit),
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                    "term_count": len(result),
                }
            )
            return result

        def wrapped_plan(goal, belief, assets, *, recovery=False):
            task_id = task_var.get()
            if task_id is not None:
                ordinal = per_task_ordinal[task_id]
                per_task_ordinal[task_id] += 1
            else:
                ordinal = -1
            semantic = _semantic_input(planner, goal, belief, bool(recovery))
            input_signature = _digest(semantic)
            token = in_plan_var.set(True)
            started = time.perf_counter()
            try:
                result = original_plan(goal, belief, assets, recovery=recovery)
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                in_plan_var.reset(token)
            normalized = _normalize_calls(result)
            rows.append(
                {
                    "task": task_id,
                    "ordinal": ordinal,
                    "recovery": bool(recovery),
                    "input_signature": input_signature,
                    "output_signature": _digest(normalized),
                    "output_calls": len(normalized),
                    "missing_count": len(semantic["missing_evidence"]),
                    "learned_count": len(semantic["learned_checks"]),
                    "asset_count": len(assets),
                    "elapsed_ms": elapsed_ms,
                }
            )
            return result

        planner_module._query_terms = wrapped_query_terms
        planner.plan = wrapped_plan

        async def one(index: int) -> None:
            token = task_var.set(index)
            try:
                summary = await engine.run(
                    f"审核商家并核对主体、授权和历史风险。planner reuse probe {index}。",
                    [],
                    domain_hint="merchant_review",
                )
                if not summary.event_chain_valid:
                    failures.append(f"{index}: invalid event chain")
            finally:
                task_var.reset(token)

        started = time.perf_counter()
        try:
            await asyncio.gather(*(one(index) for index in range(TASKS)))
        finally:
            planner.plan = original_plan
            planner_module._query_terms = original_query_terms
        wall_seconds = time.perf_counter() - started

        by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["task"] is not None:
                by_task[int(row["task"])].append(row)
        for task_rows in by_task.values():
            task_rows.sort(key=lambda row: int(row["ordinal"]))

        recovery_pairs: list[dict[str, Any]] = []
        for task_id, task_rows in sorted(by_task.items()):
            recovery_rows = [row for row in task_rows if row["recovery"]]
            if len(recovery_rows) < 2:
                continue
            first, second = recovery_rows[0], recovery_rows[1]
            recovery_pairs.append(
                {
                    "task": task_id,
                    "same_input": first["input_signature"] == second["input_signature"],
                    "same_output": first["output_signature"] == second["output_signature"],
                    "first_missing": int(first["missing_count"]),
                    "second_missing": int(second["missing_count"]),
                    "first_learned": int(first["learned_count"]),
                    "second_learned": int(second["learned_count"]),
                }
            )

        plan_by_shape: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            plan_by_shape[f"{'recovery' if row['recovery'] else 'initial'}:{row['output_calls']}"] .append(
                float(row["elapsed_ms"])
            )

        query_by_shape: dict[str, list[float]] = defaultdict(list)
        for row in term_rows:
            query_by_shape[f"limit={row['limit']}"] .append(float(row["elapsed_ms"]))

        term_repeat_beyond_first_within_task = 0
        term_by_task_digest: Counter[tuple[int, str, int]] = Counter()
        for row in term_rows:
            if row["task"] is not None:
                term_by_task_digest[(int(row["task"]), str(row["query_digest"]), int(row["limit"]))] += 1
        for count in term_by_task_digest.values():
            term_repeat_beyond_first_within_task += max(0, count - 1)

        same_input = sum(bool(row["same_input"]) for row in recovery_pairs)
        same_output = sum(bool(row["same_output"]) for row in recovery_pairs)
        same_both = sum(bool(row["same_input"] and row["same_output"]) for row in recovery_pairs)
        plan_times = [float(row["elapsed_ms"]) for row in rows]
        query_times = [float(row["elapsed_ms"]) for row in term_rows]

        result = {
            "ok": not failures,
            "tasks": TASKS,
            "wall_seconds": round(wall_seconds, 4),
            "plan_calls": len(rows),
            "plan_phase_counts": dict(
                Counter("recovery" if row["recovery"] else "initial" for row in rows)
            ),
            "calls_per_task": {
                "min": min((len(value) for value in by_task.values()), default=0),
                "median": statistics.median([len(value) for value in by_task.values()]) if by_task else 0,
                "max": max((len(value) for value in by_task.values()), default=0),
            },
            "plan_timing_ms": {
                "total": round(sum(plan_times), 3),
                "p50": round(_percentile(plan_times, 0.50), 4),
                "p95": round(_percentile(plan_times, 0.95), 4),
                "by_shape": {
                    shape: {
                        "calls": len(values),
                        "total": round(sum(values), 3),
                        "p50": round(_percentile(values, 0.50), 4),
                    }
                    for shape, values in sorted(plan_by_shape.items())
                },
            },
            "recovery_pairing": {
                "tasks_with_two_recovery_calls": len(recovery_pairs),
                "same_semantic_input": same_input,
                "same_semantic_input_ratio": round(same_input / max(1, len(recovery_pairs)), 4),
                "same_normalized_output": same_output,
                "same_normalized_output_ratio": round(same_output / max(1, len(recovery_pairs)), 4),
                "same_input_and_output": same_both,
                "same_input_and_output_ratio": round(same_both / max(1, len(recovery_pairs)), 4),
            },
            "query_terms": {
                "calls": len(term_rows),
                "total_ms": round(sum(query_times), 3),
                "p50_ms": round(_percentile(query_times, 0.50), 4),
                "p95_ms": round(_percentile(query_times, 0.95), 4),
                "within_task_repeat_calls_beyond_first": term_repeat_beyond_first_within_task,
                "unique_task_query_keys": len(term_by_task_digest),
                "by_shape": {
                    shape: {
                        "calls": len(values),
                        "total_ms": round(sum(values), 3),
                    }
                    for shape, values in sorted(query_by_shape.items())
                },
            },
            "privacy": {
                "raw_goal_or_belief_text_emitted": False,
                "input_output_storage": "sha256 fingerprints plus structural counts only",
            },
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main() -> int:
    result = asyncio.run(main_async())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
