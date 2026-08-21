from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .sandbox import ActionSandbox
from .tools import _query_terms


@dataclass(frozen=True)
class ReplayGateResult:
    passed: bool
    replay_cases: int
    regression_before: float
    regression_after: float
    safety_passed: bool
    failures: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HarnessReplayGate:
    """Deterministic, no-side-effect replay gate for declarative Harness edits.

    Replay never invokes a business tool. It re-scores historical evidence gaps against
    the registered read-only catalog, while ActionSandbox and the catalog mode jointly
    reject authority expansion before a candidate can enter shadow traffic.
    """

    FORBIDDEN = {
        "skip approval", "bypass", "ignore evidence", "auto refund", "auto approve",
        "跳过确认", "无需确认", "绕过确认", "忽略证据", "自动退款", "自动下架", "自动通过",
    }

    def __init__(self, sandbox: ActionSandbox | None = None, *, regression_tolerance: float = 0.02):
        self.sandbox = sandbox or ActionSandbox()
        self.regression_tolerance = max(0.0, min(0.25, float(regression_tolerance)))

    @staticmethod
    def _dedupe(values: Iterable[Any]) -> list[str]:
        return list(dict.fromkeys(str(value) for value in values if str(value)))

    def _legal_catalog(self, catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        legal: dict[str, dict[str, Any]] = {}
        for row in catalog:
            tool = str(row.get("tool") or "")
            mode = str(row.get("mode") or "read-only")
            decision = self.sandbox.validate_tool(tool)
            if tool and mode in {"read-only", "mcp-read"} and not row.get("requires_confirmation") and decision.allowed:
                legal[tool] = row
        return legal

    @staticmethod
    def _semantic_score(profile: dict[str, Any], case: dict[str, Any], legal: dict[str, dict[str, Any]]) -> float:
        target = set(_query_terms(" ".join([str(case.get("goal") or ""), *map(str, case.get("missing") or [])]), limit=64))
        preferred = [str(value) for value in profile.get("preferred_tools") or [] if str(value) in legal]
        avoided = {str(value) for value in profile.get("avoid_tools") or []}
        if not preferred:
            preferred = [str(value) for value in case.get("tool_sequence") or [] if str(value) in legal]
        scores: list[float] = []
        for tool in preferred:
            row = legal[tool]
            terms = set(_query_terms(" ".join([str(row.get("purpose") or ""), *map(str, row.get("evidence_tags") or [])]), limit=64))
            overlap = len(target & terms) / max(1, len(target | terms)) if target else 0.0
            cost = max(0.0, float(row.get("cost") or 0.0))
            scores.append(max(0.0, overlap - min(0.2, cost / 50.0) - (0.15 if tool in avoided else 0.0)))
        return max(scores, default=0.0)

    def evaluate(
        self,
        *,
        kind: str,
        base: dict[str, Any],
        candidate: dict[str, Any],
        cases: list[dict[str, Any]],
        tool_catalog: list[dict[str, Any]],
    ) -> ReplayGateResult:
        failures: list[str] = []
        serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True).lower()
        if any(term.lower() in serialized for term in self.FORBIDDEN):
            failures.append("authority_or_evidence_weakening")
        legal = self._legal_catalog(tool_catalog)
        for field in ("preferred_tools", "avoid_tools"):
            illegal = set(map(str, candidate.get(field) or [])) - set(legal)
            if illegal:
                failures.append(f"illegal_{field}:{','.join(sorted(illegal))}")

        usable_cases = [case for case in cases if isinstance(case, dict)]
        if kind == "tool" and usable_cases:
            before_rows = [self._semantic_score(base, case, legal) for case in usable_cases]
            after_rows = [self._semantic_score(candidate, case, legal) for case in usable_cases]
            before = sum(before_rows) / len(before_rows)
            after = sum(after_rows) / len(after_rows)
            if any(a + self.regression_tolerance < b for a, b in zip(after_rows, before_rows)):
                failures.append("historical_case_regression")
        else:
            # Non-tool text coordinates cannot be honestly performance-scored without
            # executing a model. Their replay gate is therefore safety/admissibility only;
            # online verifier cohorts remain the performance gate.
            before = after = 0.0
        safety_passed = not failures
        return ReplayGateResult(
            passed=safety_passed,
            replay_cases=len(usable_cases),
            regression_before=round(before, 6),
            regression_after=round(after, 6),
            safety_passed=safety_passed,
            failures=tuple(failures),
        )
