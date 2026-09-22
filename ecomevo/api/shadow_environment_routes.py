from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ecomevo.identity import current_principal
from ecomevo.product.shadow_environment import (
    CORPUS_REDACTION_PROFILE,
    ShadowEnterpriseSimulator,
)


Surface = Literal["mcp", "browser", "terminal", "structured_data"]
Operation = Literal["read", "governed_action"]
ObservationPhase = Literal[
    "pre_dispatch",
    "dispatch",
    "post_dispatch",
    "response",
    "parse",
    "validation",
    "unknown",
]


class ShadowScenarioRequest(BaseModel):
    surface: Surface
    operation: Operation
    mutation: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=160)
    context_labels: list[str] = Field(default_factory=list, max_length=24)
    baseline_schema: dict[str, Any] = Field(default_factory=dict)
    mutated_schema: dict[str, Any] = Field(default_factory=dict)


class ShadowCorpusProvenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str = Field(min_length=1, max_length=80)
    source_event_id: str = Field(min_length=1, max_length=160)
    observed_at: datetime
    source_record_sha256: str = Field(min_length=64, max_length=64)
    redaction_profile: Literal["ecomevo-shadow-v1"] = CORPUS_REDACTION_PROFILE
    redaction_attested: Literal[True]


class ShadowFailureObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: ObservationPhase
    status_code: int | None = Field(default=None, ge=100, le=599)
    error_code: str | None = Field(default=None, min_length=1, max_length=120)
    error_class: str | None = Field(default=None, min_length=1, max_length=120)
    latency_ms: int | None = Field(default=None, ge=0, le=3_600_000)


class ShadowCorpusImportRequest(ShadowScenarioRequest):
    model_config = ConfigDict(extra="forbid")

    provenance: ShadowCorpusProvenanceRequest
    observation: ShadowFailureObservationRequest


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

    @app.post("/api/runtime/shadow/import-fixture")
    def shadow_environment_import_fixture(req: ShadowCorpusImportRequest):
        principal = current_principal()
        provenance = req.provenance
        observation = req.observation
        try:
            return simulator.import_fixture(
                tenant_id=principal.tenant_id,
                surface=req.surface,
                operation=req.operation,
                mutation=req.mutation,
                target=req.target,
                context_labels=req.context_labels,
                baseline_schema=req.baseline_schema,
                mutated_schema=req.mutated_schema,
                source_system=provenance.source_system,
                source_event_id=provenance.source_event_id,
                observed_at=provenance.observed_at.isoformat(),
                source_record_sha256=provenance.source_record_sha256,
                redaction_profile=provenance.redaction_profile,
                redaction_attested=provenance.redaction_attested,
                phase=observation.phase,
                status_code=observation.status_code,
                error_code=observation.error_code,
                error_class=observation.error_class,
                latency_ms=observation.latency_ms,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return simulator
