from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from ecomevo.api.app import app
from ecomevo.models import BusinessAction
from ecomevo.product.queue_store import CollaborationConflict, QueueConversationStore


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


def test_collaboration_watch_comments_mentions_and_review_are_tenant_scoped(tmp_path):
    store = make_store(tmp_path)
    row = store.create_conversation("Collaboration", tenant_id="tenant-a", created_by="creator")
    cid = row["id"]
    business_updated_at = row["updated_at"]

    watched = store.watch_conversation(cid, "operator-a", tenant_id="tenant-a")
    assert watched["current_user_watching"] is True
    assert [item["user_id"] for item in watched["watchers"]] == ["operator-a"]
    assert watched["authority"] == {
        "collaboration_grants_approval": False,
        "review_request_grants_approval": False,
        "handoff_grants_approval": False,
        "comments_change_runtime": False,
        "watching_changes_runtime": False,
    }

    commented = store.add_collaboration_comment(
        cid,
        "operator-a",
        "@reviewer@example.com 请复核证据；@reviewer@example.com 重复 mention 不应重复记录。",
        tenant_id="tenant-a",
    )
    comment = [event for event in commented["events"] if event["event_type"] == "comment"][-1]
    assert comment["actor_user_id"] == "operator-a"
    assert comment["mentions"] == ["reviewer@example.com"]

    reviewed = store.request_task_review(
        cid,
        "operator-a",
        "reviewer-a",
        note="请独立复核当前结论。",
        tenant_id="tenant-a",
    )
    request = [event for event in reviewed["events"] if event["event_type"] == "review_requested"][-1]
    assert request["target_user_id"] == "reviewer-a"
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["updated_at"] == business_updated_at

    unwatched = store.unwatch_conversation(cid, "operator-a", tenant_id="tenant-a")
    assert unwatched["current_user_watching"] is False
    assert unwatched["watchers"] == []

    with pytest.raises(KeyError):
        store.list_collaboration(cid, tenant_id="tenant-b", current_user_id="operator-b")


def test_handoff_requires_target_acceptance_and_transfers_only_collaboration_owner(tmp_path):
    store = make_store(tmp_path)
    row = store.create_conversation("Handoff", tenant_id="tenant-a", created_by="creator")
    cid = row["id"]
    business_updated_at = row["updated_at"]
    claimed = store.claim_conversation(cid, "owner-a", tenant_id="tenant-a")
    assert claimed is not None

    requested = store.request_task_handoff(
        cid,
        "owner-a",
        "owner-b",
        note="请接手后续证据核对。",
        tenant_id="tenant-a",
    )
    request_id = requested["pending_handoffs"][0]["id"]
    assert requested["owner_user_id"] == "owner-a"
    with pytest.raises(CollaborationConflict):
        store.request_task_handoff(
            cid,
            "owner-a",
            "owner-c",
            tenant_id="tenant-a",
        )
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["owner_user_id"] == "owner-a"

    with pytest.raises(PermissionError):
        store.resolve_task_handoff(
            cid,
            request_id,
            "owner-a",
            "accept",
            tenant_id="tenant-a",
        )

    accepted = store.resolve_task_handoff(
        cid,
        request_id,
        "owner-b",
        "accept",
        tenant_id="tenant-a",
    )
    assert accepted["owner_user_id"] == "owner-b"
    assert accepted["pending_handoffs"] == []
    assert [event["event_type"] for event in accepted["events"]][-1] == "handoff_accepted"
    inbox_item = store.get_inbox_item(cid, tenant_id="tenant-a")
    assert inbox_item["owner_user_id"] == "owner-b"
    assert inbox_item["updated_at"] == business_updated_at

    with pytest.raises(CollaborationConflict):
        store.resolve_task_handoff(
            cid,
            request_id,
            "owner-b",
            "accept",
            tenant_id="tenant-a",
        )


def test_handoff_fails_closed_when_owner_changes_before_acceptance(tmp_path):
    store = make_store(tmp_path)
    row = store.create_conversation("Handoff race", tenant_id="tenant-a", created_by="creator")
    cid = row["id"]
    store.claim_conversation(cid, "owner-a", tenant_id="tenant-a")
    requested = store.request_task_handoff(
        cid,
        "owner-a",
        "owner-b",
        tenant_id="tenant-a",
    )
    request_id = requested["pending_handoffs"][0]["id"]

    store.release_claim(cid, "owner-a", tenant_id="tenant-a")
    after_release = store.list_collaboration(
        cid,
        tenant_id="tenant-a",
        current_user_id="owner-b",
    )
    assert after_release["pending_handoffs"] == []
    assert [event["event_type"] for event in after_release["events"]][-1] == "handoff_invalidated"
    with pytest.raises(CollaborationConflict):
        store.resolve_task_handoff(
            cid,
            request_id,
            "owner-b",
            "accept",
            tenant_id="tenant-a",
        )
    assert store.get_inbox_item(cid, tenant_id="tenant-a")["owner_user_id"] is None

    claimed = store.claim_conversation(cid, "owner-c", tenant_id="tenant-a")
    assert claimed is not None
    replacement = store.request_task_handoff(
        cid,
        "owner-c",
        "owner-d",
        tenant_id="tenant-a",
    )
    assert replacement["pending_handoffs"][0]["actor_user_id"] == "owner-c"


def test_only_current_owner_can_request_handoff(tmp_path):
    store = make_store(tmp_path)
    row = store.create_conversation("Owner gate", tenant_id="tenant-a", created_by="creator")
    cid = row["id"]
    store.claim_conversation(cid, "owner-a", tenant_id="tenant-a")
    with pytest.raises(PermissionError):
        store.request_task_handoff(
            cid,
            "operator-b",
            "owner-c",
            tenant_id="tenant-a",
        )


def test_collaboration_api_rbac_identity_and_handoff_handshake(monkeypatch):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", "tenant-collab-api")
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", "owner-api")
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "operator")

    with TestClient(app) as client:
        created = client.post(
            "/api/conversations",
            json={"title": "Collaboration API", "scene": "aftersales"},
        )
        assert created.status_code == 200
        cid = created.json()["id"]
        assert client.post(f"/api/inbox/{cid}/claim").status_code == 200

        watched = client.put(f"/api/inbox/{cid}/watch")
        assert watched.status_code == 200
        assert watched.json()["current_user_watching"] is True

        comment = client.post(
            f"/api/inbox/{cid}/comments",
            json={"body": "@reviewer-api 请核对本任务。"},
        )
        assert comment.status_code == 200
        assert [event for event in comment.json()["events"] if event["event_type"] == "comment"][-1]["mentions"] == ["reviewer-api"]

        review = client.post(
            f"/api/inbox/{cid}/review-requests",
            json={"target_user_id": "reviewer-api", "note": "只请求协作 review。"},
        )
        assert review.status_code == 200
        assert review.json()["authority"]["review_request_grants_approval"] is False

        handoff = client.post(
            f"/api/inbox/{cid}/handoffs",
            json={"target_user_id": "owner-api-b", "note": "请接手。"},
        )
        assert handoff.status_code == 200
        request_id = handoff.json()["pending_handoffs"][0]["id"]
        assert handoff.json()["owner_user_id"] == "owner-api"

        monkeypatch.setenv("ECOMEVO_LOCAL_USER", "intruder-api")
        wrong = client.post(f"/api/inbox/{cid}/handoffs/{request_id}/accept")
        assert wrong.status_code == 403

        monkeypatch.setenv("ECOMEVO_LOCAL_USER", "owner-api-b")
        accepted = client.post(f"/api/inbox/{cid}/handoffs/{request_id}/accept")
        assert accepted.status_code == 200
        assert accepted.json()["owner_user_id"] == "owner-api-b"
        assert accepted.json()["authority"]["handoff_grants_approval"] is False

        monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "viewer")
        assert client.get(f"/api/inbox/{cid}/collaboration").status_code == 200
        assert client.post(
            f"/api/inbox/{cid}/comments",
            json={"body": "viewer cannot write"},
        ).status_code == 403

        monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "admin")
        monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", "tenant-collab-other")
        assert client.get(f"/api/inbox/{cid}/collaboration").status_code == 404


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
        assert listing.json()["can_collaborate"] is False
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
