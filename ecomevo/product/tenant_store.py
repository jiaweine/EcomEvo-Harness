from __future__ import annotations

import time
import uuid

from ecomevo.identity import active_principal
from .guarded_store import ConversationStore as GuardedConversationStore


class TenantConversationStore(GuardedConversationStore):
    """Durable store with request-scoped tenant isolation and approval actor audit."""

    def _init(self):
        super()._init()
        with self._conn() as c:
            columns = {str(row["name"]) for row in c.execute("PRAGMA table_info(conversations)").fetchall()}
            if "tenant_id" not in columns:
                c.execute("ALTER TABLE conversations ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local'")
            if "created_by" not in columns:
                c.execute("ALTER TABLE conversations ADD COLUMN created_by TEXT NOT NULL DEFAULT 'local-admin'")
            job_columns = {str(row["name"]) for row in c.execute("PRAGMA table_info(conversation_jobs)").fetchall()}
            if "session_id" not in job_columns:
                c.execute("ALTER TABLE conversation_jobs ADD COLUMN session_id TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS idx_conversations_tenant_updated ON conversations(tenant_id,updated_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_conversation_jobs_session ON conversation_jobs(session_id)")

    @staticmethod
    def _request_tenant(explicit: str | None = None) -> str | None:
        if explicit is not None:
            return explicit
        principal = active_principal()
        return principal.tenant_id if principal else None

    def create_conversation(self, title="新的业务任务", scene="product_governance", *, tenant_id: str | None = None, created_by: str | None = None):
        principal = active_principal()
        tenant = tenant_id or (principal.tenant_id if principal else "local")
        creator = created_by or (principal.user_id if principal else "local-admin")
        cid = f"cv-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT INTO conversations(id,title,scene,created_at,updated_at,tenant_id,created_by) VALUES(?,?,?,?,?,?,?)",
                (cid, title, scene, now, now, tenant, creator),
            )
        return self.get_conversation(cid, tenant_id=tenant)

    def list_conversations(self, limit=50, *, tenant_id: str | None = None):
        tenant = self._request_tenant(tenant_id)
        if tenant is None:
            with self._conn() as c:
                rows = c.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM conversations WHERE tenant_id=? ORDER BY updated_at DESC LIMIT ?",
                    (tenant, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, cid, *, tenant_id: str | None = None):
        tenant = self._request_tenant(tenant_id)
        if tenant is None:
            with self._conn() as c:
                row = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        else:
            with self._conn() as c:
                row = c.execute("SELECT * FROM conversations WHERE id=? AND tenant_id=?", (cid, tenant)).fetchone()
        if not row:
            raise KeyError(cid)
        return dict(row)

    def conversation_tenant(self, cid: str) -> str:
        with self._conn() as c:
            row = c.execute("SELECT tenant_id FROM conversations WHERE id=?", (cid,)).fetchone()
        if not row:
            raise KeyError(cid)
        return str(row["tenant_id"])

    def session_belongs_to_tenant(self, session_id: str, tenant_id: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM conversation_jobs j JOIN conversations c ON c.id=j.conversation_id "
                "WHERE j.session_id=? AND c.tenant_id=? LIMIT 1",
                (session_id, tenant_id),
            ).fetchone()
        return bool(row)

    def list_assets(self, cid, include_excluded: bool = True):
        if active_principal():
            self.get_conversation(cid)
        return super().list_assets(cid, include_excluded=include_excluded)

    def get_asset(self, aid):
        principal = active_principal()
        if principal:
            with self._conn() as c:
                row = c.execute(
                    "SELECT a.* FROM assets a JOIN conversations c ON c.id=a.conversation_id "
                    "WHERE a.id=? AND c.tenant_id=?",
                    (aid, principal.tenant_id),
                ).fetchone()
            if not row:
                raise KeyError(aid)
            data = dict(row)
            import json
            data["meta"] = json.loads(data.pop("meta") or "{}")
            data["active"] = bool(data.get("active", 1))
            data["excluded_at"] = data.get("excluded_at")
            data["excluded_reason"] = data.get("excluded_reason") or ""
            return data
        return super().get_asset(aid)

    def add_asset(self, cid, **kwargs):
        if active_principal():
            self.get_conversation(cid)
        return super().add_asset(cid, **kwargs)

    def bind_asset(self, aid, cid):
        principal = active_principal()
        if principal:
            self.get_conversation(cid)
            try:
                owned = self.get_asset(aid)
            except KeyError:
                return None
            if owned.get("conversation_id") != cid:
                return None
            return owned
        return super().bind_asset(aid, cid)

    def list_actions(self, cid, status=None, terminal_limit=100):
        if active_principal():
            self.get_conversation(cid)
        return super().list_actions(cid, status=status, terminal_limit=terminal_limit)

    def get_action(self, aid):
        principal = active_principal()
        if principal:
            with self._conn() as c:
                row = c.execute(
                    "SELECT a.* FROM actions a JOIN conversations c ON c.id=a.conversation_id "
                    "WHERE a.id=? AND c.tenant_id=?",
                    (aid, principal.tenant_id),
                ).fetchone()
            if not row:
                raise KeyError(aid)
            return self._decode_action(row)
        return super().get_action(aid)

    def transition_action(self, aid, expected_status, status, payload_patch=None):
        patch = dict(payload_patch or {})
        principal = active_principal()
        if principal:
            self.get_action(aid)
            patch.update({
                "actor_tenant": principal.tenant_id,
                "actor_user": principal.user_id,
                "actor_role": principal.role,
                "actor_auth_mode": principal.auth_mode,
            })
        return super().transition_action(aid, expected_status, status, patch)

    def transition_action_with_event(self, aid, expected_status, status, payload_patch=None):
        patch = dict(payload_patch or {})
        principal = active_principal()
        if principal:
            self.get_action(aid)
            patch.update({
                "actor_tenant": principal.tenant_id,
                "actor_user": principal.user_id,
                "actor_role": principal.role,
                "actor_auth_mode": principal.auth_mode,
            })
        return super().transition_action_with_event(aid, expected_status, status, patch)
