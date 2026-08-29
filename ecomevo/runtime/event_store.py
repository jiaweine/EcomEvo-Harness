from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.models import EvolutionPatch, RuntimeEvent


class EventStore:
    """Append-only hash-chained runtime history with JSON checkpoints, forks and evolution patches."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _init(self):
        with self._conn() as c:
            c.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(
                    session_id TEXT PRIMARY KEY,
                    parent_session_id TEXT,
                    parent_seq INTEGER,
                    created_at REAL NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS events(
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ts REAL NOT NULL,
                    prev_hash TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    PRIMARY KEY(session_id,seq)
                );
                CREATE TABLE IF NOT EXISTS snapshots(
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    snapshot_blob TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id,seq)
                );
                CREATE TABLE IF NOT EXISTS evolution_patches(
                    patch_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    fingerprint TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id,seq);
                """
            )
            cols = {r["name"] for r in c.execute("PRAGMA table_info(evolution_patches)").fetchall()}
            if "fingerprint" not in cols:
                c.execute("ALTER TABLE evolution_patches ADD COLUMN fingerprint TEXT")
            snapshot_cols = {r["name"] for r in c.execute("PRAGMA table_info(snapshots)").fetchall()}
            if "state_hash" not in snapshot_cols:
                c.execute("ALTER TABLE snapshots ADD COLUMN state_hash TEXT")
            if "event_hash" not in snapshot_cols:
                c.execute("ALTER TABLE snapshots ADD COLUMN event_hash TEXT")
            rows = c.execute(
                "SELECT patch_id,payload_json,created_at FROM evolution_patches ORDER BY created_at DESC"
            ).fetchall()
            seen = set()
            for r in rows:
                try:
                    data = json.loads(r["payload_json"])
                    fp = self._patch_fingerprint(data)
                except Exception:
                    fp = None
                if fp and fp not in seen:
                    c.execute(
                        "UPDATE evolution_patches SET fingerprint=? WHERE patch_id=?",
                        (fp, r["patch_id"]),
                    )
                    seen.add(fp)
                elif fp:
                    c.execute(
                        "UPDATE evolution_patches SET fingerprint=NULL WHERE patch_id=?",
                        (r["patch_id"],),
                    )
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_evolution_fingerprint "
                "ON evolution_patches(fingerprint) WHERE fingerprint IS NOT NULL"
            )

    @staticmethod
    def _patch_fingerprint(patch: EvolutionPatch | dict[str, Any]) -> str:
        data = patch.model_dump(mode="json") if hasattr(patch, "model_dump") else dict(patch)
        semantic = {
            "target": data.get("target"),
            "patch": data.get("patch") or {},
            "accepted": bool(data.get("accepted")),
        }
        body = json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(body.encode()).hexdigest()

    @staticmethod
    def _session_tail(c: sqlite3.Connection, session_id: str):
        # The normal hot path is a non-empty session. Probe the composite event PK
        # directly and only consult sessions for the rare empty-session case.
        row = c.execute(
            "SELECT seq,hash FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is not None:
            return row
        if c.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone() is None:
            return None
        return {"seq": None, "hash": None}

    @staticmethod
    def _verify_rows(rows) -> bool:
        prev = "GENESIS"
        for r in rows:
            if str(r["prev_hash"]) != prev:
                return False
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                return False
            body = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            digest = hashlib.sha256(
                f"{r['session_id']}|{r['seq']}|{r['event_type']}|{body}|"
                f"{float(r['ts']):.6f}|{r['prev_hash']}".encode()
            ).hexdigest()
            if digest != r["hash"]:
                return False
            prev = str(r["hash"])
        return True

    @staticmethod
    def _append_in_transaction(
        c: sqlite3.Connection,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        tail=None,
    ) -> RuntimeEvent:
        current = tail if tail is not None else EventStore._session_tail(c, session_id)
        if current is None:
            raise KeyError(f"unknown session: {session_id}")
        seq = int(current["seq"]) + 1 if current["seq"] is not None else 1
        prev = str(current["hash"]) if current["hash"] is not None else "GENESIS"
        ts = time.time()
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(
            f"{session_id}|{seq}|{event_type}|{body}|{ts:.6f}|{prev}".encode()
        ).hexdigest()
        c.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
            (session_id, seq, event_type, body, ts, prev, digest),
        )
        return RuntimeEvent(
            session_id=session_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            ts=ts,
            hash=digest,
            prev_hash=prev,
        )

    @staticmethod
    def _state_body(snapshot: dict[str, Any]) -> str:
        return json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _save_checkpoint_in_transaction(
        cls,
        c: sqlite3.Connection,
        session_id: str,
        snapshot: dict[str, Any],
        *,
        seq: int | None = None,
        tail=None,
    ) -> dict[str, Any]:
        current = tail if tail is not None else cls._session_tail(c, session_id)
        if current is None:
            raise KeyError(f"unknown session: {session_id}")
        latest = int(current["seq"] or 0)
        checkpoint_seq = latest if seq is None else int(seq)
        if checkpoint_seq < 0 or checkpoint_seq > latest:
            raise ValueError("checkpoint seq is outside the session event range")
        event_hash = "GENESIS"
        if checkpoint_seq:
            if checkpoint_seq == latest and current["hash"] is not None:
                event_hash = str(current["hash"])
            else:
                row = c.execute(
                    "SELECT hash FROM events WHERE session_id=? AND seq=?",
                    (session_id, checkpoint_seq),
                ).fetchone()
                if row is None:
                    raise ValueError("checkpoint seq does not exist")
                event_hash = str(row["hash"])
        body = cls._state_body(snapshot)
        state_hash = hashlib.sha256(body.encode()).hexdigest()
        blob = "json:" + body
        c.execute(
            "INSERT OR REPLACE INTO snapshots"
            "(session_id,seq,snapshot_blob,created_at,state_hash,event_hash) VALUES(?,?,?,?,?,?)",
            (session_id, checkpoint_seq, blob, time.time(), state_hash, event_hash),
        )
        return {
            "session_id": session_id,
            "seq": checkpoint_seq,
            "state_hash": state_hash,
            "event_hash": event_hash,
        }

    def has_session(self, session_id: str) -> bool:
        with self._conn() as c:
            return c.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone() is not None

    def create_session(
        self,
        session_id: str,
        meta: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
    ):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    session_id,
                    parent_session_id,
                    parent_seq,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False, default=str),
                ),
            )

    def create_session_and_append(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
    ) -> RuntimeEvent:
        """Create a session and its first hash-chain event in one writer transaction."""
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    session_id,
                    parent_session_id,
                    parent_seq,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False, default=str),
                ),
            )
            return self._append_in_transaction(
                c,
                session_id,
                event_type,
                payload,
                tail={"seq": None, "hash": None},
            )

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> RuntimeEvent:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            return self._append_in_transaction(c, session_id, event_type, payload)

    def list_events(self, session_id: str, after_seq: int = 0) -> list[RuntimeEvent]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq",
                (session_id, after_seq),
            ).fetchall()
        return [
            RuntimeEvent(
                session_id=r["session_id"],
                seq=r["seq"],
                event_type=r["event_type"],
                payload=json.loads(r["payload_json"]),
                ts=r["ts"],
                hash=r["hash"],
                prev_hash=r["prev_hash"],
            )
            for r in rows
        ]

    def verify_chain(self, session_id: str) -> bool:
        with self._conn() as c:
            if c.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone() is None:
                return False
            rows = c.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY seq", (session_id,)
            ).fetchall()
        return self._verify_rows(rows)

    def save_snapshot(self, session_id: str, seq: int, snapshot: dict[str, Any]):
        self.save_checkpoint(session_id, snapshot, seq=seq)

    def latest_seq(self, session_id: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(seq) AS seq FROM events WHERE session_id=?", (session_id,)
            ).fetchone()
        return int(row["seq"] or 0) if row else 0

    def save_checkpoint(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        seq: int | None = None,
    ) -> dict[str, Any]:
        """Persist a state checkpoint bound atomically to an exact event-chain position."""
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            return self._save_checkpoint_in_transaction(
                c, session_id, snapshot, seq=seq
            )

    def save_checkpoint_and_append(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        event_type: str,
        event_payload: dict[str, Any] | None = None,
        *,
        seq: int | None = None,
    ) -> tuple[dict[str, Any], RuntimeEvent]:
        """Persist a checkpoint and its audit event under one SQLite writer lock.

        The checkpoint binds to the chain tail *before* the audit event. This preserves
        rollback semantics while removing one writer transaction per checkpoint.
        """
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            tail = self._session_tail(c, session_id)
            reference = self._save_checkpoint_in_transaction(
                c,
                session_id,
                snapshot,
                seq=seq,
                tail=tail,
            )
            payload = {**(event_payload or {}), **reference}
            event = self._append_in_transaction(
                c,
                session_id,
                event_type,
                payload,
                tail=tail,
            )
            return reference, event

    def restore_checkpoint(
        self, session_id: str, seq: int | None = None
    ) -> dict[str, Any] | None:
        """Load and integrity-check a checkpoint before it is used for recovery."""
        q = "SELECT seq,snapshot_blob,state_hash,event_hash FROM snapshots WHERE session_id=?"
        p: list[Any] = [session_id]
        if seq is not None:
            q += " AND seq<=?"
            p.append(int(seq))
        q += " ORDER BY seq DESC LIMIT 1"
        with self._conn() as c:
            row = c.execute(q, p).fetchone()
            if not row:
                return None
            checkpoint_seq = int(row["seq"])
            event_hash = "GENESIS"
            if checkpoint_seq:
                event = c.execute(
                    "SELECT hash FROM events WHERE session_id=? AND seq=?",
                    (session_id, checkpoint_seq),
                ).fetchone()
                if event is None:
                    return None
                event_hash = str(event["hash"])
        blob = str(row["snapshot_blob"])
        if not blob.startswith("json:"):
            return None
        state = json.loads(blob[5:])
        body = self._state_body(state)
        expected_state = str(row["state_hash"] or hashlib.sha256(body.encode()).hexdigest())
        expected_event = str(row["event_hash"] or event_hash)
        if hashlib.sha256(body.encode()).hexdigest() != expected_state or event_hash != expected_event:
            return None
        return {
            **state,
            "_checkpoint": {
                "session_id": session_id,
                "seq": checkpoint_seq,
                "state_hash": expected_state,
                "event_hash": expected_event,
            },
        }

    def get_snapshot(self, session_id: str, seq: int | None = None):
        restored = self.restore_checkpoint(session_id, seq)
        if not restored:
            return None
        restored.pop("_checkpoint", None)
        return restored

    def recent_completed(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT session_id,payload_json,ts FROM events "
                "WHERE event_type='run.completed' ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                continue
            out.append({"session_id": r["session_id"], "payload": payload, "ts": r["ts"]})
        return out

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            if not rows:
                return []
            event_rows = c.execute(
                """SELECT e.* FROM events AS e
                   JOIN (
                       SELECT session_id FROM sessions ORDER BY created_at DESC LIMIT ?
                   ) AS selected ON selected.session_id=e.session_id
                   ORDER BY e.session_id,e.seq""",
                (limit,),
            ).fetchall()
        ids = [str(r["session_id"]) for r in rows]
        grouped: dict[str, list[Any]] = {sid: [] for sid in ids}
        for event in event_rows:
            grouped[str(event["session_id"])].append(event)
        return [
            {
                "session_id": r["session_id"],
                "parent_session_id": r["parent_session_id"],
                "parent_seq": r["parent_seq"],
                "created_at": r["created_at"],
                "meta": json.loads(r["meta_json"] or "{}"),
                "event_count": len(grouped[str(r["session_id"])]),
                "hash_chain_valid": self._verify_rows(grouped[str(r["session_id"])]),
            }
            for r in rows
        ]

    def fork(
        self,
        source_session_id: str,
        at_seq: int,
        new_session_id: str,
        meta: dict[str, Any] | None = None,
    ):
        if not self.has_session(source_session_id):
            raise KeyError(source_session_id)
        source = self.list_events(source_session_id)
        max_seq = source[-1].seq if source else 0
        if at_seq < 0 or at_seq > max_seq:
            raise ValueError(f"at_seq must be between 0 and {max_seq}")
        self.create_session(
            new_session_id,
            meta=meta,
            parent_session_id=source_session_id,
            parent_seq=at_seq,
        )
        for e in source:
            if e.seq > at_seq:
                break
            payload = dict(e.payload)
            payload["_forked_from"] = source_session_id
            payload["_source_seq"] = e.seq
            self.append(new_session_id, e.event_type, payload)

    def save_patch_if_novel(self, patch: EvolutionPatch) -> dict[str, Any] | None:
        fp = self._patch_fingerprint(patch)
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fp,),
            ).fetchone()
            if row:
                try:
                    return json.loads(row["payload_json"])
                except Exception:
                    return {
                        "patch_id": "existing",
                        "accepted": patch.accepted,
                        "target": patch.target,
                        "patch": patch.patch,
                    }
            c.execute(
                "INSERT INTO evolution_patches(patch_id,created_at,payload_json,fingerprint) "
                "VALUES(?,?,?,?)",
                (patch.patch_id, patch.created_at, patch.model_dump_json(), fp),
            )
        return None

    def save_patch(self, patch: EvolutionPatch):
        return self.save_patch_if_novel(patch)

    def list_patches(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT payload_json FROM evolution_patches ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]
