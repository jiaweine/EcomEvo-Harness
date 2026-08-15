from __future__ import annotations
import asyncio, json, re, time, uuid
from pathlib import Path
from typing import Any
from ecomevo.models import ToolCall, ToolResult
from .sandbox import ActionSandbox


def _asset_text(a:dict[str,Any], limit:int=18000, *, search:bool=False)->str:
    meta=a.get('meta') or {};parts=[]
    primary=meta.get('search_text') if search else meta.get('text')
    if primary:parts.append(str(primary))
    elif meta.get('text'):parts.append(str(meta['text']))
    elif meta.get('preview'):parts.append(str(meta['preview']))
    if meta.get('semantic_text'):parts.append(str(meta['semantic_text']))
    if not parts:
        p=Path(str(a.get('path','')))
        if meta.get('kind') in {'text','document','sheet'} and p.exists():
            try:parts.append(p.read_text(encoding='utf-8',errors='ignore'))
            except Exception:pass
    return '\n'.join(parts)[:limit]

def _combined_asset_text(assets:list[dict[str,Any]],limit:int=500000)->str:
    chunks=[];total=0
    for a in assets:
        # Bound each attachment so one giant log cannot starve every other file.
        value=_asset_text(a,min(100000,limit-total),search=True)
        if value:
            chunks.append(value);total+=len(value)
        if total>=limit:break
    return '\n'.join(chunks)[:limit]

EVIDENCE_TERMS=[
    '营业执照','统一社会信用代码','授权书','授权','许可证','经营范围','主体','处罚','吊销','伪造',
    '订单','订单号','物流','签收','未收到货','破损','少件','拒收','退款','退货','假货','与描述不符',
    '商品','SKU','SPU','商品ID','主图','详情','品牌','正品','原装','治愈','功效','最低价','100%',
    '刷单','套现','异常退款','虚假物流','侵权','违禁','风险','投诉','关联账户'
]
STOP_TERMS={'这个','那个','怎么','如何','能不能','可以吗','帮我','看看','一下','继续','处理','是否','进行','当前','刚才','资料'}

def _query_terms(query:str,limit:int=40)->list[str]:
    text=str(query or '').lower();terms=[]
    def add(value):
        value=str(value).strip().lower()
        if len(value)>=2 and value not in STOP_TERMS and value not in terms:terms.append(value)
    # Domain terms are more useful than arbitrary character n-grams and should win the cap.
    for term in EVIDENCE_TERMS:
        if term.lower() in text:add(term)
    for token in re.findall(r'[A-Za-z0-9][A-Za-z0-9_-]{1,}',text):add(token)
    for chunk in re.findall(r'[\u4e00-\u9fff]{2,}',text):
        if len(chunk)<=12:add(chunk)
        # Lightweight Chinese fallback without an external tokenizer. Trigrams first
        # reduce accidental matches while still finding entity/task fragments.
        for n in (4,3,2):
            for i in range(max(0,len(chunk)-n+1)):
                gram=chunk[i:i+n]
                if gram not in STOP_TERMS:add(gram)
                if len(terms)>=limit:return terms[:limit]
    return terms[:limit]

class BaseTool:
    key='base'; cost=1.0
    async def execute(self,ctx:dict[str,Any],args:dict[str,Any])->dict[str,Any]: return {}

class MediaSummarizeTool(BaseTool):
    key='media.summarize'; cost=.4
    async def execute(self,ctx,args):
        rows=[]
        for a in ctx['assets']:
            m=a.get('meta',{}); content=_asset_text(a).strip(); rows.append({'asset_id':a.get('id'),'name':a.get('name'),'mime':a.get('mime'),'kind':m.get('kind','file'),'duration':m.get('duration'),'width':m.get('width'),'height':m.get('height'),'pages':m.get('pages'),'rows':m.get('rows'),'interpretable':bool(content),'semantic':bool(m.get('semantic_text'))})
        return {'assets':rows,'count':len(rows),'interpretable_count':sum(1 for x in rows if x['interpretable']),'semantic_count':sum(1 for x in rows if x['semantic'])}

class EvidenceSearchTool(BaseTool):
    key='evidence.search'; cost=.7
    async def execute(self,ctx,args):
        query=' '.join(args.get('keywords') or []) or ctx['text']
        words=_query_terms(query)
        hits=[]
        for a in ctx['assets']:
            raw=_asset_text(a,500000,search=True);txt=raw.lower()
            matched=[w for w in words if w in txt]
            if matched:
                positions=[txt.find(w) for w in matched if txt.find(w)>=0];pos=min(positions) if positions else 0
                start=max(0,pos-180);snippet=raw[start:start+520]
                hits.append({'asset_id':a.get('id'),'name':a.get('name'),'matched':matched[:8],'snippet':snippet})
        return {'hits':hits,'query_terms':words[:12]}

class PolicyLookupTool(BaseTool):
    key='policy.lookup'; cost=.6
    POLICY={
      'product_governance':['商品标题、主图、详情与实物/资质应保持一致','涉及功效、材质、品牌授权等高风险声明时必须有可核验证据','证据不足时优先进入复核，不直接执行下架'],
      'merchant_review':['主体资质、经营范围、授权链路、历史处罚与账户关联需要一致核对','高风险关联或材料矛盾时应转人工复核','通过/拒绝属于有业务副作用的动作，必须留痕'],
      'aftersales':['判责应同时核对订单履约、商品描述、沟通记录与用户举证','退款金额不得超过订单可退金额','争议证据不足时应补证或升级，不应直接定责'],
      'risk_review':['风险结论至少需要两个独立信号或一条强证据','模型/规则命中只能作为线索，最终处置需结合业务事实'],
      'content_audit':['图文、视频、文案需做一致性与合规检查','无法直接理解的媒体应转视觉/音视频模型或人工复核']}
    async def execute(self,ctx,args):
        domain=ctx['goal'].domain.value; return {'domain':domain,'rules':self.POLICY.get(domain,self.POLICY['risk_review'])}

class CatalogInspectTool(BaseTool):
    key='catalog.inspect'; cost=1.1
    async def execute(self,ctx,args):
        asset_text=_combined_asset_text(ctx['assets'])
        text=asset_text+'\n'+ctx['text']
        flags=[]; asset_flags=[]
        for kw,label in [('正品','品牌/真伪声明'),('授权','授权链路'),('原装','来源声明'),('100%','绝对化表达'),('治愈','功效高风险词'),('最低价','价格承诺')]:
            if kw.lower() in text.lower():flags.append(label)
            if kw.lower() in asset_text.lower():asset_flags.append(label)
        ids=re.findall(r'(?i)(?:sku|spu|商品id|item)[\s:#-]*([a-z0-9_-]{4,})',text)
        asset_ids=re.findall(r'(?i)(?:sku|spu|商品id|item)[\s:#-]*([a-z0-9_-]{4,})',asset_text)
        return {'product_ids':list(dict.fromkeys(ids))[:10],'asset_product_ids':list(dict.fromkeys(asset_ids))[:10],
                'claim_flags':flags,'asset_claim_flags':asset_flags,'text_chars':len(text),'asset_text_chars':len(asset_text.strip())}

class MerchantInspectTool(BaseTool):
    key='merchant.inspect'; cost=1.1
    async def execute(self,ctx,args):
        asset_text=_combined_asset_text(ctx['assets'])
        text=asset_text+'\n'+ctx['text']
        risk=[]; asset_risk=[]
        for kw,label in [('处罚','历史处罚'),('吊销','资质异常'),('关联账户','账户关联'),('伪造','材料真实性风险'),('投诉','投诉记录')]:
            if kw in text:risk.append(label)
            if kw in asset_text:asset_risk.append(label)
        unified=re.findall(r'\b[0-9A-Z]{18}\b',text); asset_unified=re.findall(r'\b[0-9A-Z]{18}\b',asset_text)
        mats=['营业执照','授权书','身份证','许可证'];fields=['经营范围','法定代表人','注册地址']
        present=[x for x in mats if x in text];asset_present=[x for x in mats if x in asset_text]
        asset_fields=[x for x in fields if x in asset_text]
        return {'company_codes':unified[:5],'asset_company_codes':asset_unified[:5],'risk_signals':risk,'asset_risk_signals':asset_risk,
                'materials_present':len(present),'asset_materials_present':len(asset_present),'materials':present,'asset_materials':asset_present,'asset_fields':asset_fields}

class OrderInspectTool(BaseTool):
    key='order.inspect'; cost=1.1
    async def execute(self,ctx,args):
        asset_text=_combined_asset_text(ctx['assets'])
        text=asset_text+'\n'+ctx['text']
        order_ids=re.findall(r'(?i)(?:订单|order)[\s:#-]*([a-z0-9_-]{5,})',text)
        asset_order_ids=re.findall(r'(?i)(?:订单|order)[\s:#-]*([a-z0-9_-]{5,})',asset_text)
        amounts=[float(x) for x in re.findall(r'(?:¥|￥|金额[:：]?\s*)(\d+(?:\.\d{1,2})?)',text)]
        asset_amounts=[float(x) for x in re.findall(r'(?:¥|￥|金额[:：]?\s*)(\d+(?:\.\d{1,2})?)',asset_text)]
        signals=[x for x in ['未收到货','破损','少件','假货','与描述不符','拒收','签收'] if x in text]
        asset_signals=[x for x in ['未收到货','破损','少件','假货','与描述不符','拒收','签收'] if x in asset_text]
        return {'order_ids':order_ids[:10],'asset_order_ids':asset_order_ids[:10],'amounts':amounts[:10],'asset_amounts':asset_amounts[:10],
                'signals':signals,'asset_signals':asset_signals}

class RiskScanTool(BaseTool):
    key='risk.scan'; cost=.8
    async def execute(self,ctx,args):
        asset_text=_combined_asset_text(ctx['assets']).lower()
        text=(asset_text+'\n'+ctx['text']).lower()
        lex={'身份矛盾':['不一致','伪造','冒用'],'交易异常':['刷单','套现','异常退款','批量下单'],'商品风险':['假货','侵权','违禁','三无'],'履约争议':['未收到货','虚假物流','破损','少件']}
        hits={k:[x for x in vs if x.lower() in text] for k,vs in lex.items()}; hits={k:v for k,v in hits.items() if v}
        asset_hits={k:[x for x in vs if x.lower() in asset_text] for k,vs in lex.items()}; asset_hits={k:v for k,v in asset_hits.items() if v}
        score=min(.98,.18+.16*sum(len(v) for v in hits.values()))
        return {'signals':hits,'asset_signals':asset_hits,'risk_score':round(score,3)}

class MCPReadTool(BaseTool):
    def __init__(self,mcp,spec:dict[str,Any]):
        self.mcp=mcp;self.spec=spec;self.key=str(spec['key']);self.cost=float(spec.get('cost',1.0))

    @staticmethod
    def _expand(value,context):
        if isinstance(value,dict):return {k:MCPReadTool._expand(v,context) for k,v in value.items()}
        if isinstance(value,list):return [MCPReadTool._expand(v,context) for v in value]
        if isinstance(value,str):
            exact=re.fullmatch(r'\$\{([A-Za-z0-9_]+)\}',value)
            if exact:return context.get(exact.group(1),value)
            out=value
            for k,v in context.items():out=out.replace('${'+k+'}',str(v))
            return out
        return value

    async def execute(self,ctx,args):
        context={'text':ctx.get('text',''),'domain':ctx['goal'].domain.value}
        context.update({str(k):v for k,v in (ctx.get('mcp_context') or {}).items()})
        template=self.spec.get('arguments') or {};arguments=self._expand(template,context)
        result=await self.mcp.call_tool(self.spec['server'],self.spec['tool'],arguments)
        return {'server':self.spec['server'],'remote_tool':self.spec['tool'],'result':result,'_evidence_tags':list(self.spec.get('evidence_tags') or [])}


class ToolRegistry:
    def __init__(self,mcp=None):
        tools=[MediaSummarizeTool(),EvidenceSearchTool(),PolicyLookupTool(),CatalogInspectTool(),MerchantInspectTool(),OrderInspectTool(),RiskScanTool()]
        self.tools={t.key:t for t in tools};self.remote_specs=[]
        if mcp is not None and hasattr(mcp,'read_tool_specs'):
            for spec in mcp.read_tool_specs():
                key=str(spec.get('key') or '')
                if not key or key in self.tools:continue
                tool=MCPReadTool(mcp,spec);self.tools[key]=tool;self.remote_specs.append(spec)
    def planned_calls(self,domain:str,recovery:bool=False)->list[ToolCall]:
        rows=[]
        for spec in self.remote_specs:
            if spec.get('domain')!=domain:continue
            rows.append(call(str(spec['key']),str(spec.get('purpose') or '读取企业业务数据'),cost=float(spec.get('cost',1.0)),group='mcp-recovery' if recovery else 'mcp-read'))
        return rows
    def describe(self):
        remote={str(x.get('key')) for x in self.remote_specs}
        return [{'key':k,'cost':t.cost,'mode':'mcp-read' if k in remote else 'read-only'} for k,t in self.tools.items()]

class PTCExecutor:
    def __init__(self,registry:ToolRegistry,sandbox:ActionSandbox):self.registry=registry;self.sandbox=sandbox
    async def _one(self,call:ToolCall,ctx:dict[str,Any])->ToolResult:
        started=time.perf_counter(); decision=self.sandbox.validate_tool(call.tool)
        if not decision.allowed:return ToolResult(call_id=call.call_id,tool=call.tool,ok=False,error=decision.reason,cost=0,duration_ms=0)
        tool=self.registry.tools.get(call.tool)
        if not tool:return ToolResult(call_id=call.call_id,tool=call.tool,ok=False,error='tool not registered')
        try:data=await tool.execute(ctx,call.args);return ToolResult(call_id=call.call_id,tool=call.tool,ok=True,data=data,cost=tool.cost,duration_ms=(time.perf_counter()-started)*1000)
        except Exception as exc:return ToolResult(call_id=call.call_id,tool=call.tool,ok=False,error=repr(exc),cost=tool.cost,duration_ms=(time.perf_counter()-started)*1000)
    async def execute(self,calls:list[ToolCall],ctx:dict[str,Any])->list[ToolResult]:
        groups={}
        for c in calls:groups.setdefault(c.parallel_group,[]).append(c)
        out=[]
        for _,batch in groups.items():out.extend(await asyncio.gather(*(self._one(c,ctx) for c in batch)))
        return out

def call(tool:str,purpose:str,args:dict[str,Any]|None=None,cost:float=1,group:str='g1')->ToolCall:
    return ToolCall(call_id=f'call-{uuid.uuid4().hex[:8]}',tool=tool,purpose=purpose,args=args or {},estimated_cost=cost,parallel_group=group)
