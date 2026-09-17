from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_POLICY_STATUSES = {"draft", "active", "superseded", "retired"}
_RUNTIME_STATUSES = {"active", "superseded", "retired"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | datetime | None, *, default: datetime | None = None) -> datetime:
    if value is None:
        if default is None:
            raise ValueError("time value is required")
        return default.astimezone(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if not raw:
        if default is None:
            raise ValueError("time value is required")
        return default.astimezone(timezone.utc)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime | None, *, default: datetime | None = None) -> str | None:
    if value is None and default is None:
        return None
    parsed = _parse_time(value, default=default)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_hash(
    *,
    domain: str,
    rules: list[str],
    controls: dict[str, Any],
    scope: dict[str, str],
    authority: int,
    priority: int,
    source: str,
) -> str:
    payload = _canonical(
        {
            "domain": domain,
            "rules": rules,
            "controls": controls,
            "scope": scope,
            "authority": authority,
            "priority": priority,
            "source": source,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PolicyVersion:
    policy_id: str
    version: int
    domain: str
    status: str
    rules: tuple[str, ...]
    controls: dict[str, Any]
    scope: dict[str, str]
    authority: int
    priority: int
    effective_from: str
    effective_to: str | None
    owner: str
    approver: str
    source: str
    source_hash: str
    created_at: str

    @property
    def version_id(self) -> str:
        return f"{self.policy_id}@v{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "version_id": self.version_id,
            "domain": self.domain,
            "status": self.status,
            "rules": list(self.rules),
            "controls": dict(self.controls),
            "scope": dict(self.scope),
            "authority": self.authority,
            "priority": self.priority,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "owner": self.owner,
            "approver": self.approver,
            "source": self.source,
            "source_hash": self.source_hash,
            "created_at": self.created_at,
        }


class PolicyStore:
    """SQLite-backed immutable policy versions with deterministic resolution.

    Content is immutable per ``(policy_id, version)``. Lifecycle operations only alter
    status/effective interval/approver. Runtime precedence is deterministic:
    authority -> scope specificity -> explicit priority. Equal-precedence incompatible
    structured controls become an unresolved conflict; no model gets to silently choose.
    """

    BUILTINS: tuple[dict[str, Any], ...] = (
        {
            "policy_id": "builtin.product-governance",
            "domain": "product_governance",
            "rules": [
                "商品标题、主图、详情与实物/资质应保持一致",
                "涉及功效、材质、品牌授权等高风险声明时必须有可核验证据",
                "证据不足时优先进入复核，不直接执行下架",
            ],
            "controls": {
                "high_risk_claim.require_verifiable_evidence": True,
                "insufficient_evidence.action": "review",
            },
        },
        {
            "policy_id": "builtin.merchant-review",
            "domain": "merchant_review",
            "rules": [
                "主体资质、经营范围、授权链路、历史处罚与账户关联需要一致核对",
                "高风险关联或材料矛盾时应转人工复核",
                "通过/拒绝属于有业务副作用的动作，必须留痕",
            ],
            "controls": {
                "identity.require_verifiable_identifier": True,
                "material_conflict.action": "review",
                "side_effect.require_audit": True,
            },
        },
        {
            "policy_id": "builtin.aftersales",
            "domain": "aftersales",
            "rules": [
                "判责应同时核对订单履约、商品描述、沟通记录与用户举证",
                "退款金额不得超过订单可退金额",
                "争议证据不足时应补证或升级，不应直接定责",
            ],
            "controls": {
                "refund.max_exceeds_refundable": False,
                "insufficient_evidence.action": "request_more_or_escalate",
            },
        },
        {
            "policy_id": "builtin.risk-review",
            "domain": "risk_review",
            "rules": [
                "风险结论至少需要两个独立信号或一条强证据",
                "模型/规则命中只能作为线索，最终处置需结合业务事实",
            ],
            "controls": {
                "risk.minimum_independent_signals": 2,
                "model_signal.authority": "lead_only",
            },
        },
        {
            "policy_id": "builtin.content-audit",
            "domain": "content_audit",
            "rules": [
                "图文、视频、文案需做一致性与合规检查",
                "无法直接理解的媒体应转视觉/音视频模型或人工复核",
            ],
            "controls": {"uninterpretable_media.action": "specialist_or_review"},
        },
    )

    def __init__(self, db_path: str | Path, *, seed_defaults: bool = True):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._init_schema()
        if seed_defaults:
            self._seed_defaults()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_versions (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    controls_json TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    authority INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    owner TEXT NOT NULL,
                    approver TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(policy_id, version)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_policy_resolution ON policy_versions(domain, status, effective_from, effective_to)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_policy_identity ON policy_versions(policy_id, version DESC)"
            )

    @staticmethod
    def _validate_scope(scope: dict[str, Any] | None) -> dict[str, str]:
        clean: dict[str, str] = {}
        for key, value in dict(scope or {}).items():
            name = str(key).strip()
            text = str(value).strip()
            if name and text:
                clean[name] = text
        return clean

    @staticmethod
    def _validate_rules(rules: list[str] | tuple[str, ...]) -> list[str]:
        clean = [str(rule).strip() for rule in rules if str(rule).strip()]
        if not clean:
            raise ValueError("policy version requires at least one rule")
        return clean

    @staticmethod
    def _validate_status(status: str) -> str:
        value = str(status).strip().lower()
        if value not in _POLICY_STATUSES:
            raise ValueError(f"invalid policy status: {status}")
        return value

    def _seed_defaults(self) -> None:
        for row in self.BUILTINS:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM policy_versions WHERE policy_id=? LIMIT 1", (row["policy_id"],)
                ).fetchone()
            if exists:
                continue
            try:
                self.create_version(
                    policy_id=row["policy_id"],
                    domain=row["domain"],
                    rules=row["rules"],
                    controls=row.get("controls") or {},
                    scope={},
                    authority=50,
                    priority=0,
                    status="active",
                    effective_from="1970-01-01T00:00:00Z",
                    owner="system",
                    approver="system",
                    source="builtin:v1",
                )
            except sqlite3.IntegrityError:
                # Another worker may have seeded the same runtime DB concurrently.
                pass

    def create_version(
        self,
        *,
        policy_id: str,
        domain: str,
        rules: list[str] | tuple[str, ...],
        controls: dict[str, Any] | None = None,
        scope: dict[str, Any] | None = None,
        authority: int = 50,
        priority: int = 0,
        status: str = "draft",
        effective_from: str | datetime | None = None,
        effective_to: str | datetime | None = None,
        owner: str = "",
        approver: str = "",
        source: str = "",
    ) -> PolicyVersion:
        policy_id = str(policy_id).strip()
        domain = str(domain).strip()
        if not policy_id or not domain:
            raise ValueError("policy_id and domain are required")
        clean_rules = self._validate_rules(rules)
        clean_controls = dict(controls or {})
        clean_scope = self._validate_scope(scope)
        clean_status = self._validate_status(status)
        authority = max(0, min(100, int(authority)))
        priority = int(priority)
        now = _utc_now()
        start = _iso(
            effective_from,
            default=now if clean_status != "draft" else datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        end = _iso(effective_to)
        if end is not None and _parse_time(end) <= _parse_time(start):
            raise ValueError("effective_to must be after effective_from")
        digest = _source_hash(
            domain=domain,
            rules=clean_rules,
            controls=clean_controls,
            scope=clean_scope,
            authority=authority,
            priority=priority,
            source=str(source or ""),
        )
        created_at = _iso(now)

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version, MIN(domain) AS domain FROM policy_versions WHERE policy_id=?",
                (policy_id,),
            ).fetchone()
            current_version = int(current["version"] or 0)
            existing_domain = str(current["domain"] or "")
            if existing_domain and existing_domain != domain:
                raise ValueError("all versions of a policy_id must keep the same domain")
            if clean_status == "active" and current_version > 0:
                raise ValueError("new versions of an existing policy must be created as draft and published")
            version = current_version + 1
            connection.execute(
                """
                INSERT INTO policy_versions(
                    policy_id, version, domain, status, rules_json, controls_json,
                    scope_json, authority, priority, effective_from, effective_to,
                    owner, approver, source, source_hash, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    policy_id,
                    version,
                    domain,
                    clean_status,
                    _canonical(clean_rules),
                    _canonical(clean_controls),
                    _canonical(clean_scope),
                    authority,
                    priority,
                    start,
                    end,
                    str(owner or ""),
                    str(approver or ""),
                    str(source or ""),
                    digest,
                    created_at,
                ),
            )
        return self.get_version(policy_id, version)

    def _row(self, row: sqlite3.Row | None) -> PolicyVersion | None:
        if row is None:
            return None
        return PolicyVersion(
            policy_id=str(row["policy_id"]),
            version=int(row["version"]),
            domain=str(row["domain"]),
            status=str(row["status"]),
            rules=tuple(json.loads(row["rules_json"] or "[]")),
            controls=dict(json.loads(row["controls_json"] or "{}")),
            scope=dict(json.loads(row["scope_json"] or "{}")),
            authority=int(row["authority"]),
            priority=int(row["priority"]),
            effective_from=str(row["effective_from"]),
            effective_to=str(row["effective_to"]) if row["effective_to"] else None,
            owner=str(row["owner"] or ""),
            approver=str(row["approver"] or ""),
            source=str(row["source"] or ""),
            source_hash=str(row["source_hash"] or ""),
            created_at=str(row["created_at"]),
        )

    def get_version(self, policy_id: str, version: int) -> PolicyVersion:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (str(policy_id), int(version)),
            ).fetchone()
        value = self._row(row)
        if value is None:
            raise KeyError(f"unknown policy version: {policy_id}@v{version}")
        return value

    def list_versions(self, *, policy_id: str | None = None, domain: str | None = None) -> list[PolicyVersion]:
        policy_filter = str(policy_id) if policy_id else None
        domain_filter = str(domain) if domain else None
        with self._connect() as connection:
            if policy_filter is not None and domain_filter is not None:
                rows = connection.execute(
                    "SELECT * FROM policy_versions WHERE policy_id=? AND domain=? ORDER BY policy_id, version DESC",
                    (policy_filter, domain_filter),
                ).fetchall()
            elif policy_filter is not None:
                rows = connection.execute(
                    "SELECT * FROM policy_versions WHERE policy_id=? ORDER BY policy_id, version DESC",
                    (policy_filter,),
                ).fetchall()
            elif domain_filter is not None:
                rows = connection.execute(
                    "SELECT * FROM policy_versions WHERE domain=? ORDER BY policy_id, version DESC",
                    (domain_filter,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM policy_versions ORDER BY policy_id, version DESC"
                ).fetchall()
        return [value for row in rows if (value := self._row(row)) is not None]

    def publish(
        self,
        policy_id: str,
        version: int,
        *,
        effective_from: str | datetime | None = None,
        approver: str = "",
    ) -> PolicyVersion:
        start = _iso(effective_from, default=_utc_now())
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target_row = connection.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (str(policy_id), int(version)),
            ).fetchone()
            target = self._row(target_row)
            if target is None:
                raise KeyError(f"unknown policy version: {policy_id}@v{version}")
            if target.status not in {"draft", "active"}:
                raise ValueError("only draft/active policy versions can be published")
            latest = connection.execute(
                """
                SELECT * FROM policy_versions
                WHERE policy_id=? AND version<>? AND status='active' AND effective_to IS NULL
                ORDER BY effective_from DESC, version DESC LIMIT 1
                """,
                (str(policy_id), int(version)),
            ).fetchone()
            if latest is not None:
                latest_start = _parse_time(str(latest["effective_from"]))
                if _parse_time(start) < latest_start:
                    raise ValueError("cannot publish a version before the current active version")
                connection.execute(
                    "UPDATE policy_versions SET status='superseded', effective_to=? WHERE policy_id=? AND version=?",
                    (start, str(policy_id), int(latest["version"])),
                )
            connection.execute(
                "UPDATE policy_versions SET status='active', effective_from=?, effective_to=NULL, approver=? WHERE policy_id=? AND version=?",
                (start, str(approver or target.approver), str(policy_id), int(version)),
            )
        return self.get_version(policy_id, version)

    def retire(self, policy_id: str, version: int, *, effective_to: str | datetime | None = None) -> PolicyVersion:
        end = _iso(effective_to, default=_utc_now())
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target_row = connection.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (str(policy_id), int(version)),
            ).fetchone()
            target = self._row(target_row)
            if target is None:
                raise KeyError(f"unknown policy version: {policy_id}@v{version}")
            if target.status != "active":
                raise ValueError("only an active policy version can be retired")
            if _parse_time(end) <= _parse_time(target.effective_from):
                raise ValueError("retirement must be after policy effective_from")
            connection.execute(
                "UPDATE policy_versions SET status='retired', effective_to=? WHERE policy_id=? AND version=?",
                (end, str(policy_id), int(version)),
            )
        return self.get_version(policy_id, version)

    @staticmethod
    def _scope_match(policy_scope: dict[str, str], request_scope: dict[str, str]) -> tuple[bool, int]:
        specificity = 0
        for key, expected in policy_scope.items():
            if expected in {"", "*"}:
                continue
            if str(request_scope.get(key, "")) != expected:
                return False, 0
            specificity += 1
        return True, specificity

    @staticmethod
    def _precedence(policy: PolicyVersion, specificity: int) -> tuple[int, int, int]:
        return policy.authority, specificity, policy.priority

    def resolve(
        self,
        domain: str,
        *,
        scope: dict[str, Any] | None = None,
        as_of: str | datetime | None = None,
    ) -> dict[str, Any]:
        moment = _parse_time(as_of, default=_utc_now())
        request_scope = self._validate_scope(scope)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM policy_versions WHERE domain=? AND status IN ('active','superseded','retired')",
                (str(domain),),
            ).fetchall()

        applicable: list[tuple[PolicyVersion, int]] = []
        for row in rows:
            policy = self._row(row)
            if policy is None or policy.status not in _RUNTIME_STATUSES:
                continue
            if moment < _parse_time(policy.effective_from):
                continue
            if policy.effective_to and moment >= _parse_time(policy.effective_to):
                continue
            matches, specificity = self._scope_match(policy.scope, request_scope)
            if matches:
                applicable.append((policy, specificity))

        applicable.sort(
            key=lambda item: (
                -item[0].authority,
                -item[1],
                -item[0].priority,
                item[0].policy_id,
                -item[0].version,
            )
        )
        if not applicable:
            result = {
                "domain": str(domain),
                "as_of": _iso(moment),
                "scope": request_scope,
                "status": "missing",
                "rules": [],
                "controls": {},
                "policies": [],
                "conflicts": [],
                "overridden": [],
            }
            result["resolution_hash"] = hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest()
            return result

        control_candidates: dict[str, list[tuple[PolicyVersion, int, Any]]] = {}
        for policy, specificity in applicable:
            for key, value in policy.controls.items():
                control_candidates.setdefault(str(key), []).append((policy, specificity, value))

        resolved_controls: dict[str, Any] = {}
        conflicts: list[dict[str, Any]] = []
        overridden: list[dict[str, Any]] = []
        for key, candidates in sorted(control_candidates.items()):
            top_precedence = max(self._precedence(policy, specificity) for policy, specificity, _ in candidates)
            top = [row for row in candidates if self._precedence(row[0], row[1]) == top_precedence]
            distinct = {_canonical(value) for _policy, _specificity, value in top}
            if len(distinct) > 1:
                conflicts.append(
                    {
                        "control": key,
                        "precedence": {
                            "authority": top_precedence[0],
                            "specificity": top_precedence[1],
                            "priority": top_precedence[2],
                        },
                        "candidates": [
                            {"policy_id": policy.policy_id, "version": policy.version, "version_id": policy.version_id, "value": value}
                            for policy, _specificity, value in top
                        ],
                    }
                )
                continue
            winning_value = top[0][2]
            winning_canonical = _canonical(winning_value)
            resolved_controls[key] = winning_value
            for policy, specificity, value in candidates:
                if self._precedence(policy, specificity) == top_precedence and _canonical(value) == winning_canonical:
                    continue
                if _canonical(value) != winning_canonical:
                    overridden.append(
                        {
                            "control": key,
                            "policy_id": policy.policy_id,
                            "version": policy.version,
                            "version_id": policy.version_id,
                            "value": value,
                            "precedence": {
                                "authority": policy.authority,
                                "specificity": specificity,
                                "priority": policy.priority,
                            },
                        }
                    )

        policies: list[dict[str, Any]] = []
        rules: list[str] = []
        seen_rules: set[str] = set()
        for policy, specificity in applicable:
            row = policy.as_dict()
            row["specificity"] = specificity
            row["precedence"] = {
                "authority": policy.authority,
                "specificity": specificity,
                "priority": policy.priority,
            }
            policies.append(row)
            for rule in policy.rules:
                if rule not in seen_rules:
                    seen_rules.add(rule)
                    rules.append(rule)

        status = "conflicted" if conflicts else "resolved"
        result = {
            "domain": str(domain),
            "as_of": _iso(moment),
            "scope": request_scope,
            "status": status,
            "rules": rules,
            "controls": resolved_controls,
            "policies": policies,
            "conflicts": conflicts,
            "overridden": overridden,
        }
        digest_payload = {
            "domain": result["domain"],
            "as_of": result["as_of"],
            "scope": request_scope,
            "status": status,
            "controls": resolved_controls,
            "conflicts": conflicts,
            "versions": [row["version_id"] for row in policies],
        }
        result["resolution_hash"] = hashlib.sha256(_canonical(digest_payload).encode("utf-8")).hexdigest()
        return result
