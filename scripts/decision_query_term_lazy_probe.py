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
    normalized = []
    for item in calls:
        data = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        normalized.append(
            {
                "tool": data.get("tool"),
                "purpose": data.get("purpose"),
                "args": data.get("args") or {},
                "estimated_cost": data.get("estimated_cost", data.get("cost")),
                "parallel_group": data.get("parallel_group"),
            }
        )
    return normalized


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
            "审核商家并核对主体、授权和历史风险。query-term 固定输入捕获。",
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
    with tempfile.TemporaryDirectory(prefix="ecomevo-query-term-lazy-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "probe.db")
        policy = engine.autonomy.policy
        captured_args, captured_kwargs = await _capture_fallback(engine)

        goal = captured_args[0]
        belief = captured_args[1]
        assets = captured_args[2]
        remaining_budget = float(captured_kwargs["remaining_budget"])
        previous = captured_kwargs["previous"]
        skills = captured_kwargs["skills"]

        mode_var: ContextVar[str] = ContextVar("query-term-probe-mode", default="baseline")
        in_fallback_var: ContextVar[bool] = ContextVar("query-term-probe-fallback", default=False)
        fallback_cache_var: ContextVar[dict[tuple[str, int], list[str]] | None] = ContextVar(
            "query-term-probe-cache", default=None
        )
        skip24_var: ContextVar[bool] = ContextVar("query-term-probe-skip24", default=False)
        count_var: ContextVar[bool] = ContextVar("query-term-probe-count", default=False)

        underlying_counts: dict[str, Counter[int]] = {
            "baseline": Counter(),
            "candidate": Counter(),
        }
        cache_hits = 0
        suppressed_24 = 0

        original_query_terms = control_policy._query_terms
        original_fallback = policy.fallback_calls
        original_sanitize = policy.sanitize

        def wrapped_query_terms(query: str, limit: int = 40):
            nonlocal cache_hits, suppressed_24
            mode = mode_var.get()
            fallback = in_fallback_var.get()
            if mode == "candidate" and fallback and int(limit) == 24 and skip24_var.get():
                if count_var.get():
                    suppressed_24 += 1
                return []
            if mode == "candidate" and fallback and int(limit) == 16:
                cache = fallback_cache_var.get()
                if cache is not None:
                    key = (str(query or ""), int(limit))
                    if key in cache:
                        if count_var.get():
                            cache_hits += 1
                        return list(cache[key])
                    if count_var.get():
                        underlying_counts[mode][int(limit)] += 1
                    value = original_query_terms(query, limit=limit)
                    cache[key] = list(value)
                    return value
            if count_var.get():
                underlying_counts[mode][int(limit)] += 1
            return original_query_terms(query, limit=limit)

        def wrapped_sanitize(*args, **kwargs):
            raw = args[0] if args else kwargs.get("raw")
            phase = str(kwargs.get("phase") or "")
            should_skip = False
            if mode_var.get() == "candidate" and phase == "fallback" and isinstance(raw, dict):
                evidence_rows = [
                    row
                    for row in (raw.get("tool_calls") or [])
                    if isinstance(row, dict) and str(row.get("tool") or "") == "evidence.search"
                ]
                should_skip = bool(evidence_rows) and all(
                    bool(
                        [
                            str(value).strip()
                            for value in (
                                (row.get("args") or {}).get("keywords")
                                if isinstance(row.get("args"), dict)
                                else []
                            )
                            if str(value).strip()
                        ]
                    )
                    for row in evidence_rows
                )
            token = skip24_var.set(should_skip)
            try:
                return original_sanitize(*args, **kwargs)
            finally:
                skip24_var.reset(token)

        def wrapped_fallback(*args, **kwargs):
            token_fallback = in_fallback_var.set(True)
            token_cache = fallback_cache_var.set({} if mode_var.get() == "candidate" else None)
            try:
                return original_fallback(*args, **kwargs)
            finally:
                fallback_cache_var.reset(token_cache)
                in_fallback_var.reset(token_fallback)

        control_policy._query_terms = wrapped_query_terms
        policy.sanitize = wrapped_sanitize
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
        paired_rows: list[dict[str, Any]] = []
        per_call_ms: dict[str, list[float]] = {"baseline": [], "candidate": []}

        def run_arm(mode: str, *, measured: bool) -> tuple[float, int]:
            nonlocal expected_output
            mode_token = mode_var.set(mode)
            count_token = count_var.set(measured)
            elapsed_total = 0.0
            calls = MEASURED_CALLS if measured else WARMUP_CALLS
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

        # Warm both modes before measured alternating pairs.
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

        if baseline_counts[16] != 2 * measured_per_mode:
            failures.append(
                f"baseline limit=16 shape changed: {baseline_counts[16]} != {2 * measured_per_mode}"
            )
        if candidate_counts[16] != measured_per_mode:
            failures.append(
                f"candidate limit=16 underlying calls: {candidate_counts[16]} != {measured_per_mode}"
            )
        if baseline_counts[24] != 2 * measured_per_mode:
            failures.append(
                f"baseline limit=24 shape changed: {baseline_counts[24]} != {2 * measured_per_mode}"
            )
        if candidate_counts[24] != 0:
            failures.append(
                f"candidate still executed limit=24 fallback terms: {candidate_counts[24]}"
            )
        if candidate_counts[64] != baseline_counts[64]:
            failures.append(
                "candidate changed ranking-required limit=64 query-term work: "
                f"{candidate_counts[64]} != {baseline_counts[64]}"
            )
        if cache_hits != measured_per_mode:
            failures.append(f"candidate limit=16 cache hits: {cache_hits} != {measured_per_mode}")
        if suppressed_24 != 2 * measured_per_mode:
            failures.append(
                f"candidate suppressed limit=24 calls: {suppressed_24} != {2 * measured_per_mode}"
            )
        if not expected_output:
            failures.append("fixed fallback input produced no ToolCall output")

        ratios = [float(row["ratio"]) for row in paired_rows]
        median_ratio = statistics.median(ratios)
        if median_ratio > POSITIVE_RATIO:
            failures.append(
                "lazy query-term candidate did not achieve meaningful fixed-input speedup: "
                f"{median_ratio:.4f}x > {POSITIVE_RATIO:.2f}x"
            )

        result = {
            "ok": not failures,
            "positive": median_ratio <= POSITIVE_RATIO and not failures,
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
            "candidate_limit16_cache_hits": cache_hits,
            "candidate_limit24_suppressed": suppressed_24,
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
