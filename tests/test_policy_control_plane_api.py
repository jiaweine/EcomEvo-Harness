from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecomevo.identity import IdentityMiddleware
from ecomevo.runtime import EcomEvoEngine, PolicyStore
from ecomevo.runtime.policy_context import bind_policy_scope, current_policy_scope, reset_policy_scope
from ecomevo.runtime.policy_engine import VersionedPolicyLookupTool
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


def test_engine_policy_binding_keeps_tool_registry_builtin_and_general_compatible(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")

    assert engine.plugins.descriptor("tool.registry").source == "builtin"
    assert engine.tools.tools["policy.lookup"].policies is engine.policies
    general = engine.policies.resolve("general", as_of="2026-09-16T00:00:00Z")
    assert general["status"] == "resolved"
    assert general["policies"][0]["version_id"] == "builtin.general@v1"


def test_bound_runtime_tenant_cannot_be_overridden_by_tool_scope(tmp_path):
    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    store.create_version(
        policy_id="tenant-a",
        domain="product_governance",
        rules=["A policy"],
        controls={"tenant.policy": "A"},
        scope={"tenant": "A"},
        status="active",
        effective_from="2025-01-01T00:00:00Z",
    )
    store.create_version(
        policy_id="tenant-b",
        domain="product_governance",
        rules=["B policy"],
        controls={"tenant.policy": "B"},
        scope={"tenant": "B"},
        status="active",
        effective_from="2025-01-01T00:00:00Z",
    )
    tool = VersionedPolicyLookupTool(store)
    goal = SimpleNamespace(domain=SimpleNamespace(value="product_governance"))
    token = bind_policy_scope({"tenant": "A"})
    try:
        result = asyncio.run(
            tool.execute(
                {"goal": goal, "text": "核对商品", "assets": []},
                {"scope": {"tenant": "B"}, "as_of": "2025-02-01T00:00:00Z"},
            )
        )
    finally:
        reset_policy_scope(token)

    assert result["status"] == "resolved"
    assert result["scope"]["tenant"] == "A"
    assert result["controls"]["tenant.policy"] == "A"
    assert current_policy_scope() == {}


def test_policy_aware_durable_worker_binds_conversation_tenant():
    from ecomevo.api.policy_worker import PolicyAwareDurableConversationWorker

    class Store:
        def claim_job(self, worker_id, *, job_id=None, lease_seconds=120.0):
            return {"id": "job-1", "conversation_id": "cv-1", "payload": {}}

        def conversation_tenant(self, cid):
            assert cid == "cv-1"
            return "tenant-A"

    async def emit(*args, **kwargs):
        return None

    worker = PolicyAwareDurableConversationWorker(Store(), object(), None, emit=emit, wake=lambda _cid: None)
    captured = {}

    async def fake_execute(job):
        captured.update(current_policy_scope())

    worker._execute = fake_execute
    assert asyncio.run(worker.run_once()) is True
    assert captured == {"tenant": "tenant-A"}
    assert current_policy_scope() == {}


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
        assert payload["scope"]["tenant"]
        assert payload["rule_policy_versions"]
        assert payload["controls_authority"] == "structured_controls"

        # V1 intentionally exposes no unauthenticated/admin write surface yet.
        assert client.post("/api/runtime/policies", json={}).status_code == 405

    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "viewer")
    with TestClient(app) as client:
        assert client.get("/api/runtime/policies").status_code == 403
        assert client.get("/api/runtime/policies/resolve?domain=merchant_review").status_code == 403


def test_policy_admin_api_hides_other_tenants_and_rejects_scope_spoof(monkeypatch, tmp_path):
    from ecomevo.api.policy_api import build_policy_router

    store = PolicyStore(tmp_path / "policy.db", seed_defaults=False)
    for policy_id, scope, value in (
        ("global", {}, "global"),
        ("tenant-a", {"tenant": "A"}, "A"),
        ("tenant-b", {"tenant": "B"}, "B"),
    ):
        store.create_version(
            policy_id=policy_id,
            domain="merchant_review",
            rules=[f"{policy_id} policy"],
            controls={"tenant.policy": value},
            scope=scope,
            status="active",
            effective_from="2025-01-01T00:00:00Z",
        )

    mini = FastAPI()
    mini.include_router(build_policy_router(SimpleNamespace(policies=store)))
    mini.add_middleware(IdentityMiddleware)
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "admin")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", "A")

    with TestClient(mini) as client:
        listed = client.get("/api/runtime/policies?domain=merchant_review")
        assert listed.status_code == 200
        ids = {row["policy_id"] for row in listed.json()["items"]}
        assert ids == {"global", "tenant-a"}

        resolved = client.get("/api/runtime/policies/resolve?domain=merchant_review&as_of=2025-02-01T00:00:00Z")
        assert resolved.status_code == 200
        assert resolved.json()["scope"]["tenant"] == "A"
        assert resolved.json()["controls"]["tenant.policy"] == "A"

        spoof = json.dumps({"tenant": "B"})
        rejected = client.get("/api/runtime/policies/resolve", params={"domain": "merchant_review", "scope": spoof})
        assert rejected.status_code == 403
