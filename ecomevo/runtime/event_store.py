from __future__ import annotations
import hashlib, json, sqlite3, threading, time
from pathlib import Path
from typing import Any
from ecomevo.models import RuntimeEvent, EvolutionPatch


class EventStore:
    """Append-only hash-chained runtime history with JSON checkpoints, forks and evolution patches."""
    def __init__(self, path: str | Path):
        self.path = str(path); Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock(); self._init()

    def _conn(self):
        c=sqlite3.connect(self.path, check_same_thread=False, timeout=30); c.row_factory=sqlite3.Row
        c.execute('PRAGMA busy_timeout=30000')
        return c

    def _init(self):
        with self._conn() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY,parent_session_id TEXT,parent_seq INTEGER,created_at REAL NOT NULL,meta_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS events(session_id TEXT NOT NULL,seq INTEGER NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,ts REAL NOT NULL,prev_hash TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(session_id,seq));
            CREATE TABLE IF NOT EXISTS snapshots(session_id TEXT NOT NULL,seq INTEGER NOT NULL,snapshot_blob TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(session_id,seq));
            CREATE TABLE IF NOT EXISTS evolution_patches(patch_id TEXT PRIMARY KEY,created_at REAL NOT NULL,payload_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id,seq);
            ''')

    def has_session(self,session_id:str)->bool:
        with self._conn() as c:return c.execute('SELECT 1 FROM sessions WHERE session_id=?',(session_id,)).fetchone() is not None

    def create_session(self, session_id:str, meta:dict[str,Any]|None=None, parent_session_id:str|None=None, parent_seq:int|None=None):
        with self._lock, self._conn() as c:
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(session_id,parent_session_id,parent_seq,time.time(),json.dumps(meta or {},ensure_ascii=False,default=str)))

    def append(self, session_id:str, event_type:str, payload:dict[str,Any])->RuntimeEvent:
        # BEGIN IMMEDIATE serializes the read-last-sequence + insert pair across multiple worker processes.
        with self._lock,self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM sessions WHERE session_id=?',(session_id,)).fetchone() is None:
                raise KeyError(f'unknown session: {session_id}')
            row=c.execute('SELECT seq,hash FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1',(session_id,)).fetchone()
            seq=int(row['seq'])+1 if row else 1; prev=row['hash'] if row else 'GENESIS'; ts=time.time()
            body=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
            digest=hashlib.sha256(f'{session_id}|{seq}|{event_type}|{body}|{ts:.6f}|{prev}'.encode()).hexdigest()
            c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',(session_id,seq,event_type,body,ts,prev,digest))
        return RuntimeEvent(session_id=session_id,seq=seq,event_type=event_type,payload=payload,ts=ts,hash=digest,prev_hash=prev)

    def list_events(self,session_id:str,after_seq:int=0)->list[RuntimeEvent]:
        with self._conn() as c: rows=c.execute('SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq',(session_id,after_seq)).fetchall()
        return [RuntimeEvent(session_id=r['session_id'],seq=r['seq'],event_type=r['event_type'],payload=json.loads(r['payload_json']),ts=r['ts'],hash=r['hash'],prev_hash=r['prev_hash']) for r in rows]

    def verify_chain(self,session_id:str)->bool:
        if not self.has_session(session_id):return False
        prev='GENESIS'
        for e in self.list_events(session_id):
            if e.prev_hash!=prev:return False
            body=json.dumps(e.payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
            digest=hashlib.sha256(f'{e.session_id}|{e.seq}|{e.event_type}|{body}|{e.ts:.6f}|{e.prev_hash}'.encode()).hexdigest()
            if digest!=e.hash:return False
            prev=e.hash
        return True

    def save_snapshot(self,session_id:str,seq:int,snapshot:dict[str,Any]):
        blob='json:'+json.dumps(snapshot,ensure_ascii=False,separators=(',',':'),default=str)
        with self._lock,self._conn() as c:c.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)',(session_id,seq,blob,time.time()))

    def get_snapshot(self,session_id:str,seq:int|None=None):
        q='SELECT snapshot_blob FROM snapshots WHERE session_id=?'; p=[session_id]
        if seq is not None:q+=' AND seq<=?';p.append(seq)
        q+=' ORDER BY seq DESC LIMIT 1'
        with self._conn() as c:r=c.execute(q,p).fetchone()
        if not r:return None
        blob=r['snapshot_blob']
        if not str(blob).startswith('json:'):
            # Never unpickle database content at runtime. Legacy development snapshots are intentionally ignored.
            return None
        return json.loads(blob[5:])

    def recent_completed(self,limit:int=100)->list[dict[str,Any]]:
        with self._conn() as c:
            rows=c.execute("SELECT session_id,payload_json,ts FROM events WHERE event_type='run.completed' ORDER BY ts DESC LIMIT ?",(limit,)).fetchall()
        out=[]
        for r in rows:
            try:payload=json.loads(r['payload_json'])
            except Exception:continue
            out.append({'session_id':r['session_id'],'payload':payload,'ts':r['ts']})
        return out

    def list_sessions(self, limit:int=30)->list[dict[str,Any]]:
        with self._conn() as c:
            rows=c.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return [{"session_id":r["session_id"],"parent_session_id":r["parent_session_id"],"parent_seq":r["parent_seq"],
                 "created_at":r["created_at"],"meta":json.loads(r["meta_json"] or "{}"),
                 "event_count":len(self.list_events(r["session_id"])),"hash_chain_valid":self.verify_chain(r["session_id"])} for r in rows]

    def fork(self, source_session_id:str, at_seq:int, new_session_id:str, meta:dict[str,Any]|None=None):
        if not self.has_session(source_session_id):raise KeyError(source_session_id)
        source=self.list_events(source_session_id)
        max_seq=source[-1].seq if source else 0
        if at_seq<0 or at_seq>max_seq:raise ValueError(f'at_seq must be between 0 and {max_seq}')
        self.create_session(new_session_id,meta=meta,parent_session_id=source_session_id,parent_seq=at_seq)
        for e in source:
            if e.seq>at_seq: break
            payload=dict(e.payload);payload['_forked_from']=source_session_id;payload['_source_seq']=e.seq
            self.append(new_session_id,e.event_type,payload)

    def save_patch(self, patch:EvolutionPatch):
        with self._conn() as c:c.execute('INSERT OR REPLACE INTO evolution_patches VALUES(?,?,?)',(patch.patch_id,patch.created_at,patch.model_dump_json()))

    def list_patches(self,limit:int=30)->list[dict[str,Any]]:
        with self._conn() as c:rows=c.execute('SELECT payload_json FROM evolution_patches ORDER BY created_at DESC LIMIT ?',(limit,)).fetchall()
        return [json.loads(r['payload_json']) for r in rows]
