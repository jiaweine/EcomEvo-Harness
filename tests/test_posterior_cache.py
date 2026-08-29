from __future__ import annotations

from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.posterior_cache import CachedAdaptiveRoutingStore


def _row(store: CachedAdaptiveRoutingStore, reward: float = 0.4):
    vector = [0.0] * store.dim
    vector[0] = 1.0
    vector[1] = 0.7
    vector[7] = 0.5
    return {
        "tool": "merchant.inspect",
        "vector": vector,
        "reward": reward,
        "ok": reward >= 0.0,
        "meta": {"source": "cache-test"},
    }


def test_repeated_prepare_reuses_exact_posterior_factorization(tmp_path):
    store = CachedAdaptiveRoutingStore(tmp_path / "routing.db")
    first = store.prepare_context(
        "merchant_review",
        tools=["merchant.inspect", "evidence.search"],
        exploration=0.6,
    )
    first_stats = store.posterior_cache_stats()
    assert first_stats == {"entries": 2, "hits": 0, "misses": 2}

    for _ in range(100):
        again = store.prepare_context(
            "merchant_review",
            tools=["merchant.inspect", "evidence.search"],
            exploration=0.6,
        )
        assert again["global"]["mean"] == first["global"]["mean"]
        assert again["domain"]["mean"] == first["domain"]["mean"]
        assert again["global"]["inverse"] == first["global"]["inverse"]
        assert again["domain"]["inverse"] == first["domain"]["inverse"]

    stats = store.posterior_cache_stats()
    assert stats["entries"] == 2
    assert stats["misses"] == 2
    assert stats["hits"] == 200


def test_local_write_moves_to_new_cache_version(tmp_path):
    store = CachedAdaptiveRoutingStore(tmp_path / "routing.db")
    before = store.prepare_context("merchant_review", tools=["merchant.inspect"], exploration=0.6)
    store.prepare_context("merchant_review", tools=["merchant.inspect"], exploration=0.6)
    stats_before_write = store.posterior_cache_stats()

    update = store.apply_batch(
        "merchant_review",
        phase="initial",
        rows=[_row(store, reward=0.8)],
    )
    assert update is not None
    after = store.prepare_context("merchant_review", tools=["merchant.inspect"], exploration=0.6)
    stats_after_write = store.posterior_cache_stats()

    assert after["samples"] == before["samples"] + 1
    assert stats_after_write["misses"] >= stats_before_write["misses"] + 2
    assert after["domain"]["mean"] != before["domain"]["mean"]


def test_external_process_style_write_cannot_leave_stale_cache(tmp_path):
    db = tmp_path / "routing.db"
    reader = CachedAdaptiveRoutingStore(db)
    writer = CachedAdaptiveRoutingStore(db)

    reader.prepare_context("merchant_review", tools=["merchant.inspect"], exploration=0.6)
    reader.prepare_context("merchant_review", tools=["merchant.inspect"], exploration=0.6)
    cached = reader.posterior_cache_stats()

    update = writer.apply_batch(
        "merchant_review",
        phase="recovery",
        rows=[_row(writer, reward=-0.3)],
    )
    assert update is not None

    refreshed = reader.prepare_context("merchant_review", tools=["merchant.inspect"], exploration=0.6)
    stats = reader.posterior_cache_stats()
    assert refreshed["samples"] == 1
    assert stats["misses"] >= cached["misses"] + 2


def test_production_engine_uses_cached_routing_without_changing_policy_type(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    routing = engine.autonomy.policy.routing
    assert isinstance(routing, CachedAdaptiveRoutingStore)
    assert routing.posterior_cache_stats() == {"entries": 0, "hits": 0, "misses": 0}
