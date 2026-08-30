from __future__ import annotations

import time
import threading
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from ecomevo.models import EvolutionPatch, RuntimeSummary
from .bundled_event_store import BundledEventStore
from .bundled_harness_optimizer import BundledHarnessEvolutionOptimizer
from .bundled_skills import BundledAdaptiveSkillLibrary
from .counterfactual_routing import CounterfactualAdaptiveAutonomousController
from .evolver import FailureDrivenEvolver
from .governance import GovernanceBoundary
from .harness_context import bind_harness_profile, reset_harness_profile
from .memory import RuntimeMemory
from .planner import AdaptivePlanner
from .plugins import (
    PluginContract,
    PluginContractError,
    PluginError,
    PluginLifecycleError,
    PluginRegistry,
)
from .recursive import RecursiveCoordinator
from .resilient_executor import ResilientPTCExecutor
from .sandbox import ActionSandbox
from .tools import ToolRegistry, _query_terms
from .verifier import DecisionVerifier

EventSink=Callable[[str,dict[str,Any]],Awaitable[None]]


class EcomEvoEngine:
    def __init__(
        self,
        db_path: str | Path,
        mcp=None,
        model_gateway=None,
        *,
        plugin_overrides: dict[str, Any] | None = None,
    ):
        overrides = dict(plugin_overrides or {})
        self._plugin_lock = threading.RLock()
        self._active_runs = 0
        self._injected_plugin_keys = set(overrides)

        def component(key: str, factory):
            return overrides[key] if key in overrides else factory()

        self.events = component('event.store', lambda: BundledEventStore(db_path))
        self.skills = component('memory.skills', lambda: BundledAdaptiveSkillLibrary(db_path))
        self.sandbox = component('sandbox.action', ActionSandbox)
        self.harness = component('evolver.harness', lambda: BundledHarnessEvolutionOptimizer(db_path, sandbox=self.sandbox))
        self.planner = component('planner.adaptive', AdaptivePlanner)
        self.mcp = overrides.get('mcp.remote', mcp)
        self.model_gateway = overrides.get('model.gateway', model_gateway)
        self.tools = component('tool.registry', lambda: ToolRegistry(self.mcp))
        self.ptc = component('tool.ptc', lambda: ResilientPTCExecutor(self.tools, self.sandbox))
        self.recursive = component('agent.recursive', RecursiveCoordinator)
        self.verifier = component('verifier.decision', DecisionVerifier)
        self.evolver = component('evolver.failure', lambda: FailureDrivenEvolver(self.skills))
        self.memory = component('memory.runtime', RuntimeMemory)
        self.autonomy = component(
            'agent.autonomy',
            lambda: CounterfactualAdaptiveAutonomousController(
                self.planner, self.tools, self.ptc, self.sandbox,
                self.verifier, self.recursive, self.skills,
            ),
        )
        self.plugins=PluginRegistry(on_change=self._on_plugin_change);self._register_plugins()
        for patch_row in reversed(self.events.list_patches(300)):
            self.planner.apply_evolution_patch(patch_row)
            try:self.evolver.ingest(EvolutionPatch(**patch_row))
            except Exception:pass
        for row in reversed(self.events.recent_completed(300)):
            payload=row.get('payload') or {}
            if payload.get('status')!='completed':continue
            belief=payload.get('belief') or {}
            self.memory.add({'session_id':row.get('session_id'),'domain':payload.get('domain'),'goal':str((row.get('meta') or {}).get('goal') or ''),'score':payload.get('verifier_score',0),'risks':belief.get('risks') or []})

    def _register_plugins(self):
        contracts = {
            'event.store': PluginContract(methods=(
                'append','create_session','save_checkpoint','restore_checkpoint','list_patches',
                'recent_completed','save_patch_if_novel','verify_chain',
            )),
            'model.gateway': PluginContract(methods=('current_provider',)),
            'planner.adaptive': PluginContract(methods=('parse_goal','initial_belief','plan','apply_evolution_patch')),
            'agent.autonomy': PluginContract(methods=('run','rebind')),
            'agent.recursive': PluginContract(methods=('run',)),
            'tool.registry': PluginContract(methods=('planned_calls','describe','set_mcp'),attributes=('tools',)),
            'tool.ptc': PluginContract(methods=('execute',)),
            'memory.runtime': PluginContract(methods=('relevant','add')),
            'memory.skills': PluginContract(methods=('relevant','policy')),
            'evolver.harness': PluginContract(methods=('profile','record_outcome','propose','snapshot')),
            'sandbox.action': PluginContract(methods=('validate_tool',)),
            'verifier.decision': PluginContract(methods=('verify',)),
            'evolver.failure': PluginContract(methods=('evolve','ingest','distill_success')),
            'mcp.remote': PluginContract(methods=('read_tool_specs','call_tool')),
        }

        def register(key, kind, name, description, instance, *, required=True):
            source = 'injected' if key in self._injected_plugin_keys else 'builtin'
            if key in {'model.gateway','mcp.remote'} and instance is not None and key not in self._injected_plugin_keys:
                source = 'configured'
            self.plugins.register(
                key, kind, name, description, instance=instance, required=required,
                source=source, contract=contracts[key],
            )

        register('event.store','state','事件存储','Append-only hash chain、checkpoint、fork 与 runtime patch',self.events)
        register('model.gateway','model','认知引擎服务层','云端 / 企业兼容 / 开源权重 / 自托管',self.model_gateway,required=False)
        register('planner.adaptive','planner','自适应规划器','Goal / Belief State 与证据缺口驱动的候选计划',self.planner)
        register('agent.autonomy','agent','自主任务控制器','动态任务图、Adaptive Posterior Routing、反事实 credit、重规划与只读 specialist 委派',self.autonomy)
        register('agent.recursive','agent','递归专项复核器','有界深度的证据、规则、风险与交叉核对 specialist',self.recursive)
        register('tool.registry','tool','工具注册表','本地只读工具与 MCP read tool 的统一可替换目录',self.tools)
        register('tool.ptc','tool','并行工具执行器','有界并发、超时隔离与组合并发的只读工具调用',self.ptc)
        register('memory.runtime','memory','任务记忆','保存同类任务的已验证结果',self.memory)
        register('memory.skills','memory','自进化技能库','持久化技能、后验可信度、质量多样性 niche 与晋升/退役',self.skills)
        register('evolver.harness','skill','Harness 坐标优化器','Prompt / Tool / Memory / Delegation 单坐标 shadow 进化、Verifier 后验验证与可回滚晋升',self.harness)
        register('sandbox.action','sandbox','操作安全区','阻止未确认的高影响动作',self.sandbox)
        register('verifier.decision','verifier','结果复核器','检查证据、约束与副作用',self.verifier)
        register('evolver.failure','skill','失败轨迹候选生成器','失败诊断、成功轨迹蒸馏与认知策略候选生成',self.evolver)
        register('mcp.remote','tool','企业工具连接器','接入企业内部工具与数据服务',self.mcp,required=False)
        for row in self.plugins.describe():
            self.plugins.validate(row['key'],self.plugins.get(row['key']))

    def _rebind_autonomy(self, **updates: Any) -> None:
        rebind = getattr(self.autonomy, 'rebind', None)
        if not callable(rebind):
            raise PluginContractError(
                'the active agent.autonomy plugin does not support dependency rebinding'
            )
        rebind(**updates)

    def _bind_plugin(self, key: str, instance: Any) -> None:
        if key == 'event.store': self.events = instance
        elif key == 'model.gateway': self.model_gateway = instance
        elif key == 'planner.adaptive':
            self.planner = instance;self._rebind_autonomy(planner=instance)
        elif key == 'agent.autonomy': self.autonomy = instance
        elif key == 'agent.recursive':
            self.recursive = instance;self._rebind_autonomy(reviewer=instance)
        elif key == 'tool.registry':
            self.tools = instance
            if hasattr(self.ptc,'registry'):self.ptc.registry=instance
            self._rebind_autonomy(registry=instance)
        elif key == 'tool.ptc':
            self.ptc = instance;self._rebind_autonomy(executor=instance)
        elif key == 'memory.runtime': self.memory = instance
        elif key == 'memory.skills':
            self.skills = instance
            if hasattr(self.evolver,'skills'):self.evolver.skills=instance
            self._rebind_autonomy(skills=instance)
        elif key == 'evolver.harness': self.harness = instance
        elif key == 'sandbox.action':
            self.sandbox = instance
            if hasattr(self.ptc,'sandbox'):self.ptc.sandbox=instance
            replay_gate=getattr(self.harness,'replay_gate',None)
            if replay_gate is not None and hasattr(replay_gate,'sandbox'):replay_gate.sandbox=instance
            self._rebind_autonomy(sandbox=instance)
        elif key == 'verifier.decision':
            self.verifier = instance;self._rebind_autonomy(verifier=instance)
        elif key == 'evolver.failure': self.evolver = instance
        elif key == 'mcp.remote':
            self.mcp = instance
            self.tools.set_mcp(instance)
        else: raise PluginError(f'unknown runtime plugin slot: {key}')

    def _on_plugin_change(self, key: str, instance: Any, previous: Any, event: str) -> None:
        with self._plugin_lock:
            if self._active_runs:
                raise PluginLifecycleError(
                    f'cannot change plugin {key} while {self._active_runs} task(s) are active'
                )
            try:self._bind_plugin(key,instance)
            except Exception:
                self._bind_plugin(key,previous)
                raise

    def replace_plugin(self, key: str, instance: Any, *, version: str | None = None) -> None:
        """Validate, activate and atomically rebind one runtime plugin slot."""
        self.plugins.replace(key,instance,version=version)

    def discover_plugins(self) -> list[dict[str,str]]:
        """Discover installed plugin packages without importing third-party code."""
        return self.plugins.discover_entry_points()

    def load_plugin(self, name: str) -> dict[str,Any]:
        """Explicitly load and rebind one ``ecomevo.plugins`` entry point."""
        return self.plugins.load_entry_point(name)

    async def _emit(self,sid:str,t:str,p:dict[str,Any],sink:EventSink|None):
        grouped_append=getattr(self.events,'append_grouped',None)
        if sink is None and callable(grouped_append):
            ev=await grouped_append(sid,t,p)
        else:
            ev=self.events.append(sid,t,p)
        if sink:await sink(t,p)
        return ev

    async def run(self,text:str,assets:list[dict[str,Any]],sink:EventSink|None=None,domain_hint:str|None=None,context_text:str|None=None,reasoner=None)->RuntimeSummary:
        with self._plugin_lock:self._active_runs+=1
        try:
            return await self._run_once(text,assets,sink=sink,domain_hint=domain_hint,context_text=context_text,reasoner=reasoner)
        finally:
            with self._plugin_lock:self._active_runs=max(0,self._active_runs-1)

    async def _run_once(self,text:str,assets:list[dict[str,Any]],sink:EventSink|None=None,domain_hint:str|None=None,context_text:str|None=None,reasoner=None)->RuntimeSummary:
        started=time.perf_counter()
        if reasoner is None and self.model_gateway is not None and hasattr(self.model_gateway,'current_provider'):
            try:reasoner=self.model_gateway.current_provider()
            except Exception:reasoner=None
        sid=f'run-{uuid.uuid4().hex[:12]}';goal=self.planner.parse_goal(text,assets,domain_hint=domain_hint);belief=self.planner.initial_belief(goal,assets)
        autonomy_mode='model_controller' if reasoner is not None else 'deterministic_fallback'
        belief.facts['autonomy_mode']=autonomy_mode

        harness_profile=self.harness.profile(goal.domain.value,session_key=sid)
        belief.facts['harness_profile']={
            'component_ids':list(harness_profile.get('component_ids') or []),
            'components':{
                kind:{'component_id':row.get('component_id'),'status':row.get('status'),'generation':row.get('generation')}
                for kind,row in (harness_profile.get('components') or {}).items() if isinstance(row,dict)
            },
        }
        if context_text:
            belief.facts['conversation_context_terms']=_query_terms(context_text,limit=20)
        memory_component=(harness_profile.get('components') or {}).get('memory') or {}
        evolved_memory_terms=[str(x) for x in (memory_component.get('retrieval_terms') or []) if str(x).strip()]
        memory_query=list(dict.fromkeys(_query_terms(goal.primary,limit=20)+evolved_memory_terms))[:32]
        prior_cases=self.memory.relevant(goal.domain.value,limit=6,query_terms=memory_query);memory_risks=list(dict.fromkeys(r for case in prior_cases for r in (case.get('risks') or [])))[:8]
        if prior_cases:
            belief.facts['memory_case_count']=len(prior_cases);belief.facts['memory_watch_terms']=list(dict.fromkeys(memory_risks+evolved_memory_terms))[:16]
        elif evolved_memory_terms:
            belief.facts['memory_watch_terms']=evolved_memory_terms[:16]

        session_meta={'domain':goal.domain.value,'goal':text[:220]}
        goal_payload=goal.model_dump(mode='json')
        belief_payload=belief.model_dump()
        harness_payload={
            'domain':goal.domain.value,
            'component_ids':list(harness_profile.get('component_ids') or []),
            'components':belief.facts['harness_profile']['components'],
            'authority':'cognition-only',
        }
        memory_payload={'case_count':len(prior_cases),'watch_terms':memory_risks,'usage':'planning_only_not_evidence'} if prior_cases else None
        initial_snapshot={'goal':goal.model_dump(),'belief':belief.model_dump(),'stage':'initial'}

        bootstrap_async=getattr(self.events,'create_session_events_checkpoint_async',None)
        bootstrap_bundle=getattr(self.events,'create_session_events_checkpoint',None)
        if sink is None and callable(bootstrap_async):
            initial_events=[
                ('goal.parsed',goal_payload),
                ('belief.updated',belief_payload),
                ('harness.profile.bound',harness_payload),
            ]
            if memory_payload is not None:initial_events.append(('memory.recalled',memory_payload))
            await bootstrap_async(
                sid,
                initial_events,
                initial_snapshot,
                meta=session_meta,
            )
        elif sink is None and callable(bootstrap_bundle):
            initial_events=[
                ('goal.parsed',goal_payload),
                ('belief.updated',belief_payload),
                ('harness.profile.bound',harness_payload),
            ]
            if memory_payload is not None:initial_events.append(('memory.recalled',memory_payload))
            bootstrap_bundle(
                sid,
                initial_events,
                initial_snapshot,
                meta=session_meta,
            )
        else:
            create_and_append=getattr(self.events,'create_session_and_append',None)
            if callable(create_and_append):
                create_and_append(sid,'goal.parsed',goal_payload,meta=session_meta)
                if sink:await sink('goal.parsed',goal_payload)
            else:
                self.events.create_session(sid,meta=session_meta)
                await self._emit(sid,'goal.parsed',goal_payload,sink)
            await self._emit(sid,'belief.updated',belief_payload,sink)
            await self._emit(sid,'harness.profile.bound',harness_payload,sink)
            if memory_payload is not None:await self._emit(sid,'memory.recalled',memory_payload,sink)
            self.events.save_checkpoint(sid,initial_snapshot)

        ctx={'text':text,'assets':assets,'goal':goal,'belief':belief}
        async def controller_emit(t,p):await self._emit(sid,t,p,sink)
        async def controller_checkpoint(stage,state):
            checkpoint_async=getattr(self.events,'save_checkpoint_and_append_async',None)
            checkpoint_and_append=getattr(self.events,'save_checkpoint_and_append',None)
            if sink is None and callable(checkpoint_async):
                reference,_event=await checkpoint_async(
                    sid,state,'runtime.checkpointed',{'stage':stage}
                )
                return reference
            if callable(checkpoint_and_append):
                reference,event=checkpoint_and_append(
                    sid,state,'runtime.checkpointed',{'stage':stage}
                )
                if sink:await sink('runtime.checkpointed',event.payload)
                return reference
            reference=self.events.save_checkpoint(sid,state)
            await self._emit(sid,'runtime.checkpointed',{'stage':stage,**reference},sink)
            return reference
        async def controller_restore(reference):
            seq=reference.get('seq') if isinstance(reference,dict) else None
            return self.events.restore_checkpoint(sid,seq)
        profile_token=bind_harness_profile(harness_profile)
        try:
            outcome=await self.autonomy.run(
                goal=goal,belief=belief,assets=assets,text=text,context=ctx,reasoner=reasoner,
                emit=controller_emit,checkpoint=controller_checkpoint,restore=controller_restore,
            )
        finally:
            reset_harness_profile(profile_token)
        tool_results=outcome.tool_results;agents=outcome.agents;verification=outcome.verification
        findings=[];risks=[]
        for x in agents:findings.extend(x.findings);risks.extend(x.risks)

        evolved=False;evolution_events=0
        available_tools=set(self.tools.tools)
        if not verification.passed:
            trajectory={'goal':goal.primary,'tool_sequence':[x.tool for x in tool_results],'missing':verification.missing_evidence,'autonomy_steps':outcome.autonomy_steps,'stagnated':outcome.stagnated,'stop_reason':outcome.stop_reason,'skills_used':outcome.skills_used}
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
        else:
            stop_reason=outcome.stop_reason or 'evidence_incomplete'
            stop_detail=outcome.stop_detail or '当前证据仍不足以完成最终验证'

        harness_outcome_async=getattr(self.harness,'record_outcome_async',None)
        harness_outcome_kwargs={
            'verifier_score':final_verify.score,
            'evidence_complete':bool(final_verify.evidence_complete),
            'session_id':sid,
            'meta':{
                'stop_reason':stop_reason,
                'tool_cost_used':tool_cost_used,
                'recovery_events':outcome.recovery_events,
                'autonomy_steps':outcome.autonomy_steps,
            },
        }
        if sink is None and callable(harness_outcome_async):
            harness_transitions=await harness_outcome_async(
                goal.domain.value,
                harness_profile.get('component_ids') or [],
                **harness_outcome_kwargs,
            )
        else:
            harness_transitions=self.harness.record_outcome(
                goal.domain.value,
                harness_profile.get('component_ids') or [],
                **harness_outcome_kwargs,
            )
        if harness_transitions:
            evolution_events+=len(harness_transitions)
            evolved=evolved or any(row.get('transition')=='promoted' for row in harness_transitions)
            await self._emit(sid,'harness.evolution.transition',{'transitions':harness_transitions,'authority':'cognition-only'},sink)

        if (not final_verify.passed) or outcome.recovery_events>0:
            harness_trajectory={
                'goal':goal.primary,
                'domain':goal.domain.value,
                'missing':list(final_verify.missing_evidence),
                'verifier_score':final_verify.score,
                'evidence_complete':bool(final_verify.evidence_complete),
                'stop_reason':stop_reason,
                'tool_sequence':[x.tool for x in tool_results],
                'tool_outcomes':[{'tool':x.tool,'ok':bool(x.ok),'cost':float(x.cost or 0.0)} for x in tool_results],
                'recovery_events':outcome.recovery_events,
                'autonomy_steps':outcome.autonomy_steps,
            }
            candidate=await self.harness.propose(
                goal.domain.value,
                trajectory=harness_trajectory,
                tool_catalog=self.autonomy.policy.tool_catalog(goal.domain.value),
                reasoner=reasoner,
            )
            if candidate:
                evolution_events+=1
                await self._emit(sid,'harness.evolution.candidate',candidate,sink)

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
        try:
            harness_state_async=getattr(self.harness,'state_summary_async',None)
            harness_state_sync=getattr(self.harness,'state_summary',None)
            if sink is None and callable(harness_state_async):
                harness_state=await harness_state_async(goal.domain.value)
            elif callable(harness_state_sync):
                harness_state=harness_state_sync(goal.domain.value)
            else:
                harness_snapshot=self.harness.snapshot(goal.domain.value)
                harness_state={
                    'active':{
                        row['kind']:row['generation']
                        for row in harness_snapshot.get('components',[])
                        if row.get('status')=='active'
                    },
                    'shadow':[
                        {'kind':row['kind'],'generation':row['generation']}
                        for row in harness_snapshot.get('components',[])
                        if row.get('status')=='shadow'
                    ],
                }
            belief.facts['harness_evolution']=harness_state
        except Exception:
            pass
        belief.facts['runtime_elapsed_ms']=round((time.perf_counter()-started)*1000.0,2)
        belief.risks=list(dict.fromkeys(risks))[:10];belief.uncertainties=final_verify.missing_evidence;belief.missing_evidence=final_verify.missing_evidence;belief.confidence=round(final_verify.score,3)
        self.skills.record_outcome(outcome.skills_used,success=bool(final_verify.passed),score=final_verify.score,session_id=sid,context={'domain':goal.domain.value,'missing':final_verify.missing_evidence,'recovery_events':outcome.recovery_events,'stop_reason':stop_reason})
        if not outcome.skills_used:
            note_run_async=getattr(self.skills,'note_run_async',None)
            if sink is None and callable(note_run_async):
                await note_run_async(
                    goal.domain.value,
                    success=bool(final_verify.passed),
                    skill_used=False,
                )
            else:
                self.skills.note_run(
                    goal.domain.value,
                    success=bool(final_verify.passed),
                    skill_used=False,
                )
        if final_verify.passed:self.memory.add({'session_id':sid,'domain':goal.domain.value,'goal':text[:160],'score':final_verify.score,'risks':belief.risks})
        summary=RuntimeSummary(session_id=sid,domain=goal.domain,status='completed' if final_verify.passed else 'needs_evidence',tool_calls=len(tool_results),subagents=len(agents),recovery_events=outcome.recovery_events,verifier_score=final_verify.score,evolved=evolved,event_chain_valid=True,autonomy_steps=outcome.autonomy_steps,delegations=outcome.delegations,evolution_events=evolution_events,skills_used=outcome.skills_used,task_graph=outcome.task_graph,proposed_actions=actions,evidence=evidence,findings=list(dict.fromkeys(findings))[:12],risks=list(dict.fromkeys(risks))[:10],evidence_complete=bool(final_verify.evidence_complete),missing_evidence=list(final_verify.missing_evidence),tool_cost_used=tool_cost_used,tool_cost_budget=tool_cost_budget,tool_cost_remaining=tool_cost_remaining,stop_reason=stop_reason,stop_detail=stop_detail,stagnated=bool(outcome.stagnated),autonomy_mode=autonomy_mode,belief=belief)
        await self._emit(sid,'run.completed',summary.model_dump(mode='json'),sink)
        verify_async=getattr(self.events,'verify_chain_async',None)
        if sink is None and callable(verify_async):summary.event_chain_valid=await verify_async(sid)
        else:summary.event_chain_valid=self.events.verify_chain(sid)
        return summary
