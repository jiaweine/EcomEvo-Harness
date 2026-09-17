from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ecomevo.evaluation import EvaluationRunStore, evaluate_cases, load_cases, packaged_gold_text


ROOT = Path(__file__).resolve().parents[1]


def test_packaged_gold_set_matches_engineering_fixture():
    engineering = (ROOT / "evals" / "gold_set.jsonl").read_text(encoding="utf-8")
    packaged = packaged_gold_text()

    assert hashlib.sha256(packaged.encode("utf-8")).hexdigest() == hashlib.sha256(
        engineering.encode("utf-8")
    ).hexdigest()
    assert len(load_cases()) == len(load_cases(ROOT / "evals" / "gold_set.jsonl"))


@pytest.mark.asyncio
async def test_evaluation_replay_uses_isolated_runtime_and_preserves_gate_semantics():
    case = next(row for row in load_cases() if row["id"] == "merchant_missing_authorization")

    result = await evaluate_cases([case])

    assert result["ok"] is True
    assert result["case_count"] == 1
    assert [phase["phase"] for phase in result["phases"]] == ["fresh", "persisted_replay"]
    assert all(phase["cases"][0]["status"] == "needs_evidence" for phase in result["phases"])
    assert all(not phase["failures"] for phase in result["phases"])
    assert result["comparisons"][0]["stable"] is True
    assert result["summary"]["drift_case_count"] == 0


def test_evaluation_run_store_is_append_only_snapshot_store(tmp_path):
    store = EvaluationRunStore(tmp_path / "evaluation.db")
    result = {
        "ok": True,
        "source_hash": "abc",
        "case_count": 2,
        "phase_count": 2,
        "summary": {"failed_case_count": 0, "drift_case_count": 0},
        "phases": [],
        "comparisons": [],
        "failures": [],
        "fixture_snapshot": [],
    }

    first = store.record(result)
    second = store.record(result)

    assert first["id"] != second["id"]
    assert store.get_run(first["id"])["source_hash"] == "abc"
    assert {row["id"] for row in store.list_runs()} == {first["id"], second["id"]}
    assert not hasattr(store, "update_run")
    assert not hasattr(store, "delete_run")


def test_evaluation_api_reuses_admin_runtime_rbac(monkeypatch):
    from ecomevo.api.app import app

    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "admin")
    with TestClient(app) as client:
        cases = client.get("/api/runtime/evaluations/cases")
        assert cases.status_code == 200
        assert cases.json()["case_count"] >= 1

        history = client.get("/api/runtime/evaluations/runs")
        assert history.status_code == 200
        assert "items" in history.json()

        ui = client.get("/api/runtime/evaluations/ui")
        assert ui.status_code == 200
        assert "Test Center" in ui.text

    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", "viewer")
    with TestClient(app) as client:
        assert client.get("/api/runtime/evaluations/cases").status_code == 403
        assert client.get("/api/runtime/evaluations/runs").status_code == 403
        assert client.get("/api/runtime/evaluations/ui").status_code == 403
