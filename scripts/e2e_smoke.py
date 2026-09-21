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
        from ecomevo.api import application as app_module

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

            runtime_skills_before_knowledge = app_module.engine.skills.snapshot(limit=200)
            knowledge_payload = {
                "name": "售后签收争议治理知识",
                "source_tier": "S2",
                "domain": "aftersales",
                "description": "受控售后证据要求，仅进入治理目录。",
                "owner": "E2E 治理",
                "jurisdiction": "CN",
                "tags": ["物流", "签收", "售后"],
                "version": {
                    "title": "签收争议证据要求 v1",
                    "content_text": "物流显示签收但用户否认收货时，应核对承运商原始轨迹、签收凭证和用户举证；关键事实不足时先补证据。",
                    "effective_from": None,
                    "effective_until": None,
                    "review_due_at": None,
                    "provenance": "E2E 内部治理 SOP",
                },
            }
            knowledge_created = client.post("/api/runtime/knowledge/sources", json=knowledge_payload)
            assert knowledge_created.status_code == 201
            knowledge_source = knowledge_created.json()
            knowledge_version = knowledge_source["versions"][0]
            assert knowledge_version["state"] == "draft"
            assert knowledge_version["authority"]["eligible_for_runtime_evidence"] is False
            assert client.post(
                f"/api/runtime/knowledge/versions/{knowledge_version['version_id']}/review",
                json={"note": "E2E governance review"},
            ).json()["state"] == "reviewed"
            published_knowledge = client.post(
                f"/api/runtime/knowledge/versions/{knowledge_version['version_id']}/publish",
                json={"note": "E2E catalog publish"},
            )
            assert published_knowledge.status_code == 200
            assert published_knowledge.json()["state"] == "published"
            knowledge_search = client.get("/api/runtime/knowledge/search", params={"q": "承运商"})
            assert knowledge_search.status_code == 200
            assert knowledge_search.json()["items"][0]["version_id"] == knowledge_version["version_id"]
            projection = client.get(
                f"/api/runtime/knowledge/versions/{knowledge_version['version_id']}/retrieval-projection"
            ).json()
            assert projection["runtime_projection_status"] == "blocked_pending_explicit_source_integration_gate"
            assert projection["authority"]["changes_runtime_evidence"] is False
            assert projection["authority"]["changes_production_authority"] is False
            assert projection["authority"]["s1_assignment_allowed"] is False
            assert projection["authority"]["open_web_unlocks_high_impact_actions"] is False
            assert app_module.engine.skills.snapshot(limit=200) == runtime_skills_before_knowledge
            assert client.get("/api/runtime/knowledge/ui").status_code == 200
            assert client.get("/assets/knowledge.js").status_code == 200
            assert client.get("/assets/knowledge.css").status_code == 200

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

            provider_usage = assistant["payload"]["provider_usage"]
            assert provider_usage["schema_version"] == 1
            assert provider_usage["external_calls"] == 0
            assert provider_usage["usage_reported_calls"] == 0
            assert provider_usage["events"] == []
            assert provider_usage["cost"]["available"] is False

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

            export_before = client.get(f"/api/conversations/{conv['id']}").json()
            export_action_snapshot = [
                (row["id"], row["status"], row["payload"])
                for row in export_before["actions"]
            ]
            exported = client.post(
                "/api/runtime/decision-exports",
                json={"conversation_id": conv["id"]},
            )
            assert exported.status_code == 200
            decision_export = exported.json()
            assert len(decision_export["content_hash"]) == 64
            assert decision_export["payload"]["manifest"]["asset_binary_included"] is False
            assert decision_export["payload"]["manifest"]["server_local_paths_included"] is False
            assert all("path" not in row for row in decision_export["payload"]["assets"])
            assert decision_export["payload"]["feedback"]["disputes"]
            assert decision_export["payload"]["manifest"]["counts"]["collaboration_events"] == collaboration_event_count
            export_collaboration_types = [
                row["event_type"]
                for row in decision_export["payload"]["collaboration"]["events"]
            ]
            assert "handoff_accepted" in export_collaboration_types
            assert decision_export["authority"] == {
                "export_changes_production_authority": False,
                "export_changes_action_state": False,
                "export_changes_policy": False,
                "export_changes_routing": False,
                "export_changes_runtime_skills": False,
                "export_executes_tools": False,
            }
            export_id = decision_export["id"]
            verified_export = client.get(
                f"/api/runtime/decision-exports/{export_id}/verify"
            )
            assert verified_export.status_code == 200
            assert verified_export.json()["valid"] is True
            downloaded_export = client.get(
                f"/api/runtime/decision-exports/{export_id}/download"
            )
            assert downloaded_export.status_code == 200
            assert downloaded_export.json()["content_hash"] == decision_export["content_hash"]
            assert client.get("/api/runtime/decision-exports/ui").status_code == 200
            assert client.get("/assets/decision-exports.js").status_code == 200
            assert client.get("/assets/decision-exports.css").status_code == 200
            export_after = client.get(f"/api/conversations/{conv['id']}").json()
            assert [
                (row["id"], row["status"], row["payload"])
                for row in export_after["actions"]
            ] == export_action_snapshot

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
            assert observability["model_telemetry"]["assistant_results"] >= 1
            assert observability["model_telemetry"]["instrumented_results"] >= 1
            assert observability["model_telemetry"]["result_coverage_rate"] == 1.0
            assert observability["model_telemetry"]["external_calls"] == 0
            assert observability["telemetry_availability"]["token_usage"]["available"] is False
            assert observability["telemetry_availability"]["token_usage"]["result_coverage_rate"] == 1.0
            assert observability["telemetry_availability"]["provider_cost"]["available"] is False
            assert client.get("/api/runtime/observability/ui").status_code == 200
            assert client.get("/assets/observability.js").status_code == 200
            assert client.get("/assets/observability.css").status_code == 200

            readiness_action_before = [
                (row["id"], row["status"])
                for row in client.get(f"/api/conversations/{conv['id']}").json()["actions"]
            ]
            readiness_response = client.get("/api/runtime/readiness/preview?window=24h")
            assert readiness_response.status_code == 200
            readiness = readiness_response.json()
            assert readiness["tenant_scope"] == "local"
            assert readiness["status"] == "blocked"
            readiness_checks = {row["id"]: row for row in readiness["checks"]}
            assert readiness_checks["gold_set_latest"]["status"] == "blocker"
            assert readiness["authority"] == {
                "approved_for_release": False,
                "changes_production_authority": False,
                "changes_policy": False,
                "changes_routing": False,
                "promotes_runtime_skills": False,
                "approves_business_actions": False,
                "executes_tools": False,
                "merges_or_deploys_code": False,
            }

            readiness_snapshot = client.post("/api/runtime/readiness/snapshots?window=24h")
            assert readiness_snapshot.status_code == 201
            readiness_snapshot_id = readiness_snapshot.json()["id"]
            assert client.get(f"/api/runtime/readiness/snapshots/{readiness_snapshot_id}").status_code == 200
            readiness_action_after = [
                (row["id"], row["status"])
                for row in client.get(f"/api/conversations/{conv['id']}").json()["actions"]
            ]
            assert readiness_action_after == readiness_action_before
            assert client.get("/api/runtime/readiness/ui").status_code == 200
            assert client.get("/assets/release-readiness.js").status_code == 200
            assert client.get("/assets/release-readiness.css").status_code == 200

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
                "decision_export_id": export_id,
                "decision_export_hash_valid": verified_export.json()["valid"],
                "decision_export_collaboration_events": decision_export["payload"]["manifest"]["counts"]["collaboration_events"],
                "connections_console": True,
                "connections_configuration_mutation": False,
                "observability_jobs": observability["reliability"]["jobs"],
                "observability_successful_runs": observability["throughput"]["successful_runs"],
                "knowledge_source_id": knowledge_source["source_id"],
                "knowledge_runtime_eligible": projection["authority"]["eligible_for_runtime_evidence"],
                "knowledge_runtime_skills_unchanged": True,
                "provider_usage_instrumented": provider_usage["schema_version"] == 1,
                "provider_external_calls": provider_usage["external_calls"],
                "observability_read_only": observability["methodology"]["read_only"],
                "release_readiness_status": readiness["status"],
                "release_readiness_snapshot": readiness_snapshot_id,
                "release_readiness_authority_changed": False,
                "event_chain_valid": True,
            })


if __name__ == "__main__":
    main()
