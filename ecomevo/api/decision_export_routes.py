from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.decision_exports import DecisionExportCenter


class DecisionExportCreate(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=120)


def install_decision_export_routes(
    app: FastAPI,
    store,
    frontend: Path,
    db_path: Path,
) -> DecisionExportCenter:
    """Install admin-only immutable decision/audit export routes."""

    service = DecisionExportCenter(db_path)

    @app.get("/api/runtime/decision-exports/ui", include_in_schema=False)
    def decision_exports_ui():
        return FileResponse(frontend / "decision-exports.html")

    @app.get("/api/runtime/decision-exports")
    def decision_exports_list(
        conversation_id: str | None = Query(default=None, max_length=120),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        principal = current_principal()
        items = service.list_snapshots(
            tenant_id=principal.tenant_id,
            conversation_id=conversation_id,
            limit=limit,
        )
        return {
            "count": len(items),
            "items": items,
            "authority": service._authority(),
        }

    @app.post("/api/runtime/decision-exports")
    def decision_exports_create(req: DecisionExportCreate):
        principal = current_principal()
        try:
            return service.create_snapshot(
                store,
                tenant_id=principal.tenant_id,
                created_by=principal.user_id,
                conversation_id=req.conversation_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc

    @app.get("/api/runtime/decision-exports/{export_id}/verify")
    def decision_exports_verify(export_id: str):
        principal = current_principal()
        try:
            return service.verify_snapshot(export_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "导出快照不存在") from exc

    @app.get("/api/runtime/decision-exports/{export_id}/download")
    def decision_exports_download(export_id: str):
        principal = current_principal()
        try:
            snapshot = service.get_snapshot(export_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "导出快照不存在") from exc
        body = {
            "schema_version": snapshot["schema_version"],
            "id": snapshot["id"],
            "tenant_id": snapshot["tenant_id"],
            "conversation_id": snapshot["conversation_id"],
            "created_by": snapshot["created_by"],
            "created_at": snapshot["created_at"],
            "content_hash": snapshot["content_hash"],
            "payload": snapshot["payload"],
            "authority": snapshot["authority"],
        }
        return JSONResponse(
            body,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="ecomevo-{snapshot["id"]}.json"'
                )
            },
        )

    @app.get("/api/runtime/decision-exports/{export_id}")
    def decision_exports_get(export_id: str):
        principal = current_principal()
        try:
            return service.get_snapshot(export_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "导出快照不存在") from exc

    return service
