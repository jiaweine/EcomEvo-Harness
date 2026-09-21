from __future__ import annotations

import json
import time

import pytest

from ecomevo.product import ConversationStore
from ecomevo.product.decision_exports import DecisionExportCenter


def _fixture(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    center = DecisionExportCenter(tmp_path / "decision_exports.db")
    conv = store.create_conversation(
        "导出完整性测试",
        "aftersales",
        tenant_id="tenant-export-a",
        created_by="admin-a",
    )
    store.add_message(conv["id"], "user", "请核对订单并给出建议。", {"request_id": "req-1"})
    assistant = store.add_message(
        conv["id"],
        "assistant",
        "当前证据支持先核对承运商轨迹。",
        {
            "domain": "aftersales",
            "access_token": "must-not-export",
            "nested": {"client_secret": "also-secret", "safe": "kept"},
            "evidence": [{"evidence_id": "ev-1", "source": "order.inspect"}],
        },
    )
    store.add_asset(
        conv["id"],
        name="order.log",
        mime="text/plain",
        path=str(tmp_path / "private-server-path" / "order.log"),
        size=42,
        meta={
            "sha256": "abc123",
            "api_key": "hidden-key",
            "keyframes": [str(tmp_path / "frame-1.jpg")],
            "public_note": "retained",
        },
    )
    store.add_event(
        conv["id"],
        "answer.ready",
        {"status": "completed", "authorization": "Bearer hidden", "safe": "event-kept"},
    )
    now = time.time()
    with store._conn() as db:
        db.execute(
            "INSERT INTO actions("
            "id,conversation_id,session_id,kind,title,description,risk_level,"
            "side_effect,requires_confirmation,status,payload,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "action-export-1",
                conv["id"],
                "session-export-1",
                "refund",
                "退款建议",
                "需要人工确认",
                "high",
                1,
                1,
                "proposed",
                json.dumps({"client_secret": "hidden-action", "amount": 299}),
                now,
                now,
            ),
        )
    store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-a",
        submitted_role="operator",
        category="missing_support",
        impact="decision_relevant",
        target_type="answer",
        explanation="需要更直接的承运商证据。",
        proposed_correction="补轨迹后再确认。",
        tenant_id="tenant-export-a",
    )
    return store, center, conv


def test_decision_export_is_tenant_scoped_deterministic_and_redacted(tmp_path):
    store, center, conv = _fixture(tmp_path)

    first = center.create_snapshot(
        store,
        tenant_id="tenant-export-a",
        created_by="admin-a",
        conversation_id=conv["id"],
    )
    second = center.create_snapshot(
        store,
        tenant_id=" tenant-export-a ",
        created_by=" admin-a ",
        conversation_id=f" {conv['id']} ",
    )

    assert first["id"] != second["id"]
    assert second["tenant_id"] == "tenant-export-a"
    assert second["conversation_id"] == conv["id"]
    assert second["created_by"] == "admin-a"
    assert first["content_hash"] == second["content_hash"]
    assert len(first["content_hash"]) == 64

    payload = first["payload"]
    assert payload["schema_version"] == 1
    assert payload["export_kind"] == "decision_audit_snapshot"
    assert payload["tenant_id"] == "tenant-export-a"
    assert payload["manifest"]["asset_binary_included"] is False
    assert payload["manifest"]["server_local_paths_included"] is False
    assert payload["manifest"]["redacted_field_count"] >= 5

    assert "path" not in payload["assets"][0]
    assert payload["assets"][0]["meta"]["api_key"] == "[redacted]"
    assert payload["assets"][0]["meta"]["keyframes"] == "[redacted]"
    assert payload["assets"][0]["meta"]["public_note"] == "retained"
    assistant_payload = payload["messages"][1]["payload"]
    assert assistant_payload["access_token"] == "[redacted]"
    assert assistant_payload["nested"]["client_secret"] == "[redacted]"
    assert assistant_payload["nested"]["safe"] == "kept"
    assert payload["actions"][0]["payload"]["client_secret"] == "[redacted]"
    assert payload["task_events"][0]["payload"]["authorization"] == "[redacted]"
    assert payload["feedback"]["disputes"]

    assert all(value is False for value in payload["authority"].values())
    assert center.verify_snapshot(first["id"], tenant_id="tenant-export-a")["valid"] is True

    with pytest.raises(KeyError):
        center.get_snapshot(first["id"], tenant_id="tenant-export-b")
    with pytest.raises(KeyError):
        center.create_snapshot(
            store,
            tenant_id="tenant-export-b",
            created_by="admin-b",
            conversation_id=conv["id"],
        )

    assert {row["id"] for row in center.list_snapshots(tenant_id="tenant-export-a")} == {
        first["id"],
        second["id"],
    }
    assert center.list_snapshots(tenant_id="tenant-export-b") == []


def test_decision_export_verify_detects_stored_payload_tampering(tmp_path):
    store, center, conv = _fixture(tmp_path)
    snapshot = center.create_snapshot(
        store,
        tenant_id="tenant-export-a",
        created_by="admin-a",
        conversation_id=conv["id"],
    )

    with center._conn() as db:
        row = db.execute(
            "SELECT payload FROM decision_exports WHERE id=?",
            (snapshot["id"],),
        ).fetchone()
        payload = json.loads(row["payload"])
        payload["conversation"]["title"] = "被篡改"
        db.execute(
            "UPDATE decision_exports SET payload=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), snapshot["id"]),
        )

    result = center.verify_snapshot(snapshot["id"], tenant_id="tenant-export-a")
    assert result["valid"] is False
    assert result["expected_hash"] != result["observed_hash"]
    assert all(value is False for value in result["authority"].values())
