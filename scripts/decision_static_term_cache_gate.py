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

from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.adaptive_routing import AdaptiveDecisionPolicy


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
            "审核商家并核对主体、授权和历史风险。production static-term cache gate。",
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
    with tempfile.TemporaryDirectory(prefix="ecomevo-static-term-production-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "gate.db")
        policy = engine.autonomy.policy
        captured_args, captured_kwargs = await _capture_fallback(engine)

        goal = captured_args[0]
        belief = captured_args[1]
        assets = captured_args[2]
        remaining_budget = float(captured_kwargs["remaining_budget"])
        previous = captured_kwargs["previous"]
        skills = captured_kwargs["skills"]

        mode_var: ContextVar[str] = ContextVar("static-term-production-mode", default="candidate")
        count_var: ContextVar[bool] = ContextVar("static-term-production-count", default=False)
        counters: dict[str, Counter[str]] = {
            "baseline": Counter(),
            "candidate": Counter(),
        }

        production_terms = policy._terms
        original_base_terms = AdaptiveDecisionPolicy._terms

        def counted_base_terms(value: Any) -> set[str]:
            mode = mode_var.get()
            if count_var.get():
                if isinstance(value, set):
                    counters[mode]["underlying_set_terms"] += 1
                else:
                    counters[mode]["underlying_nonset_terms"] += 1
            return original_base_terms(value)

        # Production PrecomputedAdaptiveDecisionPolicy delegates misses/non-set values to
        # AdaptiveDecisionPolicy._terms. Counting that boundary proves warmed set hits do
        # not silently recompute while target/request strings still execute normally.
        AdaptiveDecisionPolicy._terms = staticmethod(counted_base_terms)

        def mode_terms(value: Any) -> set[str]:
            mode = mode_var.get()
            if isinstance(value, set):
                if count_var.get():
                    counters[mode]["set_lookups"] += 1
                if mode == "baseline":
                    # Exact legacy set path: bypass only the new instance-local cache.
                    return counted_base_terms(value)
            else:
                if count_var.get():
                    counters[mode]["nonset_lookups"] += 1
            return production_terms(value)

        policy._terms = mode_terms

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

        try:
            # The production policy has already seen the captured fallback once; this
            # additional warm-up makes the intended steady-state cache condition explicit.
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
        finally:
            AdaptiveDecisionPolicy._terms = staticmethod(original_base_terms)

        measured_per_mode = EXPERIMENTS * MEASURED_CALLS
        baseline = counters["baseline"]
        candidate = counters["candidate"]
        if baseline["set_lookups"] != measured_per_mode:
            failures.append(
                f"baseline set lookups: {baseline['set_lookups']} != {measured_per_mode}"
            )
        if candidate["set_lookups"] != measured_per_mode:
            failures.append(
                f"candidate set lookups: {candidate['set_lookups']} != {measured_per_mode}"
            )
        if baseline["underlying_set_terms"] != measured_per_mode:
            failures.append(
                "baseline underlying set work changed: "
                f"{baseline['underlying_set_terms']} != {measured_per_mode}"
            )
        if candidate["underlying_set_terms"] != 0:
            failures.append(
                "warmed production cache still recomputed set terms: "
                f"{candidate['underlying_set_terms']}"
            )
        if candidate["nonset_lookups"] != baseline["nonset_lookups"]:
            failures.append(
                "production cache changed request/target lookup count: "
                f"{candidate['nonset_lookups']} != {baseline['nonset_lookups']}"
            )
        if candidate["underlying_nonset_terms"] != baseline["underlying_nonset_terms"]:
            failures.append(
                "production cache changed request/target underlying work: "
                f"{candidate['underlying_nonset_terms']} != {baseline['underlying_nonset_terms']}"
            )
        if len(policy._static_term_cache) > int(policy._STATIC_TERM_CACHE_LIMIT):
            failures.append(
                f"production cache exceeded bound: {len(policy._static_term_cache)} > "
                f"{policy._STATIC_TERM_CACHE_LIMIT}"
            )
        if not expected_output:
            failures.append("fixed fallback input produced no ToolCall output")

        ratios = [float(row["ratio"]) for row in paired_rows]
        median_ratio = statistics.median(ratios)
        if median_ratio > POSITIVE_RATIO:
            failures.append(
                "production static term cache missed fixed-input speedup threshold: "
                f"{median_ratio:.4f}x > {POSITIVE_RATIO:.2f}x"
            )

        result = {
            "ok": not failures,
            "experiments": EXPERIMENTS,
            "warmup_calls_per_mode": WARMUP_CALLS,
            "measured_calls_per_mode_per_experiment": MEASURED_CALLS,
            "cache_limit": int(policy._STATIC_TERM_CACHE_LIMIT),
            "cache_entries": len(policy._static_term_cache),
            "paired_operation_ratios": [round(value, 4) for value in ratios],
            "paired_operation_ratio_median": round(median_ratio, 4),
            "positive_ratio_threshold": POSITIVE_RATIO,
            "term_counters": {
                mode: {key: int(value) for key, value in sorted(values.items())}
                for mode, values in counters.items()
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
            "privacy": {
                "request_text_cached": False,
                "cached_input_types": ["set"],
                "cache_scope": "policy-instance",
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
