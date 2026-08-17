import json

import httpx
import pytest

from ecomevo.runtime.mcp import LEGACY_VERSION, MCPRegistry, MCPServer


@pytest.mark.asyncio
async def test_all_mcp_transport_timeouts_normalize_to_uncertain_compatible_read_timeout(monkeypatch):
    registry = MCPRegistry()
    registry.servers['core'] = MCPServer(key='core', name='core', url='https://core.example/mcp')
    request = httpx.Request('POST', 'https://core.example/mcp')

    async def connect_timeout(*_args, **_kwargs):
        raise httpx.ConnectTimeout('connect stalled', request=request)

    monkeypatch.setattr(registry, '_call_modern', connect_timeout)

    with pytest.raises(httpx.ReadTimeout) as exc:
        await registry.call_tool('core', 'submit', {'id': 'A-1'})

    assert exc.value.request.url == request.url
    assert isinstance(exc.value.__cause__, httpx.ConnectTimeout)


def test_mcp_timeout_configuration_is_bounded(monkeypatch):
    monkeypatch.setenv('ECOMEVO_MCP_TIMEOUT_SECONDS', '999')
    assert MCPRegistry().timeout_s == 120.0
    monkeypatch.setenv('ECOMEVO_MCP_TIMEOUT_SECONDS', '0.1')
    assert MCPRegistry().timeout_s == 3.0
    monkeypatch.setenv('ECOMEVO_MCP_TIMEOUT_SECONDS', 'not-a-number')
    assert MCPRegistry().timeout_s == 30.0


@pytest.mark.asyncio
async def test_stale_legacy_session_never_replays_tools_call():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(404, request=request, json={'error': 'session expired'})

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    server = MCPServer(key='legacy', name='legacy', url='https://legacy.example/mcp')
    registry.servers['legacy'] = server
    registry._legacy_sessions['legacy'] = (LEGACY_VERSION, 'stale-session')

    with pytest.raises(httpx.RemoteProtocolError):
        await registry._call_legacy(server, 'submit_business_action', {'action_id': 'A-1'})

    assert len(calls) == 1
    assert 'legacy' not in registry._legacy_sessions


@pytest.mark.asyncio
async def test_modern_tool_call_5xx_is_ambiguous_and_never_falls_back_or_replays():
    calls = []

    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode('utf-8'))
        calls.append(payload['method'])
        if payload['method'] == 'tools/list':
            return httpx.Response(200, request=request, json={
                'jsonrpc': '2.0', 'id': payload['id'],
                'result': {'tools': [{'name': 'submit_business_action', 'inputSchema': {'type': 'object'}}]},
            })
        return httpx.Response(503, request=request, json={
            'jsonrpc': '2.0', 'id': payload['id'],
            'error': {'code': -32000, 'message': 'upstream unavailable'},
        })

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers['core'] = MCPServer(key='core', name='core', url='https://core.example/mcp')

    with pytest.raises(httpx.RemoteProtocolError):
        await registry.call_tool('core', 'submit_business_action', {'action_id': 'A-2'})

    assert calls == ['tools/list', 'tools/call']


@pytest.mark.asyncio
async def test_modern_tool_call_explicit_4xx_remains_explicit_failure():
    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode('utf-8'))
        if payload['method'] == 'tools/list':
            return httpx.Response(200, request=request, json={
                'jsonrpc': '2.0', 'id': payload['id'],
                'result': {'tools': [{'name': 'submit_business_action', 'inputSchema': {'type': 'object'}}]},
            })
        return httpx.Response(422, request=request, json={
            'jsonrpc': '2.0', 'id': payload['id'],
            'error': {'code': -32602, 'message': 'invalid arguments'},
        })

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers['core'] = MCPServer(key='core', name='core', url='https://core.example/mcp')

    with pytest.raises(RuntimeError) as exc:
        await registry.call_tool('core', 'submit_business_action', {'action_id': 'bad'})

    assert not isinstance(exc.value, httpx.RemoteProtocolError)
    assert '422' in str(exc.value)
