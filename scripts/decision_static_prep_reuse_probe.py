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

from ecomevo.runtime import EcomEvoEngine
import ecomevo.runtime.control_policy as control_policy


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


def _static_signature(*, goal, missing, previous, skills) -> str:
    success_counts = Counter(
        str(result.tool)
        for result in previous
        if bool(getattr(result, "ok", False))
    )
    skill_rows = sorted(
        (
            str(skill.skill_id),
            round(float(skill.posterior_mean), 12),
            tuple(str(tool) for tool in skill.preferred_tools),
        )
        for skill in skills
    )
    return _digest(
        {
            "domain": str(goal.domain.value),
            "required": list(goal.required_evidence),
            "missing": list(missing),
            "has_previous": bool(previous),
            "prior_success": sorted(success_counts.items()),
            "skills": skill_rows,
        }
    )


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _timing_summary(rows: list[dict[str, Any]], phase_key: str = "phase") -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row[phase_key])].append(float(row["elapsed_ms"]))
    return {
        phase: {
            "calls": len(values),
            "total_ms": round(sum(values), 3),
            "p50_ms": round(_percentile(values, 0.50), 4),
            "p95_ms": round(_percentile(values, 0.95), 4),
        }
        for phase, values in sorted(grouped.items())
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-decision-static-prep-") as tmp:
        db = Path(tmp) / "probe.db"
        engine = EcomEvoEngine(db)
        policy = engine.autonomy.policy

        phase_var: ContextVar[str] = ContextVar("decision-static-probe-phase", default="unknown")
        inside_terms_var: ContextVar[bool] = ContextVar("decision-static-inside-terms", default=False)
        sanitize_rows: list[dict[str, Any]] = []
        rank_rows: list[dict[str, Any]] = []
        prep_rows: list[dict[str, Any]] = []
        term_rows: list[dict[str, Any]] = []
        query_term_rows: list[dict[str, Any]] = []
        fallback_times_ms: list[float] = []

        original_sanitize = policy.sanitize
        original_rank = policy._rank_candidates
        original_prepare = policy._prepare_rank_feature_snapshot
        original_terms = policy._terms
        original_fallback = policy.fallback_calls
        original_query_terms = control_policy._query_terms

        def wrapped_query_terms(query, limit=40):
            phase = phase_var.get()
            source = "terms" if inside_terms_var.get() else "direct"
            started = time.perf_counter()
            result = original_query_terms(query, limit=limit)
            query_term_rows.append(
                {
                    "phase": phase,
                    "source": source,
                    "limit": int(limit),
                    "input_chars": len(str(query or "")),
                    "result_count": len(result),
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                }
            )
            return result

        control_policy._query_terms = wrapped_query_terms

        def wrapped_sanitize(*args, **kwargs):
            phase = str(kwargs.get("phase") or "unknown")
            token = phase_var.set(phase)
            before = policy._decision_round.get()
            raw_value = args[0] if args else kwargs.get("raw")
            started = time.perf_counter()
            result = None
            try:
                result = original_sanitize(*args, **kwargs)
                return result
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                after = policy._decision_round.get()
                raw_tool_count = 0
                if isinstance(raw_value, dict):
                    raw_tool_count = len(
                        [
                            row
                            for row in (raw_value.get("tool_calls") or [])
                            if isinstance(row, dict)
                        ]
                    )
                sanitize_rows.append(
                    {
                        "phase": phase,
                        "elapsed_ms": elapsed_ms,
                        "round_before": id(before) if before is not None else None,
                        "round_after": id(after) if after is not None else None,
                        "raw_tool_count": raw_tool_count,
                        "decision_calls": len(getattr(result, "calls", []) or []),
                        "decision_delegations": len(getattr(result, "delegations", []) or []),
                        "selection_trace": len(getattr(result, "selection_trace", []) or []),
                    }
                )
                phase_var.reset(token)

        def wrapped_rank(candidates, *args, **kwargs):
            phase = phase_var.get()
            current = policy._decision_round.get()
            started = time.perf_counter()
            result = original_rank(candidates, *args, **kwargs)
            rank_rows.append(
                {
                    "phase": phase,
                    "round_id": id(current) if current is not None else None,
                    "candidate_count": len(candidates),
                    "selected_count": len(result[0]),
                    "trace_count": len(result[1]),
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                }
            )
            return result

        def wrapped_prepare(*args, **kwargs):
            phase = phase_var.get()
            current = policy._decision_round.get()
            started = time.perf_counter()
            result = original_prepare(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            tools = [str(tool) for tool in (kwargs.get("tools") or [])]
            prep_rows.append(
                {
                    "phase": phase,
                    "round_id": id(current) if current is not None else None,
                    "static_signature": _static_signature(
                        goal=kwargs["goal"],
                        missing=kwargs.get("missing") or [],
                        previous=kwargs.get("previous") or [],
                        skills=kwargs.get("skills") or [],
                    ),
                    "tools": list(dict.fromkeys(tools)),
                    "elapsed_ms": elapsed_ms,
                }
            )
            return result

        def wrapped_terms(value):
            phase = phase_var.get()
            current = policy._decision_round.get()
            token = inside_terms_var.set(True)
            started = time.perf_counter()
            try:
                result = original_terms(value)
            finally:
                inside_terms_var.reset(token)
            term_rows.append(
                {
                    "phase": phase,
                    "round_id": id(current) if current is not None else None,
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                }
            )
            return result

        def wrapped_fallback(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original_fallback(*args, **kwargs)
            finally:
                fallback_times_ms.append((time.perf_counter() - started) * 1000.0)

        policy.sanitize = wrapped_sanitize
        policy._rank_candidates = wrapped_rank
        policy._prepare_rank_feature_snapshot = wrapped_prepare
        policy._terms = wrapped_terms
        policy.fallback_calls = wrapped_fallback

        async def one(index: int) -> None:
            summary = await engine.run(
                f"审核商家并核对主体、授权和历史风险。静态准备复用探针 {index}。",
                [],
                domain_hint="merchant_review",
            )
            if not summary.event_chain_valid:
                failures.append(f"{index}: invalid event chain")

        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(TASKS)))
        wall_seconds = time.perf_counter() - started

        by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in prep_rows:
            if row["round_id"] is not None:
                by_round[int(row["round_id"])].append(row)

        paired_rounds = []
        for round_id, rows in by_round.items():
            outer = [row for row in rows if row["phase"] != "fallback"]
            fallback = [row for row in rows if row["phase"] == "fallback"]
            if not outer or not fallback:
                continue
            left = outer[-1]
            right = fallback[0]
            left_tools = set(left["tools"])
            right_tools = set(right["tools"])
            paired_rounds.append(
                {
                    "round_id": round_id,
                    "same_static_signature": left["static_signature"] == right["static_signature"],
                    "outer_tools": len(left_tools),
                    "fallback_tools": len(right_tools),
                    "overlap_tools": len(left_tools & right_tools),
                    "outer_subset_of_fallback": left_tools <= right_tools,
                    "outer_prepare_ms": float(left["elapsed_ms"]),
                    "fallback_prepare_ms": float(right["elapsed_ms"]),
                }
            )

        same_static = sum(bool(row["same_static_signature"]) for row in paired_rounds)
        subset = sum(bool(row["outer_subset_of_fallback"]) for row in paired_rounds)
        overlap_ratios = [
            float(row["overlap_tools"]) / max(1, int(row["outer_tools"]))
            for row in paired_rounds
        ]

        rank_shape_counts = Counter(
            f"{row['phase']}:{int(row['candidate_count'])}" for row in rank_rows
        )
        zero_rank_by_phase = Counter(
            row["phase"] for row in rank_rows if int(row["candidate_count"]) == 0
        )
        sanitize_output = {
            phase: {
                "calls": len(rows),
                "raw_tool_count_total": sum(int(row["raw_tool_count"]) for row in rows),
                "decision_calls_total": sum(int(row["decision_calls"]) for row in rows),
                "decision_delegations_total": sum(
                    int(row["decision_delegations"]) for row in rows
                ),
                "selection_trace_total": sum(int(row["selection_trace"]) for row in rows),
            }
            for phase in sorted({str(row["phase"]) for row in sanitize_rows})
            for rows in [[row for row in sanitize_rows if row["phase"] == phase]]
        }
        query_shapes = Counter(
            f"{row['source']}:{row['phase']}:limit={int(row['limit'])}"
            for row in query_term_rows
        )
        query_timing: dict[str, list[float]] = defaultdict(list)
        for row in query_term_rows:
            key = f"{row['source']}:{row['phase']}:limit={int(row['limit'])}"
            query_timing[key].append(float(row["elapsed_ms"]))

        return {
            "ok": not failures,
            "tasks": TASKS,
            "wall_seconds": round(wall_seconds, 4),
            "sanitize_calls": len(sanitize_rows),
            "sanitize_phase_counts": dict(Counter(row["phase"] for row in sanitize_rows)),
            "sanitize_output": sanitize_output,
            "rank_calls": len(rank_rows),
            "rank_candidate_shapes": dict(sorted(rank_shape_counts.items())),
            "zero_candidate_rank_calls_by_phase": dict(zero_rank_by_phase),
            "rank_timing": _timing_summary(rank_rows),
            "fallback_calls": len(fallback_times_ms),
            "feature_prepare_calls": len(prep_rows),
            "feature_prepare_phase_counts": dict(Counter(row["phase"] for row in prep_rows)),
            "paired_outer_fallback_rounds": len(paired_rounds),
            "paired_same_static_signature": same_static,
            "paired_same_static_signature_ratio": round(
                same_static / max(1, len(paired_rounds)), 4
            ),
            "paired_outer_subset_of_fallback": subset,
            "paired_outer_subset_ratio": round(subset / max(1, len(paired_rounds)), 4),
            "candidate_overlap_ratio_median": round(statistics.median(overlap_ratios), 4)
            if overlap_ratios
            else 0.0,
            "feature_prepare": _timing_summary(prep_rows),
            "terms": _timing_summary(term_rows),
            "query_terms_calls": len(query_term_rows),
            "query_terms_shapes": dict(sorted(query_shapes.items())),
            "query_terms_timing": {
                key: {
                    "calls": len(values),
                    "total_ms": round(sum(values), 3),
                    "p50_ms": round(_percentile(values, 0.50), 4),
                    "p95_ms": round(_percentile(values, 0.95), 4),
                }
                for key, values in sorted(query_timing.items())
            },
            "fallback_latency_ms": {
                "total": round(sum(fallback_times_ms), 3),
                "p50": round(_percentile(fallback_times_ms, 0.50), 4),
                "p95": round(_percentile(fallback_times_ms, 0.95), 4),
            },
            "paired_sample": [
                {
                    key: value
                    for key, value in row.items()
                    if key != "round_id"
                }
                for row in paired_rounds[:8]
            ],
            "failures": failures,
        }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
