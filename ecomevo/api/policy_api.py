from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ecomevo.identity import current_principal
from ecomevo.runtime.policy_view import runtime_policy_view


def _scope(value: str | None) -> dict[str, str]:
    if value is None or not value.strip():
        return {}
    if len(value) > 4000:
        raise HTTPException(422, "政策 scope 过长")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "政策 scope 必须是 JSON 对象") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(422, "政策 scope 必须是 JSON 对象")
    clean: dict[str, str] = {}
    for key, item in parsed.items():
        name = str(key).strip()
        text = str(item).strip()
        if name and text:
            clean[name] = text
    return clean


def _tenant_scope(value: str | None, tenant_id: str) -> dict[str, str]:
    requested = _scope(value)
    supplied = requested.get("tenant")
    if supplied is not None and supplied != tenant_id:
        raise HTTPException(403, "不能查询其他工作区的政策范围")
    requested["tenant"] = tenant_id
    return requested


def _visible_to_tenant(scope: dict[str, str] | None, tenant_id: str) -> bool:
    policy_tenant = str((scope or {}).get("tenant") or "").strip()
    return not policy_tenant or policy_tenant == "*" or policy_tenant == tenant_id


def build_policy_router(engine: Any) -> APIRouter:
    router = APIRouter(prefix="/api/runtime/policies", tags=["policy-control-plane"])

    @router.get("")
    def list_policy_versions(
        domain: str | None = Query(default=None, max_length=80),
        policy_id: str | None = Query(default=None, max_length=160),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        principal = current_principal()
        rows = [
            row
            for row in engine.policies.list_versions(policy_id=policy_id, domain=domain)
            if _visible_to_tenant(row.scope, principal.tenant_id)
        ]
        return {
            "items": [row.as_dict() for row in rows[:limit]],
            "count": min(len(rows), limit),
            "truncated": len(rows) > limit,
        }

    @router.get("/resolve")
    def resolve_policy(
        domain: str = Query(min_length=1, max_length=80),
        as_of: str | None = Query(default=None, max_length=80),
        scope: str | None = Query(default=None, max_length=4000),
    ):
        principal = current_principal()
        try:
            resolution = engine.policies.resolve(
                domain,
                scope=_tenant_scope(scope, principal.tenant_id),
                as_of=as_of,
            )
        except HTTPException:
            raise
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return runtime_policy_view(resolution)

    return router
