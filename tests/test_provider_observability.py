from __future__ import annotations

import json
import time

from ecomevo.product import ConversationStore
from ecomevo.product.observability import QualityObservability
from ecomevo.providers.telemetry import empty_provider_usage, summarize_provider_usage


def _store(tmp_path):
    return ConversationStore(tmp_path / "provider-observability.db", tmp_path / "assets")


def _assistant(store, cid, created, payload):
    row = store.add_message(cid, "assistant", "结果", payload)
    with store._conn() as db:
        db.execute("UPDATE messages SET created_at=? WHERE id=?", (created, row["id"]))
    return row


def _priced_usage(monkeypatch, *, input_tokens=1000, output_tokens=500):
    monkeypatch.setenv(
        "ECOMEVO_PROVIDER_PRICING_JSON",
        json.dumps(
            {
                "version": "finance-v1",
                "currency": "USD",
                "rates": {
                    "openai:gpt-test": {
                        "input_per_million": 2.0,
                        "output_per_million": 4.0,
                    }
                },
            }
        ),
    )
    return summarize_provider_usage(
        [
            {
                "provider": "openai",
                "model": "gpt-test",
                "purpose": "inference",
                "source": "test",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cached_input_tokens": None,
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": None,
                "reasoning_tokens": None,
                "usage_available": True,
                "total_tokens_derived": False,
            }
        ]
    )


def test_observability_aggregates_provider_usage_with_tenant_scope(monkeypatch, tmp_path):
    store = _store(tmp_path)
    now = time.time()
    tenant_a = store.create_conversation("A", "aftersales", tenant_id="tenant-a", created_by="admin-a")
    tenant_b = store.create_conversation("B", "risk_review", tenant_id="tenant-b", created_by="admin-b")

    priced = _priced_usage(monkeypatch, input_tokens=1000, output_tokens=500)
    _assistant(
        store,
        tenant_a["id"],
        now - 30,
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "provider_usage": priced,
        },
    )
    _assistant(
        store,
        tenant_a["id"],
        now - 20,
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "provider_usage": empty_provider_usage(),
        },
    )
    _assistant(
        store,
        tenant_b["id"],
        now - 10,
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "provider_usage": _priced_usage(monkeypatch, input_tokens=9000, output_tokens=9000),
        },
    )

    snapshot = QualityObservability(store).snapshot(
        tenant_id="tenant-a",
        window="24h",
        now=now,
    )
    telemetry = snapshot["model_telemetry"]

    assert telemetry["assistant_results"] == 2
    assert telemetry["instrumented_results"] == 2
    assert telemetry["result_coverage_rate"] == 1.0
    assert telemetry["external_calls"] == 1
    assert telemetry["usage_reported_calls"] == 1
    assert telemetry["usage_coverage_rate"] == 1.0
    assert telemetry["tokens_complete"] is True
    assert telemetry["input_tokens"] == 1000
    assert telemetry["output_tokens"] == 500
    assert telemetry["total_tokens"] == 1500
    assert telemetry["providers"] == {"openai": 1}
    assert telemetry["models"] == {"openai:gpt-test": 1}

    assert telemetry["cost"]["available"] is True
    assert telemetry["cost"]["amount"] == 0.004
    assert telemetry["cost"]["currency"] == "USD"
    assert telemetry["cost"]["known_cost_by_currency"] == {"USD": 0.004}
    assert snapshot["telemetry_availability"]["token_usage"]["available"] is True
    assert snapshot["telemetry_availability"]["token_usage"]["complete"] is True
    assert snapshot["telemetry_availability"]["provider_cost"]["available"] is True


def test_legacy_result_reduces_coverage_and_prevents_total_cost(monkeypatch, tmp_path):
    store = _store(tmp_path)
    now = time.time()
    conv = store.create_conversation("A", "aftersales", tenant_id="tenant-a", created_by="admin-a")
    _assistant(
        store,
        conv["id"],
        now - 30,
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "provider_usage": _priced_usage(monkeypatch),
        },
    )
    _assistant(
        store,
        conv["id"],
        now - 20,
        {"runtime": {"status": "completed", "belief": {"missing_evidence": []}}},
    )

    snapshot = QualityObservability(store).snapshot(
        tenant_id="tenant-a",
        window="24h",
        now=now,
    )
    telemetry = snapshot["model_telemetry"]

    assert telemetry["assistant_results"] == 2
    assert telemetry["instrumented_results"] == 1
    assert telemetry["result_coverage_rate"] == 0.5
    assert telemetry["external_calls"] == 1
    assert telemetry["usage_reported_calls"] == 1
    assert telemetry["tokens_complete"] is False
    assert telemetry["cost"]["available"] is False
    assert telemetry["cost"]["known_cost_by_currency"] == {"USD": 0.004}
    assert "historical results without provider usage telemetry" in telemetry["token_reason"]
    assert "historical results without provider usage telemetry" in telemetry["cost"]["reason"]
    assert snapshot["telemetry_availability"]["token_usage"]["available"] is True
    assert snapshot["telemetry_availability"]["token_usage"]["complete"] is False
    assert snapshot["telemetry_availability"]["provider_cost"]["available"] is False


def test_malformed_historical_provider_usage_cannot_break_dashboard(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    conv = store.create_conversation("A", "aftersales", tenant_id="tenant-a", created_by="admin-a")
    _assistant(
        store,
        conv["id"],
        now - 10,
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "provider_usage": {
                "schema_version": "1",
                "external_calls": "broken",
                "usage_reported_calls": -9,
                "input_tokens": "nan",
                "output_tokens": None,
                "total_tokens": {},
                "events": [{"provider": "x", "model": "y"}],
                "cost": {
                    "priced_calls": "broken",
                    "currency": "USD",
                    "known_amount": "nan",
                },
            },
        },
    )

    snapshot = QualityObservability(store).snapshot(
        tenant_id="tenant-a",
        window="24h",
        now=now,
    )
    telemetry = snapshot["model_telemetry"]

    assert telemetry["instrumented_results"] == 1
    assert telemetry["external_calls"] == 0
    assert telemetry["usage_reported_calls"] == 0
    assert telemetry["input_tokens"] == 0
    assert telemetry["cost"]["available"] is False
    json.dumps(snapshot, ensure_ascii=False)
