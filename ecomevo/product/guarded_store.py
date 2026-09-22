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
        self._asset_snapshot: ContextVar[
            tuple[str, tuple[tuple[str, int, float, bool], ...], bool] | None
        ] = ContextVar(
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
                    lease_fence INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    session_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_jobs_ready
                ON conversation_jobs(status, lease_until, created_at);
                CREATE INDEX IF NOT EXISTS idx_conversation_jobs_conversation
                ON conversation_jobs(conversation_id, status, created_at);
                """
            )
            columns = {str(row["name"]) for row in c.execute("PRAGMA table_info(conversation_jobs)").fetchall()}
            if "session_id" not in columns:
                c.execute("ALTER TABLE conversation_jobs ADD COLUMN session_id TEXT")
            if "lease_fence" not in columns:
                c.execute("ALTER TABLE conversation_jobs ADD COLUMN lease_fence INTEGER NOT NULL DEFAULT 0")
            turn_columns = {str(row["name"]) for row in c.execute("PRAGMA table_info(turn_leases)").fetchall()}
            if "fence" not in turn_columns:
                c.execute("ALTER TABLE turn_leases ADD COLUMN fence INTEGER NOT NULL DEFAULT 0")
            c.execute("CREATE INDEX IF NOT EXISTS idx_conversation_jobs_session ON conversation_jobs(session_id)")

    @staticmethod
    def _coordination_now(connection) -> float:
        """Read lease time from the same transaction domain that owns the lease rows."""
        row = connection.execute(
            "SELECT (julianday('now') - 2440587.5) * 86400.0 AS now"
        ).fetchone()
        return float(row["now"])

    @staticmethod
    def _revision(rows) -> tuple[tuple[str, int, float, bool], ...]:
        return tuple(sorted(
            (
                str(row.get("id") or ""),
                int(row.get("size") or 0),
                float(row.get("created_at") or 0),
                bool(row.get("active", True)),
            )
            for row in rows
        ))

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

    @staticmethod
    def coordination_capabilities() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "lease_clock": "sqlite_transaction_domain",
            "turn_lease_fencing_generation": True,
            "job_lease_fencing_generation": True,
            "stale_generation_terminal_commit_blocked": True,
            "cross_node_shared_transaction_domain": False,
            "cross_node_supported": False,
        }

    def has_active_turn(self, cid) -> bool:
        if self.has_active_job(cid):
            return True
        with self._conn() as c:
            now = self._coordination_now(c)
            row = c.execute(
                "SELECT expires_at FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
        return bool(row and float(row["expires_at"]) > now)

    def list_assets(self, cid, include_excluded: bool = True):
        rows = super().list_assets(cid, include_excluded=include_excluded)
        self._asset_snapshot.set((cid, self._revision(rows), include_excluded))
        return rows

    def claim_turn(self, cid, ttl=120.0):
        """Atomically claim a turn and advance its durable fencing generation."""
        snapshot = self._asset_snapshot.get()
        self._asset_snapshot.set(None)
        token = f"lease-{uuid.uuid4().hex}"
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self._coordination_now(c)
            active_job = c.execute(
                "SELECT 1 FROM conversation_jobs WHERE conversation_id=? AND status IN ('queued','running') LIMIT 1",
                (cid,),
            ).fetchone()
            if active_job:
                return None
            if snapshot and snapshot[0] == cid:
                scope_sql = "" if snapshot[2] else " AND active=1"
                # The suffix is selected from the two internal constants above; values remain bound.
                rows = c.execute(
                    f"SELECT id,size,created_at,active FROM assets WHERE conversation_id=?{scope_sql}",  # nosec
                    (cid,),
                ).fetchall()
                current = self._revision([dict(row) for row in rows])
                if current != snapshot[1]:
                    raise HTTPException(409, "任务资料刚刚发生变化，请重新发送以纳入最新资料")

            lease = c.execute(
                "SELECT token,expires_at,fence FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if lease and float(lease["expires_at"]) > now:
                return None
            fence = (int(lease["fence"] or 0) + 1) if lease else 1
            c.execute(
                "INSERT INTO turn_leases(conversation_id,token,expires_at,updated_at,fence) VALUES(?,?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "token=excluded.token,expires_at=excluded.expires_at,updated_at=excluded.updated_at,fence=excluded.fence",
                (cid, token, now + float(ttl), now, fence),
            )
        return token

    def renew_or_restore_turn(
        self,
        cid: str,
        token: str,
        ttl: float = 120.0,
        *,
        fence: int | None = None,
    ) -> bool:
        """Renew one turn generation; a stale generation can never restore ownership."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self._coordination_now(c)
            row = c.execute(
                "SELECT token,expires_at,fence FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if fence is not None:
                expected_fence = int(fence)
                if (
                    not row
                    or str(row["token"]) != token
                    or int(row["fence"] or 0) != expected_fence
                ):
                    return False
                cur = c.execute(
                    "UPDATE turn_leases SET expires_at=?,updated_at=? "
                    "WHERE conversation_id=? AND token=? AND fence=?",
                    (now + float(ttl), now, cid, token, expected_fence),
                )
                return cur.rowcount == 1

            # Backwards-compatible path for pre-fence callers. A replacement after expiry
            # still advances the generation so newly fenced workers cannot be confused with
            # a previous owner.
            if row and str(row["token"]) != token and float(row["expires_at"]) > now:
                return False
            if row and str(row["token"]) == token:
                next_fence = max(1, int(row["fence"] or 0))
            elif row:
                next_fence = int(row["fence"] or 0) + 1
            else:
                next_fence = 1
            c.execute(
                "INSERT INTO turn_leases(conversation_id,token,expires_at,updated_at,fence) VALUES(?,?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "token=excluded.token,expires_at=excluded.expires_at,updated_at=excluded.updated_at,fence=excluded.fence",
                (cid, token, now + float(ttl), now, next_fence),
            )
        return True

    def add_asset(self, cid, *, name, mime, path, size, meta):
        aid = f"asset-{uuid.uuid4().hex[:12]}"
        now = time.time()
        size = max(0, int(size or 0))
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            lease_now = self._coordination_now(c)
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
                if lease and float(lease["expires_at"]) > lease_now:
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
                "INSERT INTO assets(id,conversation_id,name,mime,path,size,meta,created_at,active,excluded_at,excluded_reason) "
                "VALUES(?,?,?,?,?,?,?,?,1,NULL,'')",
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
            lease_now = self._coordination_now(c)
            lease = c.execute(
                "SELECT token,expires_at,fence FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if (
                not lease
                or str(lease["token"]) != lease_token
                or float(lease["expires_at"]) <= lease_now
            ):
                raise HTTPException(409, "当前任务处理权已发生变化，请重新发送")
            job_payload["turn_fence"] = int(lease["fence"] or 0)
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
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self._coordination_now(c)
            lease_until = now + max(30.0, float(lease_seconds))
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
                "UPDATE conversation_jobs SET status='running',worker_id=?,lease_until=?,"
                "lease_fence=lease_fence+1,attempts=attempts+1,updated_at=? "
                "WHERE id=? AND (status='queued' OR (status='running' AND COALESCE(lease_until,0)<=?))",
                (worker_id, lease_until, now, row["id"], now),
            )
            if cur.rowcount != 1:
                return None
            claimed = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (row["id"],)).fetchone()
        return self._decode_job(claimed)

    def renew_job(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: float = 120.0,
        *,
        lease_fence: int | None = None,
    ) -> bool:
        with self._conn() as c:
            now = self._coordination_now(c)
            params: list[Any] = [
                now + max(30.0, float(lease_seconds)),
                now,
                job_id,
                worker_id,
            ]
            fence_sql = ""
            if lease_fence is not None:
                fence_sql = " AND lease_fence=?"
                params.append(int(lease_fence))
            params.append(now)
            cur = c.execute(
                "UPDATE conversation_jobs SET lease_until=?,updated_at=? "
                "WHERE id=? AND status='running' AND worker_id=?"
                + fence_sql
                + " AND COALESCE(lease_until,0)>?",
                params,
            )
        return cur.rowcount == 1

    def add_job_event(
        self,
        job_id: str,
        worker_id: str,
        type_: str,
        payload: dict[str, Any],
        *,
        lease_fence: int | None = None,
    ) -> dict[str, Any] | None:
        """Append progress only while the exact fenced job generation remains owned."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self._coordination_now(c)
            params: list[Any] = [job_id, worker_id]
            fence_sql = ""
            if lease_fence is not None:
                fence_sql = " AND lease_fence=?"
                params.append(int(lease_fence))
            params.append(now)
            job = c.execute(
                "SELECT conversation_id FROM conversation_jobs "
                "WHERE id=? AND status='running' AND worker_id=?"
                + fence_sql
                + " AND COALESCE(lease_until,0)>?",
                params,
            ).fetchone()
            if not job:
                return None
            cur = c.execute(
                "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
                (
                    job["conversation_id"],
                    type_,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    now,
                ),
            )
        return {
            "id": cur.lastrowid,
            "conversation_id": job["conversation_id"],
            "type": type_,
            "payload": payload,
            "created_at": now,
        }

    @staticmethod
    def _job_generation_owned(job, worker_id: str, lease_fence: int | None, now: float) -> bool:
        if (
            not job
            or job["status"] != "running"
            or str(job["worker_id"] or "") != worker_id
            or job["lease_until"] is None
            or float(job["lease_until"]) <= now
        ):
            return False
        return lease_fence is None or int(job["lease_fence"] or 0) == int(lease_fence)

    def finish_job_success(
        self,
        job_id: str,
        *,
        worker_id: str,
        session_id: str,
        actions,
        answer: str,
        result: dict[str, Any],
        lease_fence: int | None = None,
    ):
        """Commit actions, assistant message, answer.ready and job success atomically."""
        mid = f"msg-{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self._coordination_now(c)
            job = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (job_id,)).fetchone()
            if not self._job_generation_owned(job, worker_id, lease_fence, now):
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
            params: list[Any] = [session_id, now, job_id, worker_id]
            fence_sql = ""
            if lease_fence is not None:
                fence_sql = " AND lease_fence=?"
                params.append(int(lease_fence))
            updated = c.execute(
                "UPDATE conversation_jobs SET status='succeeded',session_id=?,lease_until=NULL,last_error=NULL,updated_at=? "
                "WHERE id=? AND status='running' AND worker_id=?" + fence_sql,
                params,
            )
            if updated.rowcount != 1:
                raise RuntimeError("job fencing changed during terminal success transaction")
            job_payload = json.loads(job["payload"] or "{}")
            turn_token = str(job_payload.get("lease_token") or "")
            turn_fence = job_payload.get("turn_fence")
            if turn_token:
                if turn_fence is None:
                    c.execute(
                        "DELETE FROM turn_leases WHERE conversation_id=? AND token=?",
                        (job["conversation_id"], turn_token),
                    )
                else:
                    c.execute(
                        "DELETE FROM turn_leases WHERE conversation_id=? AND token=? AND fence=?",
                        (job["conversation_id"], turn_token, int(turn_fence)),
                    )
        event = {"id": cur.lastrowid, "conversation_id": job["conversation_id"], "type": "answer.ready", "payload": event_payload, "created_at": now}
        return message, event, decoded_actions

    def finish_job_failure(
        self,
        job_id: str,
        *,
        worker_id: str,
        message: str,
        detail: str,
        lease_fence: int | None = None,
    ) -> dict[str, Any] | None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self._coordination_now(c)
            job = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (job_id,)).fetchone()
            if not self._job_generation_owned(job, worker_id, lease_fence, now):
                return None
            payload = {"message": message, "detail": detail, "job_id": job_id}
            cur = c.execute(
                "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
                (job["conversation_id"], "answer.error", json.dumps(payload, ensure_ascii=False), now),
            )
            params: list[Any] = [detail[:1000], now, job_id, worker_id]
            fence_sql = ""
            if lease_fence is not None:
                fence_sql = " AND lease_fence=?"
                params.append(int(lease_fence))
            updated = c.execute(
                "UPDATE conversation_jobs SET status='failed',lease_until=NULL,last_error=?,updated_at=? "
                "WHERE id=? AND status='running' AND worker_id=?" + fence_sql,
                params,
            )
            if updated.rowcount != 1:
                raise RuntimeError("job fencing changed during terminal failure transaction")
            job_payload = json.loads(job["payload"] or "{}")
            turn_token = str(job_payload.get("lease_token") or "")
            turn_fence = job_payload.get("turn_fence")
            if turn_token:
                if turn_fence is None:
                    c.execute(
                        "DELETE FROM turn_leases WHERE conversation_id=? AND token=?",
                        (job["conversation_id"], turn_token),
                    )
                else:
                    c.execute(
                        "DELETE FROM turn_leases WHERE conversation_id=? AND token=? AND fence=?",
                        (job["conversation_id"], turn_token, int(turn_fence)),
                    )
        return {"id": cur.lastrowid, "conversation_id": job["conversation_id"], "type": "answer.error", "payload": payload, "created_at": now}

    def recover_interrupted_turn(self, cid):
        """Recover a stale accepted turn using the lease transaction-domain clock."""
        # A queued/running durable job is recoverable work, not an interrupted turn.
        if self.has_active_job(cid):
            return None
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self._coordination_now(c)
            latest_accepted = c.execute(
                "SELECT id FROM task_events WHERE conversation_id=? AND type='message.accepted' "
                "ORDER BY id DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if not latest_accepted:
                return None
            latest_terminal = c.execute(
                "SELECT id FROM task_events WHERE conversation_id=? "
                "AND type IN ('answer.ready','answer.error') ORDER BY id DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if latest_terminal and int(latest_terminal["id"]) > int(latest_accepted["id"]):
                return None
            lease = c.execute(
                "SELECT expires_at FROM turn_leases WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if lease and float(lease["expires_at"]) > now:
                return None
            c.execute("DELETE FROM turn_leases WHERE conversation_id=?", (cid,))
            payload = {
                "message": "上次处理因服务中断未完成",
                "detail": "任务资料仍然保留，请重新发送上一条问题或继续当前任务。",
                "recovered": True,
            }
            cur = c.execute(
                "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
                (cid, "answer.error", json.dumps(payload, ensure_ascii=False), now),
            )
        return {
            "id": cur.lastrowid,
            "conversation_id": cid,
            "type": "answer.error",
            "payload": payload,
            "created_at": now,
        }
