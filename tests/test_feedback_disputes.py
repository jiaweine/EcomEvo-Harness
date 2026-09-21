from __future__ import annotations

import json

import pytest

from ecomevo.models import BusinessAction
from ecomevo.product.feedback_store import FeedbackConversationStore


def _store(tmp_path):
    return FeedbackConversationStore(tmp_path / "feedback.db", tmp_path / "assets")


def _payload():
    return {
        "domain": "aftersales",
        "session_id": "session-feedback-1",
        "evidence": [
            {
                "evidence_id": "ev-order-1",
                "source": "order.inspect",
                "title": "订单记录",
                "detail": "订单 ORDER-88421，金额：299.0，状态：已签收",
            },
            {
                "evidence_id": "ev-policy-1",
                "source": "policy.lookup",
                "title": "售后规则",
                "detail": "签收争议需要结合物流和用户举证复核",
            },
        ],
        "grounding": {
            "schema_version": 3,
            "evidence_sufficiency": "sufficient",
            "claims": [
                {
                    "text": "订单 ORDER-88421 的金额为 299 元。",
                    "kind": "fact",
                    "verdict": "supported",
                    "evidence_ids": ["ev-order-1"],
                },
                {
                    "text": "签收争议需要结合物流和用户举证复核。",
                    "kind": "rule",
                    "verdict": "supported",
                    "evidence_ids": ["ev-policy-1"],
                },
            ],
        },
    }


def _conversation(store, tenant: str, user: str = "operator-1"):
    conv = store.create_conversation("售后争议", "aftersales", tenant_id=tenant, created_by=user)
    store.add_message(conv["id"], "user", "这笔订单应该怎么处理？", {})
    assistant = store.add_message(
        conv["id"],
        "assistant",
        "订单金额为 299 元，签收争议需要结合物流和用户举证复核。",
        _payload(),
    )
    return conv, assistant


def test_claim_fingerprint_is_stable_for_equivalent_whitespace(tmp_path):
    store = _store(tmp_path)
    left = store.claim_ref({"kind": "fact", "text": "订单金额为 299 元"})
    right = store.claim_ref({"kind": "fact", "text": "  订单金额为   299 元  "})
    assert left == right
    assert left.startswith("claim-")


def test_feedback_target_must_exist_in_original_assistant_message(tmp_path):
    store = _store(tmp_path)
    conv, assistant = _conversation(store, "tenant-a")
    targets = store.feedback_targets(conv["id"], assistant["id"], tenant_id="tenant-a")
    assert len(targets["claims"]) == 2
    assert {row["ref"] for row in targets["evidence"]} == {"ev-order-1", "ev-policy-1"}

    feedback = store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-1",
        category="factual_error",
        impact="decision_relevant",
        target_type="claim",
        target_ref=targets["claims"][0]["ref"],
        explanation="订单金额需要重新核对原始支付记录。",
        proposed_correction="请以实际支付流水为准。",
        tenant_id="tenant-a",
    )
    assert feedback["target_snapshot"]["target"]["type"] == "claim"
    assert feedback["target_snapshot"]["message_hash"] == targets["message_hash"]
    assert feedback["status"] == "open"

    with pytest.raises(ValueError):
        store.submit_feedback(
            conv["id"],
            assistant["id"],
            submitted_by="operator-1",
            category="missing_support",
            impact="decision_relevant",
            target_type="evidence",
            target_ref="ev-invented",
            explanation="不能引用不存在的证据。",
            tenant_id="tenant-a",
        )




def test_precise_correction_categories_bind_to_expected_grounding_targets(tmp_path):
    store = _store(tmp_path)
    conv, assistant = _conversation(store, "tenant-a")
    targets = store.feedback_targets(conv["id"], assistant["id"], tenant_id="tenant-a")
    claim_ref = targets["claims"][0]["ref"]

    evidence_feedback = store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-1",
        category="incorrect_evidence",
        impact="decision_relevant",
        target_type="evidence",
        target_ref="ev-order-1",
        explanation="订单记录里的状态字段本身可能错误。",
        tenant_id="tenant-a",
    )
    assert evidence_feedback["target_snapshot"]["target"]["type"] == "evidence"

    rule_feedback = store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-1",
        category="rule_not_applicable",
        impact="decision_relevant",
        target_type="evidence",
        target_ref="ev-policy-1",
        explanation="该售后规则不适用于当前订单类型。",
        tenant_id="tenant-a",
    )
    assert rule_feedback["target_snapshot"]["target"]["ref"] == "ev-policy-1"

    inference_feedback = store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-1",
        category="over_inference",
        impact="decision_relevant",
        target_type="claim",
        target_ref=claim_ref,
        explanation="该结论超出了当前证据能支持的范围。",
        tenant_id="tenant-a",
    )
    assert inference_feedback["target_snapshot"]["target"]["type"] == "claim"

    with pytest.raises(ValueError, match="does not match target type"):
        store.submit_feedback(
            conv["id"],
            assistant["id"],
            submitted_by="operator-1",
            category="incorrect_evidence",
            impact="answer_only",
            target_type="answer",
            explanation="证据错误必须指向具体证据。",
            tenant_id="tenant-a",
        )


def test_action_and_asset_corrections_capture_safe_immutable_targets(tmp_path):
    store = _store(tmp_path)
    conv, assistant = _conversation(store, "tenant-a")
    action = BusinessAction(
        action_id="action-feedback-1",
        kind="refund",
        title="发起退款",
        description="建议退款 299 元",
        risk_level="high",
        side_effect=True,
        requires_confirmation=True,
        payload={"amount": 299, "internal_token": "must-not-surface"},
        status="proposed",
    )
    store.save_actions(conv["id"], "session-feedback-1", [action])
    asset_path = tmp_path / "private-order.txt"
    asset_path.write_text("private fixture", encoding="utf-8")
    asset = store.add_asset(
        conv["id"],
        name="order.txt",
        mime="text/plain",
        path=str(asset_path),
        size=15,
        meta={
            "sha256": "sha-feedback-1",
            "server_path": "/private/server/path",
            "credential": "must-not-surface",
        },
    )

    targets = store.feedback_targets(conv["id"], assistant["id"], tenant_id="tenant-a")
    action_target = next(row for row in targets["actions"] if row["ref"] == action.action_id)
    asset_target = next(row for row in targets["assets"] if row["ref"] == asset["id"])

    assert targets["action_session_id"] == "session-feedback-1"
    assert action_target["session_id"] == "session-feedback-1"
    assert action_target["status"] == "proposed"
    assert action_target["requires_confirmation"] is True
    assert "payload" not in action_target
    assert "internal_token" not in json.dumps(action_target, ensure_ascii=False)
    assert asset_target == {
        "ref": asset["id"],
        "name": "order.txt",
        "mime": "text/plain",
        "size": 15,
        "sha256": "sha-feedback-1",
        "active": True,
        "excluded_at": None,
        "excluded_reason": "",
        "created_at": asset_target["created_at"],
    }
    assert "path" not in asset_target
    assert "credential" not in json.dumps(asset_target, ensure_ascii=False)

    action_before = store.get_action(action.action_id)
    action_feedback = store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-1",
        category="inappropriate_action",
        impact="action_blocking",
        target_type="action",
        target_ref=action.action_id,
        explanation="现有证据不足以支持直接退款。",
        proposed_correction="先补充承运商轨迹再决定动作。",
        tenant_id="tenant-a",
    )
    assert action_feedback["target_snapshot"]["schema_version"] == 2
    assert action_feedback["target_snapshot"]["target"]["type"] == "action"
    assert action_feedback["target_snapshot"]["target"]["ref"] == action.action_id
    assert store.get_action(action.action_id) == action_before

    asset_before = store.get_asset(asset["id"])
    asset_feedback = store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-1",
        category="stale_attachment",
        impact="decision_relevant",
        target_type="asset",
        target_ref=asset["id"],
        explanation="该附件可能不是本次争议的最新版本。",
        tenant_id="tenant-a",
    )
    assert asset_feedback["target_snapshot"]["target"]["type"] == "asset"
    assert asset_feedback["target_snapshot"]["target"]["sha256"] == "sha-feedback-1"
    assert store.get_asset(asset["id"]) == asset_before

    with pytest.raises(ValueError, match="does not match target type"):
        store.submit_feedback(
            conv["id"],
            assistant["id"],
            submitted_by="operator-1",
            category="inappropriate_action",
            impact="decision_relevant",
            target_type="answer",
            explanation="动作类反馈不能挂在整个回答上。",
            tenant_id="tenant-a",
        )

    with pytest.raises(ValueError, match="does not exist"):
        store.submit_feedback(
            conv["id"],
            assistant["id"],
            submitted_by="operator-1",
            category="unreliable_attachment",
            impact="decision_relevant",
            target_type="asset",
            target_ref="asset-not-present",
            explanation="不能对不存在的附件提交结构化异议。",
            tenant_id="tenant-a",
        )


def test_legacy_assistant_without_session_id_does_not_guess_action_targets(tmp_path):
    store = _store(tmp_path)
    conv = store.create_conversation("legacy correction", "aftersales", tenant_id="tenant-a")
    store.add_message(conv["id"], "user", "legacy?", {})
    assistant = store.add_message(
        conv["id"],
        "assistant",
        "legacy answer",
        {"domain": "aftersales", "grounding": {"schema_version": 3, "claims": []}},
    )
    store.save_actions(
        conv["id"],
        "unrelated-session",
        [
            BusinessAction(
                action_id="action-unrelated",
                kind="refund",
                title="旧动作",
                description="不应被猜测关联到 legacy assistant",
            )
        ],
    )

    targets = store.feedback_targets(conv["id"], assistant["id"], tenant_id="tenant-a")
    assert targets["action_session_id"] is None
    assert targets["actions"] == []


def test_review_is_append_only_and_does_not_mutate_message_or_dispute(tmp_path):
    store = _store(tmp_path)
    conv, assistant = _conversation(store, "tenant-a")
    original_messages = store.list_messages(conv["id"])
    feedback = store.submit_feedback(
        conv["id"],
        assistant["id"],
        submitted_by="operator-1",
        category="wrong_rule",
        impact="action_blocking",
        target_type="evidence",
        target_ref="ev-policy-1",
        explanation="当前规则版本可能不是最新生效版本。",
        proposed_correction="复核规则生效时间。",
        tenant_id="tenant-a",
    )
    immutable = {
        key: feedback[key]
        for key in (
            "id",
            "conversation_id",
            "assistant_message_id",
            "submitted_by",
            "category",
            "impact",
            "target_type",
            "target_ref",
            "target_snapshot",
            "explanation",
            "proposed_correction",
            "created_at",
        )
    }

    reviewed = store.review_feedback(
        feedback["id"],
        "accepted_for_eval",
        actor_user_id="admin-1",
        actor_role="admin",
        note="进入回归样本候选，但不自动修改生产规则。",
        tenant_id="tenant-a",
    )
    assert reviewed["status"] == "accepted_for_eval"
    reviewed = store.review_feedback(
        feedback["id"],
        "needs_followup",
        actor_user_id="admin-2",
        actor_role="admin",
        note="还需要规则版本证据。",
        tenant_id="tenant-a",
    )
    assert reviewed["status"] == "needs_followup"
    assert {key: reviewed[key] for key in immutable} == immutable
    assert store.list_messages(conv["id"]) == original_messages

    events = store.feedback_events(feedback["id"], tenant_id="tenant-a")
    assert [row["event_type"] for row in events] == ["submitted", "accepted_for_eval", "needs_followup"]
    assert events[-1]["payload"]["authority_changed"] is False


def test_feedback_is_tenant_isolated(tmp_path):
    store = _store(tmp_path)
    conv_a, assistant_a = _conversation(store, "tenant-a", "a-user")
    conv_b, assistant_b = _conversation(store, "tenant-b", "b-user")
    fb_a = store.submit_feedback(
        conv_a["id"], assistant_a["id"],
        submitted_by="a-user", category="missing_support", impact="decision_relevant",
        target_type="answer", explanation="A workspace feedback", tenant_id="tenant-a",
    )
    fb_b = store.submit_feedback(
        conv_b["id"], assistant_b["id"],
        submitted_by="b-user", category="evidence_conflict", impact="decision_relevant",
        target_type="answer", explanation="B workspace feedback", tenant_id="tenant-b",
    )

    assert [row["id"] for row in store.list_feedback_admin(tenant_id="tenant-a")] == [fb_a["id"]]
    assert [row["id"] for row in store.list_feedback_admin(tenant_id="tenant-b")] == [fb_b["id"]]
    with pytest.raises(KeyError):
        store.get_feedback(fb_b["id"], tenant_id="tenant-a")
    with pytest.raises(KeyError):
        store.feedback_targets(conv_b["id"], assistant_b["id"], tenant_id="tenant-a")


def test_evaluation_sample_is_read_only_quality_signal(tmp_path):
    store = _store(tmp_path)
    conv, assistant = _conversation(store, "tenant-a")
    feedback = store.submit_feedback(
        conv["id"], assistant["id"],
        submitted_by="operator-1", category="evidence_conflict", impact="decision_relevant",
        target_type="answer", explanation="物流记录与用户举证存在冲突。",
        tenant_id="tenant-a",
    )
    store.review_feedback(
        feedback["id"], "accepted_for_eval",
        actor_user_id="admin-1", actor_role="admin", tenant_id="tenant-a",
    )
    sample = store.evaluation_sample(feedback["id"], tenant_id="tenant-a")
    assert sample["question"] == "这笔订单应该怎么处理？"
    assert sample["candidate_answer"].startswith("订单金额为 299 元")
    assert sample["status"] == "accepted_for_eval"
    assert sample["grounding"]["schema_version"] == 3
    assert sample["authority"] == {
        "changes_production_authority": False,
        "changes_policy": False,
        "changes_routing": False,
        "auto_promotes_to_gold_set": False,
    }
    # Export remains ordinary JSON data, not an executable promotion artifact.
    json.dumps(sample, ensure_ascii=False)
