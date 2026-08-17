import importlib

from fastapi.testclient import TestClient

from ecomevo.product.store import ConversationStore


api = importlib.import_module('ecomevo.api.app')


def _isolated_store(tmp_path, monkeypatch):
    isolated = ConversationStore(tmp_path / 'product.db', tmp_path / 'assets')
    monkeypatch.setattr(api, 'store', isolated)
    monkeypatch.setattr(api, 'queues', {})
    monkeypatch.setattr(api, 'WS_POLL_SECONDS', 0.05)
    return isolated


def test_websocket_catches_event_written_outside_process_local_emit(tmp_path, monkeypatch):
    isolated = _isolated_store(tmp_path, monkeypatch)
    cid = isolated.create_conversation()['id']

    with TestClient(api.app) as client:
        with client.websocket_connect(f'/ws/conversations/{cid}') as socket:
            written = isolated.add_event(cid, 'external.process', {'source': 'other-worker'})
            received = socket.receive_json()

    assert received['id'] == written['id']
    assert received['type'] == 'external.process'
    assert received['payload']['source'] == 'other-worker'


def test_websocket_cutoff_prevents_history_replay_when_durable_poll_catches_up(tmp_path, monkeypatch):
    isolated = _isolated_store(tmp_path, monkeypatch)
    cid = isolated.create_conversation()['id']
    first = isolated.add_event(cid, 'history.event', {'seq': 1})

    with TestClient(api.app) as client:
        with client.websocket_connect(f'/ws/conversations/{cid}') as socket:
            history = socket.receive_json()
            second = isolated.add_event(cid, 'external.process', {'seq': 2})
            live = socket.receive_json()

    assert history['id'] == first['id']
    assert live['id'] == second['id']
    assert live['payload']['seq'] == 2
    assert live['id'] > history['id']


def test_websocket_after_id_replays_only_newer_events(tmp_path, monkeypatch):
    isolated = _isolated_store(tmp_path, monkeypatch)
    cid = isolated.create_conversation()['id']
    first = isolated.add_event(cid, 'history.event', {'seq': 1})
    second = isolated.add_event(cid, 'history.event', {'seq': 2})

    with TestClient(api.app) as client:
        with client.websocket_connect(f'/ws/conversations/{cid}?after_id={first["id"]}') as socket:
            received = socket.receive_json()

    assert received['id'] == second['id']
    assert received['payload']['seq'] == 2


def test_websocket_too_high_cursor_is_clamped_and_does_not_starve_future_events(tmp_path, monkeypatch):
    isolated = _isolated_store(tmp_path, monkeypatch)
    cid = isolated.create_conversation()['id']
    first = isolated.add_event(cid, 'history.event', {'seq': 1})

    with TestClient(api.app) as client:
        with client.websocket_connect(f'/ws/conversations/{cid}?after_id=999999') as socket:
            second = isolated.add_event(cid, 'external.process', {'seq': 2})
            received = socket.receive_json()

    assert received['id'] == second['id']
    assert received['id'] > first['id']
    assert received['payload']['seq'] == 2


def test_message_accepted_event_contains_message_for_multi_tab_reconciliation():
    source = (api.ROOT / 'ecomevo' / 'api' / 'app.py').read_text(encoding='utf-8')
    assert "await emit(cid,'message.accepted',{'message_id':user['id'],'message':user" in source
