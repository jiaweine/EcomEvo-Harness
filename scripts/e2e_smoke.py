import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from ecomevo.api.app import app

c=TestClient(app)
conv=c.post('/api/conversations',json={'title':'售后 E2E','scene':'aftersales'}).json()
raw='订单 order-88421\n金额: 299\n物流显示签收，用户反馈未收到货\n客服记录：申请退款'.encode('utf-8')
a=c.post('/api/assets',files={'file':('order.log',raw,'text/plain')},data={'conversation_id':conv['id']}).json()
r=c.post(f"/api/conversations/{conv['id']}/messages",json={'content':'请结合订单和履约记录给出售后判责建议','asset_ids':[a['id']],'provider':'demo'})
assert r.status_code==200
d=c.get(f"/api/conversations/{conv['id']}").json()
assistant=[m for m in d['messages'] if m['role']=='assistant'][-1]
assert assistant['payload']['domain']=='aftersales'
assert assistant['payload']['runtime']['event_chain_valid'] is True
assert d['actions']
act=d['actions'][0]
if act['requires_confirmation']:
    done=c.post(f"/api/actions/{act['id']}/decision",json={'decision':'approve','note':'E2E'}).json();assert done['status']=='executed'
print({'conversation_id':conv['id'],'domain':assistant['payload']['domain'],'session_id':assistant['payload']['session_id'],'actions':len(d['actions']),'event_chain_valid':True})
