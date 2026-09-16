from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import EcomEvoEngine as BaseEcomEvoEngine
from .policy_control import PolicyStore
from .tools import ToolRegistry


class EcomEvoEngine(BaseEcomEvoEngine):
    """Runtime engine with the built-in versioned policy control plane attached.

    Explicit tool-registry plugins remain authoritative: if callers inject a custom
    ``tool.registry`` this wrapper does not mutate or wrap it. The built-in registry,
    however, shares a PolicyStore with the runtime database so policy versions survive
    restarts and ``policy.lookup`` can resolve the exact effective version at runtime.
    """

    def __init__(
        self,
        db_path: str | Path,
        mcp=None,
        model_gateway=None,
        *,
        plugin_overrides: dict[str, Any] | None = None,
    ):
        overrides = dict(plugin_overrides or {})
        self.policies = PolicyStore(db_path)
        if "tool.registry" not in overrides:
            active_mcp = overrides.get("mcp.remote", mcp)
            overrides["tool.registry"] = ToolRegistry(active_mcp, policies=self.policies)
        super().__init__(
            db_path,
            mcp=mcp,
            model_gateway=model_gateway,
            plugin_overrides=overrides,
        )
