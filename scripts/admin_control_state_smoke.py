from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ecomevo-admin-state-smoke-") as tmp:
        os.environ["ECOMEVO_DATA"] = tmp
        os.environ["ECOMEVO_AUTH_MODE"] = "local"
        os.environ["ECOMEVO_LOCAL_TENANT"] = "local"
        os.environ["ECOMEVO_LOCAL_USER"] = "admin-state-smoke"
        os.environ["ECOMEVO_LOCAL_ROLE"] = "admin"
        os.environ["ECOMEVO_DEPLOYMENT_NODES"] = "1"

        from fastapi.testclient import TestClient
        from ecomevo.api.app import app

        with TestClient(app) as client:
            response = client.get("/api/runtime/readiness/admin-control-state")
            assert response.status_code == 200
            manifest = response.json()
            assert manifest["manifest_status"] == "available_local_only"
            assert len(manifest["manifest_sha256"]) == 64
            assert manifest["database_count"] == 6
            assert manifest["unavailable_database_ids"] == []
            assert {row["id"] for row in manifest["databases"]} == {
                "evaluation",
                "knowledge",
                "decision_exports",
                "release_readiness",
                "skill_studio",
                "connection_governance",
            }
            assert all(row["status"] == "available" for row in manifest["databases"])
            assert all(len(row["sha256"]) == 64 for row in manifest["databases"])
            assert manifest["multi_node_ready"] is False
            assert manifest["methodology"]["cross_database_atomic_snapshot"] is False
            assert manifest["methodology"]["shared_across_application_nodes"] is False
            assert manifest["methodology"]["cross_node_supported"] is False
            assert manifest["methodology"]["requirement_current_satisfied"] is False
            assert manifest["methodology"]["release_authority_granted"] is False
            assert manifest["authority"]["read_only"] is True
            assert manifest["authority"]["changes_admin_state"] is False
            assert manifest["authority"]["changes_release_evidence"] is False
            assert manifest["authority"]["changes_storage_backend"] is False
            assert manifest["authority"]["changes_runtime_topology"] is False
            assert manifest["authority"]["executes_tools"] is False
            assert manifest["authority"]["merges_or_deploys_code"] is False

            print({
                "admin_control_manifest": manifest["manifest_sha256"],
                "admin_control_databases": manifest["database_count"],
                "admin_control_cross_database_atomic": manifest["methodology"]["cross_database_atomic_snapshot"],
                "admin_control_shared_across_nodes": manifest["methodology"]["shared_across_application_nodes"],
                "admin_control_read_only": manifest["authority"]["read_only"],
            })


if __name__ == "__main__":
    main()
