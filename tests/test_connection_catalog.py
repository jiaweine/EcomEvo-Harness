from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecomevo.api.connection_routes import install_connection_routes
from ecomevo.identity import IdentityMiddleware
from ecomevo.product.connection_catalog import MCPConnectionCatalog
from ecomevo.runtime.mcp import LEGACY_VERSION, MODERN_VERSION, MCPRegistry, MCPServer


def test_catalog_redacts_endpoint_secret_names_and_values(monkeypatch):
    monkeypatch.setenv('OMS_SECRET_TOKEN', 'super-secret-value')
    monkeypatch.setenv('ECOMEVO_MCP_CONNECTION_META', json.dumps({
        'oms': {'authority': 'authoritative_internal', 'freshness': 'live'},
    }))
    registry = MCPRegistry()
    registry.servers = {
        'oms': MCPServer('oms', '订单中心', 'https://private.internal.example/mcp', 'OMS_SECRET_TOKEN')
    }
    registry.read_tools = [{
        'key': 'mcp.orders', 'domain': 'aftersales', 'server': 'oms', 'tool': 'get_order',
        'purpose': '读取订单', 'arguments': {}, 'evidence_tags': ['order_identity'], 'cost': 1.0,
    }]
    registry.action_map = {
        'aftersales.refund': {'server': 'oms', 'tool': 'refund', 'arguments': {'id': '${action_id}'}},
    }

    payload = MCPConnectionCatalog(registry).list()
    encoded = json.dumps(payload, ensure_ascii=False)
    row = payload['connections'][0]

    assert row['authority'] == 'authoritative_internal'
    assert row['freshness'] == 'live'
    assert row['auth_configured'] is True
    assert 'private.internal.example' not in encoded
    assert 'OMS_SECRET_TOKEN' not in encoded
    assert 'super-secret-value' not in encoded
    assert {tool['capability'] for tool in row['tools']} == {'read', 'governed_action'}


def test_action_binding_wins_over_read_classification():
    registry = MCPRegistry()
    registry.servers = {'core': MCPServer('core', '核心系统', 'https://core.example/mcp')}
    registry.read_tools = [{
        'key': 'mcp.shared', 'domain': 'general', 'server': 'core', 'tool': 'shared_tool',
        'purpose': '读取', 'arguments': {}, 'evidence_tags': [], 'cost': 1.0,
    }]
    registry.action_map = {'merchant.review': {'server': 'core', 'tool': 'shared_tool', 'arguments': {}}}

    tool = MCPConnectionCatalog(registry).get('core')['tools'][0]
    assert tool['capability'] == 'governed_action'
    assert tool['risk'] == 'governed_side_effect'
    assert 'action:merchant.review' in tool['bindings']


def test_invalid_connection_metadata_fails_closed_to_unknown(monkeypatch):
    monkeypatch.setenv('ECOMEVO_MCP_CONNECTION_META', json.dumps({
        'core': {'authority': 'super_trusted', 'freshness': 'forever_fresh'},
    }))
    registry = MCPRegistry()
    registry.servers = {'core': MCPServer('core', '核心系统', 'https://core.example/mcp')}
    row = MCPConnectionCatalog(registry).get('core')
    assert row['authority'] == 'unknown'
    assert row['freshness'] == 'unknown'


def test_probe_only_discovers_tools_and_unknown_stays_unknown():
    calls = []

    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode('utf-8'))
        calls.append(payload['method'])
        assert payload['method'] == 'tools/list'
        return httpx.Response(200, request=request, json={
            'jsonrpc': '2.0', 'id': payload['id'],
            'result': {'tools': [
                {'name': 'get_order', 'description': 'Order lookup'},
                {'name': 'mystery_tool', 'description': 'No explicit EcomEvo binding'},
            ]},
        })

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers = {'oms': MCPServer('oms', '订单中心', 'https://oms.example/mcp')}
    registry.read_tools = [{
        'key': 'mcp.orders', 'domain': 'aftersales', 'server': 'oms', 'tool': 'get_order',
        'purpose': '读取订单', 'arguments': {}, 'evidence_tags': [], 'cost': 1.0,
    }]

    result = asyncio.run(MCPConnectionCatalog(registry).probe('oms'))
    tools = {row['name']: row for row in result['tools']}
    assert result['health']['state'] == 'healthy'
    assert result['health']['protocol'] == MODERN_VERSION
    assert calls == ['tools/list']
    assert tools['get_order']['capability'] == 'read'
    assert tools['mystery_tool']['capability'] == 'unknown'
    assert tools['mystery_tool']['risk'] == 'unknown'


def test_probe_supports_legacy_discovery_without_tool_call():
    calls = []

    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode('utf-8'))
        method = payload.get('method')
        calls.append(method)
        if method == 'tools/list' and 'mcp-protocol-version' in request.headers and request.headers['mcp-protocol-version'] == MODERN_VERSION:
            return httpx.Response(400, request=request, content='legacy endpoint')
        if method == 'initialize':
            return httpx.Response(200, request=request, headers={'MCP-Session-Id': 'sess-console'}, json={
                'jsonrpc': '2.0', 'id': payload['id'],
                'result': {'protocolVersion': LEGACY_VERSION, 'capabilities': {'tools': {}}},
            })
        if method == 'notifications/initialized':
            return httpx.Response(202, request=request)
        if method == 'tools/list':
            assert request.headers['mcp-session-id'] == 'sess-console'
            return httpx.Response(200, request=request, json={
                'jsonrpc': '2.0', 'id': payload['id'],
                'result': {'tools': [{'name': 'legacy_lookup', 'description': 'legacy read discovery'}]},
            })
        raise AssertionError(payload)

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers = {'legacy': MCPServer('legacy', '旧系统', 'https://legacy.example/mcp')}
    result = asyncio.run(MCPConnectionCatalog(registry).probe('legacy'))

    assert result['health']['state'] == 'healthy'
    assert result['health']['protocol'] == LEGACY_VERSION
    assert calls == ['tools/list', 'initialize', 'notifications/initialized', 'tools/list']
    assert 'tools/call' not in calls
    assert result['tools'][0]['capability'] == 'unknown'


def test_probe_failure_is_sanitized(monkeypatch):
    monkeypatch.setenv('VERY_PRIVATE_TOKEN', 'do-not-leak')

    def handler(request: httpx.Request):
        raise httpx.ConnectError('secret-host.internal failed with do-not-leak', request=request)

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers = {
        'core': MCPServer('core', '核心系统', 'https://secret-host.internal/mcp', 'VERY_PRIVATE_TOKEN')
    }
    result = asyncio.run(MCPConnectionCatalog(registry).probe('core'))
    encoded = json.dumps(result, ensure_ascii=False)
    assert result['health']['state'] == 'unhealthy'
    assert result['health']['error'] == '连接检查失败'
    assert 'secret-host' not in encoded
    assert 'do-not-leak' not in encoded
    assert 'VERY_PRIVATE_TOKEN' not in encoded


def test_connection_routes_inherit_runtime_admin_rbac(monkeypatch, tmp_path):
    registry = MCPRegistry()
    registry.servers = {'core': MCPServer('core', '核心系统', 'https://core.example/mcp')}
    app = FastAPI()
    install_connection_routes(app, registry, Path(tmp_path))
    app.add_middleware(IdentityMiddleware)

    monkeypatch.setenv('ECOMEVO_AUTH_MODE', 'local')
    monkeypatch.setenv('ECOMEVO_LOCAL_ROLE', 'viewer')
    with TestClient(app) as client:
        denied = client.get('/api/runtime/connections')
    assert denied.status_code == 403

    monkeypatch.setenv('ECOMEVO_LOCAL_ROLE', 'admin')
    with TestClient(app) as client:
        allowed = client.get('/api/runtime/connections')
    assert allowed.status_code == 200
    assert allowed.json()['scope'] == 'deployment'


def test_console_routes_do_not_expose_business_tool_execution():
    registry = MCPRegistry()
    app = FastAPI()
    install_connection_routes(app, registry, Path('.'))
    paths = {(route.path, method) for route in app.routes for method in getattr(route, 'methods', set())}
    assert ('/api/runtime/connections/{key}/probe', 'POST') in paths
    assert all('tools/call' not in path and 'execute' not in path for path, _method in paths)
