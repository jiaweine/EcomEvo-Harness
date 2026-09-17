from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from ecomevo.identity import current_principal
from ecomevo.runtime.policy_workflow import (
    PolicyWorkflow,
    PolicyWorkflowConflict,
    PolicyWorkflowError,
    PolicyWorkflowStaleApproval,
    authority_contract,
)


Domain = Literal[
    "product_governance",
    "merchant_review",
    "aftersales",
    "risk_review",
    "content_audit",
    "general",
]


class PolicyDraftRequest(BaseModel):
    policy_key: str = Field(min_length=1, max_length=80)
    domain: Domain
    rules: list[str] = Field(min_length=1, max_length=50)
    controls: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, str] = Field(default_factory=dict)
    authority: int = Field(default=60, ge=0, le=90)
    priority: int = Field(default=0, ge=-1000, le=1000)
    source: str = Field(min_length=1, max_length=500)

    @field_validator("policy_key", "source")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class ApprovalRequest(BaseModel):
    effective_from: str | None = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=2000)


class RejectRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("note cannot be blank")
        return value


class RetirementRequest(BaseModel):
    effective_to: str | None = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=2000)


def _raise_workflow(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "政策版本不存在") from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc)) from exc
    if isinstance(exc, (PolicyWorkflowConflict, PolicyWorkflowStaleApproval)):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, PolicyWorkflowError):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, (TypeError, ValueError)):
        raise HTTPException(422, str(exc)) from exc
    raise exc


def install_policy_workflow_routes(
    app: FastAPI,
    *,
    engine,
    frontend: str | Path,
) -> PolicyWorkflow:
    workflow = PolicyWorkflow(engine.policies)
    frontend_dir = Path(frontend)

    @app.get("/api/runtime/policies/ui", include_in_schema=False)
    def policy_center_ui():
        return FileResponse(frontend_dir / "policy-center.html")

    @app.get("/api/runtime/policies/workflow")
    def policy_workflow_catalog(limit: int = Query(default=100, ge=1, le=200)):
        principal = current_principal()
        return {
            "items": workflow.list_tenant_versions(principal.tenant_id, limit=limit),
            "authority": authority_contract(),
            "tenant_scope": principal.tenant_id,
        }

    @app.get("/api/runtime/policies/{policy_id}/versions/{version}/workflow")
    def policy_workflow_detail(policy_id: str, version: int):
        principal = current_principal()
        try:
            return workflow.describe(policy_id, version, tenant_id=principal.tenant_id)
        except Exception as exc:
            _raise_workflow(exc)

    @app.post("/api/runtime/policies/drafts", status_code=201)
    def create_policy_draft(req: PolicyDraftRequest):
        principal = current_principal()
        try:
            return workflow.create_draft(
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                policy_key=req.policy_key,
                domain=req.domain,
                rules=req.rules,
                controls=req.controls,
                scope=req.scope,
                authority=req.authority,
                priority=req.priority,
                source=req.source,
            )
        except Exception as exc:
            _raise_workflow(exc)

    @app.get("/api/runtime/policies/{policy_id}/versions/{version}/preview-publish")
    def preview_policy_publish(
        policy_id: str,
        version: int,
        effective_from: str | None = Query(default=None, max_length=80),
    ):
        principal = current_principal()
        try:
            return workflow.preview_publish(
                policy_id,
                version,
                tenant_id=principal.tenant_id,
                effective_from=effective_from,
                approver=principal.user_id,
            )
        except Exception as exc:
            _raise_workflow(exc)

    @app.post("/api/runtime/policies/{policy_id}/versions/{version}/approve")
    def approve_policy(policy_id: str, version: int, req: ApprovalRequest):
        principal = current_principal()
        try:
            return workflow.approve(
                policy_id,
                version,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                effective_from=req.effective_from,
                note=req.note,
            )
        except Exception as exc:
            _raise_workflow(exc)

    @app.post("/api/runtime/policies/{policy_id}/versions/{version}/reject")
    def reject_policy(policy_id: str, version: int, req: RejectRequest):
        principal = current_principal()
        try:
            return workflow.reject(
                policy_id,
                version,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                note=req.note,
            )
        except Exception as exc:
            _raise_workflow(exc)

    @app.post("/api/runtime/policies/{policy_id}/versions/{version}/publish")
    def publish_policy(policy_id: str, version: int):
        principal = current_principal()
        try:
            return workflow.publish(
                policy_id,
                version,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
            )
        except Exception as exc:
            _raise_workflow(exc)

    @app.get("/api/runtime/policies/{policy_id}/versions/{version}/preview-retire")
    def preview_policy_retire(
        policy_id: str,
        version: int,
        effective_to: str | None = Query(default=None, max_length=80),
    ):
        principal = current_principal()
        try:
            return workflow.preview_retire(
                policy_id,
                version,
                tenant_id=principal.tenant_id,
                effective_to=effective_to,
            )
        except Exception as exc:
            _raise_workflow(exc)

    @app.post("/api/runtime/policies/{policy_id}/versions/{version}/retirement-requests")
    def request_policy_retirement(policy_id: str, version: int, req: RetirementRequest):
        principal = current_principal()
        try:
            return workflow.request_retirement(
                policy_id,
                version,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                effective_to=req.effective_to,
                note=req.note,
            )
        except Exception as exc:
            _raise_workflow(exc)

    @app.post("/api/runtime/policies/{policy_id}/versions/{version}/retire")
    def retire_policy(policy_id: str, version: int):
        principal = current_principal()
        try:
            return workflow.retire(
                policy_id,
                version,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
            )
        except Exception as exc:
            _raise_workflow(exc)

    app.state.policy_workflow_authority = authority_contract()
    return workflow
