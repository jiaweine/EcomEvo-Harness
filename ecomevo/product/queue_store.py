from __future__ import annotations

import json
import time
from typing import Any, Literal

from .tenant_store import TenantConversationStore


QueueView = Literal["all", "mine", "unassigned"]
QUEUE_PRIORITIES = {"low", "normal", "high", "urgent"}


class QueueConversationStore(TenantConversationStore):
    """Tenant-safe collaboration metadata and derived queue state.

    Only ownership and priority are persisted. Runtime/workflow state is derived
    from durable jobs, leases, actions and the latest assistant result so the
    queue cannot become a second authority source.
    """

    def _init(self):
        super()._init()
        with self._conn() as db:
            columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(conversations)").fetchall()}
            if "queue_priority" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN queue_priority TEXT NOT NULL DEFAULT 'normal'")
            if "owner_user_id" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN owner_user_id TEXT")
            if "owner_claimed_at" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN owner_claimed_at REAL")
            if "queue_updated_at" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN queue_updated_at REAL NOT NULL DEFAULT 0")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_tenant_owner_updated "
                "ON conversations(tenant_id,owner_user_id,updated_at DESC)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_tenant_priority_updated "
                "ON conversations(tenant_id,queue_priority,updated_at DESC)"
            )

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            decoded = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @classmethod
    def _derived_state(cls, row: dict[str, Any]) -> tuple[str, str]:
        if bool(row.get("has_active_job")) or bool(row.get("has_active_lease")) or bool(row.get("has_approved_action")):
            return "processing", "任务正在处理或已确认操作正在执行"
        if bool(row.get("has_uncertain_action")):
            return "needs_verification", "业务操作结果无法确认，必须先核对下游状态，禁止盲目重试"
        if bool(row.get("has_proposed_action")):
            return "waiting_approval", "存在需要人工确认的业务操作"

        payload = cls._payload(row.get("latest_assistant_payload"))
        grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
        sufficiency = str(grounding.get("evidence_sufficiency") or "")
        if sufficiency == "conflicted":
            return "needs_verification", "当前证据存在直接冲突，需要人工复核"

        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        belief = runtime.get("belief") if isinstance(runtime.get("belief"), dict) else {}
        missing = runtime.get("missing_evidence") or belief.get("missing_evidence") or []
        if sufficiency == "insufficient" or runtime.get("status") == "needs_evidence" or bool(missing):
            return "waiting_evidence", "当前结论明确存在证据缺口"

        if str(row.get("latest_job_status") or "") == "failed":
            return "needs_attention", "最近一次处理任务失败，需要人工继续"
        if int(row.get("message_count") or 0) == 0:
            return "new", "尚未开始处理"
        return "ready", "当前没有待审批、待补证据或不确定执行状态"

    @classmethod
    def _decode_inbox_row(cls, row) -> dict[str, Any]:
        value = dict(row)
        state, reason = cls._derived_state(value)
        value["queue_state"] = state
        value["queue_state_reason"] = reason
        value["message_count"] = int(value.get("message_count") or 0)
        value["asset_count"] = int(value.get("asset_count") or 0)
        value["latest_user_content"] = str(value.get("latest_user_content") or "")[:240]
        value.pop("latest_assistant_payload", None)
        for key in (
            "has_active_job",
            "has_active_lease",
            "has_uncertain_action",
            "has_proposed_action",
            "has_approved_action",
        ):
            value.pop(key, None)
        return value

    def _inbox_query(
        self,
        *,
        tenant_id: str | None,
        owner_user_id: str | None,
        view: QueueView,
        scene: str | None,
        limit: int,
        cid: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant = self._request_tenant(tenant_id)
        now = time.time()
        where: list[str] = []
        where_params: list[Any] = []
        if tenant is not None:
            where.append("c.tenant_id=?")
            where_params.append(tenant)
        if cid is not None:
            where.append("c.id=?")
            where_params.append(cid)
        if scene:
            where.append("c.scene=?")
            where_params.append(scene)
        if view == "mine":
            if not owner_user_id:
                return []
            where.append("c.owner_user_id=?")
            where_params.append(owner_user_id)
        elif view == "unassigned":
            where.append("c.owner_user_id IS NULL")
        predicate = " AND ".join(where) if where else "1=1"
        bounded_limit = max(1, min(200, int(limit)))
        sql = f"""
            SELECT c.*,
              EXISTS(SELECT 1 FROM conversation_jobs j
                     WHERE j.conversation_id=c.id AND j.status IN ('queued','running')) AS has_active_job,
              EXISTS(SELECT 1 FROM turn_leases l
                     WHERE l.conversation_id=c.id AND l.expires_at>?) AS has_active_lease,
              EXISTS(SELECT 1 FROM actions a
                     WHERE a.conversation_id=c.id AND a.status='uncertain') AS has_uncertain_action,
              EXISTS(SELECT 1 FROM actions a
                     WHERE a.conversation_id=c.id AND a.requires_confirmation=1 AND a.status='proposed') AS has_proposed_action,
              EXISTS(SELECT 1 FROM actions a
                     WHERE a.conversation_id=c.id AND a.status='approved') AS has_approved_action,
              (SELECT j.status FROM conversation_jobs j
                 WHERE j.conversation_id=c.id ORDER BY j.created_at DESC,j.id DESC LIMIT 1) AS latest_job_status,
              (SELECT m.payload FROM messages m
                 WHERE m.conversation_id=c.id AND m.role='assistant'
                 ORDER BY m.created_at DESC,m.id DESC LIMIT 1) AS latest_assistant_payload,
              (SELECT m.content FROM messages m
                 WHERE m.conversation_id=c.id AND m.role='user'
                 ORDER BY m.created_at DESC,m.id DESC LIMIT 1) AS latest_user_content,
              (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count,
              (SELECT COUNT(*) FROM assets a WHERE a.conversation_id=c.id AND a.active=1) AS asset_count
            FROM conversations c
            WHERE {predicate}
            ORDER BY CASE c.queue_priority
                WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2
                WHEN 'low' THEN 3 ELSE 4 END,
                c.updated_at DESC,c.id DESC
            LIMIT ?
        """  # nosec - predicate is assembled exclusively from fixed clauses above.
        # The active-lease placeholder appears in SELECT before all WHERE placeholders.
        params: list[Any] = [now, *where_params, bounded_limit]
        with self._conn() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._decode_inbox_row(row) for row in rows]

    def list_inbox(
        self,
        *,
        tenant_id: str | None = None,
        owner_user_id: str | None = None,
        view: QueueView = "all",
        scene: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if view not in {"all", "mine", "unassigned"}:
            raise ValueError("invalid queue view")
        return self._inbox_query(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            view=view,
            scene=scene,
            limit=limit,
        )

    def get_inbox_item(self, cid: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        rows = self._inbox_query(
            tenant_id=tenant_id,
            owner_user_id=None,
            view="all",
            scene=None,
            limit=1,
            cid=cid,
        )
        if not rows:
            raise KeyError(cid)
        return rows[0]

    def claim_conversation(self, cid: str, owner_user_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
        owner = str(owner_user_id or "").strip()
        if not owner:
            raise ValueError("owner required")
        tenant = self._request_tenant(tenant_id)
        now = time.time()
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            if tenant is None:
                row = db.execute("SELECT owner_user_id FROM conversations WHERE id=?", (cid,)).fetchone()
            else:
                row = db.execute(
                    "SELECT owner_user_id FROM conversations WHERE id=? AND tenant_id=?",
                    (cid, tenant),
                ).fetchone()
            if not row:
                raise KeyError(cid)
            current = str(row["owner_user_id"] or "")
            if current and current != owner:
                return None
            if not current:
                if tenant is None:
                    cur = db.execute(
                        "UPDATE conversations SET owner_user_id=?,owner_claimed_at=?,queue_updated_at=? "
                        "WHERE id=? AND owner_user_id IS NULL",
                        (owner, now, now, cid),
                    )
                else:
                    cur = db.execute(
                        "UPDATE conversations SET owner_user_id=?,owner_claimed_at=?,queue_updated_at=? "
                        "WHERE id=? AND tenant_id=? AND owner_user_id IS NULL",
                        (owner, now, now, cid, tenant),
                    )
                if cur.rowcount != 1:
                    return None
        return self.get_inbox_item(cid, tenant_id=tenant)

    def release_claim(
        self,
        cid: str,
        actor_user_id: str,
        *,
        tenant_id: str | None = None,
        allow_override: bool = False,
    ) -> dict[str, Any]:
        actor = str(actor_user_id or "").strip()
        tenant = self._request_tenant(tenant_id)
        now = time.time()
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            if tenant is None:
                row = db.execute("SELECT owner_user_id FROM conversations WHERE id=?", (cid,)).fetchone()
            else:
                row = db.execute(
                    "SELECT owner_user_id FROM conversations WHERE id=? AND tenant_id=?",
                    (cid, tenant),
                ).fetchone()
            if not row:
                raise KeyError(cid)
            owner = str(row["owner_user_id"] or "")
            if owner and owner != actor and not allow_override:
                raise PermissionError(owner)
            if owner:
                if tenant is None:
                    db.execute(
                        "UPDATE conversations SET owner_user_id=NULL,owner_claimed_at=NULL,queue_updated_at=? WHERE id=?",
                        (now, cid),
                    )
                else:
                    db.execute(
                        "UPDATE conversations SET owner_user_id=NULL,owner_claimed_at=NULL,queue_updated_at=? "
                        "WHERE id=? AND tenant_id=?",
                        (now, cid, tenant),
                    )
        return self.get_inbox_item(cid, tenant_id=tenant)

    def update_queue_priority(self, cid: str, priority: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        value = str(priority or "").strip().lower()
        if value not in QUEUE_PRIORITIES:
            raise ValueError("invalid priority")
        tenant = self._request_tenant(tenant_id)
        now = time.time()
        with self._conn() as db:
            if tenant is None:
                cur = db.execute(
                    "UPDATE conversations SET queue_priority=?,queue_updated_at=? WHERE id=?",
                    (value, now, cid),
                )
            else:
                cur = db.execute(
                    "UPDATE conversations SET queue_priority=?,queue_updated_at=? WHERE id=? AND tenant_id=?",
                    (value, now, cid, tenant),
                )
            if cur.rowcount != 1:
                raise KeyError(cid)
        return self.get_inbox_item(cid, tenant_id=tenant)
