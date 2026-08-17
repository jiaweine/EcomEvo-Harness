from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from ecomevo.models import EvolutionPatch, RuntimeSummary
from .counterfactual_routing import CounterfactualAdaptiveAutonomousController
from .event_store import EventStore
from .evolver import FailureDrivenEvolver
from .memory import RuntimeMemory
from .governance import GovernanceBoundary
from .planner import AdaptivePlanner
from .plugins import PluginRegistry
from .recursive import RecursiveCoordinator
from .resilient_executor import ResilientPTCExecutor
from .sandbox import ActionSandbox
from .skills import AdaptiveSkillLibrary
from .tools import ToolRegistry
from .verifier import DecisionVerifier

EventSink=Callable[[str,dict[str,Any]],Awaitable[None]]


class EcomEvoEngine:
    def __init__(self,db_path:str|Path,mcp=None,model_gateway=None):
        self.events=EventStore(db_path)
        self.skills=AdaptiveSkillLibrary(db_path)
        self.planner=AdaptivePlanner()
        self.sandbox=ActionSandbox()
        self.tools=ToolRegistry(mcp)
        self.ptc=ResilientPTCExecutor(self.tools,self.sandbox)
        self.mcp=mcp
        self.model_gateway=model_gateway
        self.recursive=RecursiveCoordinator()
        self.verifier=DecisionVerifier()
        self.evolver=FailureDrivenEvolver(self.skills)
        self.memory=RuntimeMemory()
        self.autonomy=CounterfactualAdaptiveAutonomousController(
            self.planner,self.tools,self.ptc,self.sandbox,self.verifier,self.recursive,self.skills
        )
        self.plugins=PluginRegistry();self._register_plugins()
        for patch_row in reversed(self.events.list_patches(300)):
            self.planner.apply_evolution_patch(patch_row)
            try:self.evolver.ingest(EvolutionPatch(**patch_row))
            except Exception:pass
        for row in reversed(self.events.recent_completed(300)):
            payload=row.get('payload') or {}
            if payload.get('status')!='completed':continue
            belief=payload.get('belief') or {}
            self.memory.add({'session_id':row.get('session_id'),'domain':payload.get('domain'),'score':payload.get('verifier_score',0),'risks':belief.get('risks') or []})

    def _register_plugins(self):
        self.plugins.register('model.gateway','model','认知引擎服务层','云端 / 企业兼容 / 开源权重 / 自托管',instance=self.model_gateway)
        self.plugins.register('agent.autonomy','agent','自主任务控制器','动态任务图、Adaptive Posterior Routing、反事实 credit、重规划与只读 specialist 委派',instance=self.autonomy)
        self.plugins.register('tool.ptc','tool','并行工具执行器','有界并发、超时隔离与组合并发的只读工具调用',instance=self.ptc)
        self.plugins.register('memory.runtime','memory','任务记忆','保存同类任务的已验证结果',instance=self.memory)
        self.plugins.register('memory.skills','memory','自进化技能库','持久化技能、后验可信度、质量多样性 niche 与晋升/退役',instance=self.skills)
        self.plugins.register('sandbox.action','sandbox','操作安全区','阻止未确认的高影响动作',instance=self.sandbox)
        self.plugins.register('verifier.decision','verifier','结果复核器','检查证据、约束与副作用',instance=self.verifier)
        self.plugins.register('evolver.failure','skill','自进化执行层','失败诊断、成功轨迹蒸馏、shadow replay 与回归门禁',instance=self.evolver)
        self.plugins.register('mcp.remote','tool','企业工具连接器','接入企业内部工具与数据服务',instance=self.mcp)

    async def _emit(self,sid:str,t:str,p:dict[str,Any],sink:EventSink|None):
        ev=self.events.append(sid,t,p)
        if sink:await sink(t,p)
        return ev

    async def run(self,text:str,assets:list[dict[str,Any]],sink:EventSink|None=None,domain_hint:str|None=None,context_text:str|None=None,reasoner=None)->RuntimeSummary:
        started=time.perf_counter()
        if reasoner is None and self.model_gateway is not None and hasattr(self.model_gateway,'current_provider'):
            try:reasoner=self.model_gateway.current_provider()
            except Exception:reasoner=None
        sid=f'run-{uuid.uuid4().hex[:12]}';goal=self.planner.parse_goal(text,assets,domain_hint=domain_hint);belief=self.planner.initial_belief(goal,assets)
        autonomy_mode='model_controller' if reasoner is not None else 'deterministic_fallback'
        belief.facts['autonomy_mode']=autonomy_mode
        if context_text:
            from .tools import _query_terms
            belief.facts['conversation_context_terms']=_query_terms(context_text,limit=20)
        prior_cases=self.memory.relevant(goal.domain.value,limit=6);memory_risks=list(dict.fromkeys(r for case in prior_cases for r in (case.get('risks') or [])))[:8]
        if prior_cases:
            belief.facts['memory_case_count']=len(prior_cases);belief.facts['memory_watch_terms']=memory_risks
        self.events.create_session(sid,meta={'domain':goal.domain.value,'goal':text[:220]})
        await self._emit(sid,'goal.parsed',goal.model_dump(mode='json'),sink);await self._emit(sid,'belief.updated',belief.model_dump(),sink)
        if prior_cases:await self._emit(sid,'memory.recalled',{'case_count':len(prior_cases),'watch_terms':memory_risks,'usage':'planning_only_not_evidence'},sink)
        self.events.save_snapshot(sid,2,{'goal':goal.model_dump(),'belief':belief.model_dump(),'stage':'initial'})
        ctx={'text':text,'assets':assets,'goal':goal,'belief':belief}
        async def controller_emit(t,p):await self._emit(sid,t,p,sink)
        outcome=await self.autonomy.run(goal=goal,belief=belief,assets=assets,text=text,context=ctx,reasoner=reasoner,emit=controller_emit)
        tool_results=outcome.tool_results;agents=outcome.agents;verification=outcome.verification
        findings=[];risks=[]
        for x in agents:findings.extend(x.findings);risks.extend(x.risks)

        evolved=False;evolution_events=0
        available_tools=set(self.tools.tools)
        if not verification.passed:
            trajectory={'tool_sequence':[x.tool for x in tool_results],'missing':verification.missing_evidence,'autonomy_steps':outcome.autonomy_steps,'stagnated':outcome.stagnated,'skills_used':outcome.skills_used}
            patch=await self.evolver.evolve(verification,goal.domain.value,available_tools=available_tools,reasoner=reasoner,trajectory=trajectory)
            if patch:
                equivalent=self.events.save_patch_if_novel(patch)
                if equivalent:
                    if equivalent.get('accepted'):
                        self.planner.apply_evolution_patch(equivalent)
                        try:self.evolver.ingest(EvolutionPatch(**equivalent))
                        except Exception:pass
                    await self._emit(sid,'evolution.reused',{'patch_id':equivalent.get('patch_id'),'target':equivalent.get('target'),'accepted':equivalent.get('accepted',False)},sink)
                else:
                    evolution_events+=1;evolved=bool(patch.accepted)
                    if patch.accepted:self.planner.apply_evolution_patch(patch)
                    skill=self.evolver.ingest(patch)
                    await self._emit(sid,'evolution.patch',{**patch.model_dump(),'skill':skill.as_dict() if skill else None},sink)
        elif outcome.recovery_events>0:
            patch=self.evolver.distill_success(domain=goal.domain.value,goal=goal.primary,used_tools=[x.tool for x in tool_results if x.ok],recovery_events=outcome.recovery_events,verifier_score=verification.score,available_tools=available_tools)
            if patch:
                equivalent=self.events.save_patch_if_novel(patch)
                if equivalent:
                    if equivalent.get('accepted'):
                        try:self.evolver.ingest(EvolutionPatch(**equivalent))
                        except Exception:pass
                    await self._emit(sid,'evolution.reused',{'patch_id':equivalent.get('patch_id'),'target':equivalent.get('target'),'accepted':equivalent.get('accepted',False)},sink)
                else:
                    evolution_events+=1;evolved=evolved or bool(patch.accepted);skill=self.evolver.ingest(patch)
                    await self._emit(sid,'evolution.distilled',{**patch.model_dump(),'skill':skill.as_dict() if skill else None},sink)

        evidence=GovernanceBoundary.evidence(assets,tool_results);actions=GovernanceBoundary.actions(goal.domain,findings,risks,verification)
        final_verify=self.verifier.verify(goal,belief,tool_results,agents,actions);await self._emit(sid,'action.proposed',{'actions':[x.model_dump() for x in actions],'verification':final_verify.model_dump()},sink)
        tool_cost_used=round(sum(max(0.0,float(x.cost or 0.0)) for x in tool_results),3)
        tool_cost_budget=round(max(0.0,float(goal.max_tool_cost)),3)
        tool_cost_remaining=round(max(0.0,tool_cost_budget-tool_cost_used),3)
        if final_verify.passed:
            stop_reason='verified';stop_detail='证据和约束已通过最终验证'
        elif outcome.stagnated:
            stop_reason='stagnated';stop_detail='连续补证没有改变可验证状态'
        elif tool_cost_remaining<=0.05:
            stop_reason='budget_exhausted';stop_detail='本轮只读工具预算已用尽'
        elif outcome.recovery_events>=self.autonomy.max_steps:
            stop_reason='step_limit';stop_detail='已达到本轮自主补证步数上限'
        else:
            stop_reason='evidence_incomplete'
            details=list(final_verify.missing_evidence or final_verify.issues or [])
            stop_detail=('；'.join(str(x) for x in details[:3])[:300] if details else '当前证据仍不足以完成最终验证')
        belief.facts.update({'tool_results':len([x for x in tool_results if x.ok]),'review_count':len(agents),'autonomy_steps':outcome.autonomy_steps,'delegations':outcome.delegations,'skill_count':len(outcome.skills_used),'tool_cost_used':tool_cost_used,'tool_cost_budget':tool_cost_budget,'tool_cost_remaining':tool_cost_remaining,'stop_reason':stop_reason,'evidence_complete':bool(final_verify.evidence_complete)})
        try:
            routing=self.autonomy.policy.routing.snapshot(goal.domain.value)
            belief.facts['routing_policy']={
                'samples':routing.get('samples',0),
                'reward_ewma':routing.get('reward_ewma',0.0),
                'residual_ewma':routing.get('residual_ewma',0.0),
            }
        except Exception:
            pass
        belief.facts['runtime_elapsed_ms']=round((time.perf_counter()-started)*1000.0,2)
        belief.risks=list(dict.fromkeys(risks))[:10];belief.uncertainties=final_verify.missing_evidence;belief.missing_evidence=final_verify.missing_evidence;belief.confidence=round(final_verify.score,3)
        self.skills.record_outcome(outcome.skills_used,success=bool(final_verify.passed),score=final_verify.score,session_id=sid,context={'domain':goal.domain.value,'missing':final_verify.missing_evidence,'recovery_events':outcome.recovery_events,'stop_reason':stop_reason})
        if not outcome.skills_used:self.skills.note_run(goal.domain.value,success=bool(final_verify.passed),skill_used=False)
        if final_verify.passed:self.memory.add({'session_id':sid,'domain':goal.domain.value,'goal':text[:160],'score':final_verify.score,'risks':belief.risks})
        summary=RuntimeSummary(session_id=sid,domain=goal.domain,status='completed' if final_verify.passed else 'needs_evidence',tool_calls=len(tool_results),subagents=len(agents),recovery_events=outcome.recovery_events,verifier_score=final_verify.score,evolved=evolved,event_chain_valid=True,autonomy_steps=outcome.autonomy_steps,delegations=outcome.delegations,evolution_events=evolution_events,skills_used=outcome.skills_used,task_graph=outcome.task_graph,proposed_actions=actions,evidence=evidence,findings=list(dict.fromkeys(findings))[:12],risks=list(dict.fromkeys(risks))[:10],evidence_complete=bool(final_verify.evidence_complete),missing_evidence=list(final_verify.missing_evidence),tool_cost_used=tool_cost_used,tool_cost_budget=tool_cost_budget,tool_cost_remaining=tool_cost_remaining,stop_reason=stop_reason,stop_detail=stop_detail,stagnated=bool(outcome.stagnated),autonomy_mode=autonomy_mode,belief=belief)
        await self._emit(sid,'run.completed',summary.model_dump(mode='json'),sink);summary.event_chain_valid=self.events.verify_chain(sid);return summary