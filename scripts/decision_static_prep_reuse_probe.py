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


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-decision-static-prep-") as tmp:
        db = Path(tmp) / "probe.db"
        engine = EcomEvoEngine(db)
        policy = engine.autonomy.policy

        phase_var: ContextVar[str] = ContextVar("decision-static-probe-phase", default="unknown")
        sanitize_rows: list[dict[str, Any]] = []
        prep_rows: list[dict[str, Any]] = []
        term_rows: list[dict[str, Any]] = []
        fallback_times_ms: list[float] = []

        original_sanitize = policy.sanitize
        original_prepare = policy._prepare_rank_feature_snapshot
        original_terms = policy._terms
        original_fallback = policy.fallback_calls

        def wrapped_sanitize(*args, **kwargs):
            phase = str(kwargs.get("phase") or "unknown")
            token = phase_var.set(phase)
            before = policy._decision_round.get()
            started = time.perf_counter()
            try:
                result = original_sanitize(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                after = policy._decision_round.get()
                sanitize_rows.append(
                    {
                        "phase": phase,
                        "elapsed_ms": elapsed_ms,
                        "round_before": id(before) if before is not None else None,
                        "round_after": id(after) if after is not None else None,
                        "raw_tool_count": len(
                            [
                                row
                                for row in ((kwargs.get("raw") or {}).get("tool_calls") or [])
                                if isinstance(row, dict)
                            ]
                        )
                        if isinstance(kwargs.get("raw"), dict)
                        else 0,
                        "decision_calls": len(getattr(locals().get("result", None), "calls", []) or []),
                    }
                )
                phase_var.reset(token)
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
            started = time.perf_counter()
            result = original_terms(value)
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

        prep_by_phase: dict[str, list[float]] = defaultdict(list)
        for row in prep_rows:
            prep_by_phase[str(row["phase"])].append(float(row["elapsed_ms"]))
        terms_by_phase: dict[str, list[float]] = defaultdict(list)
        for row in term_rows:
            terms_by_phase[str(row["phase"])].append(float(row["elapsed_ms"]))

        return {
            "ok": not failures,
            "tasks": TASKS,
            "wall_seconds": round(wall_seconds, 4),
            "sanitize_calls": len(sanitize_rows),
            "sanitize_phase_counts": dict(Counter(row["phase"] for row in sanitize_rows)),
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
            "feature_prepare": {
                phase: {
                    "calls": len(values),
                    "total_ms": round(sum(values), 3),
                    "p50_ms": round(_percentile(values, 0.50), 4),
                    "p95_ms": round(_percentile(values, 0.95), 4),
                }
                for phase, values in sorted(prep_by_phase.items())
            },
            "terms": {
                phase: {
                    "calls": len(values),
                    "total_ms": round(sum(values), 3),
                    "p50_ms": round(_percentile(values, 0.50), 4),
                    "p95_ms": round(_percentile(values, 0.95), 4),
                }
                for phase, values in sorted(terms_by_phase.items())
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
