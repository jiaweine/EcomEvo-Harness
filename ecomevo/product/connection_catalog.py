from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ecomevo.runtime.mcp import LEGACY_VERSION, MODERN_VERSION, MCPRegistry, MCPServer, _LegacyRequired


AUTHORITY_LEVELS = {
    "authoritative_internal",
    "controlled_knowledge",
    "submitted_evidence",
    "external_public",
    "unknown",
}
FRESHNESS_LEVELS = {"live", "cached", "unknown"}
ACCESS_SCOPES = {"read_only", "governed_write", "mixed", "unknown"}
IDEMPOTENCY_LEVELS = {"required", "supported", "not_applicable", "unknown"}


def _enum(value: Any, allowed: set[str], default: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _safe_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if not text or any(ord(ch) < 32 for ch in text):
        return ""
    return text[:limit]


def _safe_tags(value: Any, limit: int = 50) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for item in value:
        tag = _safe_text(item, 120)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= limit:
            break
    return tags


class MCPConnectionCatalog:
    """Read-only governance and reliability view over deployment-scoped MCP configuration.

    Deployment configuration remains server-owned. The catalog never exposes endpoint
    URLs, token environment names or secret values. Discovery is limited to MCP
    `tools/list`; it never invokes `tools/call`. Optional probe history persists only
    sanitized operational telemetry and schema fingerprints.
    """

    def __init__(self, registry: MCPRegistry, history_path: str | Path | None = None):
        self.registry = registry
        self.history_path = Path(history_path) if history_path else None
        if self.history_path is not None:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_history()

    def _history_conn(self) -> sqlite3.Connection:
        if self.history_path is None:
            raise RuntimeError("connection probe history is not configured")
        db = sqlite3.connect(str(self.history_path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _init_history(self) -> None:
        with self._history_conn() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS connection_probe_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connection_key TEXT NOT NULL,
                    checked_at REAL NOT NULL,
                    state TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    protocol TEXT NOT NULL DEFAULT '',
                    discovered_tools INTEGER NOT NULL DEFAULT 0,
                    schema_fingerprint TEXT NOT NULL DEFAULT '',
                    schema_change TEXT NOT NULL DEFAULT 'unknown',
                    public_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_connection_probe_history_key_id
                    ON connection_probe_history(connection_key,id DESC);
                """
            )

    @staticmethod
    def _metadata() -> dict[str, dict[str, Any]]:
        raw = os.environ.get("ECOMEVO_MCP_CONNECTION_META", "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except Exception:
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(key): row for key, row in value.items() if isinstance(row, dict)}

    def _declared_tools(self, server_key: str) -> dict[str, dict[str, Any]]:
        tools: dict[str, dict[str, Any]] = {}
        for row in self.registry.read_tool_specs():
            if str(row.get("server") or "") != server_key:
                continue
            name = str(row.get("tool") or "").strip()
            if not name:
                continue
            tools[name] = {
                "name": name,
                "capability": "read",
                "risk": "read_only",
                "declared": True,
                "bindings": [str(row.get("key") or "read")],
                "domains": [str(row.get("domain") or "general")],
                "evidence_tags": _safe_tags(row.get("evidence_tags")),
                "idempotency": "not_applicable",
            }
        for action_kind, row in self.registry.action_map.items():
            if not isinstance(row, dict) or str(row.get("server") or "") != server_key:
                continue
            name = str(row.get("tool") or "").strip()
            if not name:
                continue
            action_idempotency = _enum(row.get("idempotency"), IDEMPOTENCY_LEVELS)
            if action_idempotency == "not_applicable":
                action_idempotency = "unknown"
            current = tools.get(name)
            if current is None:
                current = {
                    "name": name,
                    "capability": "governed_action",
                    "risk": "governed_side_effect",
                    "declared": True,
                    "bindings": [],
                    "domains": [],
                    "evidence_tags": _safe_tags(row.get("evidence_tags")),
                    "idempotency": action_idempotency,
                }
                tools[name] = current
            else:
                # A tool that is also configured as an action must be presented with
                # the stricter capability. The console never treats action bindings as read-only.
                current["capability"] = "governed_action"
                current["risk"] = "governed_side_effect"
                current["idempotency"] = action_idempotency
                current["evidence_tags"] = sorted(
                    set(current.get("evidence_tags", [])) | set(_safe_tags(row.get("evidence_tags")))
                )
            binding = f"action:{action_kind}"
            if binding not in current["bindings"]:
                current["bindings"].append(binding)
        return tools

    @staticmethod
    def _effective_scope(declared: dict[str, dict[str, Any]]) -> str:
        capabilities = {str(row.get("capability") or "unknown") for row in declared.values()}
        has_read = "read" in capabilities
        has_write = "governed_action" in capabilities
        if has_read and has_write:
            return "mixed"
        if has_write:
            return "governed_write"
        if has_read:
            return "read_only"
        return "unknown"

    @staticmethod
    def _governance_warnings(
        *,
        declared_scope: str,
        effective_scope: str,
        credential_owner: str,
        auth_configured: bool,
        tools: dict[str, dict[str, Any]],
    ) -> list[str]:
        warnings: list[str] = []
        if declared_scope == "unknown":
            warnings.append("access_scope_not_declared")
        if declared_scope == "read_only" and effective_scope in {"governed_write", "mixed"}:
            warnings.append("declared_read_only_but_action_binding_exists")
        if declared_scope == "governed_write" and effective_scope == "mixed":
            warnings.append("declared_write_scope_omits_read_bindings")
        if auth_configured and not credential_owner:
            warnings.append("credential_owner_not_declared")
        if any(
            row.get("capability") == "governed_action" and row.get("idempotency") == "unknown"
            for row in tools.values()
        ):
            warnings.append("governed_action_idempotency_not_declared")
        return warnings

    def _connection(self, server: MCPServer) -> dict[str, Any]:
        declared = self._declared_tools(server.key)
        read_count = sum(1 for row in declared.values() if row["capability"] == "read")
        action_count = sum(1 for row in declared.values() if row["capability"] == "governed_action")
        meta = self._metadata().get(server.key, {})
        declared_scope = _enum(meta.get("access_scope"), ACCESS_SCOPES)
        effective_scope = self._effective_scope(declared)
        auth_configured = bool(server.token_env and os.environ.get(server.token_env))
        credential_owner = _safe_text(meta.get("credential_owner"), 240)
        connection_tags = set(_safe_tags(meta.get("evidence_tags")))
        for tool in declared.values():
            connection_tags.update(tool.get("evidence_tags", []))
        connection_idempotency = _enum(meta.get("idempotency"), IDEMPOTENCY_LEVELS)
        if not action_count:
            connection_idempotency = "not_applicable"
        elif connection_idempotency == "not_applicable":
            connection_idempotency = "unknown"
        if connection_idempotency in {"required", "supported"}:
            for tool in declared.values():
                if tool.get("capability") == "governed_action" and tool.get("idempotency") == "unknown":
                    tool["idempotency"] = connection_idempotency
        warnings = self._governance_warnings(
            declared_scope=declared_scope,
            effective_scope=effective_scope,
            credential_owner=credential_owner,
            auth_configured=auth_configured,
            tools=declared,
        )
        row = {
            "key": server.key,
            "name": server.name,
            "enabled": bool(server.enabled),
            "configured": True,
            "transport": "streamable_http",
            "scope": "deployment",
            "authority": _enum(meta.get("authority"), AUTHORITY_LEVELS),
            "freshness": _enum(meta.get("freshness"), FRESHNESS_LEVELS),
            "auth_configured": auth_configured,
            "health": {"state": "not_checked" if server.enabled else "disabled"},
            "governance": {
                "data_source": _safe_text(meta.get("data_source"), 240) or server.name,
                "declared_access_scope": declared_scope,
                "effective_tool_scope": effective_scope,
                "credential_owner": credential_owner or None,
                "evidence_tags": sorted(connection_tags),
                "idempotency": connection_idempotency,
                "warnings": warnings,
            },
            "summary": {
                "declared_tools": len(declared),
                "read_tools": read_count,
                "governed_actions": action_count,
            },
            "tools": sorted(declared.values(), key=lambda item: item["name"]),
        }
        reliability = self._reliability(server.key)
        if reliability["observations"]:
            row["reliability"] = reliability
        return row

    def list(self) -> dict[str, Any]:
        connections = [self._connection(server) for server in self.registry.servers.values()]
        connections.sort(key=lambda row: (not row["enabled"], row["name"], row["key"]))
        return {
            "scope": "deployment",
            "connections": connections,
            "count": len(connections),
            "safety": {
                "probe_method": "tools/list",
                "business_tool_execution": False,
                "secrets_exposed": False,
                "authority_override": False,
                "configuration_mutation": False,
            },
        }

    def get(self, key: str) -> dict[str, Any]:
        server = self.registry.servers.get(key)
        if server is None:
            raise KeyError(key)
        return self._connection(server)

    async def _discover(self, server: MCPServer) -> dict[str, Any]:
        try:
            result = await self.registry._modern_request(
                server, "tools/list", {}, allow_legacy_probe=True
            )
            return {"protocol": MODERN_VERSION, "tools": result.get("tools", [])}
        except _LegacyRequired:
            version, session = self.registry._legacy_sessions.get(server.key) or await self.registry._legacy_initialize(server)
            rid = uuid.uuid4().hex
            payload = {"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": {}}
            headers = {**self.registry._auth_headers(server), "MCP-Protocol-Version": version or LEGACY_VERSION}
            if session:
                headers["MCP-Session-Id"] = session
            response, data = await self.registry._post(server, payload, headers)
            result = self.registry._result_or_raise(response, data)
            return {"protocol": version or LEGACY_VERSION, "tools": result.get("tools", [])}

    @staticmethod
    def _schema_fingerprint(discovered_rows: list[Any]) -> str:
        projected: list[dict[str, Any]] = []
        for row in discovered_rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            projected.append(
                {
                    "name": name,
                    "inputSchema": row.get("inputSchema") if isinstance(row.get("inputSchema"), dict) else {},
                    "outputSchema": row.get("outputSchema") if isinstance(row.get("outputSchema"), dict) else {},
                }
            )
        projected.sort(key=lambda item: item["name"])
        canonical = json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _previous_schema_fingerprint(self, key: str) -> str:
        if self.history_path is None:
            return ""
        with self._history_conn() as db:
            row = db.execute(
                "SELECT schema_fingerprint FROM connection_probe_history "
                "WHERE connection_key=? AND state='healthy' AND schema_fingerprint<>'' "
                "ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
        return str(row["schema_fingerprint"] or "") if row else ""

    def _record_probe(
        self,
        *,
        key: str,
        checked_at: float,
        state: str,
        latency_ms: float,
        protocol: str = "",
        discovered_tools: int = 0,
        schema_fingerprint: str = "",
        schema_change: str = "unknown",
        public_error: str = "",
    ) -> None:
        if self.history_path is None:
            return
        with self._history_conn() as db:
            db.execute(
                "INSERT INTO connection_probe_history("
                "connection_key,checked_at,state,latency_ms,protocol,discovered_tools,"
                "schema_fingerprint,schema_change,public_error"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    float(checked_at),
                    state,
                    float(latency_ms),
                    protocol,
                    int(discovered_tools),
                    schema_fingerprint,
                    schema_change,
                    public_error,
                ),
            )

    def _history_rows(self, key: str, limit: int = 50) -> list[dict[str, Any]]:
        if self.history_path is None:
            return []
        bounded = max(1, min(200, int(limit)))
        with self._history_conn() as db:
            rows = db.execute(
                "SELECT id,checked_at,state,latency_ms,protocol,discovered_tools,"
                "schema_fingerprint,schema_change,public_error "
                "FROM connection_probe_history WHERE connection_key=? "
                "ORDER BY id DESC LIMIT ?",
                (key, bounded),
            ).fetchall()
        return [dict(row) for row in rows]

    def _reliability(self, key: str, limit: int = 50) -> dict[str, Any]:
        rows = self._history_rows(key, limit)
        observations = len(rows)
        healthy = sum(1 for row in rows if row["state"] == "healthy")
        unhealthy = sum(1 for row in rows if row["state"] == "unhealthy")
        latencies = sorted(float(row["latency_ms"]) for row in rows if row["latency_ms"] is not None)
        p95 = None
        average = None
        maximum = None
        if latencies:
            average = round(sum(latencies) / len(latencies), 1)
            maximum = round(max(latencies), 1)
            p95 = round(latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)], 1)
        latest = rows[0] if rows else None
        latest_schema_change = next(
            (row for row in rows if row.get("schema_change") == "changed"),
            None,
        )
        return {
            "window": f"last_{limit}_probes",
            "observations": observations,
            "healthy": healthy,
            "unhealthy": unhealthy,
            "success_rate": round(healthy / observations, 4) if observations else None,
            "failure_rate": round(unhealthy / observations, 4) if observations else None,
            "latency_ms": {
                "average": average,
                "p95": p95,
                "max": maximum,
            },
            "latest_checked_at": latest["checked_at"] if latest else None,
            "latest_state": latest["state"] if latest else None,
            "latest_schema_fingerprint": latest["schema_fingerprint"] if latest else "",
            "latest_schema_change": latest["schema_change"] if latest else "unknown",
            "last_schema_change_at": latest_schema_change["checked_at"] if latest_schema_change else None,
        }

    def history(self, key: str, limit: int = 50) -> dict[str, Any]:
        if key not in self.registry.servers:
            raise KeyError(key)
        rows = self._history_rows(key, limit)
        return {
            "key": key,
            "scope": "deployment",
            "reliability": self._reliability(key, limit),
            "observations": rows,
            "safety": {
                "business_tool_execution": False,
                "secrets_exposed": False,
                "configuration_mutation": False,
            },
        }

    async def probe(self, key: str) -> dict[str, Any]:
        server = self.registry.servers.get(key)
        if server is None:
            raise KeyError(key)
        if not server.enabled:
            return {**self._connection(server), "checked_at": time.time()}

        started = time.perf_counter()
        checked_at = time.time()
        try:
            discovery = await self._discover(server)
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            declared = self._declared_tools(key)
            merged = []
            discovered_rows = discovery.get("tools", []) if isinstance(discovery.get("tools"), list) else []
            fingerprint = self._schema_fingerprint(discovered_rows)
            previous_fingerprint = self._previous_schema_fingerprint(key)
            if not previous_fingerprint:
                schema_change = "first_observation"
            elif previous_fingerprint == fingerprint:
                schema_change = "unchanged"
            else:
                schema_change = "changed"
            for row in discovered_rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                explicit = declared.pop(name, None)
                if explicit is None:
                    explicit = {
                        "name": name,
                        "capability": "unknown",
                        "risk": "unknown",
                        "declared": False,
                        "bindings": [],
                        "domains": [],
                        "evidence_tags": [],
                        "idempotency": "unknown",
                    }
                explicit["description"] = str(row.get("description") or "").strip()
                merged.append(explicit)
            merged.extend(declared.values())
            base = self._connection(server)
            self._record_probe(
                key=key,
                checked_at=checked_at,
                state="healthy",
                latency_ms=latency_ms,
                protocol=str(discovery.get("protocol") or "unknown"),
                discovered_tools=len(discovered_rows),
                schema_fingerprint=fingerprint,
                schema_change=schema_change,
            )
            return {
                **base,
                "checked_at": checked_at,
                "health": {
                    "state": "healthy",
                    "latency_ms": latency_ms,
                    "protocol": discovery.get("protocol", "unknown"),
                },
                "schema": {
                    "fingerprint": fingerprint,
                    "change": schema_change,
                    "previous_fingerprint": previous_fingerprint or None,
                },
                "reliability": self._reliability(key),
                "summary": {
                    **base["summary"],
                    "discovered_tools": len(discovered_rows),
                },
                "tools": sorted(merged, key=lambda row: row["name"]),
            }
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            public_error = self._public_error(exc)
            self._record_probe(
                key=key,
                checked_at=checked_at,
                state="unhealthy",
                latency_ms=latency_ms,
                public_error=public_error,
            )
            return {
                **self._connection(server),
                "checked_at": checked_at,
                "health": {
                    "state": "unhealthy",
                    "latency_ms": latency_ms,
                    "error": public_error,
                },
                "schema": {
                    "fingerprint": "",
                    "change": "unknown",
                    "previous_fingerprint": self._previous_schema_fingerprint(key) or None,
                },
                "reliability": self._reliability(key),
            }

    @staticmethod
    def _public_error(exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "连接检查超时"
        if isinstance(exc, httpx.HTTPStatusError):
            return "连接检查返回异常状态"
        if isinstance(exc, httpx.HTTPError):
            return "连接检查失败"
        text = str(exc).lower()
        if "not configured" in text:
            return "连接未配置"
        return "MCP 协议检查失败"
