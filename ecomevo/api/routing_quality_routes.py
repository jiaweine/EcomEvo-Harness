from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from ecomevo.identity import current_principal
from ecomevo.product.routing_quality import RoutingQualityControlTower


Window = Literal["24h", "7d", "30d"]


def install_routing_quality_routes(app: FastAPI, store, frontend: Path) -> None:
    service = RoutingQualityControlTower(store)

    @app.get("/api/runtime/routing-quality/ui", include_in_schema=False)
    def routing_quality_ui():
        return FileResponse(frontend / "routing-quality.html")

    @app.get("/api/runtime/routing-quality")
    def routing_quality_snapshot(window: Window = Query(default="7d")):
        principal = current_principal()
        try:
            return service.snapshot(tenant_id=principal.tenant_id, window=window)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
