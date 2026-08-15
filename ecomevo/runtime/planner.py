from __future__ import annotations
import re
from typing import Any
from ecomevo.models import BeliefState, DecisionDomain, GoalState, ToolCall
from .tools import call, _query_terms


class AdaptivePlanner:
    """State-conditioned planner with persisted, regression-gated learned evidence checks."""
    DOMAIN_TERMS = {
        DecisionDomain.PRODUCT_GOVERNANCE:['商品','sku','spu','主图','详情','标题','违禁','侵权','假货','下架','类目','品牌'],
        DecisionDomain.MERCHANT_REVIEW:['商家','店铺','入驻','审核','营业执照','主体','资质','授权书','经营范围'],
        DecisionDomain.AFTERSALES:['售后','退款','退货','订单','买家','卖家','签收','物流','判责','赔付','拒收'],
        DecisionDomain.RISK_REVIEW:['风控','风险','欺诈','刷单','套现','异常交易','黑产','关联账户'],
        DecisionDomain.CONTENT_AUDIT:['内容','文案','图片','视频','广告','素材','直播','违规词'],
    }


    def __init__(self):
        self.learned_checks:dict[str,list[str]]={}

    def apply_evolution_patch(self, patch:dict[str,Any]|Any)->bool:
        data=patch.model_dump(mode='json') if hasattr(patch,'model_dump') else dict(patch)
        if not data.get('accepted') or data.get('target')!='planner':return False
        body=data.get('patch') or {};domain=str(body.get('domain') or '')
        if domain not in {d.value for d in DecisionDomain}:return False
        checks=[str(x).strip() for x in (body.get('add_required_checks') or []) if str(x).strip()]
        if not checks:return False
        current=self.learned_checks.setdefault(domain,[])
        before=len(current)
        for check in checks:
            if check not in current:current.append(check)
        return len(current)>before

    def evolution_state(self)->dict[str,list[str]]:
        return {k:list(v) for k,v in self.learned_checks.items()}

    def parse_goal(self, text: str, assets: list[dict[str, Any]], domain_hint: str | None = None) -> GoalState:
        scores = {d: 0.0 for d in self.DOMAIN_TERMS}
        low = text.lower()
        for domain, terms in self.DOMAIN_TERMS.items():
            scores[domain] = float(sum(1 for term in terms if term.lower() in low))
        try:
            hint = DecisionDomain(domain_hint) if domain_hint else None
        except ValueError:
            hint = None
        if hint in scores:
            # The selected workspace should control vague prompts, while a clearly explicit cross-domain request can override it.
            scores[hint] += 1.5
        if any(str(a.get('mime','')).startswith(('image/','video/')) for a in assets):
            scores[DecisionDomain.CONTENT_AUDIT] += 0.35
        domain = max(scores, key=scores.get) if max(scores.values(), default=0) > 0 else (hint or DecisionDomain.GENERAL)
        req = {
            DecisionDomain.PRODUCT_GOVERNANCE:['商品信息','适用规则'],
            DecisionDomain.MERCHANT_REVIEW:['主体/资质信息','适用规则'],
            DecisionDomain.AFTERSALES:['订单/履约信息','争议证据','适用规则'],
            DecisionDomain.RISK_REVIEW:['风险信号','业务事实','适用规则'],
            DecisionDomain.CONTENT_AUDIT:['内容素材','适用规则'],
            DecisionDomain.GENERAL:['业务事实'],
        }[domain]
        constraints = ['证据不足时不得直接执行高影响操作','有副作用的业务操作必须二次确认','结论必须能回溯到资料或工具结果']
        return GoalState(primary=text, domain=domain, required_evidence=req, constraints=constraints)

    def initial_belief(self, goal: GoalState, assets: list[dict[str, Any]]) -> BeliefState:
        return BeliefState(
            facts={'asset_count':len(assets)},
            missing_evidence=list(goal.required_evidence),
            uncertainties=['尚未完成业务资料核对'],
            confidence=.22 if not assets else .34,
        )

    def plan(self, goal: GoalState, belief: BeliefState, assets: list[dict[str, Any]], *, recovery: bool=False) -> list[ToolCall]:
        calls=[call('media.summarize','整理当前任务素材',group='parallel-a'),call('policy.lookup','读取当前场景规则',group='parallel-a')]
        learned=self.learned_checks.get(goal.domain.value,[])
        memory_terms=[str(x) for x in (belief.facts.get('memory_watch_terms') or []) if str(x).strip()]
        keywords=list(dict.fromkeys(_query_terms(goal.primary,limit=24)+learned[:8]+memory_terms[:8]))
        calls.append(call('evidence.search','从附件中定位与问题直接相关的证据',{'keywords':keywords},group='parallel-a'))
        domain=goal.domain
        if domain==DecisionDomain.PRODUCT_GOVERNANCE:calls += [call('catalog.inspect','核对商品字段与高风险声明',group='parallel-b'),call('risk.scan','检查商品与交易风险线索',group='parallel-b')]
        elif domain==DecisionDomain.MERCHANT_REVIEW:calls += [call('merchant.inspect','核对主体资质和风险记录',group='parallel-b'),call('risk.scan','检查商家关联风险',group='parallel-b')]
        elif domain==DecisionDomain.AFTERSALES:calls += [call('order.inspect','核对订单、履约与售后事实',group='parallel-b'),call('risk.scan','检查争议中的异常信号',group='parallel-b')]
        elif domain in {DecisionDomain.RISK_REVIEW,DecisionDomain.CONTENT_AUDIT}:calls += [call('risk.scan','聚合风险线索',group='parallel-b'),call('catalog.inspect','核对文本/商品声明',group='parallel-b')]
        if recovery:
            recovery_keywords=list(dict.fromkeys(list(belief.missing_evidence)+learned[:8]))
            calls.append(call('evidence.search','针对缺口做第二轮证据检索',{'keywords':recovery_keywords},group='recovery'))
        total=0.0; out=[]
        for c in calls:
            if total+c.estimated_cost>goal.max_tool_cost:break
            total+=c.estimated_cost;out.append(c)
        return out
