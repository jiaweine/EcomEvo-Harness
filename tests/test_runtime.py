import asyncio
import pytest
from ecomevo.runtime import EcomEvoEngine, EventStore


def test_event_store_hash_chain_and_snapshot(tmp_path):
    s=EventStore(tmp_path/'events.db');s.create_session('s',meta={'x':1});s.append('s','a',{'x':1});s.append('s','b',{'x':2});s.save_snapshot('s',2,{'safe':True})
    assert s.verify_chain('s') is True
    assert s.get_snapshot('s',2)['safe'] is True


def test_checkpoint_is_bound_to_event_chain_and_tampering_blocks_restore(tmp_path):
    import sqlite3
    s=EventStore(tmp_path/'events.db');s.create_session('s');s.append('s','a',{'x':1})
    reference=s.save_checkpoint('s',{'belief':{'confidence':.4}})
    restored=s.restore_checkpoint('s',reference['seq'])
    assert restored['belief']['confidence']==.4
    assert restored['_checkpoint']['event_hash']==s.list_events('s')[-1].hash
    with sqlite3.connect(s.path) as c:
        c.execute("UPDATE snapshots SET snapshot_blob='json:{\"belief\":{\"confidence\":1}}' WHERE session_id='s'")
    assert s.restore_checkpoint('s',reference['seq']) is None


def test_engine_missing_evidence_recovers_but_never_proposes_side_effect(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('帮我判断这个商家是否可以通过入驻审核',[],domain_hint='merchant_review'))
    assert summary.status=='needs_evidence'
    assert summary.domain.value=='merchant_review'
    assert summary.tool_calls >= 5
    assert summary.subagents >= 4
    assert summary.event_chain_valid is True
    assert summary.belief.missing_evidence
    assert summary.proposed_actions==[]
    types={e.event_type for e in engine.events.list_events(summary.session_id)}
    assert {'goal.parsed','plan.created','tools.completed','verification.checked','run.completed'} <= types
    assert 'runtime.rollback' in types
    rollback=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='runtime.rollback')
    assert rollback.payload['restored'] is True
    assert rollback.payload['checkpoint_seq']>=1
    assert rollback.payload['checkpoint_state_hash']
    assert 'runtime.checkpointed' in types
    assert engine.events.list_patches()


def test_junk_asset_does_not_count_as_merchant_evidence(tmp_path):
    p=tmp_path/'junk.txt';p.write_text('hello world',encoding='utf-8')
    asset={'id':'a1','name':'junk.txt','mime':'text/plain','path':str(p),'meta':{'kind':'text','preview':'hello world'}}
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('帮我判断这个商家是否可以通过审核',[asset],domain_hint='merchant_review'))
    assert summary.status=='needs_evidence'
    assert any('主体标识' in x for x in summary.belief.missing_evidence)
    assert summary.proposed_actions==[]


def test_engine_with_asset_can_propose_controlled_action(tmp_path):
    p=tmp_path/'merchant.txt';p.write_text('商家：海风贸易有限公司\n营业执照 91310000123456789A\n品牌授权书齐全\n无历史处罚',encoding='utf-8')
    asset={'id':'a1','name':'商家资料.txt','mime':'text/plain','path':str(p),'meta':{'kind':'text','preview':p.read_text(encoding='utf-8')}}
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('审核这个商家的主体、授权和历史风险', [asset],domain_hint='merchant_review'))
    assert summary.status=='completed'
    assert summary.verifier_score >= .58
    assert summary.proposed_actions
    assert all(a.requires_confirmation for a in summary.proposed_actions if a.side_effect)


def test_scene_hint_controls_vague_prompt(tmp_path):
    p=tmp_path/'m.txt';p.write_text('营业执照 91310000123456789A',encoding='utf-8')
    asset={'id':'a1','name':'资料.txt','mime':'text/plain','path':str(p),'meta':{'kind':'text','preview':p.read_text()}}
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('帮我看看这个',[asset],domain_hint='merchant_review'))
    assert summary.domain.value=='merchant_review'


def test_event_store_fork(tmp_path):
    store=EventStore(tmp_path/'fork.db');store.create_session('base')
    store.append('base','one',{'v':1});store.append('base','two',{'v':2})
    store.fork('base',1,'branch',{'reason':'test'})
    events=store.list_events('branch')
    assert len(events)==1 and events[0].payload['_forked_from']=='base'
    assert store.verify_chain('branch')


def _event_append_worker(db_path:str, count:int, worker:int):
    store=EventStore(db_path)
    for i in range(count):
        store.append('shared','worker.event',{'worker':worker,'i':i})
    return count


def test_event_store_multi_process_append_is_serialized(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    db=str(tmp_path/'multi.db')
    store=EventStore(db);store.create_session('shared')
    with ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context('spawn')) as pool:
        counts=list(pool.map(_event_append_worker,[db]*4,[12]*4,range(4)))
    assert sum(counts)==48
    events=store.list_events('shared')
    assert len(events)==48
    assert [e.seq for e in events]==list(range(1,49))
    assert store.verify_chain('shared') is True


def test_accepted_evolution_is_merged_deduplicated_and_restored(tmp_path):
    db=tmp_path/'evolve.db';engine=EcomEvoEngine(db)
    first=asyncio.run(engine.run('审核这个商家',[],domain_hint='merchant_review'))
    assert first.evolved is True
    patches=engine.events.list_patches(20);assert len(patches)==1 and patches[0]['accepted'] is True
    assert any('主体标识' in x for x in engine.planner.evolution_state()['merchant_review'])
    second=asyncio.run(engine.run('再审核一个商家',[],domain_hint='merchant_review'))
    assert second.evolved is False
    assert len(engine.events.list_patches(20))==1
    types=[e.event_type for e in engine.events.list_events(second.session_id)]
    assert 'evolution.reused' in types
    restored=EcomEvoEngine(db)
    assert any('主体标识' in x for x in restored.planner.evolution_state()['merchant_review'])
    plan=next(e for e in restored.events.list_events(first.session_id) if e.event_type=='plan.created')
    # First run was planned before the patch existed; a later run must include the learned check.
    later=next(e for e in engine.events.list_events(second.session_id) if e.event_type=='plan.created')
    assert not any('主体标识' in x for x in plan.payload['learned_checks'])
    assert any('主体标识' in x for x in later.payload['learned_checks'])


def _text_asset(tmp_path, content, name='x.txt'):
    p=tmp_path/name;p.write_text(content,encoding='utf-8')
    return {'id':name,'name':name,'mime':'text/plain','path':str(p),'meta':{'kind':'text','text':content,'preview':content}}


def test_unrelated_product_attachment_cannot_unlock_product_action(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('这个商品宣称治愈，能不能下架？',[_text_asset(tmp_path,'hello world unrelated')],domain_hint='product_governance'))
    assert summary.status=='needs_evidence'
    assert summary.proposed_actions==[]
    assert '可关联到当前商品/声明的资料' in summary.belief.missing_evidence


def test_merchant_label_without_identifier_cannot_unlock_review(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('这个商家能通过吗？',[_text_asset(tmp_path,'营业执照')],domain_hint='merchant_review'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert any('主体标识' in x for x in summary.belief.missing_evidence)


def test_aftersales_amount_without_order_and_dispute_cannot_unlock_action(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('这个订单退款吗？',[_text_asset(tmp_path,'金额: 199')],domain_hint='aftersales'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert '可关联的订单号' in summary.belief.missing_evidence
    assert '履约或争议事实凭证' in summary.belief.missing_evidence


def test_chinese_evidence_search_uses_task_terms_not_whole_sentence(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    asset=_text_asset(tmp_path,'商品ID SKU-7788\n宣传文案：快速治愈皮肤问题')
    summary=asyncio.run(engine.run('这个商品宣称治愈，帮我核对', [asset],domain_hint='product_governance'))
    assert summary.status=='completed'
    completed=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='tools.completed')
    search=next(x for x in completed.payload['results'] if x['tool']=='evidence.search')
    assert search['data']['hits']
    assert '治愈' in search['data']['query_terms']


def test_content_image_without_visual_extraction_is_not_marked_reviewed(tmp_path):
    p=tmp_path/'image.png';p.write_bytes(b'not interpreted in demo mode')
    asset={'id':'img','name':'素材.png','mime':'image/png','path':str(p),'meta':{'kind':'image','width':100,'height':100}}
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('审核这张素材是否违规',[asset],domain_hint='content_audit'))
    assert summary.status=='needs_evidence'
    assert any('可读取的素材内容' in x for x in summary.belief.missing_evidence)


def test_missing_evidence_confidence_never_claims_near_complete(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('审核商家',[_text_asset(tmp_path,'营业执照')],domain_hint='merchant_review'))
    assert summary.status=='needs_evidence'
    assert summary.verifier_score <= .49
    assert summary.belief.confidence <= .49


def test_recursive_review_creates_second_depth_crosscheck(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    asset=_text_asset(tmp_path,'商品ID SKU-7788\n宣传文案：治愈问题')
    summary=asyncio.run(engine.run('核对这个商品的治愈宣称',[asset],domain_hint='product_governance'))
    review=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='review.completed')
    depths=[x['depth'] for x in review.payload['reviews']]
    assert 1 in depths and 2 in depths
    cross=next(x for x in review.payload['reviews'] if x['depth']==2)
    assert cross['children']>=4 and cross['parent_agent']=='任务复核'


def test_runtime_memory_is_recalled_after_restart_but_not_counted_as_evidence(tmp_path):
    db=tmp_path/'memory.db';engine=EcomEvoEngine(db)
    first_asset=_text_asset(tmp_path,'营业执照 91310000123456789A\n历史处罚记录',name='m1.txt')
    first=asyncio.run(engine.run('审核商家历史风险',[first_asset],domain_hint='merchant_review'))
    assert first.status=='completed' and first.risks
    restored=EcomEvoEngine(db)
    second_asset=_text_asset(tmp_path,'营业执照 91310000999999999X',name='m2.txt')
    second=asyncio.run(restored.run('审核另一个商家',[second_asset],domain_hint='merchant_review'))
    events=restored.events.list_events(second.session_id)
    recalled=next(e for e in events if e.event_type=='memory.recalled')
    assert recalled.payload['case_count']>=1 and recalled.payload['usage']=='planning_only_not_evidence'
    assert second.belief.facts['memory_case_count']>=1
    assert all(e.source!='memory' for e in second.evidence)


def test_configured_mcp_read_tool_participates_in_plan_and_can_supply_authoritative_identity(tmp_path):
    class FakeMCP:
        def __init__(self):self.calls=[]
        def read_tool_specs(self):return [{'key':'mcp.merchant.profile','domain':'merchant_review','server':'merchant-core','tool':'get_profile','purpose':'读取商家主体档案','arguments':{'query':'${text}'},'evidence_tags':['merchant_identity'],'cost':.8}]
        async def call_tool(self,server,tool,args):
            self.calls.append((server,tool,args));return {'merchant_id':'M-1','company_code':'91310000123456789A','status':'active'}
    mcp=FakeMCP();engine=EcomEvoEngine(tmp_path/'runtime.db',mcp=mcp)
    summary=asyncio.run(engine.run('审核商家 M-1 的主体信息',[],domain_hint='merchant_review'))
    assert summary.status=='completed' and summary.proposed_actions
    assert mcp.calls and mcp.calls[0][0:2]==('merchant-core','get_profile')
    assert any(e.source=='mcp.merchant.profile' and 'merchant_identity' in e.tags for e in summary.evidence)
    plan=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='plan.created')
    assert any(c['tool']=='mcp.merchant.profile' for c in plan.payload['calls'])


def test_plugin_registry_holds_live_runtime_instances(tmp_path):
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    assert engine.plugins.get('tool.ptc') is engine.ptc
    assert engine.plugins.get('memory.runtime') is engine.memory
    rows={x['key']:x for x in engine.plugins.describe()}
    assert rows['tool.ptc']['loaded'] is True


def test_plugin_overrides_are_wired_into_the_runtime_graph(tmp_path):
    from ecomevo.runtime.planner import AdaptivePlanner
    from ecomevo.runtime.sandbox import ActionSandbox
    from ecomevo.runtime.verifier import DecisionVerifier

    planner = AdaptivePlanner()
    sandbox = ActionSandbox()
    verifier = DecisionVerifier()
    class Memory:
        def relevant(self,*args,**kwargs):return []
        def add(self,*args,**kwargs):return None
    memory = Memory()
    engine = EcomEvoEngine(
        tmp_path/'runtime.db',
        plugin_overrides={
            'planner.adaptive': planner,
            'sandbox.action': sandbox,
            'verifier.decision': verifier,
            'memory.runtime': memory,
        },
    )

    assert engine.plugins.get('planner.adaptive') is planner
    assert engine.plugins.get('sandbox.action') is sandbox
    assert engine.plugins.get('verifier.decision') is verifier
    assert engine.plugins.get('memory.runtime') is memory
    assert engine.autonomy.planner is planner
    assert engine.autonomy.sandbox is sandbox
    assert engine.autonomy.verifier is verifier
    assert engine.harness.replay_gate.sandbox is sandbox


def test_live_plugin_replacement_rebinds_the_complete_dependency_graph(tmp_path):
    from ecomevo.runtime.planner import AdaptivePlanner
    from ecomevo.runtime.sandbox import ActionSandbox

    class LifecyclePlanner(AdaptivePlanner):
        def __init__(self):
            super().__init__();self.started=0;self.stopped=0
        def plugin_start(self, context):self.started+=1
        def plugin_stop(self, context):self.stopped+=1

    engine=EcomEvoEngine(tmp_path/'runtime.db')
    old_planner=engine.planner;planner=LifecyclePlanner();sandbox=ActionSandbox()
    engine.replace_plugin('planner.adaptive',planner,version='2.1')
    engine.replace_plugin('sandbox.action',sandbox,version='2.0')

    assert planner.started==1 and planner.stopped==0
    assert engine.planner is planner
    assert engine.plugins.get('planner.adaptive') is planner
    assert engine.autonomy.planner is planner
    assert engine.autonomy.policy.planner is planner
    assert engine.sandbox is sandbox
    assert engine.ptc.sandbox is sandbox
    assert engine.autonomy.sandbox is sandbox
    assert engine.autonomy.policy.sandbox is sandbox
    assert engine.harness.replay_gate.sandbox is sandbox
    row=next(x for x in engine.plugins.describe() if x['key']=='planner.adaptive')
    assert row['version']=='2.1' and row['source']=='runtime' and row['generation']==2
    assert row['contract_valid'] is True and row['contract_missing']==[]
    assert old_planner is not engine.planner


def test_plugin_contract_and_active_run_guard_roll_back_atomically(tmp_path):
    from ecomevo.runtime.planner import AdaptivePlanner
    from ecomevo.runtime.plugins import PluginContractError, PluginLifecycleError

    class LifecyclePlanner(AdaptivePlanner):
        def __init__(self):
            super().__init__();self.started=0;self.stopped=0
        def plugin_start(self, context):self.started+=1
        def plugin_stop(self, context):self.stopped+=1

    engine=EcomEvoEngine(tmp_path/'runtime.db');original=engine.planner
    with pytest.raises(PluginContractError):
        engine.replace_plugin('planner.adaptive',object())
    assert engine.planner is original and engine.plugins.get('planner.adaptive') is original

    candidate=LifecyclePlanner();engine._active_runs=1
    try:
        with pytest.raises(PluginLifecycleError):
            engine.replace_plugin('planner.adaptive',candidate)
    finally:engine._active_runs=0
    assert candidate.started==1 and candidate.stopped==1
    assert engine.planner is original and engine.plugins.get('planner.adaptive') is original
    with pytest.raises(PluginContractError):
        engine.plugins.set_enabled('verifier.decision',False)


def test_optional_plugin_can_be_disabled_and_reenabled(tmp_path):
    class Gateway:
        def current_provider(self):return None

    engine=EcomEvoEngine(tmp_path/'runtime.db');gateway=Gateway()
    engine.replace_plugin('model.gateway',gateway)
    assert engine.model_gateway is gateway
    engine.plugins.set_enabled('model.gateway',False)
    assert engine.model_gateway is None and engine.plugins.get('model.gateway') is None
    engine.plugins.set_enabled('model.gateway',True)
    assert engine.model_gateway is gateway and engine.plugins.get('model.gateway') is gateway


def test_disabling_mcp_removes_remote_tools_and_reenable_restores_them(tmp_path):
    class MCP:
        def read_tool_specs(self):
            return [{'key':'mcp.catalog','domain':'product_governance','server':'catalog','tool':'read'}]
        async def call_tool(self,server,tool,args):return {}

    engine=EcomEvoEngine(tmp_path/'runtime.db',mcp=MCP())
    assert 'mcp.catalog' in engine.tools.tools
    engine.plugins.set_enabled('mcp.remote',False)
    assert engine.mcp is None and 'mcp.catalog' not in engine.tools.tools
    engine.plugins.set_enabled('mcp.remote',True)
    assert engine.mcp is not None and 'mcp.catalog' in engine.tools.tools


def test_entry_point_discovery_is_metadata_only_and_loading_is_explicit(tmp_path,monkeypatch):
    from ecomevo.runtime.planner import AdaptivePlanner
    from ecomevo.runtime import plugins as plugin_module

    planner=AdaptivePlanner();loads=[]
    class Bundle:
        manifest={'key':'planner.adaptive','api_version':'1','version':'3.0'}
        def create(self):return planner
    class Point:
        name='company_planner';value='company.plugin:Bundle';group='ecomevo.plugins';dist=None
        def load(self):loads.append(self.name);return Bundle
    class Points(list):
        def select(self,**filters):
            return Points([p for p in self if all(getattr(p,key)==value for key,value in filters.items())])
    monkeypatch.setattr(plugin_module.metadata,'entry_points',lambda:Points([Point()]))

    engine=EcomEvoEngine(tmp_path/'runtime.db')
    assert engine.discover_plugins()[0]['name']=='company_planner'
    assert loads==[]
    row=engine.load_plugin('company_planner')
    assert loads==['company_planner'] and engine.planner is planner
    assert row['version']=='3.0' and row['source']=='entry-point:company_planner'


def test_evidence_search_can_find_fact_beyond_display_text_window(tmp_path):
    content='x'*30000+'\n商品ID SKU-8899 宣传治愈问题'
    p=tmp_path/'long.txt';p.write_text(content,encoding='utf-8')
    from ecomevo.product.media import probe_media
    asset={'id':'long','name':'long.txt','mime':'text/plain','path':str(p),'meta':probe_media(p,'text/plain')}
    engine=EcomEvoEngine(tmp_path/'runtime.db')
    summary=asyncio.run(engine.run('核对 SKU-8899 的治愈宣称',[asset],domain_hint='product_governance'))
    assert summary.status=='completed'
    completed=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='tools.completed')
    search=next(x for x in completed.payload['results'] if x['tool']=='evidence.search')
    assert search['data']['hits'] and any('sku-8899' in x.lower() for x in search['data']['query_terms'])


def test_specific_product_claim_requires_claim_evidence(tmp_path):
    engine=EcomEvoEngine(tmp_path/'r.db')
    asset=_text_asset(tmp_path,'SKU-8899 商品基础信息')
    summary=asyncio.run(engine.run('核对 SKU-8899 是否存在治愈功效宣称',[asset],domain_hint='product_governance'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert any('功效/治愈声明' in x for x in summary.belief.missing_evidence)


def test_specific_merchant_authorization_requires_authorization_material(tmp_path):
    engine=EcomEvoEngine(tmp_path/'r.db')
    asset=_text_asset(tmp_path,'营业执照 91310000123456789A')
    summary=asyncio.run(engine.run('审核这个商家的品牌授权是否齐全',[asset],domain_hint='merchant_review'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert '品牌/经营授权材料' in summary.belief.missing_evidence


def test_generic_risk_word_is_not_a_risk_signal(tmp_path):
    engine=EcomEvoEngine(tmp_path/'r.db')
    asset=_text_asset(tmp_path,'风险核查资料，账户正常，无异常')
    summary=asyncio.run(engine.run('核查这个账户是否存在套现风险',[asset],domain_hint='risk_review'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert any('独立风险信号' in x for x in summary.belief.missing_evidence)


def test_product_price_question_requires_actual_price_not_unrelated_numbers(tmp_path):
    engine=EcomEvoEngine(tmp_path/'price.db')
    asset=_text_asset(tmp_path,'SKU-8899 商品基础信息，库存 100 件',name='price-base.txt')
    summary=asyncio.run(engine.run('SKU-8899 现在售价多少钱？',[asset],domain_hint='product_governance'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert '当前商品的可核验价格信息' in summary.belief.missing_evidence


def test_product_price_question_accepts_attachment_price(tmp_path):
    engine=EcomEvoEngine(tmp_path/'price-ok.db')
    asset=_text_asset(tmp_path,'SKU-8899 商品基础信息\n售价：199.00',name='price-ok.txt')
    summary=asyncio.run(engine.run('SKU-8899 现在售价多少钱？',[asset],domain_hint='product_governance'))
    assert '当前商品的可核验价格信息' not in summary.belief.missing_evidence


def test_merchant_legal_representative_alias_is_normalized(tmp_path):
    engine=EcomEvoEngine(tmp_path/'legal.db')
    asset=_text_asset(tmp_path,'营业执照 91310000123456789A\n法人：张三',name='merchant-legal.txt')
    summary=asyncio.run(engine.run('核对这个商家的法人信息',[asset],domain_hint='merchant_review'))
    assert '可核验的法定代表人/负责人信息' not in summary.belief.missing_evidence


def test_incomplete_cases_never_enter_business_memory_even_after_restart(tmp_path):
    db=tmp_path/'memory-safe.db';engine=EcomEvoEngine(db)
    failed=asyncio.run(engine.run('审核商家是否有伪造风险',[],domain_hint='merchant_review'))
    assert failed.status=='needs_evidence'
    assert engine.memory.relevant('merchant_review')==[]
    restored=EcomEvoEngine(db)
    assert restored.memory.relevant('merchant_review')==[]


def test_text_attachment_cannot_make_unread_image_look_audited(tmp_path):
    img=tmp_path/'main.png';img.write_bytes(b'opaque-to-local-runtime')
    image={'id':'img','name':'主图.png','mime':'image/png','path':str(img),'meta':{'kind':'image','width':100,'height':100}}
    text=_text_asset(tmp_path,'商品文案：普通商品介绍，无违规词',name='copy.txt')
    engine=EcomEvoEngine(tmp_path/'content-mixed.db')
    summary=asyncio.run(engine.run('审核这张主图是否违规',[image,text],domain_hint='content_audit'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert any('可读取的图片素材内容' in x for x in summary.belief.missing_evidence)


def test_unread_product_image_cannot_be_replaced_by_other_text_attachment(tmp_path):
    img=tmp_path/'main.png';img.write_bytes(b'not-read-by-local-runtime')
    image={'id':'img','name':'主图.png','mime':'image/png','path':str(img),'meta':{'kind':'image','width':100,'height':100}}
    text=_text_asset(tmp_path,'SKU-8899 商品文案：治愈皮肤问题',name='copy-claim.txt')
    engine=EcomEvoEngine(tmp_path/'product-mixed.db')
    summary=asyncio.run(engine.run('核对这张主图是否有治愈字样',[image,text],domain_hint='product_governance'))
    assert summary.status=='needs_evidence' and summary.proposed_actions==[]
    assert any('可读取的商品图片内容' in x for x in summary.belief.missing_evidence)


def test_common_business_negations_do_not_become_risk_evidence(tmp_path):
    engine=EcomEvoEngine(tmp_path/'negation.db')
    asset=_text_asset(tmp_path,'经核查该交易并非刷单，账户不属于套现账户，未发现虚假物流',name='negated-risk.txt')
    summary=asyncio.run(engine.run('核查是否存在刷单或套现风险',[asset],domain_hint='risk_review'))
    completed=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='tools.completed')
    risk=next(x for x in completed.payload['results'] if x['tool']=='risk.scan')
    assert risk['data']['asset_signals']=={}
    assert summary.proposed_actions==[]

@pytest.mark.asyncio
async def test_evidence_search_finds_claim_beyond_bounded_text_index(tmp_path):
    from ecomevo.runtime.tools import EvidenceSearchTool
    path=tmp_path/'huge.log'
    path.write_text('普通记录\n' + ('x'*620000) + '\nSKU-TAIL-8899 当前页面宣称治愈并承诺最低价',encoding='utf-8')
    asset={
        'id':'tail','name':'huge.log','mime':'text/plain','path':str(path),
        'meta':{'kind':'text','text':'普通记录','search_text':('普通记录\n'+'x'*499990),'search_truncated':True},
    }
    result=await EvidenceSearchTool().execute({'assets':[asset],'text':'核对 SKU-TAIL-8899 的治愈宣称'}, {'keywords':['SKU-TAIL-8899','治愈']})
    assert result['hits']
    matched=set(result['hits'][0]['matched'])
    assert 'sku-tail-8899' in matched or '治愈' in matched
    assert '治愈' in result['hits'][0]['snippet'] or 'SKU-TAIL-8899' in result['hits'][0]['snippet']

def test_product_claim_in_tail_of_large_log_is_real_attachment_evidence(tmp_path):
    from ecomevo.product.media import probe_media
    p=tmp_path/'tail-evidence.log'
    p.write_text('常规商品日志\n'+('x'*620000)+'\nSKU-TAIL-8899 宣传文案：治愈皮肤问题',encoding='utf-8')
    asset={'id':'tail-evidence','name':p.name,'mime':'text/plain','path':str(p),'meta':probe_media(p,'text/plain')}
    assert asset['meta']['search_truncated'] is True
    engine=EcomEvoEngine(tmp_path/'tail-runtime.db')
    summary=asyncio.run(engine.run('核对 SKU-TAIL-8899 的治愈宣称',[asset],domain_hint='product_governance'))
    assert summary.status=='completed'
    assert not any('功效/治愈声明' in x for x in summary.belief.missing_evidence)

@pytest.mark.asyncio
async def test_stream_search_does_not_stop_after_early_entity_match(tmp_path):
    from ecomevo.runtime.tools import EvidenceSearchTool
    p=tmp_path/'separated.log'
    p.write_text('x'*520000+'\nSKU-SEPARATED-1\n'+'y'*320000+'\n最终宣传页面包含治愈字样',encoding='utf-8')
    asset={'id':'sep','name':p.name,'mime':'text/plain','path':str(p),'meta':{'kind':'text','search_text':'x'*500000,'search_truncated':True}}
    result=await EvidenceSearchTool().execute({'assets':[asset],'text':'核对 SKU-SEPARATED-1 是否有治愈宣称'},{'keywords':['SKU-SEPARATED-1','治愈']})
    matched=set(result['hits'][0]['matched'])
    assert 'sku-separated-1' in matched
    assert '治愈' in matched

def test_suffix_negations_in_risk_forms_do_not_become_positive_signals(tmp_path):
    engine=EcomEvoEngine(tmp_path/'risk-form.db')
    asset=_text_asset(tmp_path,'风险问卷\n刷单：否\n套现风险：否\n虚假物流：未发现\n是否存在异常退款？',name='risk-form.txt')
    summary=asyncio.run(engine.run('核查刷单、套现、虚假物流风险',[asset],domain_hint='risk_review'))
    completed=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='tools.completed')
    risk=next(x for x in completed.payload['results'] if x['tool']=='risk.scan')
    assert risk['data']['asset_signals']=={}
    assert summary.proposed_actions==[]


def test_suffix_negations_in_merchant_report_do_not_create_history_risk(tmp_path):
    engine=EcomEvoEngine(tmp_path/'merchant-form.db')
    asset=_text_asset(tmp_path,'营业执照 91310000123456789A\n处罚记录：无\n投诉情况：无\n材料伪造：否',name='merchant-form.txt')
    summary=asyncio.run(engine.run('审核商家的主体和历史风险',[asset],domain_hint='merchant_review'))
    completed=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='tools.completed')
    merchant=next(x for x in completed.payload['results'] if x['tool']=='merchant.inspect')
    assert merchant['data']['asset_risk_signals']==[]

def test_previous_question_is_retrieval_context_not_current_evidence_gate(tmp_path):
    engine=EcomEvoEngine(tmp_path/'context-gate.db')
    asset=_text_asset(tmp_path,'营业执照 91310000123456789A\n法人：张三',name='merchant-context.txt')
    summary=asyncio.run(engine.run('这个商家的法人是谁？',[asset],domain_hint='merchant_review',context_text='上一轮我问过品牌授权是否齐全'))
    assert summary.status=='completed'
    assert '品牌/经营授权材料' not in summary.belief.missing_evidence
    assert '可核验的法定代表人/负责人信息' not in summary.belief.missing_evidence
    plan=next(e for e in engine.events.list_events(summary.session_id) if e.event_type=='plan.created')
    search=next(x for x in plan.payload['calls'] if x['tool']=='evidence.search')
    assert any('授权' in str(x) for x in search['args'].get('keywords',[]))
