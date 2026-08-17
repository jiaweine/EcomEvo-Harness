from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Mapping


ROLE_RANK = {"viewer": 10, "operator": 20, "approver": 30, "admin": 40}


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    role: str
    auth_mode: str = "local"

    def can(self, required: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK.get(required, 10**9)


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = int(status)
        self.detail = detail


_current: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("ecomevo_principal", default=None)


def active_principal() -> Principal | None:
    return _current.get()


def current_principal() -> Principal:
    return active_principal() or Principal("local", "local-admin", "admin", "local")


def canonical_request(method: str, path: str, tenant: str, user: str, role: str, timestamp: str) -> bytes:
    return "\n".join([method.upper(), path, tenant, user, role, timestamp]).encode("utf-8")


def sign_request(secret: str, method: str, path: str, tenant: str, user: str, role: str, timestamp: str) -> str:
    return hmac.new(secret.encode("utf-8"), canonical_request(method, path, tenant, user, role, timestamp), hashlib.sha256).hexdigest()


def _header(headers: Mapping, name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        key_text = key.decode("latin1") if isinstance(key, bytes) else str(key)
        if key_text.lower() != target:
            continue
        return value.decode("latin1") if isinstance(value, bytes) else str(value)
    return ""


def authenticate(method: str, path: str, headers: Mapping, *, now: float | None = None) -> Principal:
    mode = os.environ.get("ECOMEVO_AUTH_MODE", "local").strip().lower() or "local"
    if mode == "local":
        role = os.environ.get("ECOMEVO_LOCAL_ROLE", "admin").strip().lower() or "admin"
        if role not in ROLE_RANK:
            role = "admin"
        return Principal(
            os.environ.get("ECOMEVO_LOCAL_TENANT", "local").strip() or "local",
            os.environ.get("ECOMEVO_LOCAL_USER", "local-admin").strip() or "local-admin",
            role,
            "local",
        )
    if mode != "hmac":
        raise AuthError(503, "身份认证模式配置无效")

    secret = os.environ.get("ECOMEVO_AUTH_HMAC_SECRET", "")
    if len(secret) < 32:
        raise AuthError(503, "身份认证密钥未安全配置")
    tenant = _header(headers, "x-ecomevo-tenant").strip()
    user = _header(headers, "x-ecomevo-user").strip()
    role = _header(headers, "x-ecomevo-role").strip().lower()
    timestamp = _header(headers, "x-ecomevo-timestamp").strip()
    signature = _header(headers, "x-ecomevo-signature").strip().lower()
    if not tenant or not user or role not in ROLE_RANK or not timestamp or not signature:
        raise AuthError(401, "缺少有效的工作区身份签名")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise AuthError(401, "身份签名时间无效") from exc
    current = time.time() if now is None else float(now)
    try:
        configured_skew = int(os.environ.get("ECOMEVO_AUTH_MAX_SKEW_SECONDS", "300"))
    except (TypeError, ValueError):
        configured_skew = 300
    max_skew = max(30, min(900, configured_skew))
    if abs(current - ts) > max_skew:
        raise AuthError(401, "身份签名已过期")
    expected = sign_request(secret, method, path, tenant, user, role, timestamp)
    if not hmac.compare_digest(expected, signature):
        raise AuthError(401, "工作区身份签名无效")
    return Principal(tenant, user, role, "hmac")


def required_role(method: str, path: str) -> str:
    method = method.upper()
    if path.startswith("/api/runtime") or path.startswith("/api/evolution"):
        return "admin"
    if path.startswith("/api/actions/") and path.endswith("/decision"):
        return "approver"
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "operator"
    return "viewer"


class IdentityMiddleware:
    """ASGI identity/RBAC boundary for every data/control HTTP and WebSocket path."""

    def __init__(self, app, store=None):
        self.app = app
        self.store = store

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        path = str(scope.get("path") or "")
        protected = path.startswith("/api/") or path.startswith("/ws/")
        if kind not in {"http", "websocket"} or not protected:
            return await self.app(scope, receive, send)
        method = str(scope.get("method") or "GET") if kind == "http" else "GET"
        headers = dict(scope.get("headers") or [])
        try:
            principal = authenticate(method, path, headers)
            need = required_role(method, path)
            if not principal.can(need):
                raise AuthError(403, f"当前角色无权执行该操作，需要 {need} 权限")
            # Runtime event IDs are global; in hardened mode require an explicit durable
            # conversation→session ownership mapping before exposing a session trace.
            marker = "/api/runtime/sessions/"
            if principal.auth_mode == "hmac" and path.startswith(marker) and path.endswith("/events"):
                session_id = path[len(marker):-len("/events")].strip("/")
                checker = getattr(self.store, "session_belongs_to_tenant", None)
                if not session_id or checker is None or not checker(session_id, principal.tenant_id):
                    raise AuthError(404, "运行记录不存在")
        except AuthError as exc:
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 4403 if exc.status == 403 else 4401, "reason": exc.detail})
                return
            body = json.dumps({"detail": exc.detail}, ensure_ascii=False).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": exc.status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"cache-control", b"private, no-store")],
            })
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})["principal"] = principal
        token = _current.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
