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
        os.environ.setdefault("ECOMEVO_DEPLOYMENT_NODES", "1")

        from fastapi.testclient import TestClient
        from ecomevo.api.app import app
        from ecomevo.api import application as app_module

        with TestClient(app) as client:
            runtime_response = client.get("/api/runtime")
            assert runtime_response.status_code == 200
            runtime_topology = runtime_response.json()["deployment_topology"]
            assert runtime_topology["declared_nodes"] == 1
            assert runtime_topology["runtime_start_allowed"] is True
            assert runtime_topology["release_supported"] is True
            assert runtime_topology["cross_node_supported"] is False
            assert runtime_topology["actual_replica_discovery"] is False

            connections = client.get("/api/runtime/connections")
            assert connections.status_code == 200
            assert connections.json()["scope"] == "deployment"
            assert connections.json()["safety"]["business_tool_execution"] is False
            assert connections.json()["safety"]["configuration_mutation"] is False
            assert connections.json()["safety"]["secrets_exposed"] is False
            assert client.get("/api/runtime/connections/ui").status_code == 200
            assert client.get("/assets/connections.js").status_code == 200
            assert client.get("/assets/connections.css").status_code == 200

            policy_scope = '{"channel":"smoke"}'
            policy_before = client.get(
                "/api/runtime/policies/resolve",
                params={"domain": "aftersales", "scope": policy_scope},
            )
            assert policy_before.status_code == 200
            assert "smoke.draft.marker" not in policy_before.json()["controls"]
            policy_draft = client.post(
                "/api/runtime/policies/drafts",
                json={
                    "policy_key": "smoke-refund-guard",
                    "domain": "aftersales",
                    "rules": ["Smoke draft must remain non-authoritative until checker approval and publish."],
                    "controls": {"smoke.draft.marker": True},
                    "scope": {"channel": "smoke"},
                    "authority": 60,
                    "priority": 0,
                    "source": "product-smoke:policy-workflow",
                },
            )
            assert policy_draft.status_code == 201
            policy_version = policy_draft.json()
            assert policy_version["status"] == "draft"
            assert policy_version["workflow_state"] == "draft"
            assert policy_version["scope"]["tenant"] == "local"
            assert policy_version["authority"]["creator_can_self_approve"] is False
            policy_after = client.get(
                "/api/runtime/policies/resolve",
                params={"domain": "aftersales", "scope": policy_scope},
            )
            assert policy_after.status_code == 200
            assert "smoke.draft.marker" not in policy_after.json()["controls"]
            assert client.get("/api/runtime/policies/ui").status_code == 200
            assert client.get("/assets/policy-center.js").status_code == 200
            assert client.get("/assets/policy-center.css").status_code == 200

            studio_before = client.get("/api/runtime/skills/catalog")
            assert studio_before.status_code == 200
            studio_catalog_before = studio_before.json()
            assert studio_catalog_before["scope"] == "mixed"
            assert studio_catalog_before["scopes"]["studio_families"] == "tenant"
            assert studio_catalog_before["scopes"]["runtime_skills"] == "deployment_read_only"
            runtime_skills_before = studio_catalog_before["runtime_skills"]
            studio_payload = {
                "domain": "aftersales",
                "name": "售后证据补全 E2E",
                "purpose": "判责前确认订单、履约和用户证据是否齐备",
                "guidance": "先核对订单与履约事实，再检查用户证据；存在关键缺口时停止并请求补证，不得直接执行退款。",
                "preferred_tools": ["order.inspect", "evidence.search"],
                "trigger_terms": ["退款", "未收到货"],
                "input_contract": {"requires": ["order_context"]},
                "output_contract": {"fields": ["evidence_gaps"]},
                "safety_notes": "不改变 Verifier、Approval 或 BusinessAction authority。",
            }
            studio_created = client.post("/api/runtime/skills/studio/families", json=studio_payload)
            assert studio_created.status_code == 201
            studio_v1 = studio_created.json()
            assert studio_v1["state"] == "draft"
            assert studio_v1["authority"]["can_promote_runtime"] is False
            studio_v2 = client.post(
                f"/api/runtime/skills/studio/families/{studio_v1['family_id']}/versions",
                json={**studio_payload, "guidance": "依次核对订单、履约、物流和用户举证；任一关键事实缺失时停止并补证，不得执行退款。"},
            )
            assert studio_v2.status_code == 201
            studio_v2_data = studio_v2.json()
            assert studio_v2_data["version"] == 2
            submitted = client.post(
                f"/api/runtime/skills/studio/{studio_v2_data['version_id']}/submit",
                json={"note": "进入离线候选评估流程"},
            )
            assert submitted.status_code == 200
            assert submitted.json()["state"] == "review"
            blocked_export = client.get(
                f"/api/runtime/skills/studio/{studio_v2_data['version_id']}/release-candidate"
            )
            assert blocked_export.status_code == 409
            studio_after = client.get("/api/runtime/skills/catalog").json()
            assert studio_after["runtime_skills"] == runtime_skills_before
            assert studio_after["authority"]["candidate_evaluation_mutates_production"] is False
            assert studio_after["authority"]["evaluation_pass_auto_promotes"] is False
            assert studio_after["authority"]["can_promote_runtime"] is False
            assert client.get("/api/runtime/skills/ui").status_code == 200
            assert client.get("/assets/skill-studio.js").status_code == 200
            assert client.get("/assets/skill-studio.css").status_code == 200

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

            activity_action_before = [
                (row["id"], row["status"], row["payload"])
                for row in client.get(f"/api/conversations/{conv['id']}").json()["actions"]
            ]
            heartbeat = client.post(
                "/api/operator-activity/heartbeat",
                json={"surface": "workbench"},
            )
            assert heartbeat.status_code == 200
            assert heartbeat.json() == {
                "recorded": True,
                "bucket_seconds": 15,
                "client_duration_accepted": False,
                "changes_authority": False,
            }
            activity_action_after = [
                (row["id"], row["status"], row["payload"])
                for row in client.get(f"/api/conversations/{conv['id']}").json()["actions"]
            ]
            assert activity_action_after == activity_action_before

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
            assert observability["north_star"]["operator_hours"]["available"] is True
            assert observability["north_star"]["operator_hours"]["active_seconds"] >= 15
            assert observability["north_star"]["operator_hours"]["bucket_seconds"] == 15
            assert observability["north_star"]["operator_hours"]["coverage_complete"] is False
            assert 0.0 < observability["north_star"]["operator_hours"]["coverage_rate"] < 1.0
            assert observability["north_star"]["verified_decisions_per_operator_hour"]["available"] is False
            assert "partial denominator" in observability["north_star"]["verified_decisions_per_operator_hour"]["reason"]
            assert observability["telemetry_availability"]["operator_active_hours"]["available"] is True
            assert observability["telemetry_availability"]["operator_active_hours"]["complete"] is False
            assert observability["telemetry_availability"]["operator_active_hours"]["retention"]["days"] == 90
            operator_retention = observability["telemetry_availability"]["operator_active_hours"]["retention"]
            assert operator_retention["client_configurable"] is False
            assert operator_retention["prune_interval_seconds"] == 24 * 60 * 60
            assert operator_retention["prune_coordination"] == "durable_database_global"
            assert operator_retention["prune_scope"] == "shared_operator_activity_database"
            assert observability["telemetry_availability"]["operator_active_hours"]["duplicate_bucket_write_suppressed"] is True
            assert observability["methodology"]["operator_active_hours_client_duration_accepted"] is False
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

            routing_quality_response = client.get("/api/runtime/routing-quality?window=24h")
            assert routing_quality_response.status_code == 200
            routing_quality = routing_quality_response.json()
            assert routing_quality["tenant_scope"] == "local"
            assert routing_quality["coverage"]["assistant_results"] >= 1
            assert routing_quality["routing_policy"]["domains"]
            assert routing_quality["authority"] == {
                "read_only": True,
                "changes_routing": False,
                "changes_policy": False,
                "changes_runtime_skills": False,
                "approves_business_actions": False,
                "executes_tools": False,
            }
            assert client.get("/api/runtime/routing-quality/ui").status_code == 200
            assert client.get("/assets/routing-quality.js").status_code == 200
            assert client.get("/assets/routing-quality.css").status_code == 200

            off_policy_response = client.get(
                "/api/runtime/routing-quality/off-policy?window=24h"
            )
            assert off_policy_response.status_code == 200
            off_policy = off_policy_response.json()
            assert off_policy["tenant_scope"] == "local"
            assert off_policy["behavior_policy"]["family"] == "deterministic_ucb"
            assert off_policy["behavior_policy"]["randomized_action_assignment"] is False
            assert off_policy["behavior_policy"]["positivity_for_alternative_actions"] is False
            assert isinstance(off_policy["coverage"]["unmatched_update_events"], int)
            assert isinstance(off_policy["coverage"]["unpairable_update_events"], int)
            assert off_policy["candidate_counterfactual"]["status"] == "unavailable"
            assert off_policy["direct_method"]["status"] == "unavailable"
            assert off_policy["doubly_robust"]["status"] == "unavailable"
            assert off_policy["authority"] == {
                "read_only": True,
                "changes_routing": False,
                "changes_policy": False,
                "changes_runtime_skills": False,
                "approves_business_actions": False,
                "executes_tools": False,
            }

            shadow_catalog = client.get("/api/runtime/shadow/catalog")
            assert shadow_catalog.status_code == 200
            assert shadow_catalog.json()["tenant_scope"] == "local"
            assert shadow_catalog.json()["authority"]["executes_real_tools"] is False
            assert shadow_catalog.json()["authority"]["changes_business_action_state"] is False
            shadow_action_before = [
                (row["id"], row["status"], row["payload"])
                for row in client.get(f"/api/conversations/{conv['id']}").json()["actions"]
            ]
            shadow_response = client.post(
                "/api/runtime/shadow/simulate",
                json={
                    "surface": "mcp",
                    "operation": "governed_action",
                    "mutation": "timeout_after_dispatch",
                    "target": "refund.execute",
                    "context_labels": ["aftersales", "refund"],
                },
            )
            assert shadow_response.status_code == 200
            shadow = shadow_response.json()
            assert shadow["expected_control"]["runtime_outcome"] == "uncertain"
            assert shadow["expected_control"]["automatic_retry_allowed"] is False
            assert shadow["expected_control"]["requires_business_state_check"] is True
            assert shadow["replay_candidate"]["executable"] is False
            assert shadow["replay_candidate"]["persisted_by_simulator"] is False
            assert shadow["replay_candidate"]["invokes_real_system"] is False
            assert shadow["replay_candidate"]["production_evidence"] is False
            shadow_action_after = [
                (row["id"], row["status"], row["payload"])
                for row in client.get(f"/api/conversations/{conv['id']}").json()["actions"]
            ]
            assert shadow_action_after == shadow_action_before
            assert client.get("/api/runtime/shadow/ui").status_code == 200
            assert client.get("/assets/shadow-environment.js").status_code == 200
            assert client.get("/assets/shadow-environment.css").status_code == 200

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
            assert readiness_checks["deployment_topology"]["status"] == "pass"
            assert readiness["sources"]["deployment_topology"]["declared_nodes"] == 1
            assert readiness["sources"]["deployment_topology"]["certified_max_nodes"] == 1
            assert readiness["sources"]["deployment_topology"]["cross_node_supported"] is False
            assert readiness["sources"]["deployment_topology"]["actual_replica_discovery"] is False
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
                "policy_draft_version": policy_version["version_id"],
                "policy_draft_non_authoritative": True,
                "skill_studio_version": studio_v2_data["version_id"],
                "skill_studio_runtime_unchanged": True,
                "observability_jobs": observability["reliability"]["jobs"],
                "observability_successful_runs": observability["throughput"]["successful_runs"],
                "knowledge_source_id": knowledge_source["source_id"],
                "knowledge_runtime_eligible": projection["authority"]["eligible_for_runtime_evidence"],
                "knowledge_runtime_skills_unchanged": True,
                "provider_usage_instrumented": provider_usage["schema_version"] == 1,
                "provider_external_calls": provider_usage["external_calls"],
                "observability_read_only": observability["methodology"]["read_only"],
                "operator_active_seconds": observability["north_star"]["operator_hours"]["active_seconds"],
                "operator_hours_coverage_rate": observability["north_star"]["operator_hours"]["coverage_rate"],
                "operator_hours_coverage_complete": observability["north_star"]["operator_hours"]["coverage_complete"],
                "verified_decisions_per_operator_hour_available": observability["north_star"]["verified_decisions_per_operator_hour"]["available"],
                "operator_activity_changes_authority": heartbeat.json()["changes_authority"],
                "operator_activity_retention_days": observability["operator_activity"]["retention"]["days"],
                "operator_activity_prune_coordination": observability["operator_activity"]["retention"]["prune_coordination"],
                "operator_activity_duplicate_write_suppression": observability["operator_activity"]["duplicate_bucket_write_suppressed"],
                "routing_quality_read_only": routing_quality["authority"]["read_only"],
                "routing_quality_domains": len(routing_quality["routing_policy"]["domains"]),
                "routing_off_policy_behavior": off_policy["behavior_policy"]["family"],
                "routing_off_policy_candidate_counterfactual": off_policy["candidate_counterfactual"]["status"],
                "routing_off_policy_doubly_robust": off_policy["doubly_robust"]["status"],
                "shadow_candidate": shadow["candidate_id"],
                "shadow_runtime_outcome": shadow["expected_control"]["runtime_outcome"],
                "shadow_invokes_real_system": shadow["replay_candidate"]["invokes_real_system"],
                "shadow_changes_action_state": shadow["authority"]["changes_business_action_state"],
                "release_readiness_status": readiness["status"],
                "deployment_topology_status": readiness_checks["deployment_topology"]["status"],
                "deployment_topology_nodes": readiness["sources"]["deployment_topology"]["declared_nodes"],
                "runtime_topology_start_allowed": runtime_topology["runtime_start_allowed"],
                "runtime_topology_actual_replica_discovery": runtime_topology["actual_replica_discovery"],
                "release_readiness_snapshot": readiness_snapshot_id,
                "release_readiness_authority_changed": False,
                "event_chain_valid": True,
            })


if __name__ == "__main__":
    main()
