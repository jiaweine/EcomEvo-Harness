from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.knowledge_sources import (
    KnowledgeSourceStore,
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
SourceTier = Literal["S2", "S4"]


class KnowledgeVersionDraft(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content_text: str = Field(min_length=20, max_length=200_000)
    effective_from: float | None = None
    effective_until: float | None = None
    review_due_at: float | None = None
    provenance: str = Field(min_length=1, max_length=2000)


class KnowledgeSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    source_tier: SourceTier
    domain: Domain
    description: str = Field(default="", max_length=3000)
    owner: str = Field(min_length=1, max_length=180)
    jurisdiction: str = Field(default="", max_length=180)
    tags: list[str] = Field(default_factory=list, max_length=20)
    version: KnowledgeVersionDraft


class LifecycleRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


def install_knowledge_routes(
    app: FastAPI,
    *,
    db_path: str | Path,
    frontend: str | Path,
) -> KnowledgeSourceStore:
    """Install tenant-scoped source governance without runtime evidence authority."""
    store = KnowledgeSourceStore(db_path)
    frontend_dir = Path(frontend)

    @app.get("/api/runtime/knowledge/ui", include_in_schema=False)
    def knowledge_ui():
        return FileResponse(frontend_dir / "knowledge.html")

    @app.get("/api/runtime/knowledge")
    def knowledge_catalog():
        principal = current_principal()
        return store.catalog(principal.tenant_id)

    @app.get("/api/runtime/knowledge/search")
    def knowledge_search(
        q: str = Query(min_length=1, max_length=1000),
        domain: Domain | None = None,
        limit: int = Query(default=20, ge=1, le=50),
    ):
        principal = current_principal()
        return {
            "query": q,
            "items": store.search_published(
                principal.tenant_id,
                q,
                domain=domain,
                limit=limit,
            ),
            "authority": authority_contract(),
        }

    @app.post("/api/runtime/knowledge/sources", status_code=201)
    def knowledge_source_create(req: KnowledgeSourceCreate):
        principal = current_principal()
        try:
            return store.create_source(
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                name=req.name,
                source_tier=req.source_tier,
                domain=req.domain,
                description=req.description,
                owner=req.owner,
                jurisdiction=req.jurisdiction,
                tags=req.tags,
                version=req.version.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/runtime/knowledge/sources/{source_id}")
    def knowledge_source_get(source_id: str):
        principal = current_principal()
        item = store.get_source(
            principal.tenant_id,
            source_id,
            include_content=True,
        )
        if item is None:
            raise HTTPException(404, "知识源不存在")
        return item

    @app.post(
        "/api/runtime/knowledge/sources/{source_id}/versions",
        status_code=201,
    )
    def knowledge_version_create(source_id: str, req: KnowledgeVersionDraft):
        principal = current_principal()
        try:
            return store.create_version(
                source_id,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                **req.model_dump(),
            )
        except KeyError as exc:
            raise HTTPException(404, "知识源不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def transition(version_id: str, event_type: str, req: LifecycleRequest):
        principal = current_principal()
        try:
            return store.transition(
                version_id,
                event_type,
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                note=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "知识版本不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runtime/knowledge/versions/{version_id}/review")
    def knowledge_review(version_id: str, req: LifecycleRequest):
        return transition(version_id, "reviewed", req)

    @app.post("/api/runtime/knowledge/versions/{version_id}/publish")
    def knowledge_publish(version_id: str, req: LifecycleRequest):
        return transition(version_id, "published", req)

    @app.post("/api/runtime/knowledge/versions/{version_id}/retire")
    def knowledge_retire(version_id: str, req: LifecycleRequest):
        return transition(version_id, "retired", req)

    @app.get("/api/runtime/knowledge/versions/{version_id}/retrieval-projection")
    def knowledge_projection(version_id: str):
        principal = current_principal()
        try:
            return store.retrieval_projection(principal.tenant_id, version_id)
        except KeyError as exc:
            raise HTTPException(404, "知识版本不存在") from exc

    app.state.knowledge_source_authority = authority_contract()
    return store
