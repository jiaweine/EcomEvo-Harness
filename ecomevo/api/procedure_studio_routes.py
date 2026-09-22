from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.procedure_studio import ProcedureStudio


Domain = Literal[
    "product_governance",
    "merchant_review",
    "aftersales",
    "risk_review",
    "content_audit",
    "general",
]


class ProcedureStep(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    instruction: str = Field(min_length=1, max_length=1600)


class ProcedureSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    domain: Domain
    purpose: str = Field(min_length=1, max_length=1200)
    guidance: str = Field(min_length=1, max_length=8000)
    trigger_terms: list[str] = Field(default_factory=list, max_length=20)
    preferred_tools: list[str] = Field(default_factory=list, max_length=12)
    required_evidence: list[str] = Field(default_factory=list, max_length=24)
    prohibited_actions: list[str] = Field(default_factory=list, max_length=24)
    steps: list[ProcedureStep] = Field(default_factory=list, max_length=24)
    output_contract: str = Field(default="", max_length=5000)


class TransitionRequest(BaseModel):
    note: str = Field(default="", max_length=4000)


def _spec(req: ProcedureSpec) -> dict:
    return req.model_dump()


def install_procedure_studio_routes(
    app: FastAPI,
    studio: ProcedureStudio,
    frontend: Path,
) -> None:
    """Install admin-only Build-plane procedure authoring routes.

    IdentityMiddleware protects every /api/runtime/* route as admin-only.
    These routes never receive a runtime skill library, action store, policy engine,
    router, verifier, or MCP execution registry.
    """

    @app.get("/api/runtime/procedures/ui", include_in_schema=False)
    def procedure_studio_ui():
        return FileResponse(frontend / "procedure-studio.html")

    @app.get("/api/runtime/procedures/capabilities")
    def procedure_capabilities():
        return {
            "schema_version": 1,
            "states": ["draft", "evaluation_candidate", "catalog_published"],
            "authority": studio.authority(),
        }

    @app.get("/api/runtime/procedures/catalog")
    def procedure_catalog(limit: int = Query(default=200, ge=1, le=500)):
        principal = current_principal()
        items = studio.catalog(tenant_id=principal.tenant_id, limit=limit)
        return {
            "count": len(items),
            "items": items,
            "authority": studio.authority(),
        }

    @app.get("/api/runtime/procedures")
    def procedure_list(limit: int = Query(default=100, ge=1, le=200)):
        principal = current_principal()
        items = studio.list(tenant_id=principal.tenant_id, limit=limit)
        return {
            "count": len(items),
            "items": items,
            "authority": studio.authority(),
        }

    @app.post("/api/runtime/procedures")
    def procedure_create(req: ProcedureSpec):
        principal = current_principal()
        try:
            return studio.create(
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                actor_role=principal.role,
                spec=_spec(req),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/runtime/procedures/{procedure_id}")
    def procedure_get(procedure_id: str):
        principal = current_principal()
        try:
            return studio.get(procedure_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "Procedure 不存在") from exc

    @app.get("/api/runtime/procedures/{procedure_id}/events")
    def procedure_events(procedure_id: str):
        principal = current_principal()
        try:
            items = studio.events(procedure_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "Procedure 不存在") from exc
        return {
            "count": len(items),
            "items": items,
            "authority": studio.authority(),
        }

    @app.post("/api/runtime/procedures/{procedure_id}/versions")
    def procedure_new_version(procedure_id: str, req: ProcedureSpec):
        principal = current_principal()
        try:
            return studio.create_version(
                procedure_id,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                actor_role=principal.role,
                spec=_spec(req),
            )
        except KeyError as exc:
            raise HTTPException(404, "Procedure 不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/runtime/procedures/{procedure_id}/versions/{version_id}")
    def procedure_version(procedure_id: str, version_id: str):
        principal = current_principal()
        try:
            item = studio.version(
                procedure_id,
                version_id,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "Procedure 版本不存在") from exc
        item["authority"] = studio.authority()
        return item

    @app.get("/api/runtime/procedures/{procedure_id}/versions/{version_id}/skill-candidate")
    def procedure_skill_candidate(procedure_id: str, version_id: str):
        principal = current_principal()
        try:
            return studio.skill_candidate(
                procedure_id,
                version_id,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "Procedure 版本不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runtime/procedures/{procedure_id}/versions/{version_id}/evaluation-candidate")
    def procedure_mark_evaluation_candidate(
        procedure_id: str,
        version_id: str,
        req: TransitionRequest,
    ):
        principal = current_principal()
        try:
            item = studio.transition(
                procedure_id,
                version_id,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                actor_role=principal.role,
                target_state="evaluation_candidate",
                note=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "Procedure 版本不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        item["authority"] = studio.authority()
        return item

    @app.post("/api/runtime/procedures/{procedure_id}/versions/{version_id}/publish")
    def procedure_publish_catalog(
        procedure_id: str,
        version_id: str,
        req: TransitionRequest,
    ):
        principal = current_principal()
        try:
            item = studio.transition(
                procedure_id,
                version_id,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                actor_role=principal.role,
                target_state="catalog_published",
                note=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "Procedure 版本不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        item["authority"] = studio.authority()
        return item
