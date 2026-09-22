from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.shadow_environment import ShadowEnterpriseSimulator


Surface = Literal["mcp", "browser", "terminal", "structured_data"]
Operation = Literal["read", "governed_action"]


class ShadowScenarioRequest(BaseModel):
    surface: Surface
    operation: Operation
    mutation: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=160)
    context_labels: list[str] = Field(default_factory=list, max_length=24)
    baseline_schema: dict[str, Any] = Field(default_factory=dict)
    mutated_schema: dict[str, Any] = Field(default_factory=dict)


def install_shadow_environment_routes(
    app: FastAPI,
    *,
    frontend: str | Path,
) -> ShadowEnterpriseSimulator:
    """Install admin-only, non-executing shadow replay routes."""
    simulator = ShadowEnterpriseSimulator()
    frontend_dir = Path(frontend)

    @app.get("/api/runtime/shadow/ui", include_in_schema=False)
    def shadow_environment_ui():
        return FileResponse(frontend_dir / "shadow-environment.html")

    @app.get("/api/runtime/shadow/catalog")
    def shadow_environment_catalog():
        principal = current_principal()
        return {
            **simulator.catalog(),
            "tenant_scope": principal.tenant_id,
        }

    @app.post("/api/runtime/shadow/simulate")
    def shadow_environment_simulate(req: ShadowScenarioRequest):
        principal = current_principal()
        try:
            return simulator.simulate(
                tenant_id=principal.tenant_id,
                surface=req.surface,
                operation=req.operation,
                mutation=req.mutation,
                target=req.target,
                context_labels=req.context_labels,
                baseline_schema=req.baseline_schema,
                mutated_schema=req.mutated_schema,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return simulator
