from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from ecomevo.product.connection_catalog import MCPConnectionCatalog
from ecomevo.runtime.mcp import MCPRegistry


def install_connection_routes(
    app: FastAPI,
    registry: MCPRegistry,
    frontend: Path,
    data_dir: Path | None = None,
) -> None:
    """Install deployment-scoped, admin-only MCP observability routes.

    RBAC is enforced by the existing IdentityMiddleware because every route lives
    under `/api/runtime`. No route in this module can execute an MCP business tool.
    """

    history_path = (data_dir / "connection_governance.db") if data_dir is not None else None
    catalog = MCPConnectionCatalog(registry, history_path=history_path)

    @app.get("/api/runtime/connections/ui", include_in_schema=False)
    def connection_console_ui():
        return FileResponse(frontend / "connections.html")

    @app.get("/api/runtime/connections")
    def connection_list():
        return catalog.list()

    @app.get("/api/runtime/connections/{key}")
    def connection_get(key: str):
        try:
            return catalog.get(key)
        except KeyError as exc:
            raise HTTPException(404, "连接不存在") from exc

    @app.get("/api/runtime/connections/{key}/history")
    def connection_history(key: str):
        try:
            return catalog.history(key)
        except KeyError as exc:
            raise HTTPException(404, "连接不存在") from exc

    @app.post("/api/runtime/connections/{key}/probe")
    async def connection_probe(key: str):
        try:
            return await catalog.probe(key)
        except KeyError as exc:
            raise HTTPException(404, "连接不存在") from exc
