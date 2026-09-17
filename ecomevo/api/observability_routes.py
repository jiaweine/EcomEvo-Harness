from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from ecomevo.identity import current_principal
from ecomevo.product.observability import QualityObservability


Window = Literal["24h", "7d", "30d"]


def install_observability_routes(app: FastAPI, store, frontend: Path) -> None:
    """Install admin-only read-model routes under the existing runtime boundary."""

    service = QualityObservability(store)

    @app.get("/api/runtime/observability/ui", include_in_schema=False)
    def observability_ui():
        return FileResponse(frontend / "observability.html")

    @app.get("/api/runtime/observability")
    def observability_snapshot(window: Window = Query(default="7d")):
        principal = current_principal()
        try:
            return service.snapshot(tenant_id=principal.tenant_id, window=window)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
