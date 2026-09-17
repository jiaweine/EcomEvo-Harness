from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import EcomEvoEngine as BaseEcomEvoEngine
from .policy_context import current_policy_scope
from .policy_control import PolicyStore
from .policy_view import runtime_policy_view
from .tools import PolicyLookupTool


class VersionedPolicyLookupTool(PolicyLookupTool):
    """Policy lookup that preserves audit provenance but limits runtime rule prose."""

    async def execute(self, ctx, args):
        bound_scope = current_policy_scope()
        if bound_scope:
            safe_ctx = dict(ctx)
            requested = dict(ctx.get("policy_scope") or args.get("scope") or {})
            # Durable identity scope is authoritative. Planner/model arguments can add
            # narrower business dimensions but can never override the tenant boundary.
            requested.update(bound_scope)
            safe_ctx["policy_scope"] = requested
            ctx = safe_ctx
        return runtime_policy_view(await super().execute(ctx, args))


class EcomEvoEngine(BaseEcomEvoEngine):
    """Runtime engine with the built-in versioned policy control plane attached.

    Explicit tool-registry plugins remain authoritative: if callers inject a custom
    ``tool.registry`` this wrapper does not mutate or wrap it. The built-in registry,
    however, keeps its original plugin identity while its ``policy.lookup`` tool is
    bound to a PolicyStore sharing the runtime database.
    """

    GENERAL_POLICY_ID = "builtin.general"

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
        self._ensure_general_policy()
        if not custom_tool_registry:
            self.tools.policies = self.policies
            self.tools.tools["policy.lookup"] = VersionedPolicyLookupTool(self.policies)

    def _ensure_general_policy(self) -> None:
        """Preserve the legacy read-only GENERAL-domain policy fallback.

        The previous in-code lookup used risk-review rules whenever a parsed domain had
        no dedicated entry. Versioned resolution must therefore seed an explicit general
        baseline instead of turning ordinary read-only analysis into `policy missing`.
        """
        if self.policies.list_versions(policy_id=self.GENERAL_POLICY_ID):
            return
        try:
            self.policies.create_version(
                policy_id=self.GENERAL_POLICY_ID,
                domain="general",
                rules=[
                    "风险结论至少需要两个独立信号或一条强证据",
                    "模型/规则命中只能作为线索，最终处置需结合业务事实",
                ],
                controls={
                    "risk.minimum_independent_signals": 2,
                    "model_signal.authority": "lead_only",
                },
                scope={},
                authority=50,
                priority=0,
                status="active",
                effective_from="1970-01-01T00:00:00Z",
                owner="system",
                approver="system",
                source="builtin:v1",
            )
        except Exception:
            # Multiple runtime processes can initialize the same SQLite file together.
            # If another process won the seed race, the postcondition is already met.
            if not self.policies.list_versions(policy_id=self.GENERAL_POLICY_ID):
                raise
