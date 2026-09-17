from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ecomevo.identity import current_principal
from ecomevo.product.operator_activity import OperatorActivityLedger


class OperatorHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str = Field(default="workbench", min_length=1, max_length=64)

    @field_validator("surface")
    @classmethod
    def clean_surface(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("surface cannot be blank")
        return value


def install_operator_activity_routes(app: FastAPI, store) -> OperatorActivityLedger:
    """Install server-clocked operator activity telemetry.

    The request deliberately has no duration/timestamp field. Identity and server time are
    authoritative; clients can only signal current foreground activity.
    """
    ledger = OperatorActivityLedger(store)

    @app.post("/api/operator-activity/heartbeat")
    def operator_activity_heartbeat(req: OperatorHeartbeat):
        principal = current_principal()
        try:
            result = ledger.record_heartbeat(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                surface=req.surface,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "recorded": result["recorded"],
            "bucket_seconds": result["bucket_seconds"],
            "client_duration_accepted": False,
            "changes_authority": False,
        }

    app.state.operator_activity_ledger = ledger
    return ledger
