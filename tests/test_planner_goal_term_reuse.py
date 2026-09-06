from __future__ import annotations

import asyncio

import pytest

from ecomevo.runtime import planner as planner_module
from ecomevo.runtime.planner import AdaptivePlanner


def _normalize(calls):
    return [
        {
            "tool": call.tool,
            "purpose": call.purpose,
            "args": call.args,
            "estimated_cost": call.estimated_cost,
            "parallel_group": call.parallel_group,
        }
        for call in calls
    ]


def _fixture(planner: AdaptivePlanner, text: str = "退款订单争议需要核对物流和适用规则"):
    goal = planner.parse_goal(text, [], domain_hint="aftersales")
    belief = planner.initial_belief(goal, [])
    return goal, belief


def test_recovery_plans_reuse_goal_terms_for_same_goal(monkeypatch: pytest.MonkeyPatch):
    planner = AdaptivePlanner()
    goal, belief = _fixture(planner)
    original = planner_module._query_terms
    calls: list[tuple[str, int]] = []

    def counted(text: str, *, limit: int):
        calls.append((text, limit))
        return original(text, limit=limit)

    monkeypatch.setattr(planner_module, "_query_terms", counted)

    planner.plan(goal, belief, [], recovery=False)
    first = planner.plan(goal, belief, [], recovery=True)
    second = planner.plan(goal, belief, [], recovery=True)

    assert calls == [(goal.primary, 24)]
    assert _normalize(first) == _normalize(second)
    assert [row.call_id for row in first] != [row.call_id for row in second]


def test_non_recovery_plan_always_refreshes_goal_terms(monkeypatch: pytest.MonkeyPatch):
    planner = AdaptivePlanner()
    goal, belief = _fixture(planner)
    original = planner_module._query_terms
    count = 0

    def counted(text: str, *, limit: int):
        nonlocal count
        count += 1
        return original(text, limit=limit)

    monkeypatch.setattr(planner_module, "_query_terms", counted)

    planner.plan(goal, belief, [], recovery=False)
    planner.plan(goal, belief, [], recovery=False)
    planner.plan(goal, belief, [], recovery=True)

    assert count == 2


def test_recovery_with_different_goal_does_not_reuse_terms(monkeypatch: pytest.MonkeyPatch):
    planner = AdaptivePlanner()
    goal_a, belief_a = _fixture(planner, "退款订单争议需要物流证据")
    goal_b, belief_b = _fixture(planner, "退款订单争议需要卖家签收证据")
    original = planner_module._query_terms
    seen: list[str] = []

    def counted(text: str, *, limit: int):
        seen.append(text)
        return original(text, limit=limit)

    monkeypatch.setattr(planner_module, "_query_terms", counted)

    planner.plan(goal_a, belief_a, [], recovery=False)
    planner.plan(goal_b, belief_b, [], recovery=True)

    assert seen == [goal_a.primary, goal_b.primary]


@pytest.mark.asyncio
async def test_goal_term_cache_is_isolated_across_concurrent_tasks(monkeypatch: pytest.MonkeyPatch):
    planner = AdaptivePlanner()
    original = planner_module._query_terms
    counts: dict[str, int] = {}

    def counted(text: str, *, limit: int):
        counts[text] = counts.get(text, 0) + 1
        return original(text, limit=limit)

    monkeypatch.setattr(planner_module, "_query_terms", counted)

    async def worker(text: str):
        goal, belief = _fixture(planner, text)
        planner.plan(goal, belief, [], recovery=False)
        await asyncio.sleep(0)
        first = planner.plan(goal, belief, [], recovery=True)
        await asyncio.sleep(0)
        second = planner.plan(goal, belief, [], recovery=True)
        return _normalize(first), _normalize(second)

    text_a = "退款订单争议需要物流证据 A"
    text_b = "退款订单争议需要物流证据 B"
    (a1, a2), (b1, b2) = await asyncio.gather(worker(text_a), worker(text_b))

    assert counts == {text_a: 1, text_b: 1}
    assert a1 == a2
    assert b1 == b2
