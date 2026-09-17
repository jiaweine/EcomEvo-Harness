from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .policy_control import PolicyStore, PolicyVersion


_POLICY_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_AUDIT_EVENTS = {
    "draft_created",
    "approved",
    "rejected",
    "published",
    "retirement_requested",
    "retired",
}


class PolicyWorkflowError(ValueError):
    pass


class PolicyWorkflowConflict(PolicyWorkflowError):
    pass


class PolicyWorkflowStaleApproval(PolicyWorkflowError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso(value: str | None, *, default_now: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        if not default_now:
            raise PolicyWorkflowError("effective time is required")
        return _utc_now()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise PolicyWorkflowError("invalid ISO effective time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _tenant_prefix(tenant_id: str) -> str:
    digest = hashlib.sha256(str(tenant_id).encode("utf-8")).hexdigest()[:12]
    return f"tenant.{digest}."


def authority_contract() -> dict[str, bool]:
    return {
        "maker_checker_required": True,
        "creator_can_self_approve": False,
        "approval_binds_source_hash": True,
        "approval_binds_preview_hash": True,
        "preview_mutates_production": False,
        "cross_tenant_write_allowed": False,
        "builtin_write_allowed": False,
        "publish_without_approval": False,
        "retire_without_second_actor": False,
    }


class PolicyWorkflow:
    """Maker-checker workflow around immutable PolicyStore versions.

    The deterministic PolicyStore remains the runtime authority resolver. This layer narrows
    the product write surface: tenant-scoped immutable drafts, separate approval, snapshot
    previews, and append-only audit. Publish/retire lifecycle mutation and their audit event
    share one SQLite transaction so an authoritative change cannot succeed without its audit.
    """

    def __init__(self, policies: PolicyStore):
        self.policies = policies
        self.path = str(policies.db_path)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS policy_workflow_audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    tenant_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_policy_workflow_audit_version
                    ON policy_workflow_audit(policy_id, version, id ASC);
                CREATE INDEX IF NOT EXISTS idx_policy_workflow_audit_tenant
                    ON policy_workflow_audit(tenant_id, id DESC);
                """
            )

    @staticmethod
    def _clean_rules(rules: list[str]) -> list[str]:
        clean = list(dict.fromkeys(str(rule).strip() for rule in rules if str(rule).strip()))
        if not clean:
            raise PolicyWorkflowError("at least one policy rule is required")
        if len(clean) > 50 or any(len(rule) > 1200 for rule in clean):
            raise PolicyWorkflowError("policy rules exceed workflow limits")
        return clean

    @staticmethod
    def _clean_controls(controls: dict[str, Any] | None) -> dict[str, Any]:
        value = dict(controls or {})
        if len(value) > 100 or len(_canonical(value)) > 20000:
            raise PolicyWorkflowError("policy controls exceed workflow limits")
        return value

    @staticmethod
    def _clean_scope(scope: dict[str, Any] | None, tenant_id: str) -> dict[str, str]:
        clean: dict[str, str] = {}
        for key, value in dict(scope or {}).items():
            name = str(key).strip()
            text = str(value).strip()
            if not name or not text:
                continue
            if len(name) > 80 or len(text) > 240:
                raise PolicyWorkflowError("policy scope exceeds workflow limits")
            clean[name] = text
        supplied = clean.get("tenant")
        if supplied is not None and supplied != tenant_id:
            raise PermissionError("cannot write policy scope for another tenant")
        clean["tenant"] = str(tenant_id)
        if len(clean) > 20:
            raise PolicyWorkflowError("policy scope has too many dimensions")
        return clean

    @staticmethod
    def _policy_key(policy_key: str) -> str:
        value = str(policy_key).strip().lower()
        if not _POLICY_KEY.fullmatch(value):
            raise PolicyWorkflowError("policy_key must match [a-z0-9][a-z0-9._-]{0,79}")
        return value

    def policy_id(self, tenant_id: str, policy_key: str) -> str:
        return _tenant_prefix(tenant_id) + self._policy_key(policy_key)

    def _tenant_version(self, policy_id: str, version: int, tenant_id: str) -> PolicyVersion:
        try:
            item = self.policies.get_version(str(policy_id), int(version))
        except KeyError as exc:
            raise KeyError(f"unknown policy version: {policy_id}@v{version}") from exc
        if item.policy_id.startswith("builtin."):
            raise PermissionError("builtin policy versions are read-only")
        if not item.policy_id.startswith(_tenant_prefix(tenant_id)):
            raise PermissionError("cannot write another tenant's policy family")
        if str(item.scope.get("tenant") or "") != str(tenant_id):
            raise PermissionError("policy version is outside the current tenant scope")
        return item

    def _append_audit_in_transaction(
        self,
        connection: sqlite3.Connection,
        item: PolicyVersion,
        *,
        tenant_id: str,
        event_type: str,
        actor_id: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        if event_type not in _AUDIT_EVENTS:
            raise PolicyWorkflowError("invalid policy workflow audit event")
        cursor = connection.execute(
            """
            INSERT INTO policy_workflow_audit(
                policy_id,version,tenant_id,event_type,actor_id,source_hash,payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                item.policy_id,
                item.version,
                str(tenant_id),
                event_type,
                str(actor_id),
                item.source_hash,
                _canonical(payload or {}),
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)

    def _append_audit(
        self,
        item: PolicyVersion,
        *,
        tenant_id: str,
        event_type: str,
        actor_id: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._append_audit_in_transaction(
                connection,
                item,
                tenant_id=tenant_id,
                event_type=event_type,
                actor_id=actor_id,
                payload=payload,
            )

    def events(self, policy_id: str, version: int, *, tenant_id: str) -> list[dict[str, Any]]:
        self._tenant_version(policy_id, version, tenant_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id,event_type,actor_id,source_hash,payload_json,created_at
                FROM policy_workflow_audit
                WHERE policy_id=? AND version=? AND tenant_id=? ORDER BY id ASC
                """,
                (str(policy_id), int(version), str(tenant_id)),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_type": str(row["event_type"]),
                "actor_id": str(row["actor_id"]),
                "source_hash": str(row["source_hash"]),
                "payload": json.loads(str(row["payload_json"] or "{}")),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _workflow_state(item: PolicyVersion, events: list[dict[str, Any]]) -> dict[str, Any]:
        latest_approval: dict[str, Any] | None = None
        retirement_request: dict[str, Any] | None = None
        rejected = False
        for event in events:
            kind = event["event_type"]
            if kind == "approved":
                latest_approval = event
                rejected = False
            elif kind == "rejected":
                latest_approval = None
                rejected = True
            elif kind == "retirement_requested":
                retirement_request = event
            elif kind == "published":
                rejected = False
            elif kind == "retired":
                retirement_request = None
        if item.status == "retired":
            state = "retired"
        elif item.status in {"active", "superseded"}:
            state = item.status
        elif rejected:
            state = "rejected"
        elif latest_approval is not None:
            state = "approved"
        else:
            state = "draft"
        return {
            "workflow_state": state,
            "latest_approval": latest_approval,
            "retirement_request": retirement_request,
        }

    def describe(self, policy_id: str, version: int, *, tenant_id: str) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        events = self.events(policy_id, version, tenant_id=tenant_id)
        return {
            **item.as_dict(),
            **self._workflow_state(item, events),
            "events": events,
            "authority": authority_contract(),
        }

    def list_tenant_versions(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        prefix = _tenant_prefix(tenant_id)
        rows = [
            item for item in self.policies.list_versions()
            if item.policy_id.startswith(prefix) and str(item.scope.get("tenant") or "") == str(tenant_id)
        ]
        return [self.describe(item.policy_id, item.version, tenant_id=tenant_id) for item in rows[: max(1, min(200, int(limit)))]]

    def create_draft(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        policy_key: str,
        domain: str,
        rules: list[str],
        controls: dict[str, Any] | None = None,
        scope: dict[str, Any] | None = None,
        authority: int = 60,
        priority: int = 0,
        source: str,
    ) -> dict[str, Any]:
        source = str(source).strip()
        if not source or len(source) > 500:
            raise PolicyWorkflowError("policy source must be 1..500 characters")
        authority = int(authority)
        priority = int(priority)
        if not 0 <= authority <= 90:
            raise PolicyWorkflowError("tenant policy authority must be between 0 and 90")
        if not -1000 <= priority <= 1000:
            raise PolicyWorkflowError("policy priority must be between -1000 and 1000")
        family_id = self.policy_id(tenant_id, policy_key)
        item = self.policies.create_version(
            policy_id=family_id,
            domain=str(domain).strip(),
            rules=self._clean_rules(rules),
            controls=self._clean_controls(controls),
            scope=self._clean_scope(scope, tenant_id),
            authority=authority,
            priority=priority,
            status="draft",
            owner=str(actor_id),
            approver="",
            source=source,
        )
        self._append_audit(
            item,
            tenant_id=tenant_id,
            event_type="draft_created",
            actor_id=actor_id,
            payload={"policy_key": self._policy_key(policy_key)},
        )
        return self.describe(item.policy_id, item.version, tenant_id=tenant_id)

    def _snapshot_copy(self, destination: Path) -> None:
        with self._connect() as source, sqlite3.connect(str(destination)) as target:
            source.backup(target)

    def preview_publish(
        self,
        policy_id: str,
        version: int,
        *,
        tenant_id: str,
        effective_from: str | None = None,
        approver: str = "preview",
    ) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        if item.status != "draft":
            raise PolicyWorkflowError("only draft policy versions can be previewed for publish")
        when = _iso(effective_from, default_now=True)
        with tempfile.TemporaryDirectory(prefix="ecomevo-policy-preview-") as tmp:
            path = Path(tmp) / "policy-preview.db"
            self._snapshot_copy(path)
            preview_store = PolicyStore(path, seed_defaults=False)
            preview_store.publish(item.policy_id, item.version, effective_from=when, approver=approver)
            resolution = preview_store.resolve(item.domain, scope=item.scope, as_of=when)
        return {
            "operation": "publish",
            "policy_id": item.policy_id,
            "version": item.version,
            "version_id": item.version_id,
            "source_hash": item.source_hash,
            "effective_from": when,
            "resolution": resolution,
            "preview_hash": str(resolution.get("resolution_hash") or ""),
            "safe_to_apply": resolution.get("status") == "resolved",
            "production_mutated": False,
        }

    def approve(
        self,
        policy_id: str,
        version: int,
        *,
        tenant_id: str,
        actor_id: str,
        effective_from: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        if item.status != "draft":
            raise PolicyWorkflowError("only draft policy versions can be approved")
        if str(actor_id) == item.owner:
            raise PermissionError("policy creator cannot self-approve")
        preview = self.preview_publish(
            policy_id,
            version,
            tenant_id=tenant_id,
            effective_from=effective_from,
            approver=actor_id,
        )
        if not preview["safe_to_apply"]:
            raise PolicyWorkflowConflict("policy publish preview is not resolved")
        event_id = self._append_audit(
            item,
            tenant_id=tenant_id,
            event_type="approved",
            actor_id=actor_id,
            payload={
                "effective_from": preview["effective_from"],
                "preview_hash": preview["preview_hash"],
                "note": str(note)[:2000],
            },
        )
        result = self.describe(policy_id, version, tenant_id=tenant_id)
        result["approval_event_id"] = event_id
        result["preview"] = preview
        return result

    def reject(
        self,
        policy_id: str,
        version: int,
        *,
        tenant_id: str,
        actor_id: str,
        note: str,
    ) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        if item.status != "draft":
            raise PolicyWorkflowError("only draft policy versions can be rejected")
        if str(actor_id) == item.owner:
            raise PermissionError("policy creator cannot self-review")
        self._append_audit(
            item,
            tenant_id=tenant_id,
            event_type="rejected",
            actor_id=actor_id,
            payload={"note": str(note)[:2000]},
        )
        return self.describe(policy_id, version, tenant_id=tenant_id)

    def _latest_approval(self, item: PolicyVersion, tenant_id: str) -> dict[str, Any] | None:
        approval: dict[str, Any] | None = None
        for event in self.events(item.policy_id, item.version, tenant_id=tenant_id):
            if event["event_type"] == "approved":
                approval = event
            elif event["event_type"] == "rejected":
                approval = None
        return approval

    def publish(
        self,
        policy_id: str,
        version: int,
        *,
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        if item.status != "draft":
            raise PolicyWorkflowError("only draft policy versions can be published")
        approval = self._latest_approval(item, tenant_id)
        if approval is None:
            raise PolicyWorkflowError("policy version requires approval before publish")
        if approval["actor_id"] != str(actor_id):
            raise PermissionError("the approving checker must perform publish")
        if approval["source_hash"] != item.source_hash:
            raise PolicyWorkflowStaleApproval("approved source hash no longer matches policy version")
        payload = dict(approval.get("payload") or {})
        when = str(payload.get("effective_from") or "")
        approved_preview_hash = str(payload.get("preview_hash") or "")
        preview = self.preview_publish(
            policy_id,
            version,
            tenant_id=tenant_id,
            effective_from=when,
            approver=actor_id,
        )
        if not preview["safe_to_apply"]:
            raise PolicyWorkflowConflict("policy publish preview is not resolved")
        if preview["preview_hash"] != approved_preview_hash:
            raise PolicyWorkflowStaleApproval("policy environment changed after approval; approve again")

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target_row = connection.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (item.policy_id, item.version),
            ).fetchone()
            if target_row is None or str(target_row["status"]) != "draft":
                raise PolicyWorkflowStaleApproval("policy version is no longer draft")
            if str(target_row["source_hash"]) != item.source_hash:
                raise PolicyWorkflowStaleApproval("policy version changed after approval")
            latest = connection.execute(
                """
                SELECT * FROM policy_versions
                WHERE policy_id=? AND version<>? AND status='active' AND effective_to IS NULL
                ORDER BY effective_from DESC, version DESC LIMIT 1
                """,
                (item.policy_id, item.version),
            ).fetchone()
            if latest is not None:
                latest_start = _parse_time(str(latest["effective_from"]))
                if _parse_time(when) < latest_start:
                    raise PolicyWorkflowError("cannot publish before the current active version")
                connection.execute(
                    "UPDATE policy_versions SET status='superseded',effective_to=? WHERE policy_id=? AND version=?",
                    (when, item.policy_id, int(latest["version"])),
                )
            connection.execute(
                """
                UPDATE policy_versions
                SET status='active',effective_from=?,effective_to=NULL,approver=?
                WHERE policy_id=? AND version=?
                """,
                (when, str(actor_id), item.policy_id, item.version),
            )
            published = self.policies._row(connection.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (item.policy_id, item.version),
            ).fetchone())
            if published is None:  # pragma: no cover
                raise RuntimeError("published policy version disappeared")
            self._append_audit_in_transaction(
                connection,
                published,
                tenant_id=tenant_id,
                event_type="published",
                actor_id=actor_id,
                payload={
                    "approval_event_id": approval["id"],
                    "effective_from": when,
                    "preview_hash": preview["preview_hash"],
                },
            )
        return self.describe(policy_id, version, tenant_id=tenant_id)

    def preview_retire(
        self,
        policy_id: str,
        version: int,
        *,
        tenant_id: str,
        effective_to: str | None = None,
    ) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        if item.status != "active":
            raise PolicyWorkflowError("only active policy versions can be previewed for retirement")
        when = _iso(effective_to, default_now=True)
        if _parse_time(when) <= _parse_time(item.effective_from):
            raise PolicyWorkflowError("retirement must be after policy effective_from")
        with tempfile.TemporaryDirectory(prefix="ecomevo-policy-retire-preview-") as tmp:
            path = Path(tmp) / "policy-retire-preview.db"
            self._snapshot_copy(path)
            preview_store = PolicyStore(path, seed_defaults=False)
            preview_store.retire(item.policy_id, item.version, effective_to=when)
            resolution = preview_store.resolve(item.domain, scope=item.scope, as_of=when)
        return {
            "operation": "retire",
            "policy_id": item.policy_id,
            "version": item.version,
            "version_id": item.version_id,
            "source_hash": item.source_hash,
            "effective_to": when,
            "resolution": resolution,
            "preview_hash": str(resolution.get("resolution_hash") or ""),
            "safe_to_apply": resolution.get("status") == "resolved",
            "production_mutated": False,
        }

    def request_retirement(
        self,
        policy_id: str,
        version: int,
        *,
        tenant_id: str,
        actor_id: str,
        effective_to: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        if item.status != "active":
            raise PolicyWorkflowError("only active policy versions can request retirement")
        preview = self.preview_retire(
            policy_id,
            version,
            tenant_id=tenant_id,
            effective_to=effective_to,
        )
        if not preview["safe_to_apply"]:
            raise PolicyWorkflowConflict("policy retirement preview is not resolved")
        event_id = self._append_audit(
            item,
            tenant_id=tenant_id,
            event_type="retirement_requested",
            actor_id=actor_id,
            payload={
                "effective_to": preview["effective_to"],
                "preview_hash": preview["preview_hash"],
                "note": str(note)[:2000],
            },
        )
        result = self.describe(policy_id, version, tenant_id=tenant_id)
        result["retirement_request_event_id"] = event_id
        result["preview"] = preview
        return result

    def _latest_retirement_request(self, item: PolicyVersion, tenant_id: str) -> dict[str, Any] | None:
        request: dict[str, Any] | None = None
        for event in self.events(item.policy_id, item.version, tenant_id=tenant_id):
            if event["event_type"] == "retirement_requested":
                request = event
            elif event["event_type"] == "retired":
                request = None
        return request

    def retire(
        self,
        policy_id: str,
        version: int,
        *,
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        item = self._tenant_version(policy_id, version, tenant_id)
        if item.status != "active":
            raise PolicyWorkflowError("only active policy versions can be retired")
        request = self._latest_retirement_request(item, tenant_id)
        if request is None:
            raise PolicyWorkflowError("retirement requires a prior request")
        if request["actor_id"] == str(actor_id):
            raise PermissionError("retirement requester cannot self-approve retirement")
        if request["source_hash"] != item.source_hash:
            raise PolicyWorkflowStaleApproval("retirement request no longer matches policy version")
        payload = dict(request.get("payload") or {})
        when = str(payload.get("effective_to") or "")
        approved_preview_hash = str(payload.get("preview_hash") or "")
        preview = self.preview_retire(
            policy_id,
            version,
            tenant_id=tenant_id,
            effective_to=when,
        )
        if not preview["safe_to_apply"]:
            raise PolicyWorkflowConflict("policy retirement preview is not resolved")
        if preview["preview_hash"] != approved_preview_hash:
            raise PolicyWorkflowStaleApproval("policy environment changed after retirement request")

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target_row = connection.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (item.policy_id, item.version),
            ).fetchone()
            if target_row is None or str(target_row["status"]) != "active":
                raise PolicyWorkflowStaleApproval("policy version is no longer active")
            if str(target_row["source_hash"]) != item.source_hash:
                raise PolicyWorkflowStaleApproval("policy version changed after retirement request")
            if _parse_time(when) <= _parse_time(str(target_row["effective_from"])):
                raise PolicyWorkflowError("retirement must be after policy effective_from")
            connection.execute(
                "UPDATE policy_versions SET status='retired',effective_to=? WHERE policy_id=? AND version=?",
                (when, item.policy_id, item.version),
            )
            retired = self.policies._row(connection.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (item.policy_id, item.version),
            ).fetchone())
            if retired is None:  # pragma: no cover
                raise RuntimeError("retired policy version disappeared")
            self._append_audit_in_transaction(
                connection,
                retired,
                tenant_id=tenant_id,
                event_type="retired",
                actor_id=actor_id,
                payload={
                    "retirement_request_event_id": request["id"],
                    "effective_to": when,
                    "preview_hash": preview["preview_hash"],
                },
            )
        return self.describe(policy_id, version, tenant_id=tenant_id)
