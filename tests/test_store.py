

def test_turn_lease_is_cross_request_and_renewable(tmp_path):
    from ecomevo.product.store import ConversationStore
    s=ConversationStore(tmp_path/'p.db',tmp_path/'assets')
    c=s.create_conversation('x','merchant_review')
    token=s.claim_turn(c['id'],ttl=60)
    assert token and s.claim_turn(c['id'],ttl=60) is None
    assert s.renew_turn(c['id'],token,ttl=60)
    assert not s.release_turn(c['id'],'wrong-token')
    assert s.release_turn(c['id'],token)
    assert s.claim_turn(c['id'],ttl=60)


def test_expired_turn_is_recovered_once(tmp_path):
    from ecomevo.product.store import ConversationStore
    s=ConversationStore(tmp_path/'p.db',tmp_path/'assets')
    c=s.create_conversation('x','merchant_review')
    s.add_event(c['id'],'message.accepted',{'message_id':'m1'})
    token=s.claim_turn(c['id'],ttl=-1)
    assert token
    recovered=s.recover_interrupted_turn(c['id'])
    assert recovered and recovered['type']=='answer.error' and recovered['payload']['recovered'] is True
    assert s.recover_interrupted_turn(c['id']) is None
    assert s.claim_turn(c['id'],ttl=60)

def test_action_listing_keeps_active_but_bounds_terminal_history(tmp_path):
    from ecomevo.models import BusinessAction
    from ecomevo.product.store import ConversationStore
    store=ConversationStore(tmp_path/'product.db',tmp_path/'assets')
    cv=store.create_conversation()
    for i in range(125):
        action=BusinessAction(action_id=f'done-{i}',kind='test',title='done',description='x',risk_level='low',side_effect=True,requires_confirmation=True,status='proposed')
        store.save_actions(cv['id'],'s',[action]);store.update_action(action.action_id,'executed')
    active=BusinessAction(action_id='still-proposed',kind='test',title='active',description='x',risk_level='low',side_effect=True,requires_confirmation=True,status='proposed')
    store.save_actions(cv['id'],'s',[active])
    rows=store.list_actions(cv['id'])
    assert any(x['id']=='still-proposed' for x in rows)
    assert sum(x['status']=='executed' for x in rows)==100
