from __future__ import annotations

import asyncio

from ecomevo.models import BeliefState, DecisionDomain, GoalState, SubAgentResult, ToolResult
from ecomevo.runtime import DecisionVerifier, EcomEvoEngine, PolicyStore
from ecomevo.runtime.governance import GovernanceBoundary


def _rule(store: PolicyStore, policy_id: str, value: str, *, scope=None, priority=0, authority=50, effective_from="2025-01-01T00:00:00Z"):
    return store.create_version(
        policy_id=policy_id,
        domain="product_governance",
        rules=[f"{policy_id} rule"],
        controls={"listing.action": value},
        scope=scope or {},
        authority=authority,
        priority=priority,
        status="active",
        effective_from=effective_from,
        owner="qa",
        approver="qa",
        source=f"test:{policy_id}",
    )


def test_builtin_policies_are_versioned_and_resolvable(tmp_path):
    store = PolicyStore(tmp_path / "policy.db")
    result = store.resolve("product_governance", as_of="2026-09-16T00:00:00Z")

    assert result["status"] == "resolved"
    assert result["policies"]
    assert result["policies"][0]["version_id"] == "builtin.product-governance@v1"
    assert result["policies"][0]["effective_from"] == "1970-01-01T00:00:00Z"
    assert len(result["policies"][0]["source_hash"]) == 64
    assert result["controls"]["high_risk_claim.require_verifiable_evidence"] is True


def test_publish_preserves_historical_resolution(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    first = store.create_version(
        policy_id="commerce.claims",
        domain="product_governance",
        rules=["旧规则"],
        controls={"claim.action": "review"},
        status="active",
        effective_from="2025-01-01T00:00:00Z",
        source="policy-manual-v1",
    )
    second = store.create_version(
        policy_id="commerce.claims",
        domain="product_governance",
        rules=["新规则"],
        controls={"claim.action": "block"},
        status="draft",
        source="policy-manual-v2",
    )
    store.publish(second.policy_id, second.version, effective_from="2026-01-01T00:00:00Z", approver="risk-owner")

    before = store.resolve("product_governance", as_of="2025-12-31T23:59:59Z")
    after = store.resolve("product_governance", as_of="2026-01-01T00:00:00Z")

    assert before["controls"]["claim.action"] == "review"
    assert before["policies"][0]["version_id"] == f"{first.policy_id}@v{first.version}"
    assert after["controls"]["claim.action"] == "block"
    assert after["policies"][0]["version_id"] == f"{second.policy_id}@v{second.version}"
    old = store.get_version(first.policy_id, first.version)
    assert old.status == "superseded"
    assert old.effective_to == "2026-01-01T00:00:00Z"


def test_scope_specific_policy_overrides_global_without_false_conflict(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    _rule(store, "global.listing", "review")
    scoped = _rule(store, "sg.listing", "block", scope={"market": "SG"})

    singapore = store.resolve("product_governance", scope={"market": "SG"}, as_of="2026-01-01T00:00:00Z")
    malaysia = store.resolve("product_governance", scope={"market": "MY"}, as_of="2026-01-01T00:00:00Z")

    assert singapore["status"] == "resolved"
    assert singapore["controls"]["listing.action"] == "block"
    assert singapore["conflicts"] == []
    assert any(row["policy_id"] == "global.listing" for row in singapore["overridden"])
    assert singapore["policies"][0]["version_id"] == f"{scoped.policy_id}@v{scoped.version}"
    assert malaysia["controls"]["listing.action"] == "review"


def test_equal_precedence_incompatible_controls_are_conflicted(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    _rule(store, "policy.a", "review", scope={"market": "SG"}, authority=80, priority=10)
    _rule(store, "policy.b", "block", scope={"market": "SG"}, authority=80, priority=10)

    result = store.resolve("product_governance", scope={"market": "SG"}, as_of="2026-01-01T00:00:00Z")

    assert result["status"] == "conflicted"
    assert "listing.action" not in result["controls"]
    assert result["conflicts"][0]["control"] == "listing.action"
    assert {row["value"] for row in result["conflicts"][0]["candidates"]} == {"review", "block"}


def test_higher_priority_control_wins_and_records_override(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    _rule(store, "policy.base", "review", priority=10)
    winner = _rule(store, "policy.override", "block", priority=20)

    result = store.resolve("product_governance", as_of="2026-01-01T00:00:00Z")

    assert result["status"] == "resolved"
    assert result["controls"]["listing.action"] == "block"
    assert result["conflicts"] == []
    assert result["policies"][0]["version_id"] == f"{winner.policy_id}@v{winner.version}"
    assert result["overridden"][0]["policy_id"] == "policy.base"


def test_runtime_engine_binds_persistent_policy_store_to_lookup(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    tool = engine.tools.tools["policy.lookup"]

    assert tool.policies is engine.policies
    goal = GoalState(primary="核对商品功效声明", domain=DecisionDomain.PRODUCT_GOVERNANCE)
    result = asyncio.run(tool.execute({"goal": goal, "text": goal.primary, "assets": []}, {}))

    assert result["status"] == "resolved"
    assert result["resolution_mode"] == "versioned_policy_store"
    assert result["policies"][0]["version_id"] == "builtin.product-governance@v1"


def test_verifier_fails_closed_on_policy_conflict():
    verifier = DecisionVerifier()
    goal = GoalState(primary="一般核对", domain=DecisionDomain.GENERAL)
    belief = BeliefState(facts={"asset_count": 0})
    tools = [
        ToolResult(
            call_id="policy-1",
            tool="policy.lookup",
            ok=True,
            data={"status": "conflicted", "rules": ["A", "B"], "conflicts": [{"control": "x"}]},
            cost=0.1,
        )
    ]
    agents = [SubAgentResult(agent="reviewer", summary="done", confidence=1.0)]

    result = verifier.verify(goal, belief, tools, agents, actions=[])

    assert result.passed is False
    assert result.evidence_complete is False
    assert "适用政策存在未解决冲突" in result.missing_evidence
    assert result.score <= 0.49


def test_governance_policy_evidence_carries_version_and_conflict_state():
    resolved_title, resolved_detail = GovernanceBoundary.tool_evidence_copy(
        "policy.lookup",
        {
            "status": "resolved",
            "as_of": "2026-09-16T00:00:00Z",
            "rules": ["规则 A"],
            "policies": [{"version_id": "commerce.claims@v3"}],
        },
    )
    conflict_title, conflict_detail = GovernanceBoundary.tool_evidence_copy(
        "policy.lookup",
        {
            "status": "conflicted",
            "as_of": "2026-09-16T00:00:00Z",
            "policies": [{"version_id": "policy.a@v1"}, {"version_id": "policy.b@v2"}],
            "conflicts": [{"control": "listing.action"}],
        },
    )

    assert resolved_title == "适用规则核对"
    assert "commerce.claims@v3" in resolved_detail
    assert "2026-09-16T00:00:00Z" in resolved_detail
    assert conflict_title == "适用规则冲突"
    assert "listing.action" in conflict_detail
    assert "policy.a@v1" in conflict_detail
