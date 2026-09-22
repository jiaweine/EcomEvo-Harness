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
    assert row['governance']['effective_tool_scope'] == 'mixed'


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


def test_governance_contract_surfaces_scope_owner_tags_and_idempotency(monkeypatch):
    monkeypatch.setenv('OMS_SECRET_TOKEN', 'configured-secret')
    monkeypatch.setenv('ECOMEVO_MCP_CONNECTION_META', json.dumps({
        'oms': {
            'authority': 'authoritative_internal',
            'freshness': 'live',
            'data_source': 'OMS production replica',
            'access_scope': 'mixed',
            'credential_owner': 'platform-security',
            'evidence_tags': ['order_core', 'shipment'],
            'idempotency': 'required',
        },
    }))
    registry = MCPRegistry()
    registry.servers = {
        'oms': MCPServer('oms', '订单中心', 'https://private.example/mcp', 'OMS_SECRET_TOKEN')
    }
    registry.read_tools = [{
        'key': 'mcp.orders',
        'domain': 'aftersales',
        'server': 'oms',
        'tool': 'get_order',
        'purpose': '读取订单',
        'arguments': {},
        'evidence_tags': ['order_identity', 'shipment'],
        'cost': 1.0,
    }]
    registry.action_map = {
        'aftersales.refund': {
            'server': 'oms',
            'tool': 'refund',
            'arguments': {'id': 'fixed-action-id'},
        },
    }

    row = MCPConnectionCatalog(registry).get('oms')
    governance = row['governance']
    tools = {tool['name']: tool for tool in row['tools']}

    assert governance == {
        'data_source': 'OMS production replica',
        'declared_access_scope': 'mixed',
        'effective_tool_scope': 'mixed',
        'credential_owner': 'platform-security',
        'evidence_tags': ['order_core', 'order_identity', 'shipment'],
        'idempotency': 'required',
        'warnings': [],
    }
    assert tools['get_order']['evidence_tags'] == ['order_identity', 'shipment']
    assert tools['get_order']['idempotency'] == 'not_applicable'
    assert tools['refund']['idempotency'] == 'required'


def test_governance_mismatches_fail_visible_without_changing_runtime(monkeypatch):
    monkeypatch.setenv('CORE_TOKEN', 'configured-secret')
    monkeypatch.setenv('ECOMEVO_MCP_CONNECTION_META', json.dumps({
        'core': {'access_scope': 'read_only'},
    }))
    registry = MCPRegistry()
    registry.servers = {
        'core': MCPServer('core', '核心系统', 'https://private.example/mcp', 'CORE_TOKEN')
    }
    registry.action_map = {
        'merchant.approve': {'server': 'core', 'tool': 'approve_merchant', 'arguments': {}},
    }

    row = MCPConnectionCatalog(registry).get('core')
    warnings = set(row['governance']['warnings'])
    assert row['governance']['declared_access_scope'] == 'read_only'
    assert row['governance']['effective_tool_scope'] == 'governed_write'
    assert row['governance']['credential_owner'] is None
    assert row['governance']['idempotency'] == 'unknown'
    assert warnings == {
        'declared_read_only_but_action_binding_exists',
        'credential_owner_not_declared',
        'governed_action_idempotency_not_declared',
    }
    assert row['tools'][0]['capability'] == 'governed_action'


def test_conflicting_action_idempotency_is_fail_visible(monkeypatch):
    monkeypatch.setenv('ECOMEVO_MCP_CONNECTION_META', json.dumps({
        'core': {'access_scope': 'governed_write', 'idempotency': 'required'},
    }))
    registry = MCPRegistry()
    registry.servers = {'core': MCPServer('core', '核心系统', 'https://core.example/mcp')}
    registry.action_map = {
        'merchant.approve': {
            'server': 'core',
            'tool': 'review_case',
            'arguments': {},
            'idempotency': 'required',
        },
        'merchant.reject': {
            'server': 'core',
            'tool': 'review_case',
            'arguments': {},
            'idempotency': 'supported',
        },
    }

    row = MCPConnectionCatalog(registry).get('core')
    tool = row['tools'][0]
    assert tool['idempotency'] == 'unknown'
    assert tool['idempotency_conflict'] is True
    assert 'governed_action_idempotency_conflict' in row['governance']['warnings']
    assert 'governed_action_idempotency_not_declared' not in row['governance']['warnings']


def test_structured_governance_metadata_is_not_stringified_to_browser(monkeypatch):
    monkeypatch.setenv('ECOMEVO_MCP_CONNECTION_META', json.dumps({
        'core': {
            'data_source': {'secret': 'do-not-render'},
            'credential_owner': {'token': 'do-not-render'},
            'evidence_tags': [{'secret': 'do-not-render'}, 'safe_tag'],
        },
    }))
    registry = MCPRegistry()
    registry.servers = {'core': MCPServer('core', '核心系统', 'https://core.example/mcp')}

    row = MCPConnectionCatalog(registry).get('core')
    encoded = json.dumps(row, ensure_ascii=False)
    assert row['governance']['data_source'] == '核心系统'
    assert row['governance']['credential_owner'] is None
    assert row['governance']['evidence_tags'] == ['safe_tag']
    assert 'do-not-render' not in encoded


def test_probe_history_tracks_failure_rate_latency_and_schema_drift(tmp_path):
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        payload = json.loads(request.content.decode('utf-8'))
        assert payload['method'] == 'tools/list'
        calls += 1
        if calls == 4:
            raise httpx.ConnectError('private-host failed', request=request)
        schema = {
            'type': 'object',
            'properties': {'order_id': {'type': 'string'}},
        }
        if calls == 3:
            schema['properties']['market'] = {'type': 'string'}
        return httpx.Response(200, request=request, json={
            'jsonrpc': '2.0',
            'id': payload['id'],
            'result': {'tools': [{
                'name': 'get_order',
                'description': 'lookup',
                'inputSchema': schema,
            }]},
        })

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers = {'oms': MCPServer('oms', '订单中心', 'https://private.example/mcp')}
    catalog = MCPConnectionCatalog(registry, history_path=tmp_path / 'connections.db')

    first = asyncio.run(catalog.probe('oms'))
    second = asyncio.run(catalog.probe('oms'))
    third = asyncio.run(catalog.probe('oms'))
    fourth = asyncio.run(catalog.probe('oms'))

    assert first['schema']['change'] == 'first_observation'
    assert second['schema']['change'] == 'unchanged'
    assert second['schema']['fingerprint'] == first['schema']['fingerprint']
    assert third['schema']['change'] == 'changed'
    assert third['schema']['fingerprint'] != first['schema']['fingerprint']
    assert fourth['health']['state'] == 'unhealthy'
    assert fourth['health']['error'] == '连接检查失败'

    history = catalog.history('oms')
    reliability = history['reliability']
    assert reliability['observations'] == 4
    assert reliability['healthy'] == 3
    assert reliability['unhealthy'] == 1
    assert reliability['success_rate'] == 0.75
    assert reliability['failure_rate'] == 0.25
    assert reliability['latency_ms']['p95'] is not None
    assert reliability['latest_state'] == 'unhealthy'
    assert reliability['latest_schema_change'] == 'changed'
    assert reliability['latest_schema_fingerprint'] == third['schema']['fingerprint']
    assert reliability['latest_schema_observed_at'] is not None
    assert reliability['last_schema_change_at'] is not None
    assert history['observations'][1]['schema_change'] == 'changed'
    assert len(history['observations'][1]['schema_fingerprint']) == 64

    encoded = json.dumps(history, ensure_ascii=False)
    assert 'private.example' not in encoded
    assert 'inputSchema' not in encoded
    assert 'order_id' not in encoded
    assert history['safety'] == {
        'business_tool_execution': False,
        'secrets_exposed': False,
        'configuration_mutation': False,
    }


def test_connection_history_route_is_admin_only_and_persistent(monkeypatch, tmp_path):
    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode('utf-8'))
        assert payload['method'] == 'tools/list'
        return httpx.Response(200, request=request, json={
            'jsonrpc': '2.0',
            'id': payload['id'],
            'result': {'tools': [{'name': 'lookup', 'inputSchema': {'type': 'object'}}]},
        })

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers = {'core': MCPServer('core', '核心系统', 'https://core.example/mcp')}
    app = FastAPI()
    install_connection_routes(app, registry, Path(tmp_path), Path(tmp_path))
    app.add_middleware(IdentityMiddleware)

    monkeypatch.setenv('ECOMEVO_AUTH_MODE', 'local')
    monkeypatch.setenv('ECOMEVO_LOCAL_ROLE', 'admin')
    with TestClient(app) as client:
        assert client.post('/api/runtime/connections/core/probe').status_code == 200
        history = client.get('/api/runtime/connections/core/history')
        assert history.status_code == 200
        assert history.json()['reliability']['observations'] == 1
        assert history.json()['observations'][0]['schema_change'] == 'first_observation'

    monkeypatch.setenv('ECOMEVO_LOCAL_ROLE', 'viewer')
    with TestClient(app) as client:
        assert client.get('/api/runtime/connections/core/history').status_code == 403


def test_release_evidence_fails_closed_until_probe_and_schema_are_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_TOKEN", "configured-secret")
    monkeypatch.setenv("ECOMEVO_MCP_CONNECTION_META", json.dumps({
        "core": {
            "access_scope": "read_only",
            "credential_owner": "platform-security",
        },
    }))

    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["method"] == "tools/list"
        calls += 1
        schema = {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
        }
        if calls == 3:
            schema["properties"]["market"] = {"type": "string"}
        return httpx.Response(200, request=request, json={
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {"tools": [{"name": "lookup", "inputSchema": schema}]},
        })

    registry = MCPRegistry(transport=httpx.MockTransport(handler))
    registry.servers = {
        "core": MCPServer("core", "核心系统", "https://core.example/mcp", "CORE_TOKEN")
    }
    registry.read_tools = [{
        "key": "mcp.orders",
        "domain": "aftersales",
        "server": "core",
        "tool": "lookup",
        "purpose": "读取订单",
        "arguments": {},
        "evidence_tags": ["order_identity"],
        "cost": 1.0,
    }]
    catalog = MCPConnectionCatalog(registry, history_path=tmp_path / "connections.db")

    initial = catalog.release_evidence()
    assert initial["status"] == "blocked"
    assert "core:probe_history_missing" in initial["blocker_ids"]

    asyncio.run(catalog.probe("core"))
    baseline = catalog.release_evidence()
    assert "core:schema_baseline_not_confirmed" in baseline["blocker_ids"]

    asyncio.run(catalog.probe("core"))
    confirmed = catalog.release_evidence()
    assert confirmed["status"] == "control_plane_ready"
    assert confirmed["ready"] is True
    assert confirmed["blocker_count"] == 0

    asyncio.run(catalog.probe("core"))
    drifted = catalog.release_evidence()
    assert drifted["status"] == "blocked"
    assert "core:schema_change_not_revalidated" in drifted["blocker_ids"]
    assert drifted["methodology"]["business_tool_execution"] is False
    assert drifted["methodology"]["provider_rate_limits_certified"] is False

    asyncio.run(catalog.probe("core"))
    revalidated = catalog.release_evidence()
    assert revalidated["status"] == "control_plane_ready"
    assert revalidated["connections"][0]["probe_evidence"]["latest_schema_change"] == "unchanged"


def test_release_evidence_blocks_missing_required_credential_without_exposing_secret_name(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("CORE_PRIVATE_TOKEN", raising=False)
    monkeypatch.setenv("ECOMEVO_MCP_CONNECTION_META", json.dumps({
        "core": {
            "access_scope": "read_only",
            "credential_owner": "platform-security",
        },
    }))
    registry = MCPRegistry()
    registry.servers = {
        "core": MCPServer("core", "核心系统", "https://core.example/mcp", "CORE_PRIVATE_TOKEN")
    }
    catalog = MCPConnectionCatalog(registry, history_path=tmp_path / "connections.db")

    evidence = catalog.release_evidence()
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert "core:credential_not_configured" in evidence["blocker_ids"]
    assert "CORE_PRIVATE_TOKEN" not in encoded
    assert evidence["connections"][0]["governance"]["auth_required"] is True
    assert evidence["connections"][0]["governance"]["auth_configured"] is False
