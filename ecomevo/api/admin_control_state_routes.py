from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from ecomevo.identity import current_principal
from ecomevo.product.admin_control_state_manifest import AdminControlStateManifest


def install_admin_control_state_routes(
    app: FastAPI,
    *,
    data_dir: str | Path,
) -> AdminControlStateManifest:
    """Install the admin-only read-only admin control-state manifest endpoint."""

    manifest = AdminControlStateManifest(data_dir)

    @app.get("/api/runtime/readiness/admin-control-state")
    def admin_control_state_manifest():
        current_principal()
        return manifest.snapshot()

    return manifest
