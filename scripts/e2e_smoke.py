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
            assert client.get("/api/inbox/ui").status_code == 200
            assert client.post(f"/api/inbox/{conv['id']}/claim").status_code == 200
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
                    "category": "missing_support",
                    "impact": "decision_relevant",
                    "target_type": "answer",
                    "explanation": "请把物流签收结论对应到更直接的承运商证据。",
                    "proposed_correction": "补充承运商原始轨迹后再确认。",
                },
            )
            assert feedback.status_code == 200
            feedback_id = feedback.json()["id"]
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
                "feedback_id": feedback_id,
                "feedback_status": reviewed.json()["status"],
                "connections_console": True,
                "knowledge_source_id": knowledge_source["source_id"],
                "knowledge_runtime_eligible": projection["authority"]["eligible_for_runtime_evidence"],
                "knowledge_runtime_skills_unchanged": True,
                "observability_jobs": observability["reliability"]["jobs"],
                "observability_successful_runs": observability["throughput"]["successful_runs"],
                "observability_read_only": observability["methodology"]["read_only"],
                "event_chain_valid": True,
            })


if __name__ == "__main__":
    main()
