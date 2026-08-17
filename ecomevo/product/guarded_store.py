from __future__ import annotations

import json
import time
import uuid

from fastapi import HTTPException

from .store import ConversationStore as BaseConversationStore


class ConversationStore(BaseConversationStore):
    """Product store with atomic turn/asset consistency guards.

    A running turn consumes a fixed evidence snapshot. Asset insertion therefore shares the
    SQLite writer lock with turn leases and is rejected while the lease is active; this keeps
    the UI's task material set from silently diverging from the evidence actually analyzed.
    """

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
