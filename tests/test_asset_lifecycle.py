import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from ecomevo.api.app import app
from ecomevo.product.store import ConversationStore


def test_store_migrates_existing_asset_table(tmp_path):
    db=tmp_path/'legacy.db';assets=tmp_path/'assets';assets.mkdir()
    with sqlite3.connect(db) as c:
        c.executescript("""
        CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,scene TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE assets(id TEXT PRIMARY KEY,conversation_id TEXT,name TEXT NOT NULL,mime TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,meta TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);
        """)
    store=ConversationStore(db,assets)
    with store._conn() as c:cols={r['name'] for r in c.execute('PRAGMA table_info(assets)').fetchall()}
    assert {'active','excluded_at','excluded_reason'} <= cols


def test_asset_can_be_excluded_and_reenabled_for_future_turns():
    c=TestClient(app);conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    asset=c.post('/api/assets',files={'file':('scope.txt','营业执照 91310000123456789A 品牌授权书齐全'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
    off=c.patch(f"/api/assets/{asset['id']}/scope",json={'active':False,'reason':'误传'});assert off.status_code==200 and off.json()['active'] is False
    blocked=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核这份资料','asset_ids':[asset['id']],'provider':'demo'});assert blocked.status_code==409 and '已排除' in blocked.json()['detail']
    on=c.patch(f"/api/assets/{asset['id']}/scope",json={'active':True});assert on.status_code==200 and on.json()['active'] is True
    assert c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核这份资料','asset_ids':[asset['id']],'provider':'demo'}).status_code==200


def test_unreferenced_asset_can_be_permanently_deleted_but_audited_asset_cannot():
    c=TestClient(app);conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    fresh=c.post('/api/assets',files={'file':('fresh.txt',b'fresh','text/plain')},data={'conversation_id':conv['id']}).json()
    deleted=c.delete(f"/api/assets/{fresh['id']}");assert deleted.status_code==200
    assert all(x['id']!=fresh['id'] for x in c.get(f"/api/conversations/{conv['id']}").json()['assets'])
    used=c.post('/api/assets',files={'file':('used.txt','营业执照 91310000123456789A 品牌授权书齐全'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
    assert c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'审核资料','asset_ids':[used['id']],'provider':'demo'}).status_code==200
    refused=c.delete(f"/api/assets/{used['id']}");assert refused.status_code==409 and '不能物理删除' in refused.json()['detail']
    excluded=c.patch(f"/api/assets/{used['id']}/scope",json={'active':False,'reason':'后续不再使用'});assert excluded.status_code==200 and excluded.json()['active'] is False


def test_excluded_historical_asset_remains_visible_but_future_turn_ignores_it():
    c=TestClient(app);conv=c.post('/api/conversations',json={'scene':'merchant_review'}).json()
    asset=c.post('/api/assets',files={'file':('history.txt','营业执照 91310000123456789A 品牌授权书齐全'.encode(),'text/plain')},data={'conversation_id':conv['id']}).json()
    assert c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'先审核资料','asset_ids':[asset['id']],'provider':'demo'}).status_code==200
    assert c.patch(f"/api/assets/{asset['id']}/scope",json={'active':False,'reason':'后续排除'}).status_code==200
    detail=c.get(f"/api/conversations/{conv['id']}").json();row=next(x for x in detail['assets'] if x['id']==asset['id']);assert row['active'] is False
    follow=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'继续，但不要使用已经排除的资料','asset_ids':[],'provider':'demo'});assert follow.status_code==200
    latest=c.get(f"/api/conversations/{conv['id']}").json()['messages'][-1]
    assert all(e.get('asset_id')!=asset['id'] for e in latest['payload']['evidence'])
