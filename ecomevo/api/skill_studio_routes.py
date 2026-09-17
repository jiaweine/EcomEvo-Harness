from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.skill_studio import SkillStudioStore, authority_contract


Domain = Literal[
    "product_governance",
    "merchant_review",
    "aftersales",
    "risk_review",
    "content_audit",
    "general",
]


class SkillVersionDraft(BaseModel):
    domain: Domain
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=600)
    guidance: str = Field(min_length=10, max_length=6000)
    preferred_tools: list[str] = Field(default_factory=list, max_length=8)
    trigger_terms: list[str] = Field(min_length=1, max_length=16)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    safety_notes: str = Field(default="", max_length=3000)
    source_skill_id: str | None = Field(default=None, max_length=160)


class SubmitRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class ArchiveRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


def install_skill_studio_routes(
    app: FastAPI,
    *,
    db_path: str | Path,
    engine,
    frontend: str | Path,
) -> SkillStudioStore:
    """Install admin-only build/review surfaces without runtime promotion authority."""
    store = SkillStudioStore(db_path, engine.skills, engine.tools)
    frontend_dir = Path(frontend)
    evaluation_lock = asyncio.Lock()

    def payload(req: SkillVersionDraft) -> dict[str, Any]:
        return {
            "domain": req.domain,
            "name": req.name,
            "purpose": req.purpose,
            "guidance": req.guidance,
            "preferred_tools": req.preferred_tools,
            "trigger_terms": req.trigger_terms,
            "input_contract": req.input_contract,
            "output_contract": req.output_contract,
            "safety_notes": req.safety_notes,
            "source_skill_id": req.source_skill_id,
        }

    @app.get("/api/runtime/skills/ui", include_in_schema=False)
    def skill_studio_ui():
        return FileResponse(frontend_dir / "skill-studio.html")

    @app.get("/api/runtime/skills/catalog")
    def skill_catalog():
        return store.catalog()

    @app.get("/api/runtime/skills/studio")
    def skill_versions(limit: int = Query(default=100, ge=1, le=200)):
        return {"items": store.list_versions(limit), "authority": authority_contract()}

    @app.get("/api/runtime/skills/studio/evaluations/{evaluation_id}")
    def skill_evaluation(evaluation_id: str):
        item = store.get_evaluation(evaluation_id)
        if item is None:
            raise HTTPException(404, "候选评估不存在")
        return item

    @app.get("/api/runtime/skills/studio/{version_id}")
    def skill_version(version_id: str):
        item = store.get_version(version_id)
        if item is None:
            raise HTTPException(404, "技能版本不存在")
        return item

    @app.post("/api/runtime/skills/studio/families", status_code=201)
    def skill_family_create(req: SkillVersionDraft):
        principal = current_principal()
        try:
            return store.create_family(actor_id=principal.user_id, **payload(req))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/runtime/skills/studio/families/{family_id}/versions", status_code=201)
    def skill_version_create(family_id: str, req: SkillVersionDraft):
        principal = current_principal()
        try:
            return store.create_version(family_id, actor_id=principal.user_id, **payload(req))
        except KeyError as exc:
            raise HTTPException(404, "技能族不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/runtime/skills/studio/{version_id}/submit")
    def skill_submit(version_id: str, req: SubmitRequest):
        principal = current_principal()
        try:
            return store.submit(version_id, actor_id=principal.user_id, note=req.note)
        except KeyError as exc:
            raise HTTPException(404, "技能版本不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runtime/skills/studio/{version_id}/evaluate")
    async def skill_evaluate(version_id: str):
        if evaluation_lock.locked():
            raise HTTPException(409, "已有 Studio 候选正在评估")
        principal = current_principal()
        try:
            async with evaluation_lock:
                return await store.evaluate(version_id, actor_id=principal.user_id)
        except KeyError as exc:
            raise HTTPException(404, "技能版本不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/runtime/skills/studio/{version_id}/archive")
    def skill_archive(version_id: str, req: ArchiveRequest):
        principal = current_principal()
        try:
            return store.archive(version_id, actor_id=principal.user_id, note=req.note)
        except KeyError as exc:
            raise HTTPException(404, "技能版本不存在") from exc

    # Deliberately no publish/promote/update/delete route. Runtime skill authority remains
    # inside AdaptiveSkillLibrary shadow/outcome promotion and deterministic release gates.
    app.state.skill_studio_authority = authority_contract()
    return store
