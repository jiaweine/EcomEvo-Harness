from __future__ import annotations

import asyncio
import copy
import json
import statistics
import tempfile
import time
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import ecomevo.runtime.control_policy as control_policy
from ecomevo.runtime import EcomEvoEngine


EXPERIMENTS = 5
WARMUP_CALLS = 24
MEASURED_CALLS = 320
POSITIVE_RATIO = 0.95


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


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


async def _capture_fallback(engine: EcomEvoEngine) -> tuple[tuple[Any, ...], dict[str, Any]]:
    policy = engine.autonomy.policy
    original = policy.fallback_calls
    captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def capture(*args, **kwargs):
        if not captured:
            captured.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
        return original(*args, **kwargs)

    policy.fallback_calls = capture
    try:
        summary = await engine.run(
            "审核商家并核对主体、授权和历史风险。query-term production gate。",
            [],
            domain_hint="merchant_review",
        )
    finally:
        policy.fallback_calls = original
    if not summary.event_chain_valid:
        raise AssertionError("capture run produced invalid event chain")
    if not captured:
        raise AssertionError("capture run did not enter fallback_calls")
    return captured[0]


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-query-term-production-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "gate.db")
        policy = engine.autonomy.policy
        captured_args, captured_kwargs = await _capture_fallback(engine)

        goal = captured_args[0]
        belief = captured_args[1]
        assets = captured_args[2]
        remaining_budget = float(captured_kwargs["remaining_budget"])
        previous = captured_kwargs["previous"]
        skills = captured_kwargs["skills"]

        # Fixed-input shape only. This discovery is outside every timed arm.
        shape_candidates = list(policy.planner.plan(goal, belief, assets, recovery=True))
        shape_candidates += list(policy.registry.planned_calls(goal.domain.value, recovery=True))
        search_candidates = sum(
            1 for item in shape_candidates if str(getattr(item, "tool", "")) == "evidence.search"
        )
        if search_candidates < 2:
            failures.append(
                f"fixed fallback workload lost repeated search shape: {search_candidates} < 2"
            )

        profile = control_policy.current_harness_profile()
        components = profile.get("components") if isinstance(profile.get("components"), dict) else {}
        memory_component = components.get("memory") if isinstance(components.get("memory"), dict) else {}
        memory_terms = [
            str(value).strip()
            for value in (memory_component.get("retrieval_terms") or [])
            if str(value).strip()
        ]
        legacy_limit24_query = " ".join(
            [goal.primary] + list(goal.required_evidence) + memory_terms
        )

        mode_var: ContextVar[str] = ContextVar("query-term-production-mode", default="candidate")
        in_fallback_var: ContextVar[bool] = ContextVar("query-term-production-fallback", default=False)
        count_var: ContextVar[bool] = ContextVar("query-term-production-count", default=False)

        underlying_counts: dict[str, Counter[int]] = {
            "baseline": Counter(),
            "candidate": Counter(),
        }

        original_query_terms = control_policy._query_terms
        original_fallback = policy.fallback_calls

        def wrapped_query_terms(query: str, limit: int = 40):
            mode = mode_var.get()
            numeric_limit = int(limit)
            if mode == "baseline" and in_fallback_var.get() and numeric_limit == 16:
                if count_var.get():
                    underlying_counts[mode][16] += max(1, search_candidates)
                value = original_query_terms(query, limit=limit)
                for _ in range(max(0, search_candidates - 1)):
                    original_query_terms(query, limit=limit)
                return value
            if count_var.get():
                underlying_counts[mode][numeric_limit] += 1
            return original_query_terms(query, limit=limit)

        def wrapped_fallback(*args, **kwargs):
            mode = mode_var.get()
            token = in_fallback_var.set(True)
            try:
                if mode == "baseline":
                    # Reinsert only the old eager sanitize work whose results were unused.
                    for _ in range(search_candidates):
                        if count_var.get():
                            underlying_counts[mode][24] += 1
                        original_query_terms(legacy_limit24_query, limit=24)
                return original_fallback(*args, **kwargs)
            finally:
                in_fallback_var.reset(token)

        control_policy._query_terms = wrapped_query_terms
        policy.fallback_calls = wrapped_fallback

        def prepare_recovery_round() -> None:
            policy._decision_round.set(None)
            policy.sanitize(
                None,
                goal=goal,
                remaining_budget=remaining_budget,
                previous=previous,
                skills=skills,
                phase="recovery",
                missing_evidence=list(belief.missing_evidence),
            )

        expected_output: list[dict[str, Any]] | None = None
        per_call_ms: dict[str, list[float]] = {"baseline": [], "candidate": []}
        paired_rows: list[dict[str, Any]] = []

        def run_arm(mode: str, *, measured: bool) -> tuple[float, int]:
            nonlocal expected_output
            mode_token = mode_var.set(mode)
            count_token = count_var.set(measured)
            calls = MEASURED_CALLS if measured else WARMUP_CALLS
            elapsed_total = 0.0
            mismatches = 0
            try:
                for _ in range(calls):
                    prepare_recovery_round()
                    started = time.perf_counter()
                    output = policy.fallback_calls(
                        goal,
                        belief,
                        assets,
                        remaining_budget=remaining_budget,
                        previous=previous,
                        skills=skills,
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    normalized = _normalize_calls(output)
                    if expected_output is None:
                        expected_output = normalized
                    elif normalized != expected_output:
                        mismatches += 1
                    if measured:
                        elapsed_total += elapsed_ms
                        per_call_ms[mode].append(elapsed_ms)
            finally:
                count_var.reset(count_token)
                mode_var.reset(mode_token)
            return elapsed_total, mismatches

        run_arm("baseline", measured=False)
        run_arm("candidate", measured=False)

        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "candidate")
                if experiment % 2 == 0
                else ("candidate", "baseline")
            )
            results: dict[str, tuple[float, int]] = {}
            for arm in order:
                results[arm] = run_arm(arm, measured=True)
            baseline_ms, baseline_mismatches = results["baseline"]
            candidate_ms, candidate_mismatches = results["candidate"]
            if baseline_mismatches or candidate_mismatches:
                failures.append(
                    f"experiment {experiment} changed normalized ToolCall output: "
                    f"baseline={baseline_mismatches}, candidate={candidate_mismatches}"
                )
            paired_rows.append(
                {
                    "experiment": experiment,
                    "order": list(order),
                    "baseline_ms": baseline_ms,
                    "candidate_ms": candidate_ms,
                    "ratio": candidate_ms / baseline_ms if baseline_ms else 0.0,
                }
            )

        measured_per_mode = EXPERIMENTS * MEASURED_CALLS
        baseline_counts = underlying_counts["baseline"]
        candidate_counts = underlying_counts["candidate"]
        expected_legacy_search_calls = search_candidates * measured_per_mode

        if candidate_counts[16] != measured_per_mode:
            failures.append(
                f"production limit=16 underlying calls: {candidate_counts[16]} != {measured_per_mode}"
            )
        if baseline_counts[16] != expected_legacy_search_calls:
            failures.append(
                f"legacy-shaped limit=16 calls: {baseline_counts[16]} != {expected_legacy_search_calls}"
            )
        if candidate_counts[24] != 0:
            failures.append(
                f"production still eagerly executed limit=24 terms: {candidate_counts[24]}"
            )
        if baseline_counts[24] != expected_legacy_search_calls:
            failures.append(
                f"legacy-shaped limit=24 calls: {baseline_counts[24]} != {expected_legacy_search_calls}"
            )
        if candidate_counts[64] != baseline_counts[64]:
            failures.append(
                "production changed ranking-required limit=64 work between arms: "
                f"{candidate_counts[64]} != {baseline_counts[64]}"
            )
        if not expected_output:
            failures.append("fixed fallback input produced no ToolCall output")

        ratios = [float(row["ratio"]) for row in paired_rows]
        median_ratio = statistics.median(ratios)
        if median_ratio > POSITIVE_RATIO:
            failures.append(
                "production lazy query-term path missed fixed-input speedup threshold: "
                f"{median_ratio:.4f}x > {POSITIVE_RATIO:.2f}x"
            )

        result = {
            "ok": not failures,
            "search_candidates": search_candidates,
            "captured": {
                "previous_results": len(previous),
                "skills": len(skills),
                "missing_evidence": len(belief.missing_evidence),
                "normalized_output": expected_output,
            },
            "experiments": EXPERIMENTS,
            "warmup_calls_per_mode": WARMUP_CALLS,
            "measured_calls_per_mode_per_experiment": MEASURED_CALLS,
            "paired_operation_ratios": [round(value, 4) for value in ratios],
            "paired_operation_ratio_median": round(median_ratio, 4),
            "positive_ratio_threshold": POSITIVE_RATIO,
            "underlying_query_terms": {
                "baseline": {str(key): int(value) for key, value in sorted(baseline_counts.items())},
                "candidate": {str(key): int(value) for key, value in sorted(candidate_counts.items())},
            },
            "per_call_latency_ms": {
                mode: {
                    "p50": round(_percentile(values, 0.50), 4),
                    "p95": round(_percentile(values, 0.95), 4),
                    "mean": round(sum(values) / max(1, len(values)), 4),
                }
                for mode, values in per_call_ms.items()
            },
            "pairs": [
                {
                    "experiment": int(row["experiment"]),
                    "order": row["order"],
                    "baseline_ms": round(float(row["baseline_ms"]), 3),
                    "candidate_ms": round(float(row["candidate_ms"]), 3),
                    "ratio": round(float(row["ratio"]), 4),
                }
                for row in paired_rows
            ],
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main() -> int:
    result = asyncio.run(main_async())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
