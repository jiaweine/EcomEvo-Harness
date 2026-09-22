from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ecomevo.product.deployment_topology import (
    current_deployment_topology,
    evaluate_deployment_topology,
    require_runtime_topology_supported,
)


def test_missing_declaration_allows_compatibility_start_but_not_release():
    topology = evaluate_deployment_topology(None, source="test")

    assert topology["declaration_explicit"] is False
    assert topology["runtime_start_allowed"] is True
    assert topology["release_supported"] is False
    assert topology["actual_replica_discovery"] is False


def test_single_node_declaration_allows_runtime_and_release_readiness():
    topology = evaluate_deployment_topology("1", source="test")

    assert topology["declaration_explicit"] is True
    assert topology["declaration_valid"] is True
    assert topology["declared_nodes"] == 1
    assert topology["runtime_start_allowed"] is True
    assert topology["release_supported"] is True


@pytest.mark.parametrize("raw_nodes", ["", "0", "-1", "two"])
def test_explicit_invalid_declaration_refuses_runtime_start(raw_nodes):
    topology = evaluate_deployment_topology(raw_nodes, source="test")

    assert topology["declaration_explicit"] is True
    assert topology["runtime_start_allowed"] is False
    assert topology["release_supported"] is False


def test_explicit_multi_node_declaration_refuses_runtime_start():
    topology = evaluate_deployment_topology("2", source="test")

    assert topology["declared_nodes"] == 2
    assert topology["runtime_start_allowed"] is False
    assert topology["cross_node_supported"] is False
    assert topology["requires_central_transactional_backend_for_multi_node"] is True


def test_current_runtime_guard_rejects_explicit_multi_node(monkeypatch):
    monkeypatch.setenv("ECOMEVO_DEPLOYMENT_NODES", "2")

    topology = current_deployment_topology()
    assert topology["declaration_source"] == "environment"
    with pytest.raises(RuntimeError, match="unsupported EcomEvo deployment topology"):
        require_runtime_topology_supported()


def test_application_import_fails_before_runtime_database_creation(tmp_path):
    data_dir = tmp_path / "runtime"
    env = os.environ.copy()
    env["ECOMEVO_DATA"] = str(data_dir)
    env["ECOMEVO_DEPLOYMENT_NODES"] = "2"

    completed = subprocess.run(
        [sys.executable, "-c", "import ecomevo.api.application"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "unsupported EcomEvo deployment topology" in combined
    assert not (data_dir / "product.db").exists()
    assert not (data_dir / "runtime.db").exists()
