from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from ecomevo.identity import current_principal
from ecomevo.product.release_readiness import ReleaseReadinessCenter


Window = Literal["24h", "7d", "30d"]


def install_release_readiness_routes(
    app: FastAPI,
    *,
    center: ReleaseReadinessCenter,
    frontend: Path,
) -> None:
    """Install admin-only readiness evidence routes under the runtime RBAC boundary."""

    @app.get("/api/runtime/readiness/ui", include_in_schema=False)
    def readiness_ui():
        return FileResponse(frontend / "release-readiness.html")

    @app.get("/api/runtime/readiness/multi-node")
    def readiness_multi_node():
        current_principal()
        return center.multi_node_readiness()

    @app.get("/api/runtime/readiness/connections")
    def readiness_connections():
        current_principal()
        return center.connection_readiness()

    @app.get("/api/runtime/readiness/preview")
    def readiness_preview(window: Window = Query(default="7d")):
        principal = current_principal()
        try:
            return center.preview(
                tenant_id=principal.tenant_id,
                window=window,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/runtime/readiness/snapshots", status_code=201)
    def readiness_snapshot_create(window: Window = Query(default="7d")):
        principal = current_principal()
        try:
            return center.create_snapshot(
                tenant_id=principal.tenant_id,
                window=window,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/runtime/readiness/snapshots")
    def readiness_snapshot_list(limit: int = Query(default=50, ge=1, le=100)):
        principal = current_principal()
        items = center.list_snapshots(
            tenant_id=principal.tenant_id,
            limit=limit,
        )
        return {
            "count": len(items),
            "items": items,
            "authority": center.authority(),
        }

    @app.get("/api/runtime/readiness/snapshots/{snapshot_id}")
    def readiness_snapshot_get(snapshot_id: str):
        principal = current_principal()
        if len(snapshot_id) > 96:
            raise HTTPException(404, "发布准备度快照不存在")
        try:
            return center.get_snapshot(
                snapshot_id,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "发布准备度快照不存在") from exc
