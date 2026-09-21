from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend/connections.html').read_text(encoding='utf-8')
JS = (ROOT / 'frontend/connections.js').read_text(encoding='utf-8')
CSS = (ROOT / 'frontend/connections.css').read_text(encoding='utf-8')
ROUTES = (ROOT / 'ecomevo/api/connection_routes.py').read_text(encoding='utf-8')


def test_connections_console_is_admin_runtime_surface():
    assert '/api/runtime/connections/ui' in ROUTES
    assert '/api/runtime/connections/{key}/probe' in ROUTES
    assert '/api/runtime/connections/{key}/history' in ROUTES
    assert '.call_tool(' not in ROUTES
    assert '"tools/call"' not in ROUTES
    assert 'No route in this module can execute an MCP business tool.' in ROUTES


def test_console_explains_deployment_scope_and_read_only_probe():
    assert '部署级声明' in HTML
    assert '只读探测' in HTML
    assert 'tools/list' in HTML
    assert '不会触发 <code>tools/call</code>' in HTML
    assert '治理 ≠ 授权' in HTML
    assert '不会修改连接配置' in HTML
    assert 'schema fingerprint' in HTML


def test_governance_and_reliability_are_visible_without_becoming_authority():
    assert 'Declared scope' in JS
    assert 'Credential owner' in JS
    assert 'Evidence tags' in JS
    assert 'Idempotency' in JS
    assert 'Failure rate' in JS
    assert 'P95 latency' in JS
    assert 'Schema fingerprint' in JS
    assert 'configuration_mutation' not in JS or 'tools/call' not in JS


def test_unknown_tools_are_visible_not_guessed_safe():
    assert 'Unknown' in JS
    assert "capability === 'unknown'" in JS
    assert '尚未声明用途' in JS
    assert 'Governed Action' in JS


def test_console_never_renders_endpoint_or_secret_fields():
    assert 'token_env' not in JS
    assert '.url' not in JS
    assert 'authorization' not in JS.lower()
    assert 'secret' not in JS.lower()
    assert 'inputSchema' not in JS
    assert 'outputSchema' not in JS


def test_console_has_mobile_layout():
    assert '@media(max-width:820px)' in CSS
    assert 'min-height:44px' in CSS
