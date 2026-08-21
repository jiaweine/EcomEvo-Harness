from __future__ import annotations

import asyncio
import json
from typing import Any

from ecomevo.models import GoalState, SubAgentResult, ToolResult
from .control_policy import DecisionPolicy


class CognitiveDelegator:
    """Adds model specialists without granting them evidence or action authority."""

    def __init__(self, reviewer, policy: DecisionPolicy):
        self.reviewer = reviewer
        self.policy = policy

    async def _one(self, reasoner, spec: dict[str, Any], results: list[ToolResult], missing: list[str]):
        if reasoner is None:
            return None
        focus = set(spec.get("focus_tools") or []); selected = [r for r in results if not focus or r.tool in focus]
        evidence = [self.policy.digest_tool_result(x) for x in selected[-12:]]
        schema = {"summary": "", "findings": [], "risks": [], "evidence_ids": [], "confidence": 0.0}
        prompt = (f"角色：{spec.get('role')}。问题：{spec.get('question')}\n"
                  "只基于已经核对的工具结果做反证式复核。你的判断不是新证据，不得绕过资料缺口或人工确认。"
                  "找不到支持就保留不确定性。只返回 JSON。\n"
                  f"当前缺口：{json.dumps(missing, ensure_ascii=False)}\n"
                  f"工具结果：{json.dumps(evidence, ensure_ascii=False, default=str)}\n"
                  f"结构：{json.dumps(schema, ensure_ascii=False)}")
        try:
            raw = await reasoner.chat(messages=[
                {"role": "system", "content": "你是只读证据复核 specialist；模型输出不构成独立业务证据。"},
                {"role": "user", "content": prompt}], assets=[], max_tokens=900, temperature=0.0)
            data = self.policy.json_payload(raw)
            if not data:
                return None
            return SubAgentResult(
                agent=str(spec.get("role") or "专项复核")[:80], summary=str(data.get("summary") or "已完成专项复核")[:1000],
                findings=[str(x)[:700] for x in (data.get("findings") or []) if str(x).strip()][:8],
                risks=[str(x)[:500] for x in (data.get("risks") or []) if str(x).strip()][:8],
                evidence_ids=[str(x)[:120] for x in (data.get("evidence_ids") or []) if str(x).strip()][:12],
                confidence=max(0.0, min(.88, float(data.get("confidence", .5) or .5))),
                parent_agent="自主任务控制器", depth=2, children=0)
        except Exception:
            return None

    async def review(self, goal: GoalState, results: list[ToolResult], reasoner, delegations: list[dict[str, Any]],
                     missing: list[str], emit, graph, parent_nodes: list[str]):
        deterministic = await self.reviewer.run(goal.domain, results); model_results = []
        if reasoner is not None and delegations:
            nodes = [graph.add("delegate", d.get("role", "专项复核"), parents=parent_nodes,
                               payload={"question": d.get("question", "")}) for d in delegations]
            rows = await asyncio.gather(*(self._one(reasoner, d, results, missing) for d in delegations))
            for node, row in zip(nodes, rows):
                if row is None:
                    graph.finish(node, "failed")
                else:
                    graph.finish(node, "completed", {"confidence": row.confidence}); model_results.append(row)
            if model_results:
                await emit("agent.delegated", {"reviews": [x.model_dump() for x in model_results]})
        return deterministic + model_results, len(model_results)
