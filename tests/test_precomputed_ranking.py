from __future__ import annotations

from types import SimpleNamespace

from ecomevo.runtime import EcomEvoEngine
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


def _inputs(tmp_path, policy_type, name: str):
    planner = AdaptivePlanner()
    registry = ToolRegistry(None)
    sandbox = ActionSandbox()
    skills = AdaptiveSkillLibrary(tmp_path / f"{name}.db")
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
    policy = policy_type(
        planner,
        registry,
        sandbox,
        skills,
        max_calls=4,
        max_delegations=3,
    )
    goal = planner.parse_goal(
        "审核商家主体、授权、经营范围、商品声明、适用规则和历史风险",
        [],
        domain_hint="merchant_review",
    )
    active = skills.relevant(
        goal.domain.value,
        query=goal.primary,
        missing=MISSING,
    )
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
    return policy, goal, active, previous, candidates


def _trace_shape(trace):
    return [
        {key: value for key, value in row.items() if key not in {"routing_ms", "posterior_prepare_ms"}}
        for row in trace
    ]


def test_precomputed_features_match_legacy_exactly(tmp_path):
    legacy, goal, active, previous, candidates = _inputs(tmp_path, AdaptiveDecisionPolicy, "legacy-features")
    optimized, _, optimized_active, optimized_previous, optimized_candidates = _inputs(
        tmp_path, PrecomputedAdaptiveDecisionPolicy, "optimized-features"
    )
    snapshot = optimized._prepare_rank_feature_snapshot(
        tools=TOOLS,
        goal=goal,
        missing=MISSING,
        previous=optimized_previous,
        skills=optimized_active,
    )
    token = optimized._rank_feature_snapshot.set(snapshot)
    try:
        for legacy_candidate, optimized_candidate in zip(candidates, optimized_candidates):
            tool = legacy_candidate["tool"]
            reliability = 0.37 + 0.05 * TOOLS.index(tool)
            expected = legacy._base_features(
                tool=tool,
                cost=legacy_candidate["cost"],
                goal=goal,
                missing=MISSING,
                previous=previous,
                skills=active,
                reliability=reliability,
            )
            actual = optimized._base_features(
                tool=tool,
                cost=optimized_candidate["cost"],
                goal=goal,
                missing=MISSING,
                previous=optimized_previous,
                skills=optimized_active,
                reliability=reliability,
            )
            assert actual == expected
    finally:
        optimized._rank_feature_snapshot.reset(token)


def test_precomputed_rank_selection_matches_legacy(tmp_path):
    legacy, goal, active, previous, candidates = _inputs(tmp_path, AdaptiveDecisionPolicy, "legacy-rank")
    optimized, _, optimized_active, optimized_previous, optimized_candidates = _inputs(
        tmp_path, PrecomputedAdaptiveDecisionPolicy, "optimized-rank"
    )
    shared_routing = FactorizedAdaptiveRoutingStore(tmp_path / "shared-routing.db")
    legacy.routing = shared_routing
    optimized.routing = shared_routing

    expected_selected, expected_trace = legacy._rank_candidates(
        candidates,
        goal=goal,
        missing=MISSING,
        previous=previous,
        skills=active,
        budget=8.0,
        limit=4,
    )
    actual_selected, actual_trace = optimized._rank_candidates(
        optimized_candidates,
        goal=goal,
        missing=MISSING,
        previous=optimized_previous,
        skills=optimized_active,
        budget=8.0,
        limit=4,
    )

    assert actual_selected == expected_selected
    assert _trace_shape(actual_trace) == _trace_shape(expected_trace)


def test_precomputed_rank_eliminates_repeated_term_and_registry_scans(tmp_path):
    legacy, goal, active, previous, candidates = _inputs(tmp_path, AdaptiveDecisionPolicy, "legacy-count")
    optimized, _, optimized_active, optimized_previous, optimized_candidates = _inputs(
        tmp_path, PrecomputedAdaptiveDecisionPolicy, "optimized-count"
    )

    def counted(policy, candidates, active, previous):
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
            policy._rank_candidates(
                candidates,
                goal=goal,
                missing=MISSING,
                previous=previous,
                skills=active,
                budget=8.0,
                limit=4,
            )
        finally:
            policy._terms = original_terms
            policy.registry.describe = original_describe
        return counts

    legacy_counts = counted(legacy, candidates, active, previous)
    optimized_counts = counted(optimized, optimized_candidates, optimized_active, optimized_previous)

    assert legacy_counts == {"terms": len(TOOLS) * (len(MISSING) + 1), "describe": len(TOOLS)}
    assert optimized_counts == {"terms": len(MISSING) + len(TOOLS), "describe": 1}


def test_default_runtime_uses_precomputed_ranking_policy(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    assert isinstance(engine.autonomy.policy, PrecomputedAdaptiveDecisionPolicy)
