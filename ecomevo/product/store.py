from __future__ import annotations
import json, sqlite3, time, uuid
from pathlib import Path
from typing import Any
from fastapi import HTTPException
from ecomevo.models import BusinessAction


class ConversationStore:
    MAX_ASSETS_PER_CONVERSATION=120
    MAX_ASSET_BYTES_PER_CONVERSATION=2*1024*1024*1024

    def __init__(self, db_path: str | Path, asset_dir: str | Path):
        self.db_path=Path(db_path); self.asset_dir=Path(asset_dir)
        self.db_path.parent.mkdir(parents=True,exist_ok=True); self.asset_dir.mkdir(parents=True,exist_ok=True); self._init()

    def _conn(self):
        c=sqlite3.connect(self.db_path,check_same_thread=False,timeout=30); c.row_factory=sqlite3.Row; return c

    def _init(self):
        with self._conn() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=30000;
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,scene TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,payload TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,conversation_id TEXT,name TEXT NOT NULL,mime TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,meta TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL,type TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,session_id TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,risk_level TEXT NOT NULL,side_effect INTEGER NOT NULL,requires_confirmation INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS turn_leases(conversation_id TEXT PRIMARY KEY,token TEXT NOT NULL,expires_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_assets_conversation ON assets(conversation_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_events_conversation ON task_events(conversation_id,id);
            CREATE INDEX IF NOT EXISTS idx_actions_conversation ON actions(conversation_id,created_at);
            ''')

    def create_conversation(self,title='新的业务任务',scene='product_governance'):
        cid=f'cv-{uuid.uuid4().hex[:12]}'; now=time.time()
        with self._conn() as c:c.execute('INSERT INTO conversations VALUES(?,?,?,?,?)',(cid,title,scene,now,now))
        return self.get_conversation(cid)

    def list_conversations(self,limit=50):
        with self._conn() as c:rows=c.execute('SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?',(limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self,cid):
        with self._conn() as c:r=c.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
        if not r:raise KeyError(cid)
        return dict(r)

    def touch(self,cid,title=None):
        now=time.time()
        with self._conn() as c:
            if title:c.execute('UPDATE conversations SET title=?,updated_at=? WHERE id=?',(title,now,cid))
            else:c.execute('UPDATE conversations SET updated_at=? WHERE id=?',(now,cid))

    def update_conversation(self,cid,*,title=None,scene=None):
        now=time.time();sets=['updated_at=?'];params=[now]
        if title is not None:sets.append('title=?');params.append(title)
        if scene is not None:sets.append('scene=?');params.append(scene)
        params.append(cid)
        with self._conn() as c:
            cur=c.execute(f"UPDATE conversations SET {','.join(sets)} WHERE id=?",params)
            if cur.rowcount!=1:raise KeyError(cid)
        return self.get_conversation(cid)

    def add_message(self,cid,role,content,payload=None):
        mid=f'msg-{uuid.uuid4().hex[:12]}';now=time.time();payload=payload or {}
        with self._conn() as c:
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,cid,role,content,json.dumps(payload,ensure_ascii=False,default=str),now));c.execute('UPDATE conversations SET updated_at=? WHERE id=?',(now,cid))
        return {'id':mid,'conversation_id':cid,'role':role,'content':content,'payload':payload,'created_at':now}

    def list_messages(self,cid,limit:int|None=None):
        if limit is not None:
            limit=max(1,int(limit))
            with self._conn() as c:rows=c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC,id DESC LIMIT ?',(cid,limit)).fetchall()
            rows=list(reversed(rows))
        else:
            with self._conn() as c:rows=c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,id',(cid,)).fetchall()
        out=[]
        for r in rows:d=dict(r);d['payload']=json.loads(d.pop('payload') or '{}');out.append(d)
        return out

    def count_messages(self,cid)->int:
        with self._conn() as c:r=c.execute('SELECT COUNT(*) AS count FROM messages WHERE conversation_id=?',(cid,)).fetchone()
        return int(r['count'] or 0)

    def has_messages(self,cid)->bool:
        with self._conn() as c:r=c.execute('SELECT 1 FROM messages WHERE conversation_id=? LIMIT 1',(cid,)).fetchone()
        return bool(r)

    def add_asset(self,cid,*,name,mime,path,size,meta):
        aid=f'asset-{uuid.uuid4().hex[:12]}';now=time.time();size=max(0,int(size or 0))
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            if cid:
                exists=c.execute('SELECT 1 FROM conversations WHERE id=?',(cid,)).fetchone()
                if not exists:raise KeyError(cid)
                quota=c.execute('SELECT COUNT(*) AS count,COALESCE(SUM(size),0) AS bytes FROM assets WHERE conversation_id=?',(cid,)).fetchone()
                count=int(quota['count'] or 0);used=int(quota['bytes'] or 0)
                if count>=self.MAX_ASSETS_PER_CONVERSATION:
                    raise HTTPException(409,'单个任务最多保留 120 份资料，请新建任务或整理现有资料')
                if used+size>self.MAX_ASSET_BYTES_PER_CONVERSATION:
                    raise HTTPException(413,'当前任务资料总量已达到上限')
            c.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?)',(aid,cid,name,mime,path,size,json.dumps(meta,ensure_ascii=False,default=str),now))
            if cid:c.execute('UPDATE conversations SET updated_at=? WHERE id=?',(now,cid))
        return self.get_asset(aid)

    def patch_asset_meta(self,aid,patch):
        """Persist server-side derived metadata without exposing it through the public API."""
        now=time.time()
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT meta FROM assets WHERE id=?',(aid,)).fetchone()
            if not row:raise KeyError(aid)
            meta=json.loads(row['meta'] or '{}');meta.update(patch or {})
            c.execute('UPDATE assets SET meta=? WHERE id=?',(json.dumps(meta,ensure_ascii=False,default=str),aid))
        return self.get_asset(aid)

    def get_asset(self,aid):
        with self._conn() as c:r=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
        if not r:raise KeyError(aid)
        d=dict(r);d['meta']=json.loads(d.pop('meta') or '{}');return d

    def bind_asset(self,aid,cid):
        """Bind an unassigned asset to a task, or verify it already belongs to that task."""
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT conversation_id FROM assets WHERE id=?',(aid,)).fetchone()
            if not r:raise KeyError(aid)
            owner=r['conversation_id']
            if owner not in (None,cid):return None
            if owner is None:c.execute('UPDATE assets SET conversation_id=? WHERE id=? AND conversation_id IS NULL',(cid,aid))
        return self.get_asset(aid)

    def list_assets(self,cid):
        with self._conn() as c:rows=c.execute('SELECT * FROM assets WHERE conversation_id=? ORDER BY created_at',(cid,)).fetchall()
        out=[]
        for r in rows:d=dict(r);d['meta']=json.loads(d.pop('meta') or '{}');out.append(d)
        return out

    def claim_turn(self,cid,ttl=120.0):
        """Acquire a renewable cross-process lease so one task turn runs at a time."""
        now=time.time();token=f'lease-{uuid.uuid4().hex}'
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT token,expires_at FROM turn_leases WHERE conversation_id=?',(cid,)).fetchone()
            if row and float(row['expires_at'])>now:return None
            c.execute('INSERT INTO turn_leases(conversation_id,token,expires_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at,updated_at=excluded.updated_at',
                      (cid,token,now+float(ttl),now))
        return token

    def renew_turn(self,cid,token,ttl=120.0):
        now=time.time()
        with self._conn() as c:
            cur=c.execute('UPDATE turn_leases SET expires_at=?,updated_at=? WHERE conversation_id=? AND token=?',(now+float(ttl),now,cid,token))
        return cur.rowcount==1

    def release_turn(self,cid,token):
        with self._conn() as c:
            cur=c.execute('DELETE FROM turn_leases WHERE conversation_id=? AND token=?',(cid,token))
        return cur.rowcount==1

    def recover_interrupted_turn(self,cid):
        """Close a stale accepted turn after its processing lease has expired.

        This prevents the UI from remaining permanently busy after a worker/process crash.
        The insert and stale-lease check are serialized so multiple reconnecting clients do not
        create duplicate terminal events.
        """
        now=time.time()
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            latest_accepted=c.execute("SELECT id FROM task_events WHERE conversation_id=? AND type='message.accepted' ORDER BY id DESC LIMIT 1",(cid,)).fetchone()
            if not latest_accepted:return None
            latest_terminal=c.execute("SELECT id FROM task_events WHERE conversation_id=? AND type IN ('answer.ready','answer.error') ORDER BY id DESC LIMIT 1",(cid,)).fetchone()
            if latest_terminal and int(latest_terminal['id'])>int(latest_accepted['id']):return None
            lease=c.execute('SELECT expires_at FROM turn_leases WHERE conversation_id=?',(cid,)).fetchone()
            if lease and float(lease['expires_at'])>now:return None
            c.execute('DELETE FROM turn_leases WHERE conversation_id=?',(cid,))
            payload={'message':'上次处理因服务中断未完成','detail':'任务资料仍然保留，请重新发送上一条问题或继续当前任务。','recovered':True}
            cur=c.execute('INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)',
                          (cid,'answer.error',json.dumps(payload,ensure_ascii=False),now))
            return {'id':cur.lastrowid,'conversation_id':cid,'type':'answer.error','payload':payload,'created_at':now}

    def add_event(self,cid,type_,payload):
        now=time.time()
        with self._conn() as c:cur=c.execute('INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)',(cid,type_,json.dumps(payload,ensure_ascii=False,default=str),now));eid=cur.lastrowid
        return {'id':eid,'conversation_id':cid,'type':type_,'payload':payload,'created_at':now}

    def list_events(self,cid,after_id=0,limit:int|None=None):
        if limit is not None:
            limit=max(1,int(limit))
            if int(after_id)>0:
                with self._conn() as c:rows=c.execute('SELECT * FROM task_events WHERE conversation_id=? AND id>? ORDER BY id LIMIT ?',(cid,after_id,limit)).fetchall()
            else:
                with self._conn() as c:rows=c.execute('SELECT * FROM task_events WHERE conversation_id=? ORDER BY id DESC LIMIT ?',(cid,limit)).fetchall()
                rows=list(reversed(rows))
        else:
            with self._conn() as c:rows=c.execute('SELECT * FROM task_events WHERE conversation_id=? AND id>? ORDER BY id',(cid,after_id)).fetchall()
        out=[]
        for r in rows:d=dict(r);d['payload']=json.loads(d.pop('payload') or '{}');out.append(d)
        return out

    def save_actions(self,cid,session_id,actions:list[BusinessAction]):
        now=time.time()
        with self._conn() as c:
            for a in actions:
                c.execute('INSERT OR REPLACE INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(a.action_id,cid,session_id,a.kind,a.title,a.description,a.risk_level,int(a.side_effect),int(a.requires_confirmation),a.status,json.dumps(a.payload,ensure_ascii=False),now,now))

    def _decode_action(self,r):
        d=dict(r);d['payload']=json.loads(d.pop('payload') or '{}');d['side_effect']=bool(d['side_effect']);d['requires_confirmation']=bool(d['requires_confirmation']);return d

    def recover_stale_actions(self,cid,older_than=300.0):
        """Mark a crashed/abandoned execution as uncertain instead of allowing a blind retry."""
        cutoff=time.time()-max(30.0,float(older_than));now=time.time();recovered=[]
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=c.execute("SELECT * FROM actions WHERE conversation_id=? AND status='approved' AND updated_at<?",(cid,cutoff)).fetchall()
            for r in rows:
                payload=json.loads(r['payload'] or '{}')
                payload.update({'execution_error':'执行进程中断，无法确认下游是否已完成；请先在业务系统核对结果，不要直接重复执行。','execution_outcome':'unknown'})
                cur=c.execute("UPDATE actions SET status='uncertain',payload=?,updated_at=? WHERE id=? AND status='approved'",(json.dumps(payload,ensure_ascii=False),now,r['id']))
                if cur.rowcount==1:
                    updated=c.execute('SELECT * FROM actions WHERE id=?',(r['id'],)).fetchone()
                    action=self._decode_action(updated)
                    c.execute(
                        'INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)',
                        (cid,'action.updated',json.dumps(action,ensure_ascii=False,default=str),now),
                    )
                    recovered.append(r['id'])
        return recovered

    def list_actions(self,cid,status=None,terminal_limit:int=100):
        if status:
            with self._conn() as c:
                rows=c.execute('SELECT * FROM actions WHERE conversation_id=? AND status=? ORDER BY created_at DESC',(cid,status)).fetchall()
            return [self._decode_action(r) for r in rows]
        with self._conn() as c:
            active=c.execute("SELECT * FROM actions WHERE conversation_id=? AND status NOT IN ('executed','rejected','failed') ORDER BY created_at DESC",(cid,)).fetchall()
            recent=c.execute("SELECT * FROM actions WHERE conversation_id=? AND status IN ('executed','rejected','failed') ORDER BY created_at DESC LIMIT ?",(cid,max(1,int(terminal_limit)))).fetchall()
        rows=sorted([*active,*recent],key=lambda r:(float(r['created_at']),str(r['id'])),reverse=True)
        return [self._decode_action(r) for r in rows]

    def get_action(self,aid):
        with self._conn() as c:r=c.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone()
        if not r:raise KeyError(aid)
        return self._decode_action(r)

    def update_action(self,aid,status,payload_patch=None):
        row=self.get_action(aid);payload=row['payload'];payload.update(payload_patch or {});now=time.time()
        with self._conn() as c:c.execute('UPDATE actions SET status=?,payload=?,updated_at=? WHERE id=?',(status,json.dumps(payload,ensure_ascii=False),now,aid))
        return self.get_action(aid)

    def update_action_with_event(self,aid,status,payload_patch=None):
        """Persist an action outcome and its authoritative task event atomically."""
        now=time.time()
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone()
            if not current:raise KeyError(aid)
            payload=json.loads(current['payload'] or '{}');payload.update(payload_patch or {})
            encoded=json.dumps(payload,ensure_ascii=False,default=str)
            c.execute('UPDATE actions SET status=?,payload=?,updated_at=? WHERE id=?',(status,encoded,now,aid))
            updated=c.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone()
            action=self._decode_action(updated)
            cur=c.execute(
                'INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)',
                (action['conversation_id'],'action.updated',json.dumps(action,ensure_ascii=False,default=str),now),
            )
        event={'id':cur.lastrowid,'conversation_id':action['conversation_id'],'type':'action.updated','payload':action,'created_at':now}
        return action,event

    def transition_action(self,aid,expected_status,status,payload_patch=None):
        """Atomic compare-and-set for side-effect decisions. Returns None when another request won the race."""
        now=time.time()
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone()
            if not r:raise KeyError(aid)
            if r['status'] != expected_status:return None
            payload=json.loads(r['payload'] or '{}');payload.update(payload_patch or {})
            cur=c.execute('UPDATE actions SET status=?,payload=?,updated_at=? WHERE id=? AND status=?',
                          (status,json.dumps(payload,ensure_ascii=False),now,aid,expected_status))
            if cur.rowcount != 1:return None
        return self.get_action(aid)

    def transition_action_with_event(self,aid,expected_status,status,payload_patch=None):
        """Compare-and-set an action and append its task event in the same transaction."""
        now=time.time()
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone()
            if not current:raise KeyError(aid)
            if current['status']!=expected_status:return None
            payload=json.loads(current['payload'] or '{}');payload.update(payload_patch or {})
            encoded=json.dumps(payload,ensure_ascii=False,default=str)
            cur=c.execute(
                'UPDATE actions SET status=?,payload=?,updated_at=? WHERE id=? AND status=?',
                (status,encoded,now,aid,expected_status),
            )
            if cur.rowcount!=1:return None
            updated=c.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone()
            action=self._decode_action(updated)
            event_cur=c.execute(
                'INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)',
                (action['conversation_id'],'action.updated',json.dumps(action,ensure_ascii=False,default=str),now),
            )
        event={'id':event_cur.lastrowid,'conversation_id':action['conversation_id'],'type':'action.updated','payload':action,'created_at':now}
        return action,event
