from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ecomevo-smoke-") as tmp:
        os.environ["ECOMEVO_DATA"] = tmp
        os.environ.setdefault("ECOMEVO_AUTH_MODE", "local")
        os.environ.setdefault("ECOMEVO_LOCAL_ROLE", "admin")

        from fastapi.testclient import TestClient
        from ecomevo.api.app import app

        with TestClient(app) as client:
            connections = client.get("/api/runtime/connections")
            assert connections.status_code == 200
            assert connections.json()["scope"] == "deployment"
            assert connections.json()["safety"]["business_tool_execution"] is False
            assert connections.json()["safety"]["configuration_mutation"] is False
            assert connections.json()["safety"]["secrets_exposed"] is False
            assert client.get("/api/runtime/connections/ui").status_code == 200
            assert client.get("/assets/connections.js").status_code == 200
            assert client.get("/assets/connections.css").status_code == 200

            conv = client.post(
                "/api/conversations",
                json={"title": "售后 E2E", "scene": "aftersales"},
            ).json()

            inbox = client.get("/api/inbox?view=all")
            assert inbox.status_code == 200
            assert any(row["id"] == conv["id"] for row in inbox.json()["items"])
            assert inbox.json()["authority"] == {
                "assignment_grants_approval": False,
                "priority_changes_runtime_routing": False,
            }
            assert inbox.json()["can_collaborate"] is True
            original_user = inbox.json()["current_user"]
            assert client.get("/api/inbox/ui").status_code == 200
            claimed = client.post(f"/api/inbox/{conv['id']}/claim")
            assert claimed.status_code == 200
            assert claimed.json()["owner_user_id"] == original_user

            watched = client.put(f"/api/inbox/{conv['id']}/watch")
            assert watched.status_code == 200
            assert watched.json()["current_user_watching"] is True
            assert watched.json()["authority"] == {
                "collaboration_grants_approval": False,
                "review_request_grants_approval": False,
                "handoff_grants_approval": False,
                "comments_change_runtime": False,
                "watching_changes_runtime": False,
            }
            comment = client.post(
                f"/api/inbox/{conv['id']}/comments",
                json={"body": "@smoke-reviewer 请复核证据和交接上下文。"},
            )
            assert comment.status_code == 200
            comment_event = [row for row in comment.json()["events"] if row["event_type"] == "comment"][-1]
            assert comment_event["mentions"] == ["smoke-reviewer"]
            review_request = client.post(
                f"/api/inbox/{conv['id']}/review-requests",
                json={"target_user_id": "smoke-reviewer", "note": "仅协作 review，不授予审批权限。"},
            )
            assert review_request.status_code == 200
            handoff = client.post(
                f"/api/inbox/{conv['id']}/handoffs",
                json={"target_user_id": "smoke-reviewer", "note": "请接手后续处理。"},
            )
            assert handoff.status_code == 200
            assert handoff.json()["owner_user_id"] == original_user
            handoff_id = handoff.json()["pending_handoffs"][0]["id"]

            os.environ["ECOMEVO_LOCAL_USER"] = "smoke-reviewer"
            accepted_handoff = client.post(
                f"/api/inbox/{conv['id']}/handoffs/{handoff_id}/accept"
            )
            assert accepted_handoff.status_code == 200
            assert accepted_handoff.json()["owner_user_id"] == "smoke-reviewer"
            assert accepted_handoff.json()["pending_handoffs"] == []
            collaboration_event_count = len(accepted_handoff.json()["events"])

            os.environ["ECOMEVO_LOCAL_USER"] = original_user
            priority = client.patch(f"/api/inbox/{conv['id']}/priority", json={"priority": "urgent"})
            assert priority.status_code == 200 and priority.json()["queue_priority"] == "urgent"
            assert client.delete(f"/api/inbox/{conv['id']}/claim").json()["owner_user_id"] is None

            raw = (
                "订单 order-88421\n金额: 299\n物流显示签收，用户反馈未收到货\n"
                "客服记录：申请退款"
            ).encode("utf-8")
            asset = client.post(
                "/api/assets",
                files={"file": ("order.log", raw, "text/plain")},
                data={"conversation_id": conv["id"]},
            ).json()
            response = client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={
                    "content": "请结合订单和履约记录给出售后判责建议",
                    "asset_ids": [asset["id"]],
                    "provider": "demo",
                },
            )
            assert response.status_code == 200
            detail = client.get(f"/api/conversations/{conv['id']}").json()
            assistant = [row for row in detail["messages"] if row["role"] == "assistant"][-1]
            assert assistant["payload"]["domain"] == "aftersales"
            assert assistant["payload"]["runtime"]["event_chain_valid"] is True
            assert detail["actions"]

            action_snapshot = [(row["id"], row["status"]) for row in detail["actions"]]
            feedback = client.post(
                f"/api/conversations/{conv['id']}/feedback",
                json={
                    "assistant_message_id": assistant["id"],
                    "category": "inappropriate_action",
                    "impact": "action_blocking",
                    "target_type": "action",
                    "target_ref": detail["actions"][0]["id"],
                    "explanation": "当前证据不足以支持直接执行这项业务动作。",
                    "proposed_correction": "补充承运商原始轨迹后再重新判断动作。",
                },
            )
            assert feedback.status_code == 200
            feedback_id = feedback.json()["id"]
            assert feedback.json()["category"] == "inappropriate_action"
            assert feedback.json()["target_snapshot"]["target"]["type"] == "action"
            assert feedback.json()["target_snapshot"]["target"]["ref"] == detail["actions"][0]["id"]
            assert "payload" not in feedback.json()["target_snapshot"]["target"]
            unchanged = client.get(f"/api/conversations/{conv['id']}").json()
            assert [(row["id"], row["status"]) for row in unchanged["actions"]] == action_snapshot
            unchanged_assistant = [row for row in unchanged["messages"] if row["id"] == assistant["id"]][0]
            assert unchanged_assistant["content"] == assistant["content"]
            assert unchanged_assistant["payload"] == assistant["payload"]

            reviewed = client.post(
                f"/api/runtime/feedback/{feedback_id}/review",
                json={"decision": "accepted_for_eval", "note": "仅进入离线评估候选。"},
            )
            assert reviewed.status_code == 200
            sample = client.get(f"/api/runtime/feedback/{feedback_id}/evaluation-sample").json()
            assert sample["authority"] == {
                "changes_production_authority": False,
                "changes_policy": False,
                "changes_routing": False,
                "auto_promotes_to_gold_set": False,
            }
            assert client.get("/api/runtime/feedback/ui").status_code == 200
            for path in (
                "/assets/feedback-admin.js", "/assets/feedback-admin.css",
                "/assets/feedback-surface.js", "/assets/feedback-surface.css",
            ):
                assert client.get(path).status_code == 200

            action = detail["actions"][0]
            queue_item = client.get(f"/api/inbox/{conv['id']}").json()
            if action["requires_confirmation"]:
                assert queue_item["queue_state"] == "waiting_approval"
                completed = client.post(
                    f"/api/actions/{action['id']}/decision",
                    json={"decision": "approve", "note": "E2E"},
                ).json()
                assert completed["status"] == "simulated"
                assert completed["payload"]["execution_outcome"] == "simulated"

            observability_response = client.get("/api/runtime/observability?window=24h")
            assert observability_response.status_code == 200
            observability = observability_response.json()
            assert observability["tenant_scope"] == "local"
            assert observability["window"]["key"] == "24h"
            assert observability["reliability"]["jobs"] >= 1
            assert observability["reliability"]["succeeded"] >= 1
            assert observability["reliability"]["end_to_end_latency_seconds"]["samples"] >= 1
            assert observability["throughput"]["successful_runs"] >= 1
            assert observability["distribution"]["job_scenes"].get("aftersales", 0) >= 1
            assert observability["methodology"]["read_only"] is True
            assert observability["methodology"]["changes_authority"] is False
            assert observability["north_star"]["operator_hours"]["available"] is False
            assert observability["north_star"]["verified_decisions_per_operator_hour"]["available"] is False
            assert observability["telemetry_availability"]["token_usage"]["available"] is False
            assert observability["telemetry_availability"]["provider_cost"]["available"] is False
            assert client.get("/api/runtime/observability/ui").status_code == 200
            assert client.get("/assets/observability.js").status_code == 200
            assert client.get("/assets/observability.css").status_code == 200

            print({
                "conversation_id": conv["id"],
                "domain": assistant["payload"]["domain"],
                "actions": len(detail["actions"]),
                "queue_state": queue_item["queue_state"],
                "collaboration_events": collaboration_event_count,
                "handoff_accepted": True,
                "feedback_id": feedback_id,
                "feedback_status": reviewed.json()["status"],
                "feedback_target_type": feedback.json()["target_snapshot"]["target"]["type"],
                "connections_console": True,
                "connections_configuration_mutation": False,
                "observability_jobs": observability["reliability"]["jobs"],
                "observability_successful_runs": observability["throughput"]["successful_runs"],
                "observability_read_only": observability["methodology"]["read_only"],
                "event_chain_valid": True,
            })


if __name__ == "__main__":
    main()
