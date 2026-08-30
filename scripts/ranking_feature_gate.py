from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ecomevo.runtime.adaptive_routing import AdaptiveDecisionPolicy
from ecomevo.runtime.factorized_routing import FactorizedAdaptiveRoutingStore
from ecomevo.runtime.planner import AdaptivePlanner
from ecomevo.runtime.precomputed_ranking import PrecomputedAdaptiveDecisionPolicy
from ecomevo.runtime.sandbox import ActionSandbox
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


TOOLS = [
    "merchant.inspect",
    "risk.scan",
    "evidence.search",
    "policy.lookup",
    "catalog.inspect",
    "order.inspect",
    "media.summarize",
]
MISSING = ["主体", "授权", "历史风险", "经营范围", "商品声明", "适用规则"]


def build(path: Path, policy_type):
    planner = AdaptivePlanner()
    registry = ToolRegistry(None)
    sandbox = ActionSandbox()
    skills = AdaptiveSkillLibrary(path)
    skills.upsert_candidate(
        domain="merchant_review",
        name="merchant-auth",
        guidance="核对主体与授权",
        preferred_tools=["merchant.inspect", "policy.lookup"],
        trigger_terms=["主体", "授权"],
        shadow_score=0.95,
        promote=True,
    )
    skills.upsert_candidate(
        domain="merchant_review",
        name="merchant-risk",
        guidance="核对历史风险",
        preferred_tools=["risk.scan", "evidence.search"],
        trigger_terms=["历史风险", "风险"],
        shadow_score=0.91,
        promote=True,
    )
    policy = policy_type(planner, registry, sandbox, skills, max_calls=4, max_delegations=3)
    goal = planner.parse_goal(
        "审核商家主体、授权、经营范围、商品声明、适用规则和历史风险",
        [],
        domain_hint="merchant_review",
    )
    active = skills.relevant(goal.domain.value, query=goal.primary, missing=MISSING)
    previous = [
        SimpleNamespace(tool="merchant.inspect", ok=True),
        SimpleNamespace(tool="evidence.search", ok=True),
        SimpleNamespace(tool="evidence.search", ok=False),
    ]
    candidates = [
        {
            "tool": tool,
            "args": {},
            "cost": float(getattr(registry.tools[tool], "cost", 1.0) or 1.0),
            "purpose": f"gate:{tool}",
            "group": "gate",
        }
        for tool in TOOLS
    ]
    reliability = {tool: 0.37 + 0.05 * index for index, tool in enumerate(TOOLS)}
    return policy, goal, active, previous, candidates, reliability


def legacy_features(policy, goal, active, previous, candidates, reliability):
    return [
        policy._base_features(
            tool=candidate["tool"],
            cost=candidate["cost"],
            goal=goal,
            missing=MISSING,
            previous=previous,
            skills=active,
            reliability=reliability[candidate["tool"]],
        )
        for candidate in candidates
    ]


def optimized_features(policy, goal, active, previous, candidates, reliability):
    snapshot = policy._prepare_rank_feature_snapshot(
        tools=[candidate["tool"] for candidate in candidates],
        goal=goal,
        missing=MISSING,
        previous=previous,
        skills=active,
    )
    token = policy._rank_feature_snapshot.set(snapshot)
    try:
        return [
            policy._base_features(
                tool=candidate["tool"],
                cost=candidate["cost"],
                goal=goal,
                missing=MISSING,
                previous=previous,
                skills=active,
                reliability=reliability[candidate["tool"]],
            )
            for candidate in candidates
        ]
    finally:
        policy._rank_feature_snapshot.reset(token)


def operation_counts(policy, call) -> dict[str, int]:
    counts = {"terms": 0, "describe": 0}
    original_terms = policy._terms
    original_describe = policy.registry.describe

    def terms(value):
        counts["terms"] += 1
        return original_terms(value)

    def describe():
        counts["describe"] += 1
        return original_describe()

    policy._terms = terms
    policy.registry.describe = describe
    try:
        call()
    finally:
        policy._terms = original_terms
        policy.registry.describe = original_describe
    return counts


def timed(call, iterations: int) -> float:
    started = time.perf_counter()
    for _ in range(iterations):
        call()
    return time.perf_counter() - started


def trace_shape(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in row.items() if key not in {"routing_ms", "posterior_prepare_ms"}}
        for row in trace
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Decision ranking feature-precompute A/B gate")
    parser.add_argument("--iterations", type=int, default=2500)
    parser.add_argument("--experiments", type=int, default=5)
    args = parser.parse_args()
    if not 200 <= args.iterations <= 20000:
        raise SystemExit("iterations must be between 200 and 20000")
    if not 3 <= args.experiments <= 9:
        raise SystemExit("experiments must be between 3 and 9")

    with tempfile.TemporaryDirectory(prefix="ecomevo-ranking-feature-") as tmp:
        root = Path(tmp)
        legacy = build(root / "legacy.db", AdaptiveDecisionPolicy)
        optimized = build(root / "optimized.db", PrecomputedAdaptiveDecisionPolicy)
        legacy_policy, goal, active, previous, candidates, reliability = legacy
        optimized_policy, _, optimized_active, optimized_previous, optimized_candidates, optimized_reliability = optimized

        expected = legacy_features(legacy_policy, goal, active, previous, candidates, reliability)
        actual = optimized_features(
            optimized_policy,
            goal,
            optimized_active,
            optimized_previous,
            optimized_candidates,
            optimized_reliability,
        )
        if actual != expected:
            raise AssertionError("precomputed feature values differ from legacy")

        shared_routing = FactorizedAdaptiveRoutingStore(root / "shared-routing.db")
        legacy_policy.routing = shared_routing
        optimized_policy.routing = shared_routing
        expected_selected, expected_trace = legacy_policy._rank_candidates(
            candidates,
            goal=goal,
            missing=MISSING,
            previous=previous,
            skills=active,
            budget=8.0,
            limit=4,
        )
        actual_selected, actual_trace = optimized_policy._rank_candidates(
            optimized_candidates,
            goal=goal,
            missing=MISSING,
            previous=optimized_previous,
            skills=optimized_active,
            budget=8.0,
            limit=4,
        )
        if actual_selected != expected_selected or trace_shape(actual_trace) != trace_shape(expected_trace):
            raise AssertionError("rank selection or trace changed")

        legacy_call = lambda: legacy_features(legacy_policy, goal, active, previous, candidates, reliability)
        optimized_call = lambda: optimized_features(
            optimized_policy,
            goal,
            optimized_active,
            optimized_previous,
            optimized_candidates,
            optimized_reliability,
        )
        legacy_counts = operation_counts(legacy_policy, legacy_call)
        optimized_counts = operation_counts(optimized_policy, optimized_call)

        timed(legacy_call, 50)
        timed(optimized_call, 50)
        legacy_times: list[float] = []
        optimized_times: list[float] = []
        for experiment in range(args.experiments):
            order = ("legacy", "optimized") if experiment % 2 == 0 else ("optimized", "legacy")
            for mode in order:
                if mode == "legacy":
                    legacy_times.append(timed(legacy_call, args.iterations))
                else:
                    optimized_times.append(timed(optimized_call, args.iterations))

        legacy_median = statistics.median(legacy_times)
        optimized_median = statistics.median(optimized_times)
        ratio = optimized_median / max(1e-12, legacy_median)
        term_ratio = optimized_counts["terms"] / max(1, legacy_counts["terms"])
        failures = []
        if legacy_counts != {"terms": len(TOOLS) * (len(MISSING) + 1), "describe": len(TOOLS)}:
            failures.append(f"legacy operation shape changed: {legacy_counts}")
        if optimized_counts != {"terms": len(MISSING) + len(TOOLS), "describe": 1}:
            failures.append(f"optimized operation shape changed: {optimized_counts}")
        if term_ratio > 0.35:
            failures.append(f"term-call ratio regressed: {term_ratio:.4f} > 0.35")
        # Pre-declared before observing Actions. Require a meaningful CPU reduction.
        if ratio > 0.80:
            failures.append(f"optimized wall ratio regressed: {ratio:.4f} > 0.80")

        result = {
            "ok": not failures,
            "iterations": args.iterations,
            "experiments": args.experiments,
            "legacy_operations": legacy_counts,
            "optimized_operations": optimized_counts,
            "term_call_ratio": round(term_ratio, 4),
            "legacy_median_seconds": round(legacy_median, 6),
            "optimized_median_seconds": round(optimized_median, 6),
            "optimized_to_legacy_ratio": round(ratio, 4),
            "selection_equivalent": True,
            "failures": failures,
        }
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
