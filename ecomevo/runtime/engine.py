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
            payload=row.get('payload') or {}
            if payload.get('status')!='completed':
                continue
            belief=payload.get('belief') or {}
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
            title,detail=self._tool_evidence_copy(r.tool,r.data)
            out.append(EvidenceRecord(evidence_id=f"tool:{r.call_id}",source=r.tool,kind='tool_result',title=title,detail=detail,confidence=.86 if tags else .78,tags=tags))
        return out

    @staticmethod
    def _tool_evidence_copy(tool:str,data:dict[str,Any])->tuple[str,str]:
        """Customer-facing evidence title/detail. Never leak raw Python dicts or connector internals."""
        if data.get('remote_tool'):
            purpose=str(data.get('_purpose') or '企业业务数据核对')
            return purpose,'已从企业业务系统完成数据核对；详细字段仅用于本次判断，不在页面直接展开。'
        if tool=='media.summarize':
            return '资料内容整理',f"已整理 {int(data.get('count',0) or 0)} 份资料，其中 {int(data.get('interpretable_count',0) or 0)} 份内容可直接读取。"
        if tool=='evidence.search':
            hits=data.get('hits') or []
            pieces=[]
            for h in hits[:3]:
                name=str(h.get('name') or '资料');matched='、'.join(str(x) for x in (h.get('matched') or [])[:4])
                pieces.append(f"{name}：{matched}" if matched else name)
            return '附件证据检索','；'.join(pieces) if pieces else '未找到与当前问题直接匹配的附件片段。'
        if tool=='policy.lookup':
            rules=[str(x) for x in (data.get('rules') or [])[:3]]
            return '适用规则核对','；'.join(rules) if rules else '已完成当前业务场景的规则核对。'
        if tool=='catalog.inspect':
            ids='、'.join(str(x) for x in (data.get('asset_product_ids') or [])[:5]);flags='、'.join(str(x) for x in (data.get('asset_claim_flags') or [])[:5])
            detail=[]
            if ids:detail.append('商品标识：'+ids)
            if flags:detail.append('资料中需要核对的声明：'+flags)
            return '商品信息核对','；'.join(detail) or '已核对当前资料中的商品字段与声明。'
        if tool=='merchant.inspect':
            codes='、'.join(str(x) for x in (data.get('asset_company_codes') or [])[:3]);materials='、'.join(str(x) for x in (data.get('asset_materials') or [])[:5]);risks='、'.join(str(x) for x in (data.get('asset_risk_signals') or [])[:5])
            detail=[]
            if codes:detail.append('主体标识：'+codes)
            if materials:detail.append('已见材料：'+materials)
            if risks:detail.append('风险项：'+risks)
            return '商家资质核对','；'.join(detail) or '已核对当前资料中的主体与资质信息。'
        if tool=='order.inspect':
            ids='、'.join(str(x) for x in (data.get('asset_order_ids') or [])[:5]);signals='、'.join(str(x) for x in (data.get('asset_signals') or [])[:5]);amounts='、'.join(str(x) for x in (data.get('asset_amounts') or [])[:5])
            detail=[]
            if ids:detail.append('订单号：'+ids)
            if amounts:detail.append('金额：'+amounts)
            if signals:detail.append('履约/争议事实：'+signals)
            return '订单履约核对','；'.join(detail) or '已核对当前资料中的订单与履约信息。'
        if tool=='risk.scan':
            signals=data.get('asset_signals') or {};parts=[f"{k}：{'、'.join(str(x) for x in v)}" for k,v in list(signals.items())[:4]]
            return '风险信号核对','；'.join(parts) if parts else '当前资料中未发现可独立确认的强风险信号。'
        return '业务信息核对','已完成当前环节的数据核对。'

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

    async def run(self,text:str,assets:list[dict[str,Any]],sink:EventSink|None=None,domain_hint:str|None=None,context_text:str|None=None)->RuntimeSummary:
        sid=f'run-{uuid.uuid4().hex[:12]}'; goal=self.planner.parse_goal(text,assets,domain_hint=domain_hint); belief=self.planner.initial_belief(goal,assets)
        if context_text:
            from .tools import _query_terms
            belief.facts['conversation_context_terms']=_query_terms(context_text,limit=20)
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
                spent_cost=sum(max(0.0,float(x.cost or 0)) for x in tool_results)
                remaining_budget=max(0.0,float(goal.max_tool_cost)-spent_cost)
                candidates=self.planner.plan(goal,belief,assets,recovery=True)
                replan=[];estimated=0.0
                for item in candidates:
                    if estimated+item.estimated_cost<=remaining_budget+1e-9:
                        replan.append(item);estimated+=item.estimated_cost
                for remote in self.tools.planned_calls(goal.domain.value,recovery=True):
                    if estimated+remote.estimated_cost<=remaining_budget+1e-9:
                        replan.append(remote);estimated+=remote.estimated_cost
                await self._emit(sid,'plan.replanned',{'calls':[x.model_dump() for x in replan],'spent_cost':round(spent_cost,3),'remaining_budget':round(remaining_budget,3)},sink)
                more=await self.ptc.execute(replan,ctx); tool_results.extend(more); await self._emit(sid,'tools.recovery_completed',{'results':[x.model_dump() for x in more]},sink)
                agents=await self.recursive.run(goal.domain,tool_results); verification=self.verifier.verify(goal,belief,tool_results,agents); await self._emit(sid,'verification.rechecked',verification.model_dump(),sink)
                findings=[];risks=[]
                for x in agents:findings.extend(x.findings);risks.extend(x.risks)
        evidence=self._evidence(assets,tool_results); actions=self._actions(goal.domain,findings,risks,verification)
        final_verify=self.verifier.verify(goal,belief,tool_results,agents,actions); await self._emit(sid,'action.proposed',{'actions':[x.model_dump() for x in actions],'verification':final_verify.model_dump()},sink)
        belief.facts.update({'tool_results':len([x for x in tool_results if x.ok]),'review_count':len(agents)}); belief.risks=list(dict.fromkeys(risks))[:10];belief.uncertainties=final_verify.missing_evidence;belief.missing_evidence=final_verify.missing_evidence;belief.confidence=round(final_verify.score,3)
        # Task memory only keeps verified outcomes. Incomplete/failed cases can guide evolution, but
        # must not become historical business facts for future tasks.
        if final_verify.passed:
            self.memory.add({'session_id':sid,'domain':goal.domain.value,'goal':text[:160],'score':final_verify.score,'risks':belief.risks})
        summary=RuntimeSummary(session_id=sid,domain=goal.domain,status='completed' if final_verify.passed else 'needs_evidence',tool_calls=len(tool_results),subagents=len(agents),recovery_events=recovery,verifier_score=final_verify.score,evolved=evolved,event_chain_valid=True,proposed_actions=actions,evidence=evidence,findings=list(dict.fromkeys(findings))[:12],risks=list(dict.fromkeys(risks))[:10],belief=belief)
        await self._emit(sid,'run.completed',summary.model_dump(mode='json'),sink); summary.event_chain_valid=self.events.verify_chain(sid)
        return summary
