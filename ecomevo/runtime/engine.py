from __future__ import annotations
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from ecomevo.models import BeliefState, BusinessAction, DecisionDomain, EvidenceRecord, RuntimeSummary, VerificationResult
from .event_store import EventStore
from .evolver import FailureDrivenEvolver
from .memory import RuntimeMemory
from .planner import AdaptivePlanner
from .plugins import PluginRegistry
from .recursive import RecursiveCoordinator
from .sandbox import ActionSandbox
from .tools import PTCExecutor, ToolRegistry
from .verifier import DecisionVerifier

EventSink=Callable[[str,dict[str,Any]],Awaitable[None]]

class EcomEvoEngine:
    def __init__(self,db_path:str|Path,mcp=None,model_gateway=None):
        self.events=EventStore(db_path); self.planner=AdaptivePlanner(); self.sandbox=ActionSandbox(); self.tools=ToolRegistry(mcp); self.ptc=PTCExecutor(self.tools,self.sandbox);self.mcp=mcp;self.model_gateway=model_gateway
        self.recursive=RecursiveCoordinator(); self.verifier=DecisionVerifier(); self.evolver=FailureDrivenEvolver(); self.memory=RuntimeMemory(); self.plugins=PluginRegistry(); self._register_plugins()
        for patch in reversed(self.events.list_patches(200)):
            self.planner.apply_evolution_patch(patch)
        for row in reversed(self.events.recent_completed(200)):
            payload=row.get('payload') or {};belief=payload.get('belief') or {}
            self.memory.add({'session_id':row.get('session_id'),'domain':payload.get('domain'),'score':payload.get('verifier_score',0),'risks':belief.get('risks') or []})
    def _register_plugins(self):
        self.plugins.register('model.gateway','model','多模型服务层','OpenAI / DeepSeek / Qwen / Doubao / Claude / Gemini / Custom',instance=self.model_gateway)
        self.plugins.register('tool.ptc','tool','并行工具执行器','组合并发的只读工具调用',instance=self.ptc)
        self.plugins.register('memory.runtime','memory','任务记忆','保存同类任务的已验证结果',instance=self.memory)
        self.plugins.register('sandbox.action','sandbox','操作安全区','阻止未确认的高影响动作',instance=self.sandbox)
        self.plugins.register('verifier.decision','verifier','结果复核器','检查证据、约束与副作用',instance=self.verifier)
        self.plugins.register('evolver.failure','skill','失败改进器','失败轨迹回放与回归门禁',instance=self.evolver)
        self.plugins.register('mcp.remote','tool','MCP 连接器','接入企业内部工具与数据服务',instance=self.mcp)

    async def _emit(self,sid:str,t:str,p:dict[str,Any],sink:EventSink|None):
        ev=self.events.append(sid,t,p)
        if sink: await sink(t,p)
        return ev

    def _evidence(self,assets:list[dict[str,Any]],tool_results)->list[EvidenceRecord]:
        out=[]
        for a in assets:
            m=a.get('meta',{}); detail=[]
            if m.get('width'):detail.append(f"{m.get('width')}×{m.get('height')}")
            if m.get('duration'):detail.append(f"{m.get('duration')}s")
            if m.get('pages'):detail.append(f"{m.get('pages')}页")
            tags=[];confidence=.9
            if m.get('sha256'):
                tags.append(f"sha256:{m.get('sha256')}")
                detail.append(f"内容指纹 {str(m.get('sha256'))[:12]}…")
            if m.get('semantic_text'):
                detail.append(f"已读取多媒体内容（{int(m.get('semantic_observation_count',1) or 1)} 项可核对事实）")
                tags.append('multimodal_observation');confidence=max(.55,min(.95,float(m.get('semantic_confidence') or .72)))
            out.append(EvidenceRecord(evidence_id=f"asset:{a.get('id')}",source='upload',kind=m.get('kind','file'),title=a.get('name','附件'),detail=' · '.join(detail),confidence=confidence,asset_id=a.get('id'),tags=tags))
        for r in tool_results:
            if not r.ok:continue
            tags=['mcp']+[str(x) for x in (r.data.get('_evidence_tags') or [])] if r.data.get('remote_tool') else []
            out.append(EvidenceRecord(evidence_id=f"tool:{r.call_id}",source=r.tool,kind='tool_result',title={'policy.lookup':'适用规则核对','catalog.inspect':'商品信息核对','merchant.inspect':'商家资质核对','order.inspect':'订单履约核对','risk.scan':'风险信号核对','evidence.search':'附件证据检索','media.summarize':'素材信息整理'}.get(r.tool,r.tool),detail=str(r.data)[:420],confidence=.86 if tags else .78,tags=tags))
        return out

    def _actions(self,domain:DecisionDomain,findings:list[str],risks:list[str],verification:VerificationResult)->list[BusinessAction]:
        def a(kind,title,desc,risk='medium'):
            return BusinessAction(action_id=f'act-{uuid.uuid4().hex[:10]}',kind=kind,title=title,description=desc,risk_level=risk,side_effect=True,requires_confirmation=True,payload={'verifier_score':verification.score})
        if not verification.passed or not verification.evidence_complete:return []
        if domain==DecisionDomain.PRODUCT_GOVERNANCE:
            return [a('listing.review','提交商品处置复核','已发现需要核对的商品声明/风险项；确认后进入商品治理队列。','high' if risks else 'medium')]
        if domain==DecisionDomain.MERCHANT_REVIEW:
            return [a('merchant.review','提交商家审核结论','将当前资质与风险核对结果提交到商家审核队列。','high' if risks else 'medium')]
        if domain==DecisionDomain.AFTERSALES:
            return [a('aftersales.review','提交售后判责建议','将订单、履约和争议证据整理后的建议提交售后处理。','medium')]
        if domain==DecisionDomain.RISK_REVIEW:
            return [a('risk.escalate','提交风险复核','将风险信号与证据提交到风险处置队列。','high')]
        return []

    async def run(self,text:str,assets:list[dict[str,Any]],sink:EventSink|None=None,domain_hint:str|None=None)->RuntimeSummary:
        sid=f'run-{uuid.uuid4().hex[:12]}'; goal=self.planner.parse_goal(text,assets,domain_hint=domain_hint); belief=self.planner.initial_belief(goal,assets)
        prior_cases=self.memory.relevant(goal.domain.value,limit=6)
        memory_risks=list(dict.fromkeys(r for case in prior_cases for r in (case.get('risks') or [])))[:8]
        if prior_cases:
            belief.facts['memory_case_count']=len(prior_cases)
            belief.facts['memory_watch_terms']=memory_risks
        self.events.create_session(sid,meta={'domain':goal.domain.value,'goal':text[:220]})
        await self._emit(sid,'goal.parsed',goal.model_dump(mode='json'),sink); await self._emit(sid,'belief.updated',belief.model_dump(),sink)
        if prior_cases:
            await self._emit(sid,'memory.recalled',{'case_count':len(prior_cases),'watch_terms':memory_risks,'usage':'planning_only_not_evidence'},sink)
        self.events.save_snapshot(sid,2,{'goal':goal.model_dump(),'belief':belief.model_dump(),'stage':'initial'})
        plan=self.planner.plan(goal,belief,assets)
        for remote in self.tools.planned_calls(goal.domain.value):
            if sum(x.estimated_cost for x in plan)+remote.estimated_cost<=goal.max_tool_cost:plan.append(remote)
        await self._emit(sid,'plan.created',{'calls':[x.model_dump() for x in plan],'estimated_cost':sum(x.estimated_cost for x in plan),'learned_checks':self.planner.evolution_state().get(goal.domain.value,[])},sink)
        ctx={'text':text,'assets':assets,'goal':goal,'belief':belief}; tool_results=await self.ptc.execute(plan,ctx)
        await self._emit(sid,'tools.completed',{'results':[x.model_dump() for x in tool_results]},sink)
        agents=await self.recursive.run(goal.domain,tool_results); await self._emit(sid,'review.completed',{'reviews':[x.model_dump() for x in agents]},sink)
        findings=[];risks=[]
        for x in agents:
            findings.extend(x.findings);risks.extend(x.risks)
        verification=self.verifier.verify(goal,belief,tool_results,agents); await self._emit(sid,'verification.checked',verification.model_dump(),sink)
        recovery=0;evolved=False
        if not verification.passed:
            patch=self.evolver.build_patch(verification,goal.domain.value)
            if patch:
                equivalent=next((p for p in self.events.list_patches(200) if p.get('target')==patch.target and p.get('patch')==patch.patch and p.get('accepted')==patch.accepted),None)
                if equivalent:
                    await self._emit(sid,'evolution.reused',{'patch_id':equivalent.get('patch_id'),'target':equivalent.get('target'),'accepted':equivalent.get('accepted',False)},sink)
                else:
                    self.events.save_patch(patch);evolved=patch.accepted
                    if patch.accepted:self.planner.apply_evolution_patch(patch)
                    await self._emit(sid,'evolution.patch',patch.model_dump(),sink)
            if verification.recommendation in {'rollback','replan'}:
                recovery=1; snap=self.events.get_snapshot(sid,2); await self._emit(sid,'runtime.rollback',{'restored':bool(snap),'reason':verification.issues+verification.missing_evidence},sink)
                belief=BeliefState(**snap['belief']) if snap else belief; belief.missing_evidence=verification.missing_evidence; ctx['belief']=belief
                replan=self.planner.plan(goal,belief,assets,recovery=True)
                for remote in self.tools.planned_calls(goal.domain.value,recovery=True):
                    if sum(x.estimated_cost for x in replan)+remote.estimated_cost<=goal.max_tool_cost:replan.append(remote)
                await self._emit(sid,'plan.replanned',{'calls':[x.model_dump() for x in replan]},sink)
                more=await self.ptc.execute(replan,ctx); tool_results.extend(more); await self._emit(sid,'tools.recovery_completed',{'results':[x.model_dump() for x in more]},sink)
                agents=await self.recursive.run(goal.domain,tool_results); verification=self.verifier.verify(goal,belief,tool_results,agents); await self._emit(sid,'verification.rechecked',verification.model_dump(),sink)
                findings=[];risks=[]
                for x in agents:findings.extend(x.findings);risks.extend(x.risks)
        evidence=self._evidence(assets,tool_results); actions=self._actions(goal.domain,findings,risks,verification)
        final_verify=self.verifier.verify(goal,belief,tool_results,agents,actions); await self._emit(sid,'action.proposed',{'actions':[x.model_dump() for x in actions],'verification':final_verify.model_dump()},sink)
        belief.facts.update({'tool_results':len([x for x in tool_results if x.ok]),'review_count':len(agents)}); belief.risks=list(dict.fromkeys(risks))[:10];belief.uncertainties=final_verify.missing_evidence;belief.missing_evidence=final_verify.missing_evidence;belief.confidence=round(final_verify.score,3)
        self.memory.add({'session_id':sid,'domain':goal.domain.value,'goal':text[:160],'score':final_verify.score,'risks':belief.risks})
        summary=RuntimeSummary(session_id=sid,domain=goal.domain,status='completed' if final_verify.passed else 'needs_evidence',tool_calls=len(tool_results),subagents=len(agents),recovery_events=recovery,verifier_score=final_verify.score,evolved=evolved,event_chain_valid=True,proposed_actions=actions,evidence=evidence,findings=list(dict.fromkeys(findings))[:12],risks=list(dict.fromkeys(risks))[:10],belief=belief)
        await self._emit(sid,'run.completed',summary.model_dump(mode='json'),sink); summary.event_chain_valid=self.events.verify_chain(sid)
        return summary
