from concurrent.futures import ThreadPoolExecutor

from ecomevo.models import BusinessAction
from ecomevo.product.store import ConversationStore


def test_concurrent_turn_claim_has_exactly_one_winner(tmp_path):
    store = ConversationStore(tmp_path / 'product.db', tmp_path / 'assets')
    cid = store.create_conversation()['id']

    def claim(_):
        return store.claim_turn(cid, ttl=60)

    with ThreadPoolExecutor(max_workers=32) as pool:
        tokens = list(pool.map(claim, range(64)))

    winners = [token for token in tokens if token]
    assert len(winners) == 1
    assert store.release_turn(cid, winners[0]) is True
    assert store.claim_turn(cid, ttl=60) is not None


def test_concurrent_approve_reject_transition_has_exactly_one_winner(tmp_path):
    store = ConversationStore(tmp_path / 'product.db', tmp_path / 'assets')
    cid = store.create_conversation()['id']
    action = BusinessAction(
        action_id='act-race',
        kind='merchant.review',
        title='审核商家',
        description='测试并发确认边界',
        side_effect=True,
        risk_level='high',
        requires_confirmation=True,
    )
    store.save_actions(cid, 'session-race', [action])

    def decide(index):
        target = 'approved' if index % 2 == 0 else 'rejected'
        return store.transition_action_with_event('act-race', 'proposed', target, {'winner_attempt': index})

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(decide, range(64)))

    winners = [row for row in results if row is not None]
    assert len(winners) == 1
    final = store.get_action('act-race')
    assert final['status'] in {'approved', 'rejected'}
    assert final['payload']['winner_attempt'] == winners[0][0]['payload']['winner_attempt']
    updates = [event for event in store.list_events(cid) if event['type'] == 'action.updated']
    assert len(updates) == 1
    assert updates[0]['payload']['status'] == final['status']


def test_concurrent_event_writes_are_unique_and_monotonic_when_read_back(tmp_path):
    store = ConversationStore(tmp_path / 'product.db', tmp_path / 'assets')
    cid = store.create_conversation()['id']

    def append(index):
        return store.add_event(cid, 'stress.event', {'index': index})

    with ThreadPoolExecutor(max_workers=32) as pool:
        written = list(pool.map(append, range(128)))

    ids = [int(row['id']) for row in written]
    assert len(ids) == len(set(ids)) == 128

    events = store.list_events(cid)
    read_ids = [int(row['id']) for row in events]
    assert read_ids == sorted(read_ids)
    assert set(read_ids) == set(ids)
