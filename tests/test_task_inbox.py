from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from ecomevo.api.app import app
from ecomevo.models import BusinessAction
from ecomevo.product.queue_store import QueueConversationStore


def make_store(tmp_path):
    return QueueConversationStore(tmp_path / "product.db", tmp_path / "assets")


def test_inbox_is_tenant_scoped(tmp_path):
    store = make_store(tmp_path)
    alpha = store.create_conversation("Alpha task", tenant_id="tenant-alpha", created_by="alice")
    beta = store.create_conversation("Beta task", tenant_id="tenant-beta", created_by="bob")

    alpha_rows = store.list_inbox(tenant_id="tenant-alpha")
    beta_rows = store.list_inbox(tenant_id="tenant-beta")

    assert [row["id"] for row in alpha_rows] == [alpha["id"]]
    assert [row["id"] for row in beta_rows] == [beta["id"]]
    try:
        store.get_inbox_item(beta["id"], tenant_id="tenant-alpha")
    except KeyError:
        pass
    else:
        raise AssertionError("cross-tenant inbox item leaked")


def test_claim_is_atomic_single_winner(tmp_path):
    store = make_store(tmp_path)
    row = store.create_conversation("Race", tenant_id="tenant-a", created_by="creator")

    def claim(owner):
        return store.claim_conversation(row["id"], owner, tenant_id="tenant-a")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["operator-a", "operator-b"]))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    current = store.get_inbox_item(row["id"], tenant_id="tenant-a")
    assert current["owner_user_id"] in {"operator-a", "operator-b"}


def test_queue_state_is_derived_from_authoritative_runtime_records(tmp_path):
    store = make_store(tmp_path)
    row = store.create_conversation("Derived state", tenant_id="tenant-a", created_by="creator")
    cid = row["id"]
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["queue_state"] == "new"

    store.add_message(cid, "user", "请核对订单", {})
    store.add_message(
        cid,
        "assistant",
        "还需要补充物流签收证据",
        {"runtime": {"status": "needs_evidence", "belief": {"missing_evidence": ["签收证明"]}}},
    )
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["queue_state"] == "waiting_evidence"

    store.save_actions(
        cid,
        "session-1",
        [BusinessAction(action_id="act-proposed", kind="refund", title="退款", description="待确认", status="proposed")],
    )
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["queue_state"] == "waiting_approval"

    store.save_actions(
        cid,
        "session-1",
        [BusinessAction(action_id="act-uncertain", kind="refund", title="退款", description="结果未知", status="uncertain")],
    )
    uncertain = store.get_inbox_item(cid, tenant_id="tenant-a")
    assert uncertain["queue_state"] == "needs_verification"
    assert "禁止盲目重试" in uncertain["queue_state_reason"]

    lease = store.claim_turn(cid)
    assert lease
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["queue_state"] == "processing"
    assert store.release_turn(cid, lease) is True
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["queue_state"] == "needs_verification"


def test_grounding_conflict_and_insufficiency_are_queue_signals(tmp_path):
    store = make_store(tmp_path)
    conflict = store.create_conversation("Conflict", tenant_id="tenant-a", created_by="creator")
    store.add_message(conflict["id"], "user", "核对规则", {})
    store.add_message(
        conflict["id"],
        "assistant",
        "证据存在冲突",
        {"grounding": {"evidence_sufficiency": "conflicted"}},
    )
    assert store.get_inbox_item(conflict["id"], tenant_id="tenant-a")["queue_state"] == "needs_verification"

    insufficient = store.create_conversation("Insufficient", tenant_id="tenant-a", created_by="creator")
    store.add_message(insufficient["id"], "user", "核对资质", {})
    store.add_message(
        insufficient["id"],
        "assistant",
        "缺少支持",
        {"grounding": {"evidence_sufficiency": "insufficient"}},
    )
    assert store.get_inbox_item(insufficient["id"], tenant_id="tenant-a")["queue_state"] == "waiting_evidence"


def test_priority_and_claim_metadata_do_not_rewrite_business_activity_time(tmp_path):
    store = make_store(tmp_path)
    row = store.create_conversation("Activity time", tenant_id="tenant-a", created_by="creator")
    cid = row["id"]
    business_updated_at = row["updated_at"]

    updated = store.update_queue_priority(cid, "urgent", tenant_id="tenant-a")
    assert updated["queue_priority"] == "urgent"
    assert updated["updated_at"] == business_updated_at
    assert updated["queue_updated_at"] > 0

    claimed = store.claim_conversation(cid, "operator-a", tenant_id="tenant-a")
    assert claimed is not None
    assert claimed["updated_at"] == business_updated_at
    assert claimed["owner_user_id"] == "operator-a"


def test_inbox_api_inherits_global_rbac(monkeypatch):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", "queue-test-user")
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "admin")

    with TestClient(app) as client:
        created = client.post(
            "/api/conversations",
            json={"title": "RBAC queue task", "scene": "risk_review"},
        )
        assert created.status_code == 200
        cid = created.json()["id"]

        monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "viewer")
        listing = client.get("/api/inbox?view=all")
        assert listing.status_code == 200
        assert listing.json()["authority"] == {
            "assignment_grants_approval": False,
            "priority_changes_runtime_routing": False,
        }
        assert client.post(f"/api/inbox/{cid}/claim").status_code == 403
        assert client.patch(f"/api/inbox/{cid}/priority", json={"priority": "high"}).status_code == 403

        monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "operator")
        claimed = client.post(f"/api/inbox/{cid}/claim")
        assert claimed.status_code == 200
        assert claimed.json()["owner_user_id"] == "queue-test-user"
        priority = client.patch(f"/api/inbox/{cid}/priority", json={"priority": "high"})
        assert priority.status_code == 200
        assert priority.json()["queue_priority"] == "high"


def test_inbox_ui_is_packaged_and_protected(monkeypatch):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "viewer")
    with TestClient(app) as client:
        page = client.get("/api/inbox/ui")
        assert page.status_code == 200
        assert "EcomEvo · 任务队列" in page.text
        assert client.get("/assets/inbox.js").status_code == 200
        assert client.get("/assets/inbox.css").status_code == 200
