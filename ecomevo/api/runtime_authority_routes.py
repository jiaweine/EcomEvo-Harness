from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from ecomevo.identity import current_principal
from ecomevo.product.runtime_authority_snapshot import RuntimeAuthoritySnapshot


def install_runtime_authority_routes(
    app: FastAPI,
    *,
    db_path: str | Path,
) -> RuntimeAuthoritySnapshot:
    """Install the admin-only read-only runtime authority snapshot endpoint."""

    snapshotter = RuntimeAuthoritySnapshot(db_path)

    @app.get("/api/runtime/readiness/runtime-authority")
    def runtime_authority_snapshot():
        current_principal()
        return snapshotter.snapshot()

    return snapshotter
