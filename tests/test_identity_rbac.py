from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import ecomevo.api.app as api
from ecomevo.api.auth import AuthError, authenticate, sign_request
from ecomevo.models import BusinessAction
from ecomevo.product.tenant_store import TenantConversationStore


SECRET = "identity-test-secret-0123456789abcdef"


def signed_headers(method: str, path: str, *, tenant="tenant-a", user="alice", role="operator", timestamp=None):
    ts = str(int(time.time()) if timestamp is None else int(timestamp))
    return {
        "X-EcomEvo-Tenant": tenant,
        "X-EcomEvo-User": user,
        "X-EcomEvo-Role": role,
        "X-EcomEvo-Timestamp": ts,
        "X-EcomEvo-Signature": sign_request(SECRET, method, path, tenant, user, role, ts),
    }


def test_hmac_identity_accepts_valid_signature_and_rejects_tamper_and_expiry(monkeypatch):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "hmac")
    monkeypatch.setenv("ECOMEVO_AUTH_HMAC_SECRET", SECRET)
    now = int(time.time())
    headers = signed_headers("GET", "/api/conversations", timestamp=now)
    principal = authenticate("GET", "/api/conversations", headers, now=now)
    assert (principal.tenant_id, principal.user_id, principal.role) == ("tenant-a", "alice", "operator")

    tampered = dict(headers)
    tampered["X-EcomEvo-Tenant"] = "tenant-b"
    with pytest.raises(AuthError) as exc:
        authenticate("GET", "/api/conversations", tampered, now=now)
    assert exc.value.status == 401

    expired = signed_headers("GET", "/api/conversations", timestamp=now - 1000)
    with pytest.raises(AuthError) as exc:
        authenticate("GET", "/api/conversations", expired, now=now)
    assert exc.value.status == 401


def test_tenant_store_isolates_conversation_reads(tmp_path):
    store = TenantConversationStore(tmp_path / "product.db", tmp_path / "assets")
    a = store.create_conversation("A", tenant_id="tenant-a", created_by="alice")
    b = store.create_conversation("B", tenant_id="tenant-b", created_by="bob")
    assert [row["id"] for row in store.list_conversations(tenant_id="tenant-a")] == [a["id"]]
    assert [row["id"] for row in store.list_conversations(tenant_id="tenant-b")] == [b["id"]]
    with pytest.raises(KeyError):
        store.get_conversation(a["id"], tenant_id="tenant-b")


def test_hmac_api_enforces_tenant_and_approval_role_and_records_actor(monkeypatch, tmp_path):
    isolated = TenantConversationStore(tmp_path / "product.db", tmp_path / "assets")
    monkeypatch.setattr(api, "store", isolated)
    monkeypatch.setattr(api.job_worker, "store", isolated)
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "hmac")
    monkeypatch.setenv("ECOMEVO_AUTH_HMAC_SECRET", SECRET)

    with TestClient(api.app) as client:
        create_a = "/api/conversations"
        a = client.post(create_a, json={"title": "A", "scene": "merchant_review"}, headers=signed_headers("POST", create_a, tenant="tenant-a", user="alice", role="operator"))
        assert a.status_code == 200
        a = a.json()
        b = client.post(create_a, json={"title": "B", "scene": "merchant_review"}, headers=signed_headers("POST", create_a, tenant="tenant-b", user="bob", role="operator"))
        assert b.status_code == 200

        listing = client.get("/api/conversations", headers=signed_headers("GET", "/api/conversations", tenant="tenant-a", user="alice", role="viewer"))
        assert listing.status_code == 200
        assert [row["id"] for row in listing.json()] == [a["id"]]

        foreign_path = f"/api/conversations/{b.json()['id']}"
        foreign = client.get(foreign_path, headers=signed_headers("GET", foreign_path, tenant="tenant-a", user="alice", role="viewer"))
        assert foreign.status_code == 404

        action = BusinessAction(action_id="approval-a", kind="merchant.review", title="审核", description="提交审核")
        isolated.save_actions(a["id"], "session-a", [action])
        decision_path = "/api/actions/approval-a/decision"
        denied = client.post(decision_path, json={"decision": "approve", "note": "operator cannot"}, headers=signed_headers("POST", decision_path, tenant="tenant-a", user="alice", role="operator"))
        assert denied.status_code == 403

        approved = client.post(decision_path, json={"decision": "approve", "note": "reviewed"}, headers=signed_headers("POST", decision_path, tenant="tenant-a", user="carol", role="approver"))
        assert approved.status_code == 200
        payload = approved.json()["payload"]
        assert payload["actor_tenant"] == "tenant-a"
        assert payload["actor_user"] == "carol"
        assert payload["actor_role"] == "approver"
