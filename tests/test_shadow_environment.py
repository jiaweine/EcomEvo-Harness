from __future__ import annotations

from ecomevo.product.shadow_environment import ShadowEnterpriseSimulator


def test_shadow_candidate_is_deterministic_and_tenant_scoped():
    simulator = ShadowEnterpriseSimulator()
    payload = {
        "surface": "mcp",
        "operation": "governed_action",
        "mutation": "timeout_after_dispatch",
        "target": "refund.execute",
        "context_labels": ["aftersales", "refund"],
    }

    first = simulator.simulate(tenant_id="tenant-a", **payload)
    second = simulator.simulate(tenant_id="tenant-a", **payload)
    other = simulator.simulate(tenant_id="tenant-b", **payload)

    assert first == second
    assert first["candidate_id"] == second["candidate_id"]
    assert first["candidate_id"] != other["candidate_id"]
    assert first["tenant_scope"] == "tenant-a"
    assert first["expected_control"]["runtime_outcome"] == "uncertain"
    assert first["expected_control"]["automatic_retry_allowed"] is False
    assert first["expected_control"]["requires_business_state_check"] is True
    assert first["expected_control"]["approval_still_required_for_side_effect"] is True
    assert first["replay_candidate"]["executable"] is False
    assert first["replay_candidate"]["invokes_real_system"] is False
    assert first["replay_candidate"]["production_evidence"] is False
    assert first["authority"]["changes_business_action_state"] is False
    assert first["authority"]["changes_production_authority"] is False


def test_shadow_explicit_rejection_is_not_mislabeled_uncertain():
    simulator = ShadowEnterpriseSimulator()
    result = simulator.simulate(
        tenant_id="tenant-a",
        surface="mcp",
        operation="governed_action",
        mutation="permission_denied",
        target="refund.execute",
    )

    assert result["expected_control"]["runtime_outcome"] == "failed"
    assert result["expected_control"]["requires_business_state_check"] is False
    assert result["expected_control"]["automatic_retry_allowed"] is False


def test_shadow_schema_mutation_requires_distinct_fingerprints():
    simulator = ShadowEnterpriseSimulator()
    before = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
    }
    after = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"type": "string"}},
    }

    result = simulator.simulate(
        tenant_id="tenant-a",
        surface="structured_data",
        operation="read",
        mutation="missing_required_field",
        target="warehouse.snapshot",
        baseline_schema=before,
        mutated_schema=after,
    )

    scenario = result["scenario"]
    assert scenario["baseline_schema_fingerprint"]
    assert scenario["mutated_schema_fingerprint"]
    assert scenario["baseline_schema_fingerprint"] != scenario["mutated_schema_fingerprint"]
    assert result["expected_control"]["runtime_outcome"] == "blocked_for_revalidation"
    assert result["expected_control"]["requires_schema_revalidation"] is True
    assert "warehouse.snapshot" in str(result)
    assert "properties" not in str(result)

    try:
        simulator.simulate(
            tenant_id="tenant-a",
            surface="structured_data",
            operation="read",
            mutation="missing_required_field",
            target="warehouse.snapshot",
            baseline_schema=before,
            mutated_schema=before,
        )
    except ValueError as exc:
        assert "changed schema fingerprint" in str(exc)
    else:
        raise AssertionError("expected unchanged schema mutation to fail")


def test_shadow_surface_mutation_pair_is_explicit():
    simulator = ShadowEnterpriseSimulator()
    try:
        simulator.simulate(
            tenant_id="tenant-a",
            surface="browser",
            operation="read",
            mutation="permission_denied",
            target="admin.console",
        )
    except ValueError as exc:
        assert "unsupported mutation" in str(exc)
    else:
        raise AssertionError("expected cross-surface mutation to fail")


def test_shadow_catalog_states_non_execution_authority():
    catalog = ShadowEnterpriseSimulator.catalog()
    assert set(catalog["surfaces"]) == {"mcp", "browser", "terminal", "structured_data"}
    assert catalog["authority"] == {
        "read_only_runtime": True,
        "executes_real_tools": False,
        "launches_real_browser": False,
        "launches_real_terminal": False,
        "changes_routing": False,
        "changes_policy": False,
        "changes_runtime_skills": False,
        "approves_business_actions": False,
        "changes_business_action_state": False,
        "changes_production_authority": False,
    }
    assert "offline replay/training candidates only" in catalog["methodology"]["execution"]
