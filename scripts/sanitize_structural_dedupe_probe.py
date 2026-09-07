from __future__ import annotations

import asyncio
import copy
import json
import statistics
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine


EXPERIMENTS = 5
WARMUP_CALLS = 24
MEASURED_CALLS = 320
POSITIVE_RATIO = 0.92
DOMAIN = "merchant_review"


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


def _normalize_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tool": str(row.get("tool") or ""),
            "args": copy.deepcopy(row.get("args") or {}),
            "cost": float(row.get("cost", 0.0) or 0.0),
            "purpose": str(row.get("purpose") or ""),
            "group": str(row.get("group") or ""),
        }
        for row in candidates
    ]


def _normalize_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ignored = {"routing_ms", "posterior_prepare_ms"}
    return [
        {key: copy.deepcopy(value) for key, value in row.items() if key not in ignored}
        for row in trace
    ]


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
            "审核商家并核对主体、授权和历史风险。structural dedupe 固定输入捕获。",
            [],
            domain_hint=DOMAIN,
        )
    finally:
        policy.fallback_calls = original
    if not summary.event_chain_valid:
        raise AssertionError("capture run produced invalid event chain")
    if not captured:
        raise AssertionError("capture run did not enter fallback_calls")
    return captured[0]


def _structural_key(tool: str, args: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    if tool == "evidence.search":
        return (tool, tuple(args.get("keywords") or []))
    return (tool, ())


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-sanitize-structural-dedupe-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "probe.db")
        policy = engine.autonomy.policy
        if type(policy).__name__ != "CounterfactualAdaptiveDecisionPolicy":
            failures.append(f"unexpected production policy: {type(policy).__name__}")

        captured_args, captured_kwargs = await _capture_fallback(engine)
        goal = captured_args[0]
        belief = captured_args[1]
        assets = captured_args[2]
        remaining_budget = float(captured_kwargs["remaining_budget"])
        previous = captured_kwargs["previous"]
        skills = captured_kwargs["skills"]

        mode_var: ContextVar[str] = ContextVar("sanitize-dedupe-mode", default="baseline")
        in_sanitize_var: ContextVar[bool] = ContextVar("sanitize-dedupe-in-sanitize", default=False)
        count_var: ContextVar[bool] = ContextVar("sanitize-dedupe-count", default=False)
        capture_rank_var: ContextVar[bool] = ContextVar("sanitize-dedupe-capture-rank", default=False)

        original_signature = policy.call_signature
        original_sanitize = policy.sanitize
        original_rank = policy._rank_candidates

        counts = {
            "baseline": {"legacy": 0, "structural": 0},
            "candidate": {"legacy": 0, "structural": 0},
        }
        shape_violations: list[str] = []
        rank_captures: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}

        def wrapped_signature(tool: str, args: dict[str, Any]):
            mode = mode_var.get()
            if mode == "candidate" and in_sanitize_var.get():
                if tool == "evidence.search":
                    if set(args) != {"keywords"} or not isinstance(args.get("keywords"), list):
                        shape_violations.append(f"unexpected evidence.search args: {args!r}")
                elif args != {}:
                    shape_violations.append(f"unexpected non-search args for {tool}: {args!r}")
                if count_var.get():
                    counts[mode]["structural"] += 1
                return _structural_key(str(tool), args)
            if count_var.get() and in_sanitize_var.get():
                counts[mode]["legacy"] += 1
            return original_signature(tool, args)

        def wrapped_sanitize(*args, **kwargs):
            token = in_sanitize_var.set(True)
            try:
                return original_sanitize(*args, **kwargs)
            finally:
                in_sanitize_var.reset(token)

        def wrapped_rank(candidates, *args, **kwargs):
            selected, trace = original_rank(candidates, *args, **kwargs)
            if capture_rank_var.get():
                rank_captures[mode_var.get()].append(
                    {
                        "candidates": _normalize_candidates(candidates),
                        "selected": _normalize_candidates(selected),
                        "trace": _normalize_trace(trace),
                    }
                )
            return selected, trace

        policy.call_signature = wrapped_signature
        policy.sanitize = wrapped_sanitize
        policy._rank_candidates = wrapped_rank

        # The candidate is intentionally local to sanitize. The public signature path
        # outside sanitize must remain byte-for-byte the legacy digest behavior.
        outside_expected = original_signature("evidence.search", {"keywords": ["主体", "授权"]})
        token_mode = mode_var.set("candidate")
        try:
            outside_actual = policy.call_signature("evidence.search", {"keywords": ["主体", "授权"]})
        finally:
            mode_var.reset(token_mode)
        if outside_actual != outside_expected:
            failures.append("candidate changed public call_signature outside sanitize")

        def prepare_recovery_round(mode: str) -> None:
            token_mode = mode_var.set(mode)
            try:
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
            finally:
                mode_var.reset(token_mode)

        def invoke(mode: str, *, measured: bool, capture_rank: bool = False):
            prepare_recovery_round(mode)
            token_mode = mode_var.set(mode)
            token_count = count_var.set(measured)
            token_capture = capture_rank_var.set(capture_rank)
            started = time.perf_counter()
            try:
                output = policy.fallback_calls(
                    goal,
                    belief,
                    assets,
                    remaining_budget=remaining_budget,
                    previous=previous,
                    skills=skills,
                )
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                capture_rank_var.reset(token_capture)
                count_var.reset(token_count)
                mode_var.reset(token_mode)
            return output, elapsed_ms

        # One untimed semantic pair first. Routing/fallback is read-only here, so both
        # arms see the same persistent state and a freshly prepared decision round.
        baseline_semantic, _ = invoke("baseline", measured=False, capture_rank=True)
        candidate_semantic, _ = invoke("candidate", measured=False, capture_rank=True)
        if _normalize_calls(baseline_semantic) != _normalize_calls(candidate_semantic):
            failures.append("candidate changed normalized fallback ToolCall output")
        if rank_captures["baseline"] != rank_captures["candidate"]:
            failures.append("candidate changed fallback rank input/selection/trace semantics")
        if len(rank_captures["baseline"]) != 1:
            failures.append(
                f"semantic fallback expected one rank call, saw {len(rank_captures['baseline'])}"
            )

        per_call_ms: dict[str, list[float]] = {"baseline": [], "candidate": []}
        expected_output = _normalize_calls(baseline_semantic)

        def run_arm(mode: str, *, measured: bool) -> tuple[float, int]:
            calls = MEASURED_CALLS if measured else WARMUP_CALLS
            total_ms = 0.0
            mismatches = 0
            for _ in range(calls):
                output, elapsed_ms = invoke(mode, measured=measured)
                if _normalize_calls(output) != expected_output:
                    mismatches += 1
                if measured:
                    total_ms += elapsed_ms
                    per_call_ms[mode].append(elapsed_ms)
            return total_ms, mismatches

        run_arm("baseline", measured=False)
        run_arm("candidate", measured=False)

        pairs: list[dict[str, Any]] = []
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
                    f"experiment {experiment} changed ToolCall output: "
                    f"baseline={baseline_mismatches}, candidate={candidate_mismatches}"
                )
            pairs.append(
                {
                    "experiment": experiment,
                    "order": list(order),
                    "baseline_ms": baseline_ms,
                    "candidate_ms": candidate_ms,
                    "ratio": candidate_ms / baseline_ms if baseline_ms else 0.0,
                }
            )

        measured_per_mode = EXPERIMENTS * MEASURED_CALLS
        expected_signature_calls = measured_per_mode * 2
        if counts["baseline"]["legacy"] != expected_signature_calls:
            failures.append(
                "baseline signature shape changed: "
                f"{counts['baseline']['legacy']} != {expected_signature_calls}"
            )
        if counts["candidate"]["legacy"] != 0:
            failures.append(
                f"candidate still executed legacy sanitize signatures: {counts['candidate']['legacy']}"
            )
        if counts["candidate"]["structural"] != expected_signature_calls:
            failures.append(
                "candidate structural-key count changed: "
                f"{counts['candidate']['structural']} != {expected_signature_calls}"
            )
        if shape_violations:
            failures.extend(shape_violations[:8])

        ratios = [float(row["ratio"]) for row in pairs]
        median_ratio = statistics.median(ratios)
        if median_ratio > POSITIVE_RATIO:
            failures.append(
                "structural sanitize key did not improve full fixed-input fallback enough: "
                f"{median_ratio:.4f}x > {POSITIVE_RATIO:.2f}x"
            )

        result = {
            "ok": not failures,
            "positive": median_ratio <= POSITIVE_RATIO and not failures,
            "policy_class": type(policy).__name__,
            "captured": {
                "previous_results": len(previous),
                "skills": len(skills),
                "missing_evidence": len(belief.missing_evidence),
                "normalized_output": expected_output,
                "fallback_rank": rank_captures["baseline"],
            },
            "public_signature_outside_sanitize_unchanged": outside_actual == outside_expected,
            "shape_violations": shape_violations,
            "experiments": EXPERIMENTS,
            "warmup_calls_per_mode": WARMUP_CALLS,
            "measured_calls_per_mode_per_experiment": MEASURED_CALLS,
            "signature_counts": counts,
            "paired_fallback_ratios": [round(value, 4) for value in ratios],
            "paired_fallback_ratio_median": round(median_ratio, 4),
            "positive_ratio_threshold": POSITIVE_RATIO,
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
                for row in pairs
            ],
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main() -> int:
    report = asyncio.run(main_async())
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
