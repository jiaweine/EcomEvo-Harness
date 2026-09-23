from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ecomevo-authority-smoke-") as tmp:
        os.environ["ECOMEVO_DATA"] = tmp
        os.environ["ECOMEVO_AUTH_MODE"] = "local"
        os.environ["ECOMEVO_LOCAL_TENANT"] = "local"
        os.environ["ECOMEVO_LOCAL_USER"] = "authority-smoke-admin"
        os.environ["ECOMEVO_LOCAL_ROLE"] = "admin"
        os.environ["ECOMEVO_DEPLOYMENT_NODES"] = "1"

        from fastapi.testclient import TestClient
        from ecomevo.api.app import app

        with TestClient(app) as client:
            response = client.get("/api/runtime/readiness/runtime-authority")
            assert response.status_code == 200
            snapshot = response.json()
            assert snapshot["snapshot_status"] == "available"
            assert len(snapshot["snapshot_sha256"]) == 64
            assert set(snapshot["surfaces"]) == {
                "policy_versions",
                "runtime_skills",
                "evolution_policy",
                "routing_policy",
                "routing_tool_stats",
                "harness_components",
            }
            assert snapshot["authority"]["read_only"] is True
            assert snapshot["authority"]["changes_policy"] is False
            assert snapshot["authority"]["changes_routing"] is False
            assert snapshot["authority"]["promotes_runtime_skills"] is False
            assert snapshot["authority"]["changes_harness"] is False
            assert snapshot["methodology"]["history_tables_included"] is False
            assert snapshot["methodology"]["process_plugin_lifecycle_covered"] is False
            assert snapshot["methodology"]["cross_node_shared"] is False
            assert snapshot["methodology"]["cross_node_supported"] is False
            assert snapshot["methodology"]["multi_node_certification"] is False
            assert snapshot["methodology"]["fingerprint_equality_proves_shared_transaction_domain"] is False

            print({
                "runtime_authority_snapshot": snapshot["snapshot_sha256"],
                "runtime_authority_surfaces": len(snapshot["surfaces"]),
                "runtime_authority_cross_node_shared": snapshot["methodology"]["cross_node_shared"],
                "runtime_authority_cross_node_supported": snapshot["methodology"]["cross_node_supported"],
                "runtime_authority_read_only": snapshot["authority"]["read_only"],
            })


if __name__ == "__main__":
    main()
