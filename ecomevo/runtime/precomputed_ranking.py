from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .adaptive_routing import AdaptiveDecisionPolicy


class PrecomputedAdaptiveDecisionPolicy(AdaptiveDecisionPolicy):
    """Reuse pure feature inputs within one candidate-ranking call only.

    This deliberately does not cache across decision rounds. The snapshot lives in a
    ContextVar so concurrent tasks sharing one policy instance cannot see each other's
    candidates, evidence gaps, skills, or previous results.
    """

    def __init__(self, planner, registry, sandbox, skills, *, max_calls: int, max_delegations: int):
        super().__init__(
            planner,
            registry,
            sandbox,
            skills,
            max_calls=max_calls,
            max_delegations=max_delegations,
        )
        self._rank_feature_snapshot: ContextVar[dict[str, Any] | None] = ContextVar(
            f"ecomevo-rank-feature-snapshot-{id(self)}",
            default=None,
        )

    def _prepare_rank_feature_snapshot(
        self,
        *,
        tools: list[str],
        goal: Any,
        missing: list[str],
        previous: list[Any],
        skills: list[Any],
    ) -> dict[str, Any]:
        targets = list(missing) or list(goal.required_evidence)
        target_terms = [self._terms(target) for target in targets]

        # Preserve _tool_meta's first-match semantics while avoiding one full registry
        # scan per candidate. Registry state cannot change during this synchronous call.
        described: dict[str, dict[str, Any]] = {}
        for row in self.registry.describe():
            key = str(row.get("key"))
            described.setdefault(key, row)
        remote: dict[str, dict[str, Any]] = {}
        for row in getattr(self.registry, "remote_specs", []):
            key = str(row.get("key"))
            remote.setdefault(key, row)

        skill_support: dict[str, float] = {}
        for skill in skills:
            posterior = float(skill.posterior_mean)
            for preferred in skill.preferred_tools:
                key = str(preferred)
                if posterior > skill_support.get(key, 0.0):
                    skill_support[key] = posterior

        prior_success: dict[str, int] = {}
        for result in previous:
            if not result.ok:
                continue
            key = str(result.tool)
            prior_success[key] = prior_success.get(key, 0) + 1

        tool_rows: dict[str, dict[str, Any]] = {}
        for tool in dict.fromkeys(str(value) for value in tools if str(value)):
            remote_row = remote.get(tool) or {}
            described_row = described.get(tool) or {}
            evidence_tags = list(remote_row.get("evidence_tags") or [])
            mode = described_row.get("mode", "read-only")
            purpose = str(remote_row.get("purpose") or self.PURPOSES.get(tool) or "读取业务事实")
            channels = set(self.TOOL_CHANNELS.get(tool, set()))
            channels.update(str(value) for value in evidence_tags)
            channel_terms = self._terms(channels | {purpose})
            tool_rows[tool] = {
                "channel_terms": channel_terms,
                "authority": 1.0 if mode == "mcp-read" and evidence_tags else 0.0,
                "skill_support": skill_support.get(tool, 0.0),
                "prior_success": prior_success.get(tool, 0),
                "specificity": min(1.0, len(channel_terms) / 10.0),
            }

        gap_pressure = min(
            1.0,
            len(targets) / max(1, len(goal.required_evidence) or len(targets)),
        )
        return {
            "targets": targets,
            "target_terms": target_terms,
            "has_previous": bool(previous),
            "gap_pressure": gap_pressure,
            "tools": tool_rows,
        }

    def _base_features(
        self,
        *,
        tool: str,
        cost: float,
        goal: Any,
        missing: list[str],
        previous: list[Any],
        skills: list[Any],
        reliability: float,
    ) -> tuple[dict[str, float], set[str]]:
        snapshot = self._rank_feature_snapshot.get()
        prepared = (snapshot or {}).get("tools", {}).get(tool)
        if snapshot is None or prepared is None:
            return super()._base_features(
                tool=tool,
                cost=cost,
                goal=goal,
                missing=missing,
                previous=previous,
                skills=skills,
                reliability=reliability,
            )

        channel_terms = prepared["channel_terms"]
        coverage_scores = []
        for target_terms in snapshot["target_terms"]:
            overlap = target_terms & channel_terms
            if overlap:
                coverage_scores.append(min(1.0, 0.42 + 0.18 * len(overlap)))
            elif tool == "evidence.search":
                coverage_scores.append(0.34)
            else:
                coverage_scores.append(0.0)
        coverage = sum(coverage_scores) / max(1, len(coverage_scores))

        prior_success = int(prepared["prior_success"])
        novelty = 1.0 / (1.0 + 0.72 * prior_success)
        targets = snapshot["targets"]
        contradiction = (
            1.0
            if tool in self.CONTRADICTION_TOOLS
            and bool(targets)
            and (coverage > 0.0 or bool(snapshot["has_previous"]))
            else 0.0
        )
        return {
            "bias": 1.0,
            "coverage": coverage,
            "authority": float(prepared["authority"]),
            "skill_support": float(prepared["skill_support"]),
            "novelty": novelty,
            "contradiction": contradiction,
            "specificity": float(prepared["specificity"]),
            "tool_reliability": max(0.0, min(1.0, float(reliability))),
            "cost_pressure": min(1.0, max(0.0, float(cost)) / 3.0),
            "redundancy": 0.0,
            "gap_pressure": float(snapshot["gap_pressure"]),
            "recovery_context": 1.0 if snapshot["has_previous"] else 0.0,
        }, channel_terms

    def _rank_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        goal: Any,
        missing: list[str],
        previous: list[Any],
        skills: list[Any],
        budget: float,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # For zero/one candidate there is no repeated feature work to amortize. Keep the
        # legacy path so empty/no-op decision rounds do not pay a registry scan merely to
        # build a snapshot that cannot save work.
        if len(candidates) <= 1:
            return super()._rank_candidates(
                candidates,
                goal=goal,
                missing=missing,
                previous=previous,
                skills=skills,
                budget=budget,
                limit=limit,
            )

        snapshot = self._prepare_rank_feature_snapshot(
            tools=[str(candidate.get("tool") or "") for candidate in candidates],
            goal=goal,
            missing=missing,
            previous=previous,
            skills=skills,
        )
        token = self._rank_feature_snapshot.set(snapshot)
        try:
            return super()._rank_candidates(
                candidates,
                goal=goal,
                missing=missing,
                previous=previous,
                skills=skills,
                budget=budget,
                limit=limit,
            )
        finally:
            self._rank_feature_snapshot.reset(token)
