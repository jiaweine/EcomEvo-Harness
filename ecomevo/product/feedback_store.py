from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from .tenant_store import TenantConversationStore


FEEDBACK_CATEGORIES = {
    "factual_error",
    "missing_support",
    "wrong_rule",
    "stale_source",
    "evidence_conflict",
    "other",
}
FEEDBACK_IMPACTS = {"answer_only", "decision_relevant", "action_blocking"}
TARGET_TYPES = {"answer", "claim", "evidence"}
REVIEW_DECISIONS = {"acknowledged", "accepted_for_eval", "needs_followup", "dismissed"}


class FeedbackConversationStore(TenantConversationStore):
    """Tenant-safe, append-only evidence dispute ledger.

    A dispute is a quality signal only. It never mutates messages, evidence, policy,
    routing state, Verifier output, or BusinessAction authority. Review decisions are
    append-only events so a later reviewer cannot erase the audit trail.
    """

    SCHEMA_VERSION = 1

    def _init(self):
        super()._init()
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_disputes(
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    assistant_message_id TEXT NOT NULL,
                    submitted_by TEXT NOT NULL,
                    category TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_ref TEXT NOT NULL DEFAULT '',
                    target_snapshot TEXT NOT NULL DEFAULT '{}',
                    explanation TEXT NOT NULL,
                    proposed_correction TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_dispute_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dispute_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_disputes_conversation_created
                    ON evidence_disputes(conversation_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_evidence_disputes_message
                    ON evidence_disputes(assistant_message_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_evidence_dispute_events_dispute
                    ON evidence_dispute_events(dispute_id,id);
                """
            )

    @staticmethod
    def _json(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _normalize_claim_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @classmethod
    def claim_ref(cls, claim: dict[str, Any]) -> str:
        kind = str(claim.get("kind") or "fact").strip().lower()
        text = cls._normalize_claim_text(claim.get("text"))
        digest = hashlib.sha256(f"{kind}\n{text}".encode("utf-8")).hexdigest()[:16]
        return f"claim-{digest}"

    @staticmethod
    def _message_hash(content: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"content": str(content or ""), "payload": payload or {}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _assistant_message(self, cid: str, message_id: str, tenant_id: str | None) -> dict[str, Any]:
        tenant = self._request_tenant(tenant_id)
        params: list[Any] = [cid, message_id]
        where = "m.conversation_id=? AND m.id=? AND m.role='assistant'"
        if tenant is not None:
            where += " AND c.tenant_id=?"
            params.append(tenant)
        with self._conn() as db:
            row = db.execute(
                "SELECT m.*,c.tenant_id,c.scene FROM messages m "
                "JOIN conversations c ON c.id=m.conversation_id WHERE " + where,
                params,
            ).fetchone()
        if not row:
            raise KeyError(message_id)
        data = dict(row)
        data["payload"] = self._json(data.get("payload"))
        return data

    def feedback_targets(
        self,
        cid: str,
        assistant_message_id: str,
        *,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        message = self._assistant_message(cid, assistant_message_id, tenant_id)
        payload = message["payload"]
        grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
        raw_claims = grounding.get("claims") if isinstance(grounding.get("claims"), list) else []
        claims: list[dict[str, Any]] = []
        seen_claims: set[str] = set()
        for claim in raw_claims:
            if not isinstance(claim, dict):
                continue
            text = self._normalize_claim_text(claim.get("text"))
            if not text:
                continue
            ref = self.claim_ref(claim)
            if ref in seen_claims:
                continue
            seen_claims.add(ref)
            claims.append(
                {
                    "ref": ref,
                    "text": text[:1200],
                    "kind": str(claim.get("kind") or "fact")[:32],
                    "verdict": str(claim.get("verdict") or "")[:32],
                    "evidence_ids": [str(x) for x in (claim.get("evidence_ids") or [])[:20]],
                }
            )

        raw_evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        evidence: list[dict[str, Any]] = []
        seen_evidence: set[str] = set()
        for row in raw_evidence:
            if not isinstance(row, dict):
                continue
            evidence_id = str(row.get("evidence_id") or "").strip()
            if not evidence_id or evidence_id in seen_evidence:
                continue
            seen_evidence.add(evidence_id)
            evidence.append(
                {
                    "ref": evidence_id,
                    "source": str(row.get("source") or "")[:120],
                    "title": str(row.get("title") or "")[:400],
                    "detail": str(row.get("detail") or "")[:1600],
                }
            )

        return {
            "schema_version": self.SCHEMA_VERSION,
            "conversation_id": cid,
            "assistant_message_id": assistant_message_id,
            "message_hash": self._message_hash(message.get("content") or "", payload),
            "answer_excerpt": str(message.get("content") or "")[:4000],
            "grounding_schema_version": grounding.get("schema_version"),
            "evidence_sufficiency": grounding.get("evidence_sufficiency"),
            "claims": claims,
            "evidence": evidence,
        }

    @staticmethod
    def _select_target(targets: dict[str, Any], target_type: str, target_ref: str) -> dict[str, Any]:
        if target_type == "answer":
            return {
                "type": "answer",
                "ref": "",
                "answer_excerpt": str(targets.get("answer_excerpt") or "")[:4000],
            }
        key = "claims" if target_type == "claim" else "evidence"
        for row in targets.get(key) or []:
            if str(row.get("ref") or "") == target_ref:
                return {"type": target_type, **row}
        raise ValueError("target reference does not exist in the assistant message")

    def submit_feedback(
        self,
        cid: str,
        assistant_message_id: str,
        *,
        submitted_by: str,
        category: str,
        impact: str,
        target_type: str,
        target_ref: str = "",
        explanation: str,
        proposed_correction: str = "",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        category = str(category or "").strip().lower()
        impact = str(impact or "").strip().lower()
        target_type = str(target_type or "").strip().lower()
        target_ref = str(target_ref or "").strip()
        actor = str(submitted_by or "").strip()
        explanation = str(explanation or "").strip()
        proposed_correction = str(proposed_correction or "").strip()
        if category not in FEEDBACK_CATEGORIES:
            raise ValueError("invalid feedback category")
        if impact not in FEEDBACK_IMPACTS:
            raise ValueError("invalid feedback impact")
        if target_type not in TARGET_TYPES:
            raise ValueError("invalid feedback target type")
        if not actor:
            raise ValueError("submitter required")
        if not explanation:
            raise ValueError("explanation required")
        if target_type != "answer" and not target_ref:
            raise ValueError("target reference required")
        if target_type == "answer":
            target_ref = ""

        tenant = self._request_tenant(tenant_id)
        self.get_conversation(cid, tenant_id=tenant)
        targets = self.feedback_targets(cid, assistant_message_id, tenant_id=tenant)
        selected = self._select_target(targets, target_type, target_ref)
        snapshot = {
            "schema_version": self.SCHEMA_VERSION,
            "message_hash": targets["message_hash"],
            "grounding_schema_version": targets.get("grounding_schema_version"),
            "evidence_sufficiency": targets.get("evidence_sufficiency"),
            "target": selected,
        }
        feedback_id = f"fb-{uuid.uuid4().hex[:16]}"
        now = time.time()
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            # Re-check tenant/message ownership inside the write transaction so the
            # append cannot race a mismatched conversation reference.
            params: list[Any] = [cid, assistant_message_id]
            where = "m.conversation_id=? AND m.id=? AND m.role='assistant'"
            if tenant is not None:
                where += " AND c.tenant_id=?"
                params.append(tenant)
            owned = db.execute(
                "SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE " + where,
                params,
            ).fetchone()
            if not owned:
                raise KeyError(assistant_message_id)
            db.execute(
                "INSERT INTO evidence_disputes("
                "id,conversation_id,assistant_message_id,submitted_by,category,impact,target_type,target_ref,"
                "target_snapshot,explanation,proposed_correction,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    feedback_id,
                    cid,
                    assistant_message_id,
                    actor,
                    category,
                    impact,
                    target_type,
                    target_ref,
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str),
                    explanation[:6000],
                    proposed_correction[:6000],
                    now,
                ),
            )
            db.execute(
                "INSERT INTO evidence_dispute_events(dispute_id,event_type,actor_user_id,actor_role,payload,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    feedback_id,
                    "submitted",
                    actor,
                    "operator",
                    json.dumps({"authority_changed": False}, ensure_ascii=False),
                    now,
                ),
            )
        return self.get_feedback(feedback_id, tenant_id=tenant)

    def _decode_feedback(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["target_snapshot"] = self._json(data.get("target_snapshot"))
        data["review_payload"] = self._json(data.get("review_payload"))
        data["status"] = str(data.get("review_status") or "open")
        data.pop("review_status", None)
        return data

    def _feedback_query(
        self,
        *,
        tenant_id: str | None,
        cid: str | None = None,
        feedback_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant = self._request_tenant(tenant_id)
        where: list[str] = []
        params: list[Any] = []
        if tenant is not None:
            where.append("c.tenant_id=?")
            params.append(tenant)
        if cid is not None:
            where.append("d.conversation_id=?")
            params.append(cid)
        if feedback_id is not None:
            where.append("d.id=?")
            params.append(feedback_id)
        predicate = " AND ".join(where) if where else "1=1"
        params.append(max(1, min(500, int(limit))))
        with self._conn() as db:
            rows = db.execute(
                f"""
                SELECT d.*,
                  (SELECT e.event_type FROM evidence_dispute_events e
                   WHERE e.dispute_id=d.id AND e.event_type!='submitted'
                   ORDER BY e.id DESC LIMIT 1) AS review_status,
                  (SELECT e.payload FROM evidence_dispute_events e
                   WHERE e.dispute_id=d.id AND e.event_type!='submitted'
                   ORDER BY e.id DESC LIMIT 1) AS review_payload,
                  (SELECT e.actor_user_id FROM evidence_dispute_events e
                   WHERE e.dispute_id=d.id AND e.event_type!='submitted'
                   ORDER BY e.id DESC LIMIT 1) AS reviewed_by,
                  (SELECT e.created_at FROM evidence_dispute_events e
                   WHERE e.dispute_id=d.id AND e.event_type!='submitted'
                   ORDER BY e.id DESC LIMIT 1) AS reviewed_at
                FROM evidence_disputes d
                JOIN conversations c ON c.id=d.conversation_id
                WHERE {predicate}
                ORDER BY d.created_at DESC,d.id DESC
                LIMIT ?
                """,  # nosec - predicate is assembled only from fixed clauses above.
                params,
            ).fetchall()
        return [self._decode_feedback(row) for row in rows]

    def list_feedback(
        self,
        cid: str,
        *,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.get_conversation(cid, tenant_id=tenant_id)
        return self._feedback_query(tenant_id=tenant_id, cid=cid, limit=limit)

    def list_feedback_admin(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        rows = self._feedback_query(tenant_id=tenant_id, limit=max(200, limit))
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if category:
            rows = [row for row in rows if row.get("category") == category]
        return rows[: max(1, min(200, int(limit)))]

    def get_feedback(self, feedback_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        rows = self._feedback_query(tenant_id=tenant_id, feedback_id=feedback_id, limit=1)
        if not rows:
            raise KeyError(feedback_id)
        return rows[0]

    def review_feedback(
        self,
        feedback_id: str,
        decision: str,
        *,
        actor_user_id: str,
        actor_role: str,
        note: str = "",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        decision = str(decision or "").strip().lower()
        if decision not in REVIEW_DECISIONS:
            raise ValueError("invalid review decision")
        tenant = self._request_tenant(tenant_id)
        self.get_feedback(feedback_id, tenant_id=tenant)
        now = time.time()
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            params: list[Any] = [feedback_id]
            tenant_clause = ""
            if tenant is not None:
                tenant_clause = " AND c.tenant_id=?"
                params.append(tenant)
            owned = db.execute(
                "SELECT 1 FROM evidence_disputes d JOIN conversations c ON c.id=d.conversation_id "
                "WHERE d.id=?" + tenant_clause,
                params,
            ).fetchone()
            if not owned:
                raise KeyError(feedback_id)
            db.execute(
                "INSERT INTO evidence_dispute_events(dispute_id,event_type,actor_user_id,actor_role,payload,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    feedback_id,
                    decision,
                    str(actor_user_id or "")[:240],
                    str(actor_role or "")[:64],
                    json.dumps(
                        {"note": str(note or "")[:4000], "authority_changed": False},
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        return self.get_feedback(feedback_id, tenant_id=tenant)

    def feedback_events(self, feedback_id: str, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        tenant = self._request_tenant(tenant_id)
        self.get_feedback(feedback_id, tenant_id=tenant)
        with self._conn() as db:
            rows = db.execute(
                "SELECT * FROM evidence_dispute_events WHERE dispute_id=? ORDER BY id",
                (feedback_id,),
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["payload"] = self._json(data.get("payload"))
            result.append(data)
        return result

    def evaluation_sample(self, feedback_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        feedback = self.get_feedback(feedback_id, tenant_id=tenant_id)
        tenant = self._request_tenant(tenant_id)
        message = self._assistant_message(
            feedback["conversation_id"],
            feedback["assistant_message_id"],
            tenant,
        )
        with self._conn() as db:
            params: list[Any] = [feedback["conversation_id"], float(message["created_at"])]
            tenant_clause = ""
            if tenant is not None:
                tenant_clause = " AND c.tenant_id=?"
                params.append(tenant)
            user_row = db.execute(
                "SELECT m.content,m.created_at FROM messages m "
                "JOIN conversations c ON c.id=m.conversation_id "
                "WHERE m.conversation_id=? AND m.role='user' AND m.created_at<=?"
                + tenant_clause
                + " ORDER BY m.created_at DESC,m.id DESC LIMIT 1",
                params,
            ).fetchone()
        payload = message["payload"]
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else None
        return {
            "schema_version": self.SCHEMA_VERSION,
            "feedback_id": feedback_id,
            "status": feedback["status"],
            "category": feedback["category"],
            "impact": feedback["impact"],
            "target": feedback["target_snapshot"].get("target"),
            "operator_observation": feedback["explanation"],
            "proposed_correction": feedback["proposed_correction"],
            "conversation_id": feedback["conversation_id"],
            "assistant_message_id": feedback["assistant_message_id"],
            "question": str(user_row["content"] if user_row else "")[:8000],
            "candidate_answer": str(message.get("content") or "")[:12000],
            "grounding": grounding,
            "evidence": evidence[:40],
            "message_hash": feedback["target_snapshot"].get("message_hash"),
            "authority": {
                "changes_production_authority": False,
                "changes_policy": False,
                "changes_routing": False,
                "auto_promotes_to_gold_set": False,
            },
        }
