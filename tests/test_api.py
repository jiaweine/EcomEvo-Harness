from fastapi.testclient import TestClient
from ecomevo.api.app import app


def test_health_product_runtime_and_providers():
    c=TestClient(app)
    h=c.get('/api/health');assert h.status_code==200 and h.json()['product']=='EcomEvo 商业决策工作台'
    p=c.get('/api/product').json();assert len(p['scenes'])==5 and 'Excel' in p['accepted']
    providers=c.get('/api/providers').json();keys={x['key'] for x in providers}
    assert {'auto','demo','openai','deepseek','qwen','doubao','anthropic','gemini'} <= keys
    r=c.get('/api/runtime').json();assert r['event_store']['hash_chain'] and r['planner']['recursive_review'] and r['recovery']['failure_driven_evolution']


def test_full_conversation_asset_message_and_action(tmp_path):
    c=TestClient(app)
    conv=c.post('/api/conversations',json={'title':'商家审核','scene':'merchant_review'}).json()
    files={'file':('merchant.txt','营业执照 91310000123456789A\n品牌授权书齐全\n无历史处罚'.encode('utf-8'),'text/plain')}
    asset=c.post('/api/assets',files=files,data={'conversation_id':conv['id']});assert asset.status_code==200
    a=asset.json()
    sent=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核这个商家的主体、授权和历史风险，给出是否通过的建议','asset_ids':[a['id']],'provider':'demo'})
    assert sent.status_code==200
    detail=c.get(f"/api/conversations/{conv['id']}").json()
    assert len(detail['messages'])>=2
    assistant=[m for m in detail['messages'] if m['role']=='assistant'][-1]
    assert '处理结论' in assistant['content']
    assert assistant['payload']['runtime']['event_chain_valid'] is True
    actions=detail['actions'];assert actions
    row=c.post(f"/api/actions/{actions[0]['id']}/decision",json={'decision':'approve','note':'测试确认'})
    assert row.status_code==200 and row.json()['status']=='executed'


def test_frontend_is_customer_facing():
    c=TestClient(app);html=c.get('/').text
    assert 'EcomEvo 商业决策工作台' in html
    assert '商品治理' in html and '售后判责' in html and '待确认操作' in html
    assert 'Harness 边界' not in html and '策略先验' not in html


def test_invalid_scene_and_empty_upload_are_rejected():
    c=TestClient(app)
    assert c.post('/api/conversations',json={'scene':'not-a-scene'}).status_code==422
    conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    r=c.post('/api/assets',files={'file':('empty.txt',b'','text/plain')},data={'conversation_id':conv['id']})
    assert r.status_code==400


def test_asset_cannot_cross_conversations():
    c=TestClient(app)
    a=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    b=c.post('/api/conversations',json={'scene':'aftersales'}).json()
    asset=c.post('/api/assets',files={'file':('private.txt','营业执照 91310000123456789A'.encode(),'text/plain')},data={'conversation_id':a['id']}).json()
    r=c.post(f"/api/conversations/{b['id']}/messages",json={'content':'帮我看看这个','asset_ids':[asset['id']],'provider':'demo'})
    assert r.status_code==409


def test_conversation_scene_reaches_runtime():
    c=TestClient(app)
    conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    asset=c.post('/api/assets',files={'file':('m.txt','营业执照 91310000123456789A'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
    r=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'帮我看看这个','asset_ids':[asset['id']],'provider':'demo'})
    assert r.status_code==200
    detail=c.get(f"/api/conversations/{conv['id']}").json()
    assistant=[m for m in detail['messages'] if m['role']=='assistant'][-1]
    assert assistant['payload']['domain']=='merchant_review'


def test_missing_evidence_action_is_not_executable_side_effect():
    c=TestClient(app)
    conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    r=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'这个商家能过吗','asset_ids':[],'provider':'demo'})
    assert r.status_code==200
    detail=c.get(f"/api/conversations/{conv['id']}").json()
    assert detail['actions']==[]
    assert detail['messages'][-1]['payload']['runtime']['status']=='needs_evidence'


def test_action_approval_is_atomic_under_race():
    from concurrent.futures import ThreadPoolExecutor
    c=TestClient(app)
    conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    asset=c.post('/api/assets',files={'file':('m.txt','营业执照 91310000123456789A 品牌授权书齐全'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
    c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核商家主体资质授权','asset_ids':[asset['id']],'provider':'demo'})
    action=c.get(f"/api/conversations/{conv['id']}").json()['actions'][0]
    assert action['side_effect'] is True
    def approve(i):
        with TestClient(app) as tc:
            return tc.post(f"/api/actions/{action['id']}/decision",json={'decision':'approve','note':str(i)}).status_code
    with ThreadPoolExecutor(max_workers=2) as ex:
        codes=sorted(ex.map(approve,[1,2]))
    assert codes==[200,409]


def test_empty_conversation_scene_can_change_but_historical_one_cannot():
    c=TestClient(app)
    conv=c.post('/api/conversations',json={'scene':'product_governance'}).json()
    changed=c.patch(f"/api/conversations/{conv['id']}",json={'scene':'merchant_review'})
    assert changed.status_code==200 and changed.json()['scene']=='merchant_review'
    c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'先看一下','asset_ids':[],'provider':'demo'})
    blocked=c.patch(f"/api/conversations/{conv['id']}",json={'scene':'aftersales'})
    assert blocked.status_code==409


def test_asset_ids_are_deduplicated_and_capped():
    c=TestClient(app);conv=c.post('/api/conversations',json={'scene':'content_audit'}).json()
    a=c.post('/api/assets',files={'file':('x.txt',b'abc','text/plain')},data={'conversation_id':conv['id']}).json()
    r=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'看资料','asset_ids':[a['id'],a['id']],'provider':'demo'})
    assert r.status_code==200
    detail=c.get(f"/api/conversations/{conv['id']}").json()
    assert detail['messages'][0]['payload']['asset_ids']==[a['id']]
    too_many=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'x','asset_ids':[f'a{i}' for i in range(31)],'provider':'demo'})
    assert too_many.status_code==422


def test_confirmed_action_reaches_configured_mcp_mapping(monkeypatch):
    import importlib
    appmod=importlib.import_module('ecomevo.api.app')
    old_map=dict(appmod.mcp.action_map)
    appmod.mcp.action_map={'merchant.review':{'server':'merchant-core','tool':'submit_review','arguments':{
        'action_id':'${action_id}','conversation_id':'${conversation_id}','score':'${verifier_score}'
    }}}
    seen={}
    async def fake_call(server,tool,args):
        seen.update({'server':server,'tool':tool,'args':args});return {'accepted':True,'business_id':'R-1'}
    monkeypatch.setattr(appmod.mcp,'call_tool',fake_call)
    try:
        c=TestClient(appmod.app)
        conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
        asset=c.post('/api/assets',files={'file':('m.txt','营业执照 91310000123456789A 品牌授权书'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
        c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核主体资质','asset_ids':[asset['id']],'provider':'demo'})
        action=c.get(f"/api/conversations/{conv['id']}").json()['actions'][0]
        assert action['payload']['arguments']['action_id']==action['id']
        r=c.post(f"/api/actions/{action['id']}/decision",json={'decision':'approve','note':'确认'})
        assert r.status_code==200 and r.json()['payload']['execution_mode']=='mcp'
        assert seen['server']=='merchant-core' and seen['tool']=='submit_review'
        assert seen['args']['action_id']==action['id'] and seen['args']['conversation_id']==conv['id']
    finally:
        appmod.mcp.action_map=old_map


def test_whitespace_message_is_rejected():
    c=TestClient(app);conv=c.post('/api/conversations',json={'scene':'product_governance'}).json()
    assert c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'   \n  ','asset_ids':[],'provider':'demo'}).status_code==422


def test_uploaded_asset_has_content_digest_and_runtime_keeps_it():
    c=TestClient(app);conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    content='营业执照 91310000123456789A 品牌授权书'.encode()
    asset=c.post('/api/assets',files={'file':('m.txt',content,'text/plain')},data={'conversation_id':conv['id']}).json()
    digest=asset['meta']['sha256'];assert len(digest)==64
    c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核资料','asset_ids':[asset['id']],'provider':'demo'})
    detail=c.get(f"/api/conversations/{conv['id']}").json();evidence=detail['messages'][-1]['payload']['evidence']
    upload=next(x for x in evidence if x.get('asset_id')==asset['id'])
    assert f'sha256:{digest}' in upload['tags']


def test_api_security_headers_and_no_wildcard_cors_by_default():
    c=TestClient(app)
    r=c.get('/api/health',headers={'Origin':'https://evil.example'})
    assert r.status_code==200
    assert r.headers['x-content-type-options']=='nosniff'
    assert r.headers['x-frame-options']=='DENY'
    assert r.headers['cache-control']=='private, no-store'
    assert r.headers.get('access-control-allow-origin') is None


def test_followup_reuses_task_assets_and_prior_user_context():
    c=TestClient(app)
    conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    asset=c.post('/api/assets',files={'file':('merchant.txt','营业执照 91310000123456789A 品牌授权书齐全'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
    first=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'先核对这个商家的主体和授权','asset_ids':[asset['id']],'provider':'demo'})
    assert first.status_code==200
    second=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'那按刚才资料继续处理','asset_ids':[],'provider':'demo'})
    assert second.status_code==200
    detail=c.get(f"/api/conversations/{conv['id']}").json()
    assistant=[m for m in detail['messages'] if m['role']=='assistant'][-1]
    assert assistant['payload']['runtime']['status']=='completed'
    assert any(e.get('asset_id')==asset['id'] for e in assistant['payload']['evidence'])
    events=c.get(f"/api/runtime/sessions/{assistant['payload']['session_id']}/events").json()
    goal=next(e['payload'] for e in events if e['event_type']=='goal.parsed')
    assert '先核对这个商家的主体和授权' in goal['primary']


def test_asset_content_tampering_is_detected_before_runtime():
    import importlib
    appmod=importlib.import_module('ecomevo.api.app')
    c=TestClient(appmod.app);conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    asset=c.post('/api/assets',files={'file':('m.txt','营业执照 91310000123456789A'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
    stored=appmod.store.get_asset(asset['id'])
    from pathlib import Path
    Path(stored['path']).write_text('tampered content',encoding='utf-8')
    r=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核资料','asset_ids':[asset['id']],'provider':'demo'})
    assert r.status_code==409 and '指纹校验失败' in r.json()['detail']
    detail=c.get(f"/api/conversations/{conv['id']}").json()
    assert detail['messages']==[]

def test_asset_api_hides_internal_paths_and_search_index(tmp_path,monkeypatch):
    c=TestClient(app)
    conv=c.post('/api/conversations',json={'scene':'aftersales','title':'asset-public'}).json()
    body=('prefix '*5000+' ORDER-PRIVATE-42 delivered').encode()
    r=c.post('/api/assets',files={'file':('long.txt',body,'text/plain')},data={'conversation_id':conv['id']})
    assert r.status_code==200
    asset=r.json()
    assert 'path' not in asset
    assert 'search_text' not in asset.get('meta',{})
    assert 'text' not in asset.get('meta',{})
    assert 'keyframes' not in asset.get('meta',{})
    assert asset['url'].endswith('/file')
    detail=c.get(f"/api/conversations/{conv['id']}").json()
    exposed=detail['assets'][0]
    assert 'path' not in exposed
    assert 'search_text' not in exposed.get('meta',{})
    assert 'text' not in exposed.get('meta',{})

def test_asset_upload_must_belong_to_a_task():
    c=TestClient(app)
    r=c.post('/api/assets',files={'file':('x.txt',b'abc','text/plain')})
    assert r.status_code==422

def test_frontend_cross_scene_shortcut_creates_new_task_after_conversation_started():
    js=TestClient(app).get('/assets/app.js').text
    assert "state.messages.length>0&&state.conversation.scene!==scene){await newConversation(scene)" in js
