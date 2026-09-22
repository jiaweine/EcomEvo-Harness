from __future__ import annotations

import pytest

from ecomevo.product.shadow_environment import (
    CORPUS_REDACTION_PROFILE,
    ShadowEnterpriseSimulator,
)


def _fixture_payload() -> dict:
    return {
        "surface": "mcp",
        "operation": "governed_action",
        "mutation": "timeout_after_dispatch",
        "target": "refund.execute",
        "context_labels": ["aftersales", "refund"],
        "source_system": "mcp.gateway",
        "source_event_id": "evt-20260922-001",
        "observed_at": "2026-09-22T12:34:56+08:00",
        "source_record_sha256": "a" * 64,
        "redaction_profile": CORPUS_REDACTION_PROFILE,
        "redaction_attested": True,
        "phase": "post_dispatch",
        "status_code": 504,
        "error_code": "UPSTREAM_TIMEOUT",
        "error_class": "GatewayTimeout",
        "latency_ms": 8120,
    }


def test_shadow_corpus_fixture_is_deterministic_tenant_scoped_and_provenance_bound():
    simulator = ShadowEnterpriseSimulator()
    payload = _fixture_payload()

    first = simulator.import_fixture(tenant_id="tenant-a", **payload)
    second = simulator.import_fixture(tenant_id="tenant-a", **payload)
    other_tenant = simulator.import_fixture(tenant_id="tenant-b", **payload)
    other_source = simulator.import_fixture(
        tenant_id="tenant-a",
        **{**payload, "source_record_sha256": "b" * 64},
    )

    assert first == second
    assert first["fixture_id"] == second["fixture_id"]
    assert first["fixture_id"] != other_tenant["fixture_id"]
    assert first["fixture_id"] != other_source["fixture_id"]
    assert first["tenant_scope"] == "tenant-a"
    assert first["provenance"]["observed_at"] == "2026-09-22T04:34:56Z"
    assert first["provenance"]["source_record_sha256"] == "a" * 64
    assert first["provenance"]["source_digest_verified_by_shadow"] is False
    assert first["sanitized_observation"] == {
        "phase": "post_dispatch",
        "status_code": 504,
        "error_code": "UPSTREAM_TIMEOUT",
        "error_class": "GatewayTimeout",
        "latency_ms": 8120,
    }
    assert first["expected_control"]["runtime_outcome"] == "uncertain"
    assert first["expected_control"]["automatic_retry_allowed"] is False
    assert first["replay_fixture"]["provenance_bound"] is True
    assert first["replay_fixture"]["pre_redacted_metadata_only"] is True
    assert first["replay_fixture"]["raw_payload_accepted"] is False
    assert first["replay_fixture"]["production_evidence"] is False
    assert first["authority"]["changes_business_action_state"] is False
    assert first["authority"]["changes_production_authority"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_record_sha256", "abc", "64-character SHA-256"),
        ("redaction_attested", False, "redaction_attested=true"),
        ("phase", "after_everything", "unsupported failure observation phase"),
        ("error_code", "Bearer secret-token", "bounded identifier"),
        ("source_event_id", "customer@example.com", "bounded opaque identifier"),
    ],
)
def test_shadow_corpus_fixture_fails_closed_on_untrusted_or_free_form_metadata(
    field: str,
    value: object,
    message: str,
):
    simulator = ShadowEnterpriseSimulator()
    payload = {**_fixture_payload(), field: value}

    with pytest.raises(ValueError, match=message):
        simulator.import_fixture(tenant_id="tenant-a", **payload)


def test_shadow_corpus_catalog_is_explicit_about_import_trust_boundary():
    catalog = ShadowEnterpriseSimulator.catalog()
    corpus = catalog["corpus_import"]

    assert corpus["supported"] is True
    assert corpus["endpoint"] == "/api/runtime/shadow/import-fixture"
    assert corpus["redaction_profile"] == CORPUS_REDACTION_PROFILE
    assert corpus["accepts_raw_payload"] is False
    assert corpus["accepts_headers"] is False
    assert corpus["accepts_body"] is False
    assert corpus["requires_upstream_sha256"] is True
    assert corpus["upstream_digest_verified_by_shadow"] is False
    assert "raw payloads, headers, bodies" in catalog["methodology"]["corpus_import"]
    assert "does not independently verify" in catalog["methodology"]["provenance_binding"]
