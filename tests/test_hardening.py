import asyncio
import io
import wave
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import __version__ as pillow_version

import importlib
appmod=importlib.import_module('ecomevo.api.app')
from ecomevo.api.app import app
from ecomevo.models import BusinessAction
from ecomevo.product.store import ConversationStore


def new_conv(client, scene='merchant_review'):
    return client.post('/api/conversations', json={'scene': scene}).json()


def test_unknown_provider_and_blank_titles_are_rejected():
    c=TestClient(app)
    assert c.post('/api/conversations',json={'title':'   ','scene':'merchant_review'}).status_code==422
    conv=new_conv(c)
    assert c.patch(f"/api/conversations/{conv['id']}",json={'title':'  '}).status_code==422
    r=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'看看','provider':'not-real','asset_ids':[]})
    assert r.status_code==422


def test_security_headers_include_browser_hardening():
    c=TestClient(app);r=c.get('/')
    csp=r.headers['content-security-policy']
    assert "object-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers['permissions-policy']=='camera=(), microphone=(), geolocation=(), payment=()'
    assert r.headers['cross-origin-resource-policy']=='same-origin'


def test_public_liveness_is_minimal_while_detailed_health_stays_protected(monkeypatch):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "hmac")
    monkeypatch.setenv("ECOMEVO_AUTH_HMAC_SECRET", "liveness-test-secret-0123456789abcdef")
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.get("/api/health").status_code == 401


def test_pillow_security_floor_is_installed():
    version = tuple(int(part) for part in pillow_version.split(".")[:3])
    assert version >= (12, 3, 0)


def test_corrupt_image_is_rejected():
    c=TestClient(app);conv=new_conv(c,'content_audit')
    r=c.post('/api/assets',files={'file':('bad.png',b'not a png','image/png')},data={'conversation_id':conv['id']})
    assert r.status_code==400 and '图片' in r.json()['detail']


def test_fake_audio_video_and_pdf_are_rejected():
    c=TestClient(app);conv=new_conv(c,'content_audit')
    for name,mime in [('fake.mp4','video/mp4'),('fake.wav','audio/wav'),('fake.pdf','application/pdf')]:
        r=c.post('/api/assets',files={'file':(name,b'hello world',mime)},data={'conversation_id':conv['id']})
        assert r.status_code==400, (name,r.text)


def test_generic_zip_renamed_as_office_file_is_rejected():
    c=TestClient(app);conv=new_conv(c)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr('random.txt','hello')
    for name,mime in [('bad.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document'),('bad.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')]:
        r=c.post('/api/assets',files={'file':(name,buf.getvalue(),mime)},data={'conversation_id':conv['id']})
        assert r.status_code==400


def test_executable_upload_type_is_rejected():
    c=TestClient(app);conv=new_conv(c)
    r=c.post('/api/assets',files={'file':('evil.exe',b'MZ fake','application/octet-stream')},data={'conversation_id':conv['id']})
    assert r.status_code==415


def test_valid_wav_upload_is_accepted():
    c=TestClient(app);conv=new_conv(c,'content_audit')
    buf=io.BytesIO()
    with wave.open(buf,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*800)
    r=c.post('/api/assets',files={'file':('ok.wav',buf.getvalue(),'audio/wav')},data={'conversation_id':conv['id']})
    assert r.status_code==200 and r.json()['meta']['kind']=='audio'


def test_asset_download_is_attachment_and_missing_file_returns_410(tmp_path):
    c=TestClient(app);conv=new_conv(c)
    a=c.post('/api/assets',files={'file':('proof.txt',b'proof','text/plain')},data={'conversation_id':conv['id']}).json()
    r=c.get(f"/api/assets/{a['id']}/file")
    assert r.status_code==200 and r.headers['content-disposition'].startswith('attachment;')
    stored=appmod.store.get_asset(a['id']);Path(stored['path']).unlink()
    assert c.get(f"/api/assets/{a['id']}/file").status_code==410


def test_slow_websocket_queue_prefers_newest_event():
    async def run():
        c=TestClient(app);conv=new_conv(c)
        q=asyncio.Queue(maxsize=1);q.put_nowait({'id':-1,'type':'old'})
        appmod.queues.setdefault(conv['id'],[]).append(q)
        try:
            ev=await appmod.emit(conv['id'],'answer.ready',{'ok':True})
            got=q.get_nowait()
            assert got['id']==ev['id'] and got['type']=='answer.ready'
        finally:
            appmod.queues[conv['id']].remove(q)
    asyncio.run(run())


def test_conversation_detail_bounds_long_ui_history_but_keeps_count():
    c=TestClient(app);conv=new_conv(c)
    for i in range(205):appmod.store.add_message(conv['id'],'user',f'message-{i}')
    d=c.get(f"/api/conversations/{conv['id']}").json()
    assert d['message_count']==205 and d['history_truncated'] is True
    assert len(d['messages'])==200 and d['messages'][0]['content']=='message-5' and d['messages'][-1]['content']=='message-204'


def test_store_recent_windows_preserve_order(tmp_path):
    store=ConversationStore(tmp_path/'db.sqlite',tmp_path/'assets');c=store.create_conversation()
    for i in range(6):store.add_message(c['id'],'user',str(i))
    assert [x['content'] for x in store.list_messages(c['id'],limit=3)]==['3','4','5']
    for i in range(6):store.add_event(c['id'],'x',{'i':i})
    assert [x['payload']['i'] for x in store.list_events(c['id'],limit=3)]==[3,4,5]


def test_stale_approved_action_becomes_uncertain(tmp_path):
    import sqlite3,time
    store=ConversationStore(tmp_path/'db.sqlite',tmp_path/'assets');c=store.create_conversation()
    a=BusinessAction(action_id='a1',kind='merchant.review',title='审核',description='提交审核')
    store.save_actions(c['id'],'s1',[a]);store.transition_action('a1','proposed','approved')
    with store._conn() as db:db.execute('UPDATE actions SET updated_at=? WHERE id=?',(time.time()-600,'a1'))
    recovered=store.recover_stale_actions(c['id'],older_than=300)
    row=store.get_action('a1')
    assert recovered==['a1'] and row['status']=='uncertain' and row['payload']['execution_outcome']=='unknown'
    updates=[event for event in store.list_events(c['id']) if event['type']=='action.updated']
    assert len(updates)==1 and updates[0]['payload']['status']=='uncertain'


def test_mcp_transport_timeout_marks_action_uncertain(monkeypatch):
    c=TestClient(app);conv=new_conv(c)
    action=BusinessAction(action_id='timeout-action',kind='merchant.review',title='审核',description='提交审核',payload={'mcp_server':'x','mcp_tool':'y','arguments':{}})
    appmod.store.save_actions(conv['id'],'s1',[action])
    async def timeout(*args,**kwargs):raise httpx.ReadTimeout('timed out')
    monkeypatch.setattr(appmod.mcp,'call_tool',timeout)
    r=c.post('/api/actions/timeout-action/decision',json={'decision':'approve','note':''})
    assert r.status_code==502
    row=appmod.store.get_action('timeout-action')
    assert row['status']=='uncertain' and row['payload']['execution_outcome']=='unknown'
    assert c.post('/api/actions/timeout-action/decision',json={'decision':'approve','note':''}).status_code==409


def test_mcp_definite_application_failure_marks_action_failed(monkeypatch):
    c=TestClient(app);conv=new_conv(c)
    action=BusinessAction(action_id='fail-action',kind='merchant.review',title='审核',description='提交审核',payload={'mcp_server':'x','mcp_tool':'y','arguments':{}})
    appmod.store.save_actions(conv['id'],'s1',[action])
    async def fail(*args,**kwargs):raise RuntimeError('rejected')
    monkeypatch.setattr(appmod.mcp,'call_tool',fail)
    r=c.post('/api/actions/fail-action/decision',json={'decision':'approve','note':''})
    assert r.status_code==502 and appmod.store.get_action('fail-action')['status']=='failed'


def test_task_total_asset_cap_applies_to_new_file_not_only_existing_total(tmp_path):
    c=TestClient(app);conv=new_conv(c)
    fake=tmp_path/'old.txt';fake.write_text('old')
    appmod.store.add_asset(conv['id'],name='old.txt',mime='text/plain',path=str(fake),size=2*1024*1024*1024-1024,meta={'kind':'text'})
    r=c.post('/api/assets',files={'file':('new.txt',b'x'*2048,'text/plain')},data={'conversation_id':conv['id']})
    assert r.status_code==413


def test_media_extension_cannot_bypass_validation_by_claiming_text_plain():
    c=TestClient(app);conv=new_conv(c,'content_audit')
    r=c.post('/api/assets',files={'file':('fake.mp4',b'not-video','text/plain')},data={'conversation_id':conv['id']})
    assert r.status_code==400
    r=c.post('/api/assets',files={'file':('fake.png',b'not-image','text/plain')},data={'conversation_id':conv['id']})
    assert r.status_code==400

def test_image_decompression_bomb_is_reported_as_size_error(monkeypatch,tmp_path):
    import importlib
    from fastapi import HTTPException
    appmod=importlib.import_module('ecomevo.api.app')
    path=tmp_path/'huge.png';path.write_bytes(b'x')
    def boom(*args,**kwargs):raise appmod.Image.DecompressionBombError('too many pixels')
    monkeypatch.setattr(appmod.Image,'open',boom)
    try:
        appmod._validate_raster(path)
        assert False,'expected HTTPException'
    except HTTPException as exc:
        assert exc.status_code==413
