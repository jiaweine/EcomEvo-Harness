from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ecomevo-certification-contract-smoke-") as tmp:
        os.environ["ECOMEVO_DATA"] = tmp
        os.environ["ECOMEVO_AUTH_MODE"] = "local"
        os.environ["ECOMEVO_LOCAL_TENANT"] = "local"
        os.environ["ECOMEVO_LOCAL_USER"] = "certification-contract-smoke"
        os.environ["ECOMEVO_LOCAL_ROLE"] = "admin"
        os.environ["ECOMEVO_DEPLOYMENT_NODES"] = "1"

        from fastapi.testclient import TestClient
        from ecomevo.api.app import app

        with TestClient(app) as client:
            response = client.get(
                "/api/runtime/readiness/multi-node/certification-contract"
            )
            assert response.status_code == 200
            contract = response.json()
            assert contract["contract_status"] == "complete_not_executed"
            assert contract["gate_count"] == 6
            assert contract["certification_ready"] is False
            assert contract["certification_executed"] is False
            assert contract["certification_passed"] is False
            assert contract["contract_integrity"]["matches_migration_gate_ids"] is True
            assert contract["contract_integrity"]["missing_gate_contract_ids"] == []
            assert contract["contract_integrity"]["unexpected_gate_contract_ids"] == []
            assert all(gate["passed"] is False for gate in contract["gates"])
            assert all(
                gate["requires_real_cross_node_execution"] is True
                for gate in contract["gates"]
            )
            assert all(
                gate["same_process_simulation_accepted"] is False
                for gate in contract["gates"]
            )
            assert all(
                gate["same_host_multi_process_accepted"] is False
                for gate in contract["gates"]
            )
            assert contract["methodology"]["client_supplied_evidence_accepted"] is False
            assert contract["methodology"]["historical_single_node_ci_sufficient"] is False
            assert contract["methodology"]["release_authority_granted"] is False
            assert contract["authority"]["read_only"] is True
            assert contract["authority"]["runs_certification_tests"] is False
            assert contract["authority"]["executes_tools"] is False
            assert contract["authority"]["merges_or_deploys_code"] is False

            print({
                "multi_node_certification_contract": contract["contract_status"],
                "multi_node_certification_gates": contract["gate_count"],
                "multi_node_certification_executed": contract["certification_executed"],
                "multi_node_certification_passed": contract["certification_passed"],
                "multi_node_real_cross_node_required": all(
                    gate["requires_real_cross_node_execution"]
                    for gate in contract["gates"]
                ),
            })


if __name__ == "__main__":
    main()
