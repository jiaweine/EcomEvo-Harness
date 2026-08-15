"""Live-network smoke test. Start uvicorn first, then run this script."""
from __future__ import annotations
import argparse, asyncio, json, time
import httpx, websockets

async def main(base:str):
    base=base.rstrip('/')
    ws_base=('wss://' if base.startswith('https://') else 'ws://')+base.split('://',1)[1]
    async with httpx.AsyncClient(timeout=20) as c:
        for path in ('/api/health','/','/assets/app.js','/docs'):
            r=await c.get(base+path);assert r.status_code==200,(path,r.status_code)
        conv=(await c.post(base+'/api/conversations',json={'title':'Live Smoke','scene':'merchant_review'})).json()
        asset=(await c.post(base+'/api/assets',files={'file':('merchant.txt','营业执照 91310000123456789A\n品牌授权书齐全'.encode(),'text/plain')},data={'conversation_id':conv['id']})).json()
        assert 'path' not in asset and 'search_text' not in asset.get('meta',{})
        async with websockets.connect(f"{ws_base}/ws/conversations/{conv['id']}",open_timeout=5) as ws:
            sent=await c.post(base+f"/api/conversations/{conv['id']}/messages",json={'content':'审核这个商家的主体和授权','asset_ids':[asset['id']],'provider':'demo'})
            assert sent.status_code==200
            seen=[];deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                event=json.loads(await asyncio.wait_for(ws.recv(),timeout=5));seen.append(event.get('type'))
                if event.get('type')=='answer.ready':break
            assert {'message.accepted','progress','answer.ready'} <= set(seen),seen
        detail=(await c.get(base+f"/api/conversations/{conv['id']}")).json()
        assert len(detail['messages'])>=2 and detail['actions'] and 'path' not in detail['assets'][0]
        print({'status':'ok','conversation_id':conv['id'],'messages':len(detail['messages']),'actions':len(detail['actions']),'websocket':True})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8000');args=p.parse_args()
    asyncio.run(main(args.base))
