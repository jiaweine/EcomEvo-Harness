from __future__ import annotations

from collections import Counter

from ecomevo.runtime.adaptive_routing import AdaptiveDecisionPolicy
from ecomevo.runtime.precomputed_ranking import PrecomputedAdaptiveDecisionPolicy


def _policy() -> PrecomputedAdaptiveDecisionPolicy:
    policy = object.__new__(PrecomputedAdaptiveDecisionPolicy)
    policy._static_term_cache = {}
    policy._static_term_cache_order = []
    return policy


def test_set_terms_are_cached_and_return_isolated_copy(monkeypatch):
    calls: Counter[str] = Counter()

    def base_terms(value):
        calls["base"] += 1
        if isinstance(value, set):
            return {str(item).lower() for item in value}
        return {str(value).lower()}

    monkeypatch.setattr(AdaptiveDecisionPolicy, "_terms", staticmethod(base_terms))
    policy = _policy()
    metadata = {"主体", "授权"}

    first = policy._terms(metadata)
    first.add("mutated")
    second = policy._terms(metadata)

    assert calls["base"] == 1
    assert second == {"主体", "授权"}
    assert "mutated" not in second
    assert len(policy._static_term_cache) == 1


def test_string_targets_never_enter_static_cache(monkeypatch):
    calls: Counter[str] = Counter()

    def base_terms(value):
        calls["base"] += 1
        return {str(value)}

    monkeypatch.setattr(AdaptiveDecisionPolicy, "_terms", staticmethod(base_terms))
    policy = _policy()

    assert policy._terms("授权材料") == {"授权材料"}
    assert policy._terms("授权材料") == {"授权材料"}

    assert calls["base"] == 2
    assert policy._static_term_cache == {}
    assert policy._static_term_cache_order == []


def test_changed_tool_metadata_uses_a_new_cache_entry(monkeypatch):
    calls: Counter[str] = Counter()

    def base_terms(value):
        calls["base"] += 1
        return {str(item) for item in value}

    monkeypatch.setattr(AdaptiveDecisionPolicy, "_terms", staticmethod(base_terms))
    policy = _policy()

    first = policy._terms({"主体"})
    changed = policy._terms({"主体", "授权"})

    assert first == {"主体"}
    assert changed == {"主体", "授权"}
    assert calls["base"] == 2
    assert len(policy._static_term_cache) == 2


def test_static_cache_is_bounded(monkeypatch):
    def base_terms(value):
        return {str(item) for item in value}

    monkeypatch.setattr(AdaptiveDecisionPolicy, "_terms", staticmethod(base_terms))
    policy = _policy()
    policy._STATIC_TERM_CACHE_LIMIT = 3

    for index in range(4):
        policy._terms({f"tool-meta-{index}"})

    assert len(policy._static_term_cache) == 3
    assert len(policy._static_term_cache_order) == 3
    assert "tool-meta-0" not in policy._static_term_cache
    assert "tool-meta-1" in policy._static_term_cache
