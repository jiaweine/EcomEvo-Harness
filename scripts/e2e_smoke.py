from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    # A smoke test must never consume or mutate the operator's normal runtime data.
    # Import the application only after binding a fresh durable root.
    with tempfile.TemporaryDirectory(prefix="ecomevo-smoke-") as tmp:
        os.environ["ECOMEVO_DATA"] = tmp
        os.environ.setdefault("ECOMEVO_AUTH_MODE", "local")
        os.environ.setdefault("ECOMEVO_LOCAL_ROLE", "admin")

        from fastapi.testclient import TestClient
        from ecomevo.api.app import app

        with TestClient(app) as client:
            conv = client.post(
                "/api/conversations",
                json={"title": "售后 E2E", "scene": "aftersales"},
            ).json()
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
            action = detail["actions"][0]
            if action["requires_confirmation"]:
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

            print({
                "conversation_id": conv["id"],
                "domain": assistant["payload"]["domain"],
                "session_id": assistant["payload"]["session_id"],
                "actions": len(detail["actions"]),
                "event_chain_valid": True,
                "observability_jobs": observability["reliability"]["jobs"],
                "observability_successful_runs": observability["throughput"]["successful_runs"],
                "observability_read_only": observability["methodology"]["read_only"],
            })


if __name__ == "__main__":
    main()
