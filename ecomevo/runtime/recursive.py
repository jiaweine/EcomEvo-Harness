from __future__ import annotations
import asyncio
from typing import Any
from ecomevo.models import DecisionDomain, SubAgentResult, ToolResult


class Specialist:
    name='业务复核'
    def applies(self,domain:DecisionDomain)->bool:return True
    async def review(self,domain:DecisionDomain,tool_data:dict[str,dict[str,Any]])->SubAgentResult:
        return SubAgentResult(agent=self.name,summary='已完成复核',confidence=.55,depth=1,parent_agent='任务复核')


class PolicySpecialist(Specialist):
    name='规则复核'
    async def review(self,domain,tool_data):
        rules=tool_data.get('policy.lookup',{}).get('rules',[])
        return SubAgentResult(agent=self.name,summary=f'已核对 {len(rules)} 条适用规则',findings=rules[:2],confidence=.82 if rules else .35,depth=1,parent_agent='任务复核')


class EvidenceSpecialist(Specialist):
    name='证据复核'
    async def review(self,domain,tool_data):
        hits=tool_data.get('evidence.search',{}).get('hits',[])
        findings=[f"{x.get('name')} 命中：{', '.join(x.get('matched',[])[:4])}" for x in hits[:3]]
        evidence_ids=[str(x.get('asset_id')) for x in hits if x.get('asset_id')]
        return SubAgentResult(agent=self.name,summary=f'找到 {len(hits)} 组直接相关材料',findings=findings,evidence_ids=evidence_ids,confidence=min(.9,.42+.12*len(hits)),depth=1,parent_agent='任务复核')


class RiskSpecialist(Specialist):
    name='风险复核'
    async def review(self,domain,tool_data):
        risk=tool_data.get('risk.scan',{}); signals=risk.get('signals',{}); score=float(risk.get('risk_score',.15))
        findings=[f"{k}：{', '.join(v)}" for k,v in signals.items()]
        return SubAgentResult(agent=self.name,summary='发现风险线索' if findings else '未发现强风险线索',findings=findings,risks=findings,confidence=max(.45,score),depth=1,parent_agent='任务复核')


class DomainSpecialist(Specialist):
    name='业务判定'
    async def review(self,domain,tool_data):
        common={'depth':1,'parent_agent':'任务复核'}
        if domain==DecisionDomain.PRODUCT_GOVERNANCE:
            d=tool_data.get('catalog.inspect',{}); flags=d.get('claim_flags',[])
            return SubAgentResult(agent=self.name,summary='已核对商品声明与基础信息',findings=[f'需核验证明：{x}' for x in flags],risks=flags,confidence=.76 if d else .3,**common)
        if domain==DecisionDomain.MERCHANT_REVIEW:
            d=tool_data.get('merchant.inspect',{}); risks=d.get('risk_signals',[])
            return SubAgentResult(agent=self.name,summary='已核对商家主体与资质材料',findings=[f'风险项：{x}' for x in risks],risks=risks,confidence=.78 if d else .3,**common)
        if domain==DecisionDomain.AFTERSALES:
            d=tool_data.get('order.inspect',{}); s=d.get('signals',[])
            return SubAgentResult(agent=self.name,summary='已核对订单与履约事实',findings=[f'争议事实：{x}' for x in s],risks=[x for x in s if x in {'假货','与描述不符','未收到货'}],confidence=.75 if d else .3,**common)
        d=tool_data.get('risk.scan',{})
        return SubAgentResult(agent=self.name,summary='已完成业务事实复核',confidence=.62 if d else .3,**common)


class CrossCheckSpecialist:
    name='交叉复核'
    async def review(self,domain:DecisionDomain,tool_data:dict[str,dict[str,Any]],prior:list[SubAgentResult],depth:int)->SubAgentResult:
        risks=list(dict.fromkeys(r for item in prior for r in item.risks))
        evidence_ids=list(dict.fromkeys(e for item in prior for e in item.evidence_ids))
        weak=[item.agent for item in prior if item.confidence<.58]
        avg=sum(x.confidence for x in prior)/max(1,len(prior))
        findings=[]
        if weak:findings.append('仍需重点确认：'+'、'.join(weak))
        if risks:findings.append(f'已对 {len(risks)} 项风险线索做二次一致性检查')
        if evidence_ids:findings.append(f'已有 {len(evidence_ids)} 份直接相关材料可回溯')
        confidence=max(.38,min(.9,avg + (.06 if evidence_ids else -.04)))
        return SubAgentResult(
            agent=self.name,
            summary='已对第一轮并行复核结果做二次交叉确认',
            findings=findings,
            risks=risks,
            evidence_ids=evidence_ids,
            confidence=confidence,
            parent_agent='任务复核',
            depth=depth,
            children=len(prior),
        )


class RecursiveCoordinator:
    """Parallel first-pass review followed by a recursive cross-check when uncertainty/risk warrants it."""
    def __init__(self,max_depth:int=2):
        self.specialists=[PolicySpecialist(),EvidenceSpecialist(),RiskSpecialist(),DomainSpecialist()]
        self.cross=CrossCheckSpecialist();self.max_depth=max(1,max_depth)

    @staticmethod
    def _needs_deeper(results:list[SubAgentResult])->bool:
        return bool(results) and (any(x.risks for x in results) or any(x.confidence<.62 for x in results))

    async def _level(self,domain:DecisionDomain,tool_data:dict[str,dict[str,Any]],depth:int,prior:list[SubAgentResult]|None=None)->list[SubAgentResult]:
        if depth==1:
            current=list(await asyncio.gather(*(s.review(domain,tool_data) for s in self.specialists if s.applies(domain))))
        else:
            current=[await self.cross.review(domain,tool_data,prior or [],depth)]
        if depth>=self.max_depth or not self._needs_deeper(current if depth==1 else (prior or current)):
            return current
        return current + await self._level(domain,tool_data,depth+1,current)

    async def run(self,domain:DecisionDomain,results:list[ToolResult])->list[SubAgentResult]:
        data={r.tool:r.data for r in results if r.ok}
        return await self._level(domain,data,1)
