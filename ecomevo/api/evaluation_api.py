from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse


def build_evaluation_router(center: Any, frontend: str | Path) -> APIRouter:
    """Admin-only evaluation surface mounted under the existing /api/runtime RBAC boundary."""

    router = APIRouter(prefix="/api/runtime/evaluations", tags=["evaluation-center"])
    frontend_dir = Path(frontend)
    run_lock = asyncio.Lock()

    @router.get("/ui", include_in_schema=False)
    def evaluation_ui():
        return FileResponse(frontend_dir / "evaluation.html")

    @router.get("/cases")
    def evaluation_cases():
        return center.catalog()

    @router.get("/runs")
    def evaluation_runs(limit: int = Query(default=30, ge=1, le=100)):
        return {"items": center.store.list_runs(limit)}

    @router.get("/runs/{run_id}")
    def evaluation_run(run_id: str):
        if len(run_id) > 80:
            raise HTTPException(404, "评估记录不存在")
        row = center.store.get_run(run_id)
        if row is None:
            raise HTTPException(404, "评估记录不存在")
        return row

    @router.post("/runs", status_code=201)
    async def evaluation_run_create():
        if run_lock.locked():
            raise HTTPException(409, "已有评估正在运行，请等待本轮完成")
        async with run_lock:
            return await center.run()

    return router
