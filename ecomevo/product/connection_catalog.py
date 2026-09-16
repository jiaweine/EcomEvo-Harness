from __future__ import annotations

import time
from typing import Any

import httpx

from ecomevo.runtime.mcp import MCPRegistry, MCPServer


AUTHORITY_LEVELS = {
    "authoritative_internal",
    "controlled_knowledge",
    "submitted_evidence",
    "external_public",
    "unknown",
}
FRESHNESS_LEVELS = {"live", "cached", "unknown"}


def _enum(value: Any, allowed: set[str], default: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


class MCPConnectionCatalog:
    """Read-only product view over deployment-scoped MCP configuration.

    The catalog never exposes endpoint URLs, token environment names or secret values.
    Discovery is limited to MCP `tools/list`; it never invokes `tools/call`.
    """

    def __init__(self, registry: MCPRegistry):
        self.registry = registry

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
            }
        for action_kind, row in self.registry.action_map.items():
            if not isinstance(row, dict) or str(row.get("server") or "") != server_key:
                continue
            name = str(row.get("tool") or "").strip()
            if not name:
                continue
            current = tools.get(name)
            if current is None:
                current = {
                    "name": name,
                    "capability": "governed_action",
                    "risk": "governed_side_effect",
                    "declared": True,
                    "bindings": [],
                    "domains": [],
                }
                tools[name] = current
            else:
                # A tool that is also configured as an action must be presented with
                # the stricter capability. The console never treats action bindings as read-only.
                current["capability"] = "governed_action"
                current["risk"] = "governed_side_effect"
            binding = f"action:{action_kind}"
            if binding not in current["bindings"]:
                current["bindings"].append(binding)
        return tools

    def _connection(self, server: MCPServer) -> dict[str, Any]:
        declared = self._declared_tools(server.key)
        read_count = sum(1 for row in declared.values() if row["capability"] == "read")
        action_count = sum(1 for row in declared.values() if row["capability"] == "governed_action")
        return {
            "key": server.key,
            "name": server.name,
            "enabled": bool(server.enabled),
            "configured": True,
            "transport": "streamable_http",
            "scope": "deployment",
            "authority": _enum(getattr(server, "authority", "unknown"), AUTHORITY_LEVELS),
            "freshness": _enum(getattr(server, "freshness", "unknown"), FRESHNESS_LEVELS),
            "auth_configured": bool(server.token_env and self.registry.has_server_credential(server.key)),
            "health": {"state": "not_checked" if server.enabled else "disabled"},
            "summary": {
                "declared_tools": len(declared),
                "read_tools": read_count,
                "governed_actions": action_count,
            },
            "tools": sorted(declared.values(), key=lambda row: row["name"]),
        }

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
            },
        }

    def get(self, key: str) -> dict[str, Any]:
        server = self.registry.servers.get(key)
        if server is None:
            raise KeyError(key)
        return self._connection(server)

    async def probe(self, key: str) -> dict[str, Any]:
        server = self.registry.servers.get(key)
        if server is None:
            raise KeyError(key)
        if not server.enabled:
            return {**self._connection(server), "checked_at": time.time()}

        started = time.perf_counter()
        checked_at = time.time()
        try:
            discovery = await self.registry.discover_tools(key)
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            declared = self._declared_tools(key)
            merged = []
            for row in discovery.get("tools", []):
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
                    }
                explicit["description"] = str(row.get("description") or "").strip()
                merged.append(explicit)
            merged.extend(declared.values())
            base = self._connection(server)
            return {
                **base,
                "checked_at": checked_at,
                "health": {
                    "state": "healthy",
                    "latency_ms": latency_ms,
                    "protocol": discovery.get("protocol", "unknown"),
                },
                "summary": {
                    **base["summary"],
                    "discovered_tools": len(discovery.get("tools", [])),
                },
                "tools": sorted(merged, key=lambda row: row["name"]),
            }
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            return {
                **self._connection(server),
                "checked_at": checked_at,
                "health": {
                    "state": "unhealthy",
                    "latency_ms": latency_ms,
                    "error": self._public_error(exc),
                },
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
