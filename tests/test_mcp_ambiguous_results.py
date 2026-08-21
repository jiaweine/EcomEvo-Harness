import json

import httpx
import pytest

from ecomevo.runtime.mcp import MCPRegistry, MCPServer


def registry_with_handler(handler):
    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers['core'] = MCPServer(key='core', name='core', url='https://core.example/mcp')
    return registry


def tools_list_response(request, payload):
    return httpx.Response(200, request=request, json={
        'jsonrpc': '2.0',
        'id': payload['id'],
        'result': {'tools': [{'name': 'submit', 'inputSchema': {'type': 'object'}}]},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['invalid-json', 'missing-result', 'is-error', 'internal-error'])
async def test_unconfirmed_tool_call_results_are_ambiguous(mode):
    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode('utf-8'))
        if payload['method'] == 'tools/list':
            return tools_list_response(request, payload)
        if mode == 'invalid-json':
            return httpx.Response(200, request=request, headers={'content-type': 'application/json'}, content=b'{broken')
        if mode == 'missing-result':
            return httpx.Response(200, request=request, json={'jsonrpc': '2.0', 'id': payload['id']})
        if mode == 'is-error':
            return httpx.Response(200, request=request, json={
                'jsonrpc': '2.0', 'id': payload['id'],
                'result': {'content': [{'type': 'text', 'text': 'execution error'}], 'isError': True},
            })
        return httpx.Response(200, request=request, json={
            'jsonrpc': '2.0', 'id': payload['id'],
            'error': {'code': -32603, 'message': 'internal error'},
        })

    registry = registry_with_handler(handler)
    with pytest.raises(httpx.RemoteProtocolError):
        await registry.call_tool('core', 'submit', {'action_id': 'A-1'})


@pytest.mark.asyncio
async def test_jsonrpc_invalid_params_is_a_definite_pre_execution_failure():
    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode('utf-8'))
        if payload['method'] == 'tools/list':
            return tools_list_response(request, payload)
        return httpx.Response(200, request=request, json={
            'jsonrpc': '2.0', 'id': payload['id'],
            'error': {'code': -32602, 'message': 'invalid params'},
        })

    registry = registry_with_handler(handler)
    with pytest.raises(RuntimeError) as exc:
        await registry.call_tool('core', 'submit', {'action_id': 'bad'})
    assert not isinstance(exc.value, httpx.RemoteProtocolError)
    assert 'invalid params' in str(exc.value)
