from __future__ import annotations

from contextvars import ContextVar
import json
import time
import uuid
from typing import Any

from fastapi import HTTPException

from .store import ConversationStore as BaseConversationStore


class ConversationStore(BaseConversationStore):
    """Product store with atomic evidence snapshots and a durable conversation-job queue."""

    JOB_STATUSES = {"queued", "running", "succeeded", "failed"}

    def __init__(self, *args, **kwargs):
        self._asset_snapshot: ContextVar[tuple[str, int, int, float] | None] = ContextVar(
            f"ecomevo_asset_snapshot_{id(self)}",
            default=None,
        )
        super().__init__(*args, **kwargs)

    def _init(self):
        super()._init()
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_jobs(
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    worker_id TEXT,
                    lease_until REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_jobs_ready
                ON conversation_jobs(status, lease_until, created_at);
                CREATE INDEX IF NOT EXISTS idx_conversation_jobs_conversation
                ON conversation_jobs(conversation_id, status, created_at);
                """
            )

    @staticmethod
    def _revision(rows) -> tuple[int, int, float]:
        return (
            len(rows),
            sum(int(row.get("size") or 0) for row in rows),
            max((float(row.get("created_at") or 0) for row in rows), default=0.0),
        )

    @staticmethod
    def _decode_job(row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = json.loads(value.get("payload") or "{}")
        return value

    def has_active_job(self, cid: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM conversation_jobs WHERE conversation_id=? AND status IN ('queued','running') LIMIT 1",
                (cid,),
            ).fetchone()
        return bool(row)

    def job_counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT status,COUNT(*) AS count FROM conversation_jobs GROUP BY status").fetchall()
        return {str(row["status"]): int(row["count"] or 0) for row in rows}

    def has_active_turn(self, cid) -> bool:
        if self.has_active_job(cid):
            return True
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT expires_at FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
        return bool(row and float(row["expires_at"]) > now)

    def list_assets(self, cid):
        rows = super().list_assets(cid)
        count, total_bytes, latest_created = self._revision(rows)
        self._asset_snapshot.set((cid, count, total_bytes, latest_created))
        return rows

    def claim_turn(self, cid, ttl=120.0):
        """Atomically claim a turn only if the caller's asset snapshot is still current."""
        snapshot = self._asset_snapshot.get()
        self._asset_snapshot.set(None)
        now = time.time()
        token = f"lease-{uuid.uuid4().hex}"
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            active_job = c.execute(
                "SELECT 1 FROM conversation_jobs WHERE conversation_id=? AND status IN ('queued','running') LIMIT 1",
                (cid,),
            ).fetchone()
            if active_job:
                return None
            if snapshot and snapshot[0] == cid:
                row = c.execute(
                    "SELECT COUNT(*) AS count,COALESCE(SUM(size),0) AS bytes,"
                    "COALESCE(MAX(created_at),0) AS latest FROM assets WHERE conversation_id=?",
                    (cid,),
                ).fetchone()
                current = (int(row["count"] or 0), int(row["bytes"] or 0), float(row["latest"] or 0))
                expected = (snapshot[1], snapshot[2], snapshot[3])
                if current != expected:
                    raise HTTPException(409, "任务资料刚刚发生变化，请重新发送以纳入最新资料")

            lease = c.execute(
                "SELECT token,expires_at FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if lease and float(lease["expires_at"]) > now:
                return None
            c.execute(
                "INSERT INTO turn_leases(conversation_id,token,expires_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at,updated_at=excluded.updated_at",
                (cid, token, now + float(ttl), now),
            )
        return token

    def renew_or_restore_turn(self, cid: str, token: str, ttl: float = 120.0) -> bool:
        """Renew a durable job's turn lease, restoring the same token after a process crash."""
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT token,expires_at FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if row and str(row["token"]) != token and float(row["expires_at"]) > now:
                return False
            c.execute(
                "INSERT INTO turn_leases(conversation_id,token,expires_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at,updated_at=excluded.updated_at",
                (cid, token, now + float(ttl), now),
            )
        return True

    def add_asset(self, cid, *, name, mime, path, size, meta):
        aid = f"asset-{uuid.uuid4().hex[:12]}"
        now = time.time()
        size = max(0, int(size or 0))
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if cid:
                exists = c.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone()
                if not exists:
                    raise KeyError(cid)

                active_job = c.execute(
                    "SELECT 1 FROM conversation_jobs WHERE conversation_id=? AND status IN ('queued','running') LIMIT 1",
                    (cid,),
                ).fetchone()
                if active_job:
                    raise HTTPException(409, "当前任务正在处理中，请在本轮完成后再追加资料")
                lease = c.execute(
                    "SELECT expires_at FROM turn_leases WHERE conversation_id=?",
                    (cid,),
                ).fetchone()
                if lease and float(lease["expires_at"]) > now:
                    raise HTTPException(409, "当前任务正在处理中，请在本轮完成后再追加资料")

                quota = c.execute(
                    "SELECT COUNT(*) AS count,COALESCE(SUM(size),0) AS bytes "
                    "FROM assets WHERE conversation_id=?",
                    (cid,),
                ).fetchone()
                count = int(quota["count"] or 0)
                used = int(quota["bytes"] or 0)
                if count >= self.MAX_ASSETS_PER_CONVERSATION:
                    raise HTTPException(409, "单个任务最多保留 120 份资料，请新建任务或整理现有资料")
                if used + size > self.MAX_ASSET_BYTES_PER_CONVERSATION:
                    raise HTTPException(413, "当前任务资料总量已达到上限")

            c.execute(
                "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?)",
                (aid, cid, name, mime, path, size, json.dumps(meta, ensure_ascii=False, default=str), now),
            )
            if cid:
                c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        return self.get_asset(aid)

    def accept_message_job(
        self,
        cid: str,
        *,
        lease_token: str,
        content: str,
        asset_ids: list[str],
        provider: str,
        domain: str,
        history: list[dict[str, Any]],
        asset_snapshot: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Persist user message, accepted event and durable job in one SQLite transaction."""
        now = time.time()
        mid = f"msg-{uuid.uuid4().hex[:12]}"
        jid = f"job-{uuid.uuid4().hex[:16]}"
        user_payload = {"asset_ids": list(asset_ids)}
        user = {
            "id": mid,
            "conversation_id": cid,
            "role": "user",
            "content": content,
            "payload": user_payload,
            "created_at": now,
        }
        event_payload = {
            "message_id": mid,
            "message": user,
            "asset_count": len(asset_ids),
            "task_asset_count": len(asset_snapshot),
            "job_id": jid,
        }
        job_payload = {
            "content": content,
            "provider": provider,
            "domain": domain,
            "history": history,
            "assets": asset_snapshot,
            "lease_token": lease_token,
            "message_id": mid,
        }
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            lease = c.execute(
                "SELECT token,expires_at FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if not lease or str(lease["token"]) != lease_token:
                raise HTTPException(409, "当前任务处理权已发生变化，请重新发送")
            had_messages = bool(c.execute(
                "SELECT 1 FROM messages WHERE conversation_id=? LIMIT 1", (cid,)
            ).fetchone())
            c.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?)",
                (mid, cid, "user", content, json.dumps(user_payload, ensure_ascii=False), now),
            )
            if had_messages:
                c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
            else:
                title = content.strip().replace("\n", " ")[:30] or "新的业务任务"
                c.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?", (title, now, cid))
            cur = c.execute(
                "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
                (cid, "message.accepted", json.dumps(event_payload, ensure_ascii=False, default=str), now),
            )
            c.execute(
                "INSERT INTO conversation_jobs(id,conversation_id,message_id,status,payload,worker_id,lease_until,attempts,last_error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (jid, cid, mid, "queued", json.dumps(job_payload, ensure_ascii=False, default=str), None, None, 0, None, now, now),
            )
        event = {"id": cur.lastrowid, "conversation_id": cid, "type": "message.accepted", "payload": event_payload, "created_at": now}
        return user, event, {"id": jid, "conversation_id": cid, "message_id": mid, "status": "queued", "payload": job_payload}

    def claim_job(self, worker_id: str, *, job_id: str | None = None, lease_seconds: float = 120.0) -> dict[str, Any] | None:
        now = time.time()
        lease_until = now + max(30.0, float(lease_seconds))
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if job_id:
                row = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (job_id,)).fetchone()
                if not row or row["status"] in {"succeeded", "failed"}:
                    return None
                if row["status"] == "running" and row["lease_until"] is not None and float(row["lease_until"]) > now:
                    return None
            else:
                row = c.execute(
                    "SELECT * FROM conversation_jobs WHERE status='queued' OR (status='running' AND COALESCE(lease_until,0)<=?) "
                    "ORDER BY created_at,id LIMIT 1",
                    (now,),
                ).fetchone()
                if not row:
                    return None
            cur = c.execute(
                "UPDATE conversation_jobs SET status='running',worker_id=?,lease_until=?,attempts=attempts+1,updated_at=? "
                "WHERE id=? AND (status='queued' OR (status='running' AND COALESCE(lease_until,0)<=?))",
                (worker_id, lease_until, now, row["id"], now),
            )
            if cur.rowcount != 1:
                return None
            claimed = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (row["id"],)).fetchone()
        return self._decode_job(claimed)

    def renew_job(self, job_id: str, worker_id: str, lease_seconds: float = 120.0) -> bool:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE conversation_jobs SET lease_until=?,updated_at=? WHERE id=? AND status='running' AND worker_id=?",
                (now + max(30.0, float(lease_seconds)), now, job_id, worker_id),
            )
        return cur.rowcount == 1

    def finish_job_success(self, job_id: str, *, worker_id: str, session_id: str, actions, answer: str, result: dict[str, Any]):
        """Commit actions, assistant message, answer.ready and job success atomically."""
        now = time.time()
        mid = f"msg-{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            job = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] != "running" or str(job["worker_id"] or "") != worker_id:
                return None
            for action in actions:
                c.execute(
                    "INSERT OR REPLACE INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        action.action_id, job["conversation_id"], session_id, action.kind, action.title,
                        action.description, action.risk_level, int(action.side_effect), int(action.requires_confirmation),
                        action.status, json.dumps(action.payload, ensure_ascii=False, default=str), now, now,
                    ),
                )
            message = {
                "id": mid, "conversation_id": job["conversation_id"], "role": "assistant",
                "content": answer, "payload": result, "created_at": now,
            }
            c.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?)",
                (mid, job["conversation_id"], "assistant", answer, json.dumps(result, ensure_ascii=False, default=str), now),
            )
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, job["conversation_id"]))
            action_rows = c.execute(
                "SELECT * FROM actions WHERE conversation_id=? ORDER BY created_at DESC",
                (job["conversation_id"],),
            ).fetchall()
            decoded_actions = [self._decode_action(row) for row in action_rows]
            event_payload = {"message": message, "result": result, "actions": decoded_actions, "job_id": job_id}
            cur = c.execute(
                "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
                (job["conversation_id"], "answer.ready", json.dumps(event_payload, ensure_ascii=False, default=str), now),
            )
            c.execute(
                "UPDATE conversation_jobs SET status='succeeded',lease_until=NULL,last_error=NULL,updated_at=? "
                "WHERE id=? AND status='running' AND worker_id=?",
                (now, job_id, worker_id),
            )
        event = {"id": cur.lastrowid, "conversation_id": job["conversation_id"], "type": "answer.ready", "payload": event_payload, "created_at": now}
        return message, event, decoded_actions

    def finish_job_failure(self, job_id: str, *, worker_id: str, message: str, detail: str) -> dict[str, Any] | None:
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            job = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] != "running" or str(job["worker_id"] or "") != worker_id:
                return None
            payload = {"message": message, "detail": detail, "job_id": job_id}
            cur = c.execute(
                "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
                (job["conversation_id"], "answer.error", json.dumps(payload, ensure_ascii=False), now),
            )
            c.execute(
                "UPDATE conversation_jobs SET status='failed',lease_until=NULL,last_error=?,updated_at=? "
                "WHERE id=? AND status='running' AND worker_id=?",
                (detail[:1000], now, job_id, worker_id),
            )
        return {"id": cur.lastrowid, "conversation_id": job["conversation_id"], "type": "answer.error", "payload": payload, "created_at": now}

    def recover_interrupted_turn(self, cid):
        # A queued/running durable job is recoverable work, not an interrupted turn.
        if self.has_active_job(cid):
            return None
        return super().recover_interrupted_turn(cid)
