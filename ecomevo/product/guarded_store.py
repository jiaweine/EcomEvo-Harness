from __future__ import annotations

from contextvars import ContextVar
import json
import time
import uuid

from fastapi import HTTPException

from .store import ConversationStore as BaseConversationStore


class ConversationStore(BaseConversationStore):
    """Product store with atomic turn/asset consistency guards."""

    def __init__(self, *args, **kwargs):
        self._asset_snapshot: ContextVar[tuple[str, int, int, float] | None] = ContextVar(
            f"ecomevo_asset_snapshot_{id(self)}",
            default=None,
        )
        super().__init__(*args, **kwargs)

    @staticmethod
    def _revision(rows) -> tuple[int, int, float]:
        return (
            len(rows),
            sum(int(row.get("size") or 0) for row in rows),
            max((float(row.get("created_at") or 0) for row in rows), default=0.0),
        )

    def has_active_turn(self, cid) -> bool:
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
