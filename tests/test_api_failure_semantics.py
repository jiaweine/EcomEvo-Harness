import importlib

import httpx
from fastapi.testclient import TestClient

from ecomevo.models import BusinessAction


appmod = importlib.import_module('ecomevo.api.app')


def new_conv(client):
    return client.post('/api/conversations', json={'scene': 'merchant_review'}).json()


def test_active_turn_rejects_upload_before_media_validation(monkeypatch):
    with TestClient(appmod.app) as client:
        conv = new_conv(client)
        cid = conv['id']
        lease = appmod.store.claim_turn(cid)
        assert lease

        def should_not_validate(*_args, **_kwargs):
            raise AssertionError('media validation must not run for an active turn')

        monkeypatch.setattr(appmod, '_validate_uploaded_file', should_not_validate)
        try:
            response = client.post(
                '/api/assets',
                files={'file': ('proof.txt', b'evidence', 'text/plain')},
                data={'conversation_id': cid},
            )
            assert response.status_code == 409
            assert '正在处理中' in response.json()['detail']
        finally:
            appmod.store.release_turn(cid, lease)


def test_connect_error_marks_high_impact_action_uncertain_and_non_retryable(monkeypatch):
    with TestClient(appmod.app) as client:
        conv = new_conv(client)
        action = BusinessAction(
            action_id='connect-uncertain-action',
            kind='merchant.review',
            title='审核',
            description='提交审核',
            payload={'mcp_server': 'x', 'mcp_tool': 'y', 'arguments': {}},
        )
        appmod.store.save_actions(conv['id'], 's1', [action])
        request = httpx.Request('POST', 'https://business.example/mcp')

        async def fail_connect(*_args, **_kwargs):
            raise httpx.ConnectError('connection reset', request=request)

        monkeypatch.setattr(appmod.mcp, 'call_tool', fail_connect)
        response = client.post(
            '/api/actions/connect-uncertain-action/decision',
            json={'decision': 'approve', 'note': ''},
        )
        assert response.status_code == 502
        row = appmod.store.get_action('connect-uncertain-action')
        assert row['status'] == 'uncertain'
        assert row['payload']['execution_outcome'] == 'unknown'

        second = client.post(
            '/api/actions/connect-uncertain-action/decision',
            json={'decision': 'approve', 'note': ''},
        )
        assert second.status_code == 409
