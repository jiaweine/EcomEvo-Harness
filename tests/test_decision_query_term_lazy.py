from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import ecomevo.runtime.control_policy as control_policy
from ecomevo.models import BeliefState, DecisionDomain, GoalState, ToolCall
from ecomevo.runtime.control_policy import DecisionPolicy


class _Tool:
    def __init__(self, cost: float) -> None:
        self.cost = cost


class _Planner:
    def __init__(self, calls: list[ToolCall]) -> None:
        self.calls = calls

    def plan(self, goal, belief, assets, recovery=False):
        return list(self.calls)


class _Registry:
    remote_specs: list[dict] = []

    def __init__(self) -> None:
        self.tools = {
            "evidence.search": _Tool(0.7),
            "policy.lookup": _Tool(0.6),
        }

    def describe(self):
        return [
            {"key": "evidence.search", "cost": 0.7, "mode": "read-only"},
            {"key": "policy.lookup", "cost": 0.6, "mode": "read-only"},
        ]

    def planned_calls(self, domain, recovery=False):
        return []


class _Sandbox:
    def validate_tool(self, tool: str):
        return SimpleNamespace(allowed=True, requires_confirmation=False)


class _Skills:
    def policy(self, domain: str):
        return {"exploration": 0.6}


def _goal() -> GoalState:
    return GoalState(
        primary="审核商家并核对主体、授权和历史风险",
        domain=DecisionDomain.MERCHANT_REVIEW,
        required_evidence=["主体标识", "授权材料"],
        max_tool_cost=8.0,
    )


def _search(call_id: str, *, keywords: list[str] | None = None) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool="evidence.search",
        purpose="补充证据检索",
        args={"keywords": list(keywords or [])},
        estimated_cost=0.7,
        parallel_group="parallel-a",
    )


def _policy(calls: list[ToolCall]) -> DecisionPolicy:
    policy = DecisionPolicy(
        _Planner(calls),
        _Registry(),
        _Sandbox(),
        _Skills(),
        max_calls=4,
        max_delegations=0,
    )
    # These tests isolate candidate normalization/query-term behavior from ranking.
    policy._rank_candidates = lambda candidates, **kwargs: (list(candidates), [])
    return policy


def test_fallback_reuses_goal_terms_across_search_candidates(monkeypatch):
    counts: Counter[int] = Counter()

    def fake_query_terms(query: str, limit: int = 40):
        counts[int(limit)] += 1
        return [f"goal-term-{limit}"]

    monkeypatch.setattr(control_policy, "_query_terms", fake_query_terms)
    policy = _policy([_search("a"), _search("b")])
    belief = BeliefState(missing_evidence=["主体标识"])

    calls = policy.fallback_calls(
        _goal(),
        belief,
        [],
        remaining_budget=8.0,
        previous=[],
        skills=[],
    )

    assert counts[16] == 1
    assert counts[24] == 0
    assert len(calls) == 1  # identical raw candidates still dedupe exactly as before
    assert calls[0].args["keywords"] == ["主体标识", "goal-term-16"]


def test_fallback_does_not_prepare_goal_terms_without_search_candidate(monkeypatch):
    counts: Counter[int] = Counter()

    def fake_query_terms(query: str, limit: int = 40):
        counts[int(limit)] += 1
        return ["unused"]

    monkeypatch.setattr(control_policy, "_query_terms", fake_query_terms)
    lookup = ToolCall(
        call_id="lookup",
        tool="policy.lookup",
        purpose="读取规则",
        args={},
        estimated_cost=0.6,
        parallel_group="parallel-a",
    )
    policy = _policy([lookup])

    policy.fallback_calls(
        _goal(),
        BeliefState(missing_evidence=["主体标识"]),
        [],
        remaining_budget=8.0,
        previous=[],
        skills=[],
    )

    assert counts[16] == 0
    assert counts[24] == 0


def test_sanitize_skips_unused_fallback_terms_when_keywords_are_supplied(monkeypatch):
    counts: Counter[int] = Counter()

    def fake_query_terms(query: str, limit: int = 40):
        counts[int(limit)] += 1
        return ["unexpected"]

    monkeypatch.setattr(control_policy, "_query_terms", fake_query_terms)
    policy = _policy([])

    decision = policy.sanitize(
        {
            "tool_calls": [
                {
                    "tool": "evidence.search",
                    "purpose": "provided",
                    "args": {"keywords": ["授权", "授权", ""]},
                }
            ]
        },
        goal=_goal(),
        remaining_budget=8.0,
        previous=[],
        skills=[],
        phase="fallback",
        missing_evidence=["授权材料"],
    )

    assert counts[24] == 0
    assert decision.calls[0].args["keywords"] == ["授权"]


def test_sanitize_still_builds_fallback_terms_when_keywords_are_empty(monkeypatch):
    counts: Counter[int] = Counter()

    def fake_query_terms(query: str, limit: int = 40):
        counts[int(limit)] += 1
        return ["主体", "授权", "主体"]

    monkeypatch.setattr(control_policy, "_query_terms", fake_query_terms)
    policy = _policy([])

    decision = policy.sanitize(
        {
            "tool_calls": [
                {
                    "tool": "evidence.search",
                    "purpose": "fallback",
                    "args": {"keywords": []},
                }
            ]
        },
        goal=_goal(),
        remaining_budget=8.0,
        previous=[],
        skills=[],
        phase="fallback",
        missing_evidence=["授权材料"],
    )

    assert counts[24] == 1
    assert decision.calls[0].args["keywords"] == ["主体", "授权"]
