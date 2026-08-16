import asyncio, json
import httpx
from ecomevo.runtime.mcp import MCPRegistry, MCPServer, MODERN_VERSION, LEGACY_VERSION


def test_modern_mcp_headers_sse_and_x_mcp_header():
    seen=[]
    def handler(request:httpx.Request):
        body=json.loads(request.content.decode())
        seen.append((body,dict(request.headers)))
        assert request.headers['mcp-protocol-version']==MODERN_VERSION
        assert request.headers['mcp-method']==body['method']
        if body['method']=='tools/list':
            return httpx.Response(200,headers={'content-type':'application/json'},json={
                'jsonrpc':'2.0','id':body['id'],'result':{'ttlMs':5000,'tools':[{
                    'name':'submit_review','inputSchema':{'type':'object','properties':{
                        'tenant':{'type':'string','x-mcp-header':'Tenant'},'case_id':{'type':'string'}
                    }}
                }]}
            })
        assert request.headers['mcp-name']=='submit_review'
        assert request.headers['mcp-param-tenant']=='cn-east'
        data=json.dumps({'jsonrpc':'2.0','id':body['id'],'result':{'accepted':True}})
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=f'event: message\ndata: {data}\n\n')
    reg=MCPRegistry(transport=httpx.MockTransport(handler));reg.servers['biz']=MCPServer('biz','业务系统','https://mcp.test/mcp')
    result=asyncio.run(reg.call_tool('biz','submit_review',{'tenant':'cn-east','case_id':'C1'}))
    assert result=={'accepted':True}
    assert len(seen)==2


def test_legacy_mcp_fallback_initializes_and_uses_session():
    calls=[]
    def handler(request:httpx.Request):
        body=json.loads(request.content.decode());calls.append(body.get('method'))
        if body.get('method')=='tools/list':
            return httpx.Response(400,headers={'content-type':'text/plain'},content='legacy server')
        if body.get('method')=='initialize':
            return httpx.Response(200,headers={'content-type':'application/json','MCP-Session-Id':'sess-1'},json={
                'jsonrpc':'2.0','id':body['id'],'result':{'protocolVersion':LEGACY_VERSION,'capabilities':{'tools':{}},'serverInfo':{'name':'legacy','version':'1'}}
            })
        if body.get('method')=='notifications/initialized':
            assert request.headers['mcp-session-id']=='sess-1';return httpx.Response(202)
        if body.get('method')=='tools/call':
            assert request.headers['mcp-protocol-version']==LEGACY_VERSION
            assert request.headers['mcp-session-id']=='sess-1'
            return httpx.Response(200,headers={'content-type':'application/json'},json={'jsonrpc':'2.0','id':body['id'],'result':{'ok':1}})
        raise AssertionError(body)
    reg=MCPRegistry(transport=httpx.MockTransport(handler));reg.servers['legacy']=MCPServer('legacy','旧系统','https://legacy.test/mcp')
    result=asyncio.run(reg.call_tool('legacy','do_work',{'x':1}))
    assert result=={'ok':1}
    assert calls==['tools/list','initialize','notifications/initialized','tools/call']


def test_action_mcp_binding_expands_context():
    reg=MCPRegistry();reg.action_map={'aftersales.review':{'server':'oms','tool':'submit_case','arguments':{
        'conversation':'${conversation_id}','score':'${verifier_score}','fixed':True
    }}}
    row=reg.action_binding('aftersales.review',{'conversation_id':'cv-1','verifier_score':0.81})
    assert row=={'mcp_server':'oms','mcp_tool':'submit_case','arguments':{'conversation':'cv-1','score':0.81,'fixed':True}}


def test_mcp_read_tool_specs_are_loaded_from_env(monkeypatch):
    from ecomevo.runtime.mcp import MCPRegistry
    monkeypatch.setenv('ECOMEVO_MCP_READ_TOOLS','[{"key":"mcp.orders","domain":"aftersales","server":"oms","tool":"get_order","arguments":{"q":"${text}"},"evidence_tags":["order_identity","dispute_fact"],"cost":0.7}]')
    reg=MCPRegistry()
    rows=reg.read_tool_specs()
    assert rows[0]['key']=='mcp.orders' and rows[0]['domain']=='aftersales'
    assert rows[0]['evidence_tags']==['order_identity','dispute_fact']


def test_modern_tool_application_error_never_replays_via_legacy():
    calls=[]
    def handler(request:httpx.Request):
        body=json.loads(request.content.decode());calls.append(body['method'])
        if body['method']=='tools/list':
            return httpx.Response(200,headers={'content-type':'application/json'},json={'jsonrpc':'2.0','id':body['id'],'result':{'tools':[{'name':'refund','inputSchema':{'type':'object'}}]}})
        if body['method']=='tools/call':
            return httpx.Response(400,headers={'content-type':'application/json'},json={'jsonrpc':'2.0','id':body['id'],'error':{'code':-32602,'message':'invalid amount'}})
        if body['method']=='initialize':
            raise AssertionError('side-effect application error must not trigger legacy replay')
        raise AssertionError(body)
    reg=MCPRegistry(transport=httpx.MockTransport(handler));reg.servers['biz']=MCPServer('biz','业务系统','https://mcp.test/mcp')
    import pytest
    with pytest.raises(RuntimeError):asyncio.run(reg.call_tool('biz','refund',{'amount':-1}))
    assert calls==['tools/list','tools/call']


def test_mcp_server_list_does_not_leak_endpoint_or_token_env(monkeypatch):
    reg=MCPRegistry();reg.servers['internal']=MCPServer('internal','订单中心','https://secret.internal/mcp','VERY_SECRET_TOKEN')
    row=next(x for x in reg.list() if x['key']=='internal')
    assert row=={'key':'internal','name':'订单中心','enabled':True,'configured':True}
    assert 'url' not in row and 'token_env' not in row
