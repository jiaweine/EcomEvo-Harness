from __future__ import annotations

import asyncio
from pathlib import Path

from ecomevo.runtime.adaptive_routing import AdaptiveDecisionPolicy
from ecomevo.runtime.counterfactual_routing import CounterfactualAdaptiveDecisionPolicy
from ecomevo.runtime.planner import AdaptivePlanner
from ecomevo.runtime.sandbox import ActionSandbox
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


class CountingSkills(AdaptiveSkillLibrary):
    def __init__(self, path: Path):
        self.policy_calls = 0
        super().__init__(path)

    def policy(self, domain: str):
        self.policy_calls += 1
        return super().policy(domain)


def _policy_inputs(path: Path, policy_type):
    planner = AdaptivePlanner()
    registry = ToolRegistry(None)
    sandbox = ActionSandbox()
    skills = CountingSkills(path)
    policy = policy_type(
        planner,
        registry,
        sandbox,
        skills,
        max_calls=4,
        max_delegations=3,
    )
    goal = planner.parse_goal(
        "审核商家并核对主体、授权和历史风险",
        [],
        domain_hint="merchant_review",
    )
    belief = planner.initial_belief(goal, [])
    active = skills.relevant(
        goal.domain.value,
        query=goal.primary,
        missing=belief.missing_evidence,
    )
    return planner, skills, policy, goal, belief, active


def _count_routing_prepares(policy):
    source = getattr(policy, "_routing_source", policy.routing)
    original = source.prepare_context
    count = {"calls": 0}

    def wrapped(*args, **kwargs):
        count["calls"] += 1
        return original(*args, **kwargs)

    source.prepare_context = wrapped
    return count


def _run_recovery_then_fallback(policy, goal, belief, active):
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
    fallback = policy.fallback_calls(
        goal,
        belief,
        [],
        remaining_budget=goal.max_tool_cost,
        previous=[],
        skills=active,
    )
    return fallback


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


def test_decision_round_context_preserves_selection_and_eliminates_duplicate_reads(tmp_path):
    _, legacy_skills, legacy, legacy_goal, legacy_belief, legacy_active = _policy_inputs(
        tmp_path / "legacy.db",
        AdaptiveDecisionPolicy,
    )
    legacy_routing = _count_routing_prepares(legacy)
    legacy_calls = _run_recovery_then_fallback(
        legacy,
        legacy_goal,
        legacy_belief,
        legacy_active,
    )

    _, cached_skills, cached, cached_goal, cached_belief, cached_active = _policy_inputs(
        tmp_path / "cached.db",
        CounterfactualAdaptiveDecisionPolicy,
    )
    cached_routing = _count_routing_prepares(cached)
    cached_calls = _run_recovery_then_fallback(
        cached,
        cached_goal,
        cached_belief,
        cached_active,
    )

    assert _call_shape(cached_calls) == _call_shape(legacy_calls)
    assert legacy_skills.policy_calls == 4
    assert cached_skills.policy_calls == 1
    assert legacy_routing["calls"] == 2
    assert cached_routing["calls"] == 1


def test_routing_learning_invalidates_the_active_decision_round(tmp_path):
    _, skills, policy, goal, belief, active = _policy_inputs(
        tmp_path / "learning.db",
        CounterfactualAdaptiveDecisionPolicy,
    )
    routing = _count_routing_prepares(policy)
    _run_recovery_then_fallback(policy, goal, belief, active)
    assert skills.policy_calls == 1
    assert routing["calls"] == 1

    policy.routing.apply_batch(
        goal.domain.value,
        phase="recovery",
        rows=[
            {
                "tool": "merchant.inspect",
                "vector": [0.0] * len(policy.routing.FEATURE_NAMES),
                "reward": 0.25,
                "ok": True,
                "meta": {"test": True},
            }
        ],
    )
    policy.sanitize(
        None,
        goal=goal,
        remaining_budget=goal.max_tool_cost,
        previous=[],
        skills=active,
        phase="fallback",
        missing_evidence=list(belief.missing_evidence),
    )
    assert skills.policy_calls == 2
    assert routing["calls"] == 2


def test_decision_round_context_is_task_local(tmp_path):
    _, skills, policy, goal, belief, active = _policy_inputs(
        tmp_path / "concurrent.db",
        CounterfactualAdaptiveDecisionPolicy,
    )
    routing = _count_routing_prepares(policy)

    async def one():
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
        await asyncio.sleep(0)
        return policy.fallback_calls(
            goal,
            belief,
            [],
            remaining_budget=goal.max_tool_cost,
            previous=[],
            skills=active,
        )

    async def exercise():
        return await asyncio.gather(one(), one())

    first, second = asyncio.run(exercise())
    assert _call_shape(first) == _call_shape(second)
    assert skills.policy_calls == 2
    assert routing["calls"] == 2
