from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecomevo.api.growth_routes import install_growth_routes
from ecomevo.product.growth_center import GrowthCenter


def test_growth_center_seeds_incremental_first_dashboard(tmp_path: Path):
    center = GrowthCenter(tmp_path / "growth.db")
    dashboard = center.dashboard("tenant-a", days=30)

    assert dashboard["data_mode"] == "reference_seed"
    assert dashboard["period_days"] == 30
    assert len(dashboard["time_series"]) == 30
    assert dashboard["kpis"]["incremental_revenue"] > 0
    assert dashboard["kpis"]["incremental_profit"] > 0
    assert dashboard["kpis"]["avoided_cost"] > 0
    assert dashboard["kpis"]["no_treatment"] > 0
    assert any(row["status"] == "canary" for row in dashboard["campaigns"])
    assert dashboard["active_agent_run"]["harness_version"] == "growth-harness@1.0"


def test_campaign_never_skips_approval_before_canary(tmp_path: Path):
    center = GrowthCenter(tmp_path / "growth.db")
    campaign = center.create_campaign(
        "tenant-a",
        {
            "name": "春节新用户首购",
            "objective": "增量利润",
            "audience_size": 100_000,
            "channel": "App Push",
            "evidence_tier": "B",
            "risk_level": "L2",
            "budget_rmb": 500_000,
            "canary_percent": 5,
        },
    )
    assert campaign["status"] == "draft"

    approval = center.request_canary("tenant-a", campaign["campaign_id"], requested_by="operator")
    assert approval["status"] == "pending"
    pending_campaign = next(row for row in center.campaigns("tenant-a") if row["campaign_id"] == campaign["campaign_id"])
    assert pending_campaign["status"] == "awaiting_approval"

    center.decide_approval(
        "tenant-a",
        approval["approval_id"],
        decision="approve",
        decided_by="approver",
        note="5% canary only",
    )
    canary_campaign = next(row for row in center.campaigns("tenant-a") if row["campaign_id"] == campaign["campaign_id"])
    assert canary_campaign["status"] == "canary"


def test_agent_goal_compiles_to_candidates_not_side_effects(tmp_path: Path):
    center = GrowthCenter(tmp_path / "growth.db")
    run = center.plan_goal("tenant-a", "提升春节前新用户首购率，同时控制用户疲劳")

    assert run["status"] == "running"
    assert run["policy_status"] == "pass"
    assert any(row["type"] == "Strategy Proposal" for row in run["artifacts"])
    assert any(row["label"].startswith("Shadow") for row in run["steps"])
    assert "Approval/Canary" in run["outcome"]


def test_growth_routes_expose_dashboard_and_draft_flow(tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "growth.html").write_text("<h1>GrowthEvo</h1>", encoding="utf-8")

    app = FastAPI()
    install_growth_routes(app, frontend=frontend, data_dir=tmp_path)

    with TestClient(app) as client:
        ui = client.get("/growth")
        assert ui.status_code == 200
        assert "GrowthEvo" in ui.text

        dashboard = client.get("/api/growth/dashboard?days=7")
        assert dashboard.status_code == 200
        assert dashboard.json()["period_days"] == 7

        created = client.post(
            "/api/growth/campaigns",
            json={
                "name": "API Draft",
                "audience_size": 12000,
                "budget_rmb": 12000,
                "evidence_tier": "B",
            },
        )
        assert created.status_code == 201
        assert created.json()["status"] == "draft"

        planned = client.post("/api/growth/agent/plan", json={"goal": "分析会员到期增量机会"})
        assert planned.status_code == 201
        assert planned.json()["artifacts"]
