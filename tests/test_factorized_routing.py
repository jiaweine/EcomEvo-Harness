from __future__ import annotations

from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.adaptive_routing import AdaptiveRoutingStore
from ecomevo.runtime.factorized_routing import FactorizedAdaptiveRoutingStore


def precision_row(store: AdaptiveRoutingStore, *, domain: str, scope: str, offset: float):
    matrix = store._prior_a()
    vector = store._prior_b()
    for step in range(1, 25):
        features = [
            (((step + 3 * index) % 11) - 5) / 7.0 + offset * (index + 1) / 30.0
            for index in range(store.dim)
        ]
        reward = (((step * 7) % 13) - 6) / 8.0
        for i, left in enumerate(features):
            vector[i] += reward * left
            for j, right in enumerate(features):
                matrix[i][j] += left * right
    return {
        "policy_key": store._key(domain, scope),
        "domain": domain,
        "scope": scope,
        "a": matrix,
        "b": vector,
        "samples": 96,
        "reward_ewma": 0.17,
        "residual_ewma": 0.21,
        "updated_at": 1.0,
    }


def assert_close(left: float, right: float, tolerance: float = 1e-10):
    assert abs(float(left) - float(right)) <= tolerance


def test_factorized_posterior_mean_matches_full_inverse(tmp_path):
    base = AdaptiveRoutingStore(tmp_path / "base.db")
    factorized = FactorizedAdaptiveRoutingStore(tmp_path / "factor.db")
    row = precision_row(base, domain="merchant_review", scope="domain", offset=0.13)

    expected = base._posterior_from_row(row)
    actual = factorized._posterior_from_row(row)

    assert "factor" in actual
    assert "inverse" not in actual
    for left, right in zip(expected["mean"], actual["mean"]):
        assert_close(left, right)


def test_factorized_score_matches_full_inverse(tmp_path):
    base = AdaptiveRoutingStore(tmp_path / "base-score.db")
    factorized = FactorizedAdaptiveRoutingStore(tmp_path / "factor-score.db")
    global_row = precision_row(base, domain="*", scope="global", offset=0.07)
    domain_row = precision_row(base, domain="merchant_review", scope="domain", offset=0.19)

    common = {
        "tau": 0.63,
        "residual": 0.21,
        "activation": 0.82,
        "mode": "adaptive",
        "beta": 0.41,
        "samples": 96,
        "global_samples": 180,
        "reliability": {"merchant.inspect": 0.73},
        "prepare_ms": 0.0,
    }
    legacy_prepared = {
        **common,
        "global": base._posterior_from_row(global_row),
        "domain": base._posterior_from_row(domain_row),
    }
    factor_prepared = {
        **common,
        "global": factorized._posterior_from_row(global_row),
        "domain": factorized._posterior_from_row(domain_row),
    }

    vectors = [
        [1.0, 0.7, 0.2, 0.6, 1.0, 0.0, 0.5, 0.8, 0.3, 0.0, 0.9, 1.0],
        [1.0, 0.1, 1.0, 0.2, 0.5, 1.0, 0.7, 0.4, 0.8, 0.3, 0.2, 1.0],
        factorized.abstain_vector(gap_pressure=0.8, recovery_context=1.0),
    ]
    for vector in vectors:
        expected = base.score_prepared(vector, legacy_prepared)
        actual = factorized.score_prepared(vector, factor_prepared)
        assert expected["mode"] == actual["mode"]
        assert expected["samples"] == actual["samples"]
        for key in ("score", "prior", "posterior", "uncertainty", "activation", "residual"):
            assert_close(expected[key], actual[key])


def test_factorized_store_falls_back_for_non_spd_precision(tmp_path):
    base = AdaptiveRoutingStore(tmp_path / "base-fallback.db")
    factorized = FactorizedAdaptiveRoutingStore(tmp_path / "factor-fallback.db")
    row = base._default_row("merchant_review", "domain")
    row["a"] = [list(values) for values in row["a"]]
    row["a"][0][0] = -1.0

    expected = base._posterior_from_row(row)
    actual = factorized._posterior_from_row(row)

    assert "inverse" in actual
    assert "factor" not in actual
    for left, right in zip(expected["mean"], actual["mean"]):
        assert_close(left, right)


def test_prepare_context_and_scoring_remain_equivalent(tmp_path):
    path = tmp_path / "shared.db"
    base = AdaptiveRoutingStore(path)
    factorized = FactorizedAdaptiveRoutingStore(path)
    tools = ["merchant.inspect", "risk.scan", "evidence.search"]

    legacy = base.prepare_context("merchant_review", tools=tools, exploration=0.61)
    prepared = factorized.prepare_context("merchant_review", tools=tools, exploration=0.61)

    assert legacy["mode"] == prepared["mode"]
    assert legacy["samples"] == prepared["samples"]
    assert legacy["global_samples"] == prepared["global_samples"]
    assert legacy["reliability"] == prepared["reliability"]
    for key in ("tau", "residual", "activation", "beta"):
        assert_close(legacy[key], prepared[key])

    vector = [1.0, 0.5, 1.0, 0.0, 1.0, 0.2, 0.4, 0.5, 0.3, 0.0, 0.7, 1.0]
    expected = base.score_prepared(vector, legacy)
    actual = factorized.score_prepared(vector, prepared)
    for key in ("score", "prior", "posterior", "uncertainty", "activation", "residual"):
        assert_close(expected[key], actual[key])


def test_default_counterfactual_policy_uses_factorized_store(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    assert isinstance(engine.autonomy.policy._routing_source, FactorizedAdaptiveRoutingStore)
