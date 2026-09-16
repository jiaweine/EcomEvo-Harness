from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import EcomEvoEngine as BaseEcomEvoEngine
from .policy_control import PolicyStore
from .tools import PolicyLookupTool


class EcomEvoEngine(BaseEcomEvoEngine):
    """Runtime engine with the built-in versioned policy control plane attached.

    Explicit tool-registry plugins remain authoritative: if callers inject a custom
    ``tool.registry`` this wrapper does not mutate or wrap it. The built-in registry,
    however, keeps its original plugin identity while its ``policy.lookup`` tool is
    bound to a PolicyStore sharing the runtime database.
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
        custom_tool_registry = "tool.registry" in overrides
        super().__init__(
            db_path,
            mcp=mcp,
            model_gateway=model_gateway,
            plugin_overrides=overrides,
        )
        self.policies = PolicyStore(db_path)
        if not custom_tool_registry:
            self.tools.policies = self.policies
            self.tools.tools["policy.lookup"] = PolicyLookupTool(self.policies)
