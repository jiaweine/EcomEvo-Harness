from __future__ import annotations

from pathlib import Path

import pytest

from ecomevo.runtime.engine import EcomEvoEngine
from ecomevo.runtime.factorized_routing import FactorizedAdaptiveRoutingStore


DOMAIN = "aftersales"


def _project(snapshot):
    return {
        "domain": snapshot["domain"],
        "samples": int(snapshot.get("samples", 0)),
        "reward_ewma": float(snapshot.get("reward_ewma", 0.0)),
        "residual_ewma": float(snapshot.get("residual_ewma", 0.0)),
    }


def _seed(store: FactorizedAdaptiveRoutingStore) -> None:
    for index in range(12):
        vector = [0.0] * store.dim
        vector[0] = 1.0
        vector[1] = 0.4 + 0.03 * index
        vector[4] = 0.5
        vector[7] = 0.65
        vector[10] = 0.5
        vector[11] = 1.0
        store.apply_batch(
            DOMAIN,
            phase="recovery",
            rows=[
                {
                    "tool": "evidence.search",
                    "vector": vector,
                    "reward": 0.2 + 0.04 * (index % 5),
                    "ok": index % 4 != 0,
                    "meta": {"test": True},
                }
            ],
        )


def test_state_summary_matches_snapshot_projection_for_empty_and_learned_domain(tmp_path: Path):
    store = FactorizedAdaptiveRoutingStore(tmp_path / "routing.db")

    assert store.state_summary("content_audit") == _project(store.snapshot("content_audit"))

    _seed(store)
    assert store.state_summary(DOMAIN) == _project(store.snapshot(DOMAIN))


def test_state_summary_does_not_decode_or_factorize_posterior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = FactorizedAdaptiveRoutingStore(tmp_path / "routing.db")
    _seed(store)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("compact state summary touched posterior state")

    monkeypatch.setattr(store, "_decode_row", unexpected)
    monkeypatch.setattr(store, "_posterior_from_row", unexpected)

    summary = store.state_summary(DOMAIN)
    assert summary["samples"] == 12
    assert set(summary) == {"domain", "samples", "reward_ewma", "residual_ewma"}


def test_engine_prefers_optional_routing_state_summary():
    class Routing:
        def __init__(self):
            self.summary_calls = 0

        def state_summary(self, domain):
            self.summary_calls += 1
            return {"domain": domain, "samples": 7, "reward_ewma": 0.4, "residual_ewma": 0.2}

        def snapshot(self, _domain):
            raise AssertionError("snapshot fallback should not run when summary exists")

    routing = Routing()
    result = EcomEvoEngine._routing_state_summary(routing, DOMAIN)

    assert routing.summary_calls == 1
    assert result["samples"] == 7


def test_engine_preserves_snapshot_fallback_for_custom_routing():
    class CustomRouting:
        def __init__(self):
            self.snapshot_calls = 0

        def snapshot(self, domain):
            self.snapshot_calls += 1
            return {
                "domain": domain,
                "samples": 3,
                "reward_ewma": 0.1,
                "residual_ewma": 0.25,
                "posterior_mean": {"bias": 0.0},
            }

    routing = CustomRouting()
    result = EcomEvoEngine._routing_state_summary(routing, DOMAIN)

    assert routing.snapshot_calls == 1
    assert result["samples"] == 3
