from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.growth_center import GrowthCenter


class GrowthCampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    campaign_type: Literal["Campaign", "Experiment"] = "Campaign"
    objective: str = Field(default="增量利润", min_length=1, max_length=80)
    audience_size: int = Field(default=0, ge=0, le=1_000_000_000)
    channel: str = Field(default="App Push", min_length=1, max_length=80)
    evidence_tier: Literal["A", "B", "C", "D"] = "C"
    expected_lift: float = Field(default=0, ge=-100, le=100)
    incremental_roi: float = Field(default=0, ge=-100, le=1000)
    budget_rmb: float = Field(default=0, ge=0, le=10_000_000_000)
    risk_level: Literal["L0", "L1", "L2", "L3", "L4"] = "L2"
    canary_percent: float = Field(default=5, ge=0, le=100)
    starts_at: str | None = Field(default=None, max_length=40)


class GrowthAgentPlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)


class GrowthApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


def install_growth_routes(
    app: FastAPI,
    *,
    frontend: str | Path,
    data_dir: str | Path,
) -> GrowthCenter:
    """Install the GrowthEvo product surface without bypassing runtime authority.

    The API persists candidate campaigns, evidence-facing decision records,
    harness runs and approvals. No route in this module dispatches an external
    channel side effect.
    """

    frontend_dir = Path(frontend)
    center = GrowthCenter(Path(data_dir) / "growth_center.db")

    @app.get("/growth", include_in_schema=False)
    def growth_product_ui():
        return FileResponse(frontend_dir / "growth.html")

    @app.get("/api/growth")
    def growth_product_contract():
        current_principal()
        return {
            "name": "GrowthEvo",
            "positioning": "Agentic Causal Growth Platform",
            "decision_principle": "Context × Action → Incremental Effect",
            "no_treatment_first_class": True,
            "execution_boundary": "Candidate → Causal Evaluation → Policy → Guardrail → Approval → Canary → Execution",
            "side_effects_from_this_surface": False,
            "surfaces": [
                "growth_dashboard",
                "opportunity_map",
                "campaign_candidates",
                "approvals",
                "agent_harness_runs",
                "decision_log",
            ],
        }

    @app.get("/api/growth/dashboard")
    def growth_dashboard(days: int = Query(default=30, ge=7, le=90)):
        principal = current_principal()
        return center.dashboard(principal.tenant_id, days=days)

    @app.get("/api/growth/opportunities")
    def growth_opportunities():
        principal = current_principal()
        return center.opportunities(principal.tenant_id)

    @app.get("/api/growth/campaigns")
    def growth_campaigns():
        principal = current_principal()
        return center.campaigns(principal.tenant_id)

    @app.post("/api/growth/campaigns", status_code=201)
    def growth_campaign_create(req: GrowthCampaignCreate):
        principal = current_principal()
        return center.create_campaign(principal.tenant_id, req.model_dump())

    @app.post("/api/growth/campaigns/{campaign_id}/canary-request", status_code=201)
    def growth_campaign_canary_request(campaign_id: str):
        principal = current_principal()
        try:
            return center.request_canary(
                principal.tenant_id,
                campaign_id,
                requested_by=principal.user_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "Campaign 不存在") from exc

    @app.get("/api/growth/approvals")
    def growth_approvals():
        principal = current_principal()
        return center.approvals(principal.tenant_id)

    @app.post("/api/growth/approvals/{approval_id}/decision")
    def growth_approval_decision(approval_id: str, req: GrowthApprovalDecision):
        principal = current_principal()
        if not principal.can("approver"):
            raise HTTPException(403, "当前角色无权审批，需要 approver 权限")
        try:
            return center.decide_approval(
                principal.tenant_id,
                approval_id,
                decision=req.decision,
                decided_by=principal.user_id,
                note=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "审批项不存在") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/growth/agent-runs")
    def growth_agent_runs(limit: int = Query(default=30, ge=1, le=100)):
        principal = current_principal()
        return center.agent_runs(principal.tenant_id, limit=limit)

    @app.get("/api/growth/agent-runs/{run_id}")
    def growth_agent_run(run_id: str):
        principal = current_principal()
        try:
            return center.agent_run(principal.tenant_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "Agent Run 不存在") from exc

    @app.post("/api/growth/agent/plan", status_code=201)
    def growth_agent_plan(req: GrowthAgentPlanRequest):
        principal = current_principal()
        try:
            return center.plan_goal(principal.tenant_id, req.goal)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/growth/decisions")
    def growth_decisions(limit: int = Query(default=50, ge=1, le=200)):
        principal = current_principal()
        return center.decisions(principal.tenant_id, limit=limit)

    return center
