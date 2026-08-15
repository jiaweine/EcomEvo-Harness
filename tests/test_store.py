

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
