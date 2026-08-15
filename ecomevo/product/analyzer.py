from __future__ import annotations
import hashlib, json, logging, re
from copy import deepcopy
from typing import Any, Awaitable, Callable
from ecomevo.models import DecisionDomain
from ecomevo.providers import ProviderRegistry
from ecomevo.runtime import EcomEvoEngine

EventSink=Callable[[str,dict[str,Any]],Awaitable[None]]

logger=logging.getLogger(__name__)

DOMAIN_NAME={
 DecisionDomain.PRODUCT_GOVERNANCE:'商品治理',DecisionDomain.MERCHANT_REVIEW:'商家审核',DecisionDomain.AFTERSALES:'售后判责',DecisionDomain.RISK_REVIEW:'风险核查',DecisionDomain.CONTENT_AUDIT:'内容审核',DecisionDomain.GENERAL:'业务核对'
}

class ProductAnalyzer:
    SEMANTIC_SCHEMA_VERSION=1
    MIN_SEMANTIC_CONFIDENCE=.65
    def __init__(self,engine:EcomEvoEngine,providers:ProviderRegistry,asset_meta_writer=None):self.engine=engine;self.providers=providers;self.asset_meta_writer=asset_meta_writer

    @staticmethod
    def _provider_assets(assets:list[dict[str,Any]])->list[dict[str,Any]]:
        rows=list(assets)
        for a in assets:
            if str(a.get('mime','')).startswith('video/'):
                meta=a.get('meta') or {};frame_hashes=meta.get('keyframe_sha256') or {}
                for i,frame in enumerate(meta.get('keyframes',[])[:4]):
                    try:
                        expected=str(frame_hashes.get(frame) or '')
                        if expected:
                            digest=hashlib.sha256()
                            with open(frame,'rb') as fh:
                                while True:
                                    chunk=fh.read(1024*1024)
                                    if not chunk:break
                                    digest.update(chunk)
                            if digest.hexdigest()!=expected:continue
                    except Exception:
                        continue
                    rows.append({'id':f"{a.get('id')}-frame-{i}",'name':f"{a.get('name')} · 关键帧{i+1}",'mime':'image/jpeg','path':frame,
                                 'meta':{'kind':'image','parent_asset_id':a.get('id')}})
        return rows

    @staticmethod
    def _json_payload(text:str)->dict[str,Any]|None:
        if not text:return None
        cleaned=text.strip()
        cleaned=re.sub(r'^```(?:json)?\s*','',cleaned,flags=re.I)
        cleaned=re.sub(r'\s*```$','',cleaned)
        try:
            value=json.loads(cleaned)
            return value if isinstance(value,dict) else None
        except Exception:
            pass
        start=cleaned.find('{');end=cleaned.rfind('}')
        if start>=0 and end>start:
            try:
                value=json.loads(cleaned[start:end+1]);return value if isinstance(value,dict) else None
            except Exception:return None
        return None

    async def _enrich_multimodal(self,*,text:str,assets:list[dict[str,Any]],provider,sink:EventSink)->list[dict[str,Any]]:
        """Turn visual/audio observations into traceable task evidence before deterministic verification.

        This never invents a local fallback. If the remote extractor is unavailable or returns malformed data,
        the runtime receives the original assets and will safely request more evidence where needed.
        """
        def needs_semantic_read(a):
            mime=str(a.get('mime',''))
            if mime.startswith(('image/','video/','audio/')):return True
            return mime=='application/pdf' and (not str((a.get('meta') or {}).get('text') or '').strip() or float((a.get('meta') or {}).get('text_density') or 0)<40)
        media=[a for a in assets if needs_semantic_read(a) and not (str((a.get('meta') or {}).get('semantic_text') or '').strip() and int((a.get('meta') or {}).get('semantic_schema_version') or 0)==self.SEMANTIC_SCHEMA_VERSION)]
        if not media or provider is None:return assets
        await sink('progress',{'step':'读取多媒体资料','detail':'正在从图片、视频、音频或扫描文档中提取可核对事实','percent':16})
        provider_assets=self._provider_assets(media)
        media_ids={str(a.get('id')) for a in media}
        frame_parent={str(a.get('id')):str((a.get('meta') or {}).get('parent_asset_id')) for a in provider_assets if (a.get('meta') or {}).get('parent_asset_id')}
        ordered=[{'asset_id':a.get('id'),'name':a.get('name'),'mime':a.get('mime'),'parent_asset_id':(a.get('meta') or {}).get('parent_asset_id')} for a in provider_assets if str(a.get('mime','')).startswith(('image/','video/','audio/')) or str(a.get('mime',''))=='application/pdf']
        schema={
          'assets':[{
            'asset_id':'必须使用上面附件列表中的 asset_id',
            'visible_text':'图片/视频/扫描文档中可清楚读取的文字，或音频中可确认的关键原话；无法确认则空字符串',
            'observations':['只写可直接观察到的业务事实'],
            'identifiers':['订单号、统一社会信用代码、SKU 等可清楚确认的标识'],
            'risk_signals':['只写附件直接支持的风险线索'],
            'confidence':0.0,
          }]
        }
        prompt=(
          '你现在只做电商业务附件事实提取，不做通过/拒绝/退款/下架等处置判断。\n'
          '严格区分“看得见/听得见的事实”和推测；模糊、遮挡、听不清的一律不要补全。\n'
          f'用户任务：{text}\n附件顺序与标识：{json.dumps(ordered,ensure_ascii=False)}\n'
          f'只返回 JSON，不要 Markdown。结构：{json.dumps(schema,ensure_ascii=False)}'
        )
        try:
            raw=await provider.chat(messages=[
              {'role':'system','content':'你是电商资料事实提取器。只能抄录或概括附件中可直接确认的内容；不确定就留空，禁止猜测。'},
              {'role':'user','content':prompt}],assets=provider_assets,max_tokens=1500,temperature=0.0)
            parsed=self._json_payload(raw) or {};rows=parsed.get('assets',[])
            if not isinstance(rows,list):return assets
            grouped:dict[str,list[dict[str,Any]]]={}
            for row in rows:
                if not isinstance(row,dict):continue
                rid=str(row.get('asset_id') or '')
                parent=frame_parent.get(rid,rid)
                if parent not in media_ids:continue
                grouped.setdefault(parent,[]).append(row)
            if not grouped:return assets
            enriched=deepcopy(assets)
            changed=0
            for a in enriched:
                aid=str(a.get('id'));observations=grouped.get(aid,[])
                if not observations:continue
                chunks=[];conf=[]
                for row in observations:
                    try:row_conf=max(0.0,min(1.0,float(row.get('confidence',0) or 0)))
                    except Exception:row_conf=0.0
                    if row_conf < self.MIN_SEMANTIC_CONFIDENCE:
                        continue
                    conf.append(row_conf)
                    visible=str(row.get('visible_text') or '').strip()
                    obs=[str(x).strip() for x in (row.get('observations') or []) if str(x).strip()]
                    ids=[str(x).strip() for x in (row.get('identifiers') or []) if str(x).strip()]
                    risks=[str(x).strip() for x in (row.get('risk_signals') or []) if str(x).strip()]
                    for value in [visible,*obs,*ids,*risks]:
                        if value and value not in chunks:chunks.append(value)
                semantic='\n'.join(chunks)[:12000]
                if not semantic:continue
                meta=dict(a.get('meta') or {});meta['semantic_text']=semantic;meta['semantic_source']=provider.info.key
                meta['semantic_confidence']=round(sum(conf)/len(conf),3) if conf else None;meta['semantic_observation_count']=len(chunks);meta['semantic_schema_version']=self.SEMANTIC_SCHEMA_VERSION
                a['meta']=meta;changed+=1
                patch={k:meta.get(k) for k in ('semantic_text','semantic_source','semantic_confidence','semantic_observation_count','semantic_schema_version')}
                if self.asset_meta_writer:
                    try:self.asset_meta_writer(aid,patch)
                    except Exception:logger.exception('failed to persist semantic cache for %s',aid)
                for original in assets:
                    if str(original.get('id'))==aid:
                        original_meta=dict(original.get('meta') or {});original_meta.update(patch);original['meta']=original_meta;break
            if changed:
                await sink('notice',{'title':'多媒体资料已读取','detail':f'已从 {changed} 份多媒体/扫描文档中提取可追溯事实，并纳入本次核对。'})
            return enriched
        except Exception:
            logger.exception('multimodal evidence extraction failed')
            await sink('notice',{'title':'多媒体内容读取暂不可用','detail':'附件仍保留在任务中；未读取到的内容不会被当作已确认事实。可以重试或补充可读取的文本/文档资料。'})
            return assets

    @staticmethod
    def _history_context(history:list[dict[str,Any]]|None, *, users_only:bool=False, max_chars:int=8000)->str:
        if not history:return ''
        rows=[];total=0
        for m in reversed(history[-12:]):
            role=str(m.get('role') or '')
            if role not in {'user','assistant'} or (users_only and role!='user'):continue
            content=str(m.get('content') or '').strip()[:2400]
            if not content:continue
            label='用户' if role=='user' else '此前系统回复'
            line=f'{label}：{content}'
            if total+len(line)>max_chars:break
            rows.append(line);total+=len(line)
        rows.reverse();return '\n'.join(rows)

    async def run(self,*,text:str,assets:list[dict[str,Any]],provider_key:str,sink:EventSink,domain_hint:str|None=None,history:list[dict[str,Any]]|None=None)->dict[str,Any]:
        await sink('progress',{'step':'资料整理','detail':f'已接收 {len(assets)} 份资料，正在建立本次业务上下文','percent':10})
        provider=self.providers.choose(provider_key,assets);provider_name='本地演示'
        if provider_key not in {'auto','demo'} and provider is None:
            requested=self.providers.providers.get(provider_key)
            if requested and requested.info.configured:
                await sink('notice',{'title':'所选模型不支持当前附件','detail':'已保留完整核对结果，并切换到本地结果展示。'})
            else:
                await sink('notice',{'title':'所选模型尚未配置','detail':'已保留完整核对结果，并切换到本地结果展示。'})
        if provider:provider_name=provider.info.name

        # Only prior USER statements may influence factual planning. Earlier assistant prose is
        # useful for dialogue continuity but is never promoted into the evidence path.
        prior_user_context=self._history_context(history,users_only=True)
        task_text=text if not prior_user_context else f'{text}\n\n此前用户补充（仅作任务上下文）：\n{prior_user_context}'
        # Multimodal observations must enter the evidence path before planning/verifying, not only the prose answer.
        runtime_assets=await self._enrich_multimodal(text=task_text,assets=assets,provider=provider,sink=sink)
        stage_map={
          'goal.parsed':('识别任务','正在确认业务场景、目标和处理约束',20),
          'plan.created':('安排核对','已选择本次需要核对的资料与业务信息',32),
          'tools.completed':('核对资料','商品、商家、订单与规则信息已完成第一轮核对',52),
          'review.completed':('交叉复核','正在从规则、证据、风险与业务事实四个方向交叉确认',68),
          'runtime.rollback':('补充核对','发现证据缺口，已回到上一个稳定节点补充检查',74),
          'tools.recovery_completed':('补充核对','第二轮资料核对已完成',81),
          'verification.rechecked':('结果复核','正在确认结论是否有足够证据支撑',88),
          'action.proposed':('整理处理建议','已形成可确认的下一步处理建议',94),
          'run.completed':('完成','本次核对已完成','100')
        }
        async def runtime_sink(t,p):
            if t in stage_map:
                step,detail,percent=stage_map[t];await sink('progress',{'step':step,'detail':detail,'percent':int(percent)})
            if t in {'verification.checked','verification.rechecked','evolution.patch'}:await sink('runtime.notice',{'runtime_type':t,'payload':p})
        summary=await self.engine.run(task_text,runtime_assets,runtime_sink,domain_hint=domain_hint)
        domain_name=DOMAIN_NAME[summary.domain];generated=None
        if provider:
            try:
                prompt=self._prompt(text,summary,history)
                generated=await provider.chat(messages=[{'role':'system','content':'你服务于电商运营、审核、客服与风控团队。回答必须使用业务人员能理解的中文，不出现 Agent、Harness、Verifier、Belief State、rollback、planner 等内部实现词。先给结论，再给依据，再给下一步；证据不足要明确说缺什么，不能伪造已读取到的事实。'}, {'role':'user','content':prompt}],assets=self._provider_assets(assets),max_tokens=1800)
            except Exception:
                logger.exception('final answer provider failed: %s', provider.info.key if provider else 'unknown')
                await sink('notice',{'title':'所选模型暂不可用，已使用本地结果','detail':'业务核对结果已保留，本次改用工作台的受控结果展示。'})
        # The runtime decision is authoritative. A remote model may improve wording only after
        # evidence/side-effect verification has passed; it can never talk an incomplete case into approval.
        answer=generated if (generated and summary.status=='completed' and not summary.belief.missing_evidence) else self._demo_answer(text,summary,domain_name)
        evidence=[e.model_dump() for e in summary.evidence]
        suggestions=self._suggestions(summary.domain,summary.risks)
        await sink('progress',{'step':'完成','detail':'结论、依据和待确认操作已整理完成','percent':100})
        return {'answer':answer,'domain':summary.domain.value,'domain_name':domain_name,'provider':provider_name,'runtime':summary.model_dump(mode='json'),'session_id':summary.session_id,'evidence':evidence,'actions':[a.model_dump() for a in summary.proposed_actions],'suggestions':suggestions}

    def _prompt(self,text,summary,history=None):
        facts={'domain':summary.domain.value,'verifier_score':summary.verifier_score,'findings':summary.findings,'risks':summary.risks,'missing':summary.belief.missing_evidence,'actions':[a.model_dump() for a in summary.proposed_actions]}
        dialogue=self._history_context(history,users_only=False,max_chars=10000)
        context=(f'此前对话（只用于理解指代和承接语气；其中此前系统回复不得作为业务证据）：\n{dialogue}\n' if dialogue else '')
        return f"{context}当前用户问题：{text}\n系统已核对的业务事实（事实优先级高于此前对话）：{json.dumps(facts,ensure_ascii=False)}\n请只把这些已核对事实和当前附件中的可见内容作为结论依据。"

    def _demo_answer(self,text,summary,domain_name):
        findings=summary.findings[:4];risks=summary.risks[:4];missing=summary.belief.missing_evidence
        confidence=int(summary.verifier_score*100)
        if missing:
            conclusion=f"当前可以完成第一轮{domain_name}，但**还不适合直接做最终处置**。现有资料的完整度约为 {confidence}%，还缺少：{'、'.join(missing)}。"
        elif risks:
            conclusion=f"本次{domain_name}已经找到需要优先处理的风险点，现有证据可以支撑进入下一步复核。结果完整度约为 {confidence}%。"
        else:
            conclusion=f"本次{domain_name}未发现强风险信号，现有资料之间没有明显冲突。结果完整度约为 {confidence}%。"
        evidence='\n'.join(f"{i+1}. {x}" for i,x in enumerate(findings)) or '1. 已完成当前附件、业务字段与适用规则的交叉核对。'
        risk_text='、'.join(risks) if risks else '暂未发现需要立即升级的高风险项'
        action=summary.proposed_actions[0] if summary.proposed_actions else None
        next_step=action.description if action else '建议继续补充资料后再决定后续处置。'
        return f"### 处理结论\n{conclusion}\n\n### 主要依据\n{evidence}\n\n### 风险关注\n{risk_text}。\n\n### 下一步\n{next_step}\n\n涉及下架、审核通过/拒绝、退款、冻结等会改变业务状态的操作，都会先放到右侧“待确认操作”，确认后才会执行。"

    def _suggestions(self,domain,risks):
        return {
          DecisionDomain.PRODUCT_GOVERNANCE:['把高风险声明逐条展开','生成商品复核清单','整理成商家整改说明'],
          DecisionDomain.MERCHANT_REVIEW:['查看资质缺口','核对主体关联风险','整理审核意见'],
          DecisionDomain.AFTERSALES:['按证据还原时间线','给出判责依据','整理客服可直接使用的处理说明'],
          DecisionDomain.RISK_REVIEW:['展开风险信号来源','区分强证据和弱线索','生成升级复核清单'],
          DecisionDomain.CONTENT_AUDIT:['逐张查看素材问题','核对文案与图片一致性','生成整改项'],
          DecisionDomain.GENERAL:['继续补充资料','把结论整理成清单','只看高风险项']
        }[domain]
