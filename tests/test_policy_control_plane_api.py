from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.runtime import EcomEvoEngine, PolicyStore
from ecomevo.runtime.policy_view import runtime_policy_view


def test_authority_precedes_scope_specificity(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    store.create_version(
        policy_id="regulator",
        domain="product_governance",
        rules=["监管规则"],
        controls={"claim.allow": False},
        authority=90,
        status="active",
        effective_from="2025-01-01T00:00:00Z",
    )
    store.create_version(
        policy_id="tenant-override",
        domain="product_governance",
        rules=["租户规则"],
        controls={"claim.allow": True},
        scope={"tenant": "A"},
        authority=70,
        priority=999,
        status="active",
        effective_from="2025-01-01T00:00:00Z",
    )

    result = store.resolve(
        "product_governance",
        scope={"tenant": "A"},
        as_of="2025-02-01T00:00:00Z",
    )

    assert result["controls"]["claim.allow"] is False
    assert any(row["policy_id"] == "tenant-override" for row in result["overridden"])


def test_runtime_rule_prose_uses_only_highest_document_tier(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    store.create_version(
        policy_id="base",
        domain="aftersales",
        rules=["低优先级自然语言规则"],
        controls={"refund.max": 100, "evidence.action": "review"},
        authority=50,
        status="active",
        effective_from="2025-01-01T00:00:00Z",
    )
    store.create_version(
        policy_id="regulator",
        domain="aftersales",
        rules=["最高优先级自然语言规则"],
        controls={"refund.max": 80},
        authority=90,
        status="active",
        effective_from="2025-01-01T00:00:00Z",
    )

    view = runtime_policy_view(store.resolve("aftersales", as_of="2025-02-01T00:00:00Z"))

    assert view["controls"] == {"evidence.action": "review", "refund.max": 80}
    assert view["rules"] == ["最高优先级自然语言规则"]
    assert view["rule_policy_versions"] == ["regulator@v1"]
    assert {row["version_id"] for row in view["policies"]} == {"base@v1", "regulator@v1"}
    assert view["controls_authority"] == "structured_controls"


def test_conflicted_runtime_view_exposes_no_executable_rule_prose(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    for policy_id, value in (("a", "review"), ("b", "block")):
        store.create_version(
            policy_id=policy_id,
            domain="risk_review",
            rules=[f"{policy_id} rule"],
            controls={"risk.action": value},
            authority=80,
            priority=10,
            status="active",
            effective_from="2025-01-01T00:00:00Z",
        )

    view = runtime_policy_view(store.resolve("risk_review", as_of="2025-02-01T00:00:00Z"))

    assert view["status"] == "conflicted"
    assert view["rules"] == []
    assert view["rule_policy_versions"] == []
    assert set(view["policy_documents"]) == {"a@v1", "b@v1"}


def test_engine_policy_binding_keeps_tool_registry_builtin(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")

    assert engine.plugins.descriptor("tool.registry").source == "builtin"
    assert engine.tools.tools["policy.lookup"].policies is engine.policies


def test_policy_admin_api_is_read_only_and_rbac_protected(monkeypatch):
    from ecomevo.api.app import app

    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "admin")
    with TestClient(app) as client:
        listed = client.get("/api/runtime/policies?domain=merchant_review")
        assert listed.status_code == 200
        assert listed.json()["items"]

        resolved = client.get("/api/runtime/policies/resolve?domain=merchant_review")
        assert resolved.status_code == 200
        payload = resolved.json()
        assert payload["status"] == "resolved"
        assert payload["rule_policy_versions"]
        assert payload["controls_authority"] == "structured_controls"

        # V1 intentionally exposes no unauthenticated/admin write surface yet.
        assert client.post("/api/runtime/policies", json={}).status_code == 405

    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "viewer")
    with TestClient(app) as client:
        assert client.get("/api/runtime/policies").status_code == 403
        assert client.get("/api/runtime/policies/resolve?domain=merchant_review").status_code == 403
