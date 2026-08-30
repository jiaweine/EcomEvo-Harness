from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary
from ecomevo.runtime.counterfactual_routing import (
    CounterfactualAdaptiveDecisionPolicy,
    _ControllerDecisionSkillView,
)
from ecomevo.runtime.factorized_routing import FactorizedAdaptiveRoutingStore
from ecomevo.runtime.planner import AdaptivePlanner
from ecomevo.runtime.sandbox import ActionSandbox
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


class CountingBundledSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path: Path):
        self.connections = 0
        self.policy_calls = 0
        super().__init__(path)

    def _conn(self):
        self.connections += 1
        return super()._conn()

    def policy(self, domain: str):
        self.policy_calls += 1
        return super().policy(domain)


class CountingRouting(FactorizedAdaptiveRoutingStore):
    def __init__(self, path: Path):
        self.connections = 0
        super().__init__(path)

    def _conn(self):
        self.connections += 1
        return super()._conn()


def _seed(skills: CountingBundledSkills, routing: CountingRouting, domain: str) -> None:
    # Bootstrap the evolution-policy row using the production cold-start path.
    skills.policy(domain)
    now = time.time()
    with skills._conn() as connection:
        connection.execute(
            "INSERT INTO runtime_skills("
            "skill_id,domain,niche,name,guidance,preferred_tools_json,trigger_terms_json,"
            "status,shadow_score,alpha,beta,uses,wins,losses,created_at,updated_at,source_patch_id"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "skill-active-1",
                domain,
                "merchant-auth",
                "merchant evidence",
                "prefer merchant identity and authorization checks",
                json.dumps(["merchant.inspect", "policy.lookup"]),
                json.dumps(["主体", "授权"]),
                "active",
                0.82,
                8.0,
                2.0,
                12,
                9,
                3,
                now,
                now,
                None,
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO routing_tool_stats(domain,tool,alpha,beta,uses,reward_ewma,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (domain, "merchant.inspect", 9.0, 3.0, 12, 0.5, now),
        )
    vector = [0.0] * routing.dim
    vector[0] = 1.0
    vector[1] = 0.8
    routing.apply_batch(
        domain,
        phase="recovery",
        rows=[
            {
                "tool": "merchant.inspect",
                "vector": vector,
                "reward": 0.4,
                "ok": True,
                "meta": {"seed": True},
            }
        ],
    )
    skills.connections = 0
    skills.policy_calls = 0
    routing.connections = 0


def _shape_skills(rows) -> list[dict[str, Any]]:
    return [row.as_dict() for row in rows]


def _routing_shape(prepared: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in prepared.items() if key != "prepare_ms"}


def _policy_inputs(path: Path, skills_type=CountingBundledSkills):
    planner = AdaptivePlanner()
    registry = ToolRegistry(None)
    sandbox = ActionSandbox()
    skills = skills_type(path)
    policy = CounterfactualAdaptiveDecisionPolicy(
        planner,
        registry,
        sandbox,
        skills,
        max_calls=4,
        max_delegations=3,
    )
    if isinstance(skills, CountingBundledSkills):
        routing = CountingRouting(path)
        policy._routing_source = routing
        policy.routing._source = routing
    goal = planner.parse_goal(
        "审核商家并核对主体、授权和历史风险",
        [],
        domain_hint="merchant_review",
    )
    belief = planner.initial_belief(goal, [])
    return planner, registry, skills, policy, goal, belief


def _call_shape(calls):
    return [
        {
            "tool": call.tool,
            "args": call.args,
            "purpose": call.purpose,
            "estimated_cost": call.estimated_cost,
            "parallel_group": call.parallel_group,
        }
        for call in calls
    ]


def _recovery(policy, goal, belief, active):
    decision = policy.sanitize(
        None,
        goal=goal,
        remaining_budget=goal.max_tool_cost,
        previous=[],
        skills=active,
        phase="recovery",
        missing_evidence=list(belief.missing_evidence),
    )
    assert decision.calls == []
    return policy.fallback_calls(
        goal,
        belief,
        [],
        remaining_budget=goal.max_tool_cost,
        previous=[],
        skills=active,
    )


def test_fused_snapshot_halves_physical_connections_and_matches_legacy(tmp_path):
    path = tmp_path / "fusion.db"
    skills = CountingBundledSkills(path)
    routing = CountingRouting(path)
    domain = "merchant_review"
    _seed(skills, routing, domain)
    tools = ["merchant.inspect", "policy.lookup", "risk.scan"]

    legacy_skills = skills.relevant(domain, query="主体 授权", missing=["历史风险"])
    legacy_policy = skills.policy(domain)
    legacy_routing = routing.prepare_context(
        domain,
        tools=tools,
        exploration=float(legacy_policy["exploration"]),
    )
    legacy_connections = skills.connections + routing.connections
    assert legacy_connections == 2

    skills.connections = 0
    skills.policy_calls = 0
    routing.connections = 0
    fused = skills.prepare_decision_snapshot(
        routing,
        domain,
        query="主体 授权",
        missing=["历史风险"],
        tools=tools,
    )
    assert fused is not None
    assert skills.connections + routing.connections == 1
    assert _shape_skills(fused["skills"]) == _shape_skills(legacy_skills)
    assert fused["policy"] == legacy_policy
    assert _routing_shape(fused["routing"]) == _routing_shape(legacy_routing)


def test_preloaded_round_preserves_recovery_and_fallback_selection(tmp_path):
    legacy_path = tmp_path / "legacy.db"
    _, legacy_registry, legacy_skills, legacy, legacy_goal, legacy_belief = _policy_inputs(legacy_path)
    _seed(legacy_skills, legacy._routing_source, legacy_goal.domain.value)
    legacy_active = legacy_skills.relevant(
        legacy_goal.domain.value,
        query=legacy_goal.primary,
        missing=legacy_belief.missing_evidence,
    )
    legacy_calls = _recovery(legacy, legacy_goal, legacy_belief, legacy_active)
    legacy_connections = legacy_skills.connections + legacy._routing_source.connections
    assert legacy_connections == 2

    fused_path = tmp_path / "fused.db"
    _, fused_registry, fused_skills, fused, fused_goal, fused_belief = _policy_inputs(fused_path)
    _seed(fused_skills, fused._routing_source, fused_goal.domain.value)
    fused_active = fused.prepare_round_skills(
        fused_goal.domain.value,
        query=fused_goal.primary,
        missing=fused_belief.missing_evidence,
    )
    assert fused_active is not None
    fused_calls = _recovery(fused, fused_goal, fused_belief, fused_active)

    assert list(legacy_registry.tools) == list(fused_registry.tools)
    assert _call_shape(fused_calls) == _call_shape(legacy_calls)
    assert fused_skills.connections + fused._routing_source.connections == 1
    assert fused_skills.policy_calls == 0
    assert fused._routing_source.connections == 0


def test_controller_skill_view_keeps_planner_only_initial_read_cheap(tmp_path):
    _, _, skills, policy, goal, belief = _policy_inputs(tmp_path / "view.db")
    _seed(skills, policy._routing_source, goal.domain.value)
    fuse_next: ContextVar[bool] = ContextVar("test-fuse-next", default=False)
    view = _ControllerDecisionSkillView(skills, policy, fuse_next)

    initial = view.relevant(
        goal.domain.value,
        query=goal.primary,
        missing=belief.missing_evidence,
    )
    assert initial
    assert skills.connections == 1
    assert policy._routing_source.connections == 0

    skills.connections = 0
    policy._routing_source.connections = 0
    recovery = view.relevant(
        goal.domain.value,
        query=goal.primary,
        missing=belief.missing_evidence,
    )
    assert _shape_skills(recovery) == _shape_skills(initial)
    assert skills.connections == 1
    assert policy._routing_source.connections == 0


def test_fused_round_is_task_local(tmp_path):
    _, _, skills, policy, goal, belief = _policy_inputs(tmp_path / "task-local.db")
    _seed(skills, policy._routing_source, goal.domain.value)

    async def one():
        active = policy.prepare_round_skills(
            goal.domain.value,
            query=goal.primary,
            missing=belief.missing_evidence,
        )
        assert active is not None
        await asyncio.sleep(0)
        return _call_shape(_recovery(policy, goal, belief, active))

    async def exercise():
        return await asyncio.gather(one(), one())

    first, second = asyncio.run(exercise())
    assert first == second
    assert skills.connections == 2
    assert policy._routing_source.connections == 0


def test_custom_skill_plugin_falls_back_without_fused_api(tmp_path):
    _, _, skills, policy, goal, belief = _policy_inputs(
        tmp_path / "custom.db",
        skills_type=AdaptiveSkillLibrary,
    )
    assert policy.prepare_round_skills(
        goal.domain.value,
        query=goal.primary,
        missing=belief.missing_evidence,
    ) is None
    active = skills.relevant(
        goal.domain.value,
        query=goal.primary,
        missing=belief.missing_evidence,
    )
    calls = _recovery(policy, goal, belief, active)
    assert isinstance(calls, list)
