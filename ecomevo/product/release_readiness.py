from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from ecomevo.product.connection_catalog import MCPConnectionCatalog
from ecomevo.product.observability import QualityObservability


READINESS_WINDOWS = {"24h", "7d", "30d"}


def _ratio_text(value: Any) -> str:
    if value is None:
        return "unavailable"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "unavailable"


class ReleaseReadinessCenter:
    """Read-only release evidence aggregator with immutable tenant-scoped snapshots.

    This center can tell an admin whether deterministic pre-release evidence is
    currently blocked or ready for human review. It cannot publish policy, promote
    skills, approve BusinessActions, execute tools, or merge/deploy code.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        store: Any,
        evaluation_center: Any,
        mcp_registry: Any,
        policy_store: Any,
    ):
        self.db_path = str(db_path)
        self.store = store
        self.evaluation_center = evaluation_center
        self.connections = MCPConnectionCatalog(mcp_registry)
        self.policy_store = policy_store
        self.observability = QualityObservability(store)
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _init(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS release_readiness_snapshots(
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    window_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    blocker_count INTEGER NOT NULL,
                    warning_count INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_release_readiness_tenant_created
                    ON release_readiness_snapshots(tenant_id,created_at DESC,id DESC);
                """
            )

    @staticmethod
    def authority() -> dict[str, bool]:
        return {
            "approved_for_release": False,
            "changes_production_authority": False,
            "changes_policy": False,
            "changes_routing": False,
            "promotes_runtime_skills": False,
            "approves_business_actions": False,
            "executes_tools": False,
            "merges_or_deploys_code": False,
        }

    @staticmethod
    def _visible_policy(row: Any, tenant_id: str) -> bool:
        scope = getattr(row, "scope", {}) or {}
        policy_tenant = str(scope.get("tenant") or "").strip()
        return not policy_tenant or policy_tenant == "*" or policy_tenant == tenant_id

    def _policy_inventory(self, tenant_id: str) -> dict[str, Any]:
        rows = [
            row
            for row in self.policy_store.list_versions()
            if self._visible_policy(row, tenant_id)
        ]
        statuses = Counter(str(getattr(row, "status", "unknown") or "unknown") for row in rows)
        domains = Counter(str(getattr(row, "domain", "general") or "general") for row in rows)
        return {
            "visible_versions": len(rows),
            "statuses": dict(sorted(statuses.items())),
            "domains": dict(sorted(domains.items())),
            "resolution_evaluated": False,
            "reason": "policy resolution is request-scope dependent; inventory is shown without inventing a universal conflict verdict",
        }

    def _feedback_snapshot(self, tenant_id: str) -> dict[str, Any]:
        # Readiness cannot reuse the admin list helper because that helper deliberately
        # caps its scan for UI responsiveness. A capped scan could miss an old open
        # action-blocking dispute and create a false green. These fixed statements count
        # the entire tenant-scoped open set and only cap the non-authoritative examples.
        with self.store._conn() as db:
            count_rows = db.execute(
                """
                SELECT d.impact,COUNT(*) AS count
                FROM evidence_disputes d
                JOIN conversations c ON c.id=d.conversation_id
                WHERE c.tenant_id=?
                  AND (SELECT e.event_type
                       FROM evidence_dispute_events e
                       WHERE e.dispute_id=d.id AND e.event_type!='submitted'
                       ORDER BY e.id DESC LIMIT 1) IS NULL
                GROUP BY d.impact
                """,
                (tenant_id,),
            ).fetchall()
            examples = db.execute(
                """
                SELECT d.id,d.category,d.impact
                FROM evidence_disputes d
                JOIN conversations c ON c.id=d.conversation_id
                WHERE c.tenant_id=?
                  AND (SELECT e.event_type
                       FROM evidence_dispute_events e
                       WHERE e.dispute_id=d.id AND e.event_type!='submitted'
                       ORDER BY e.id DESC LIMIT 1) IS NULL
                ORDER BY d.created_at DESC,d.id DESC
                LIMIT 5
                """,
                (tenant_id,),
            ).fetchall()
        impacts = Counter()
        for row in count_rows:
            impacts[str(row["impact"] or "unknown")] = int(row["count"] or 0)
        open_count = sum(impacts.values())
        return {
            "open_count": open_count,
            "exact_count": True,
            "action_blocking": int(impacts.get("action_blocking", 0)),
            "decision_relevant": int(impacts.get("decision_relevant", 0)),
            "answer_only": int(impacts.get("answer_only", 0)),
            "examples": [
                {
                    "id": str(row["id"] or ""),
                    "category": str(row["category"] or ""),
                    "impact": str(row["impact"] or ""),
                }
                for row in examples
            ],
        }

    def _evaluation_snapshot(self) -> dict[str, Any]:
        rows = self.evaluation_center.store.list_runs(1)
        if not rows:
            return {
                "available": False,
                "latest": None,
                "reason": "no Gold Set evaluation snapshot exists",
            }
        latest = rows[0]
        return {
            "available": True,
            "latest": {
                "id": str(latest.get("id") or ""),
                "ok": bool(latest.get("ok")),
                "case_count": int(latest.get("case_count") or 0),
                "failed_case_count": int(latest.get("failed_case_count") or 0),
                "drift_case_count": int(latest.get("drift_case_count") or 0),
                "source_hash": str(latest.get("source_hash") or ""),
                "created_at": float(latest.get("created_at") or 0.0),
            },
            "reason": None,
        }

    @staticmethod
    def _check(
        check_id: str,
        status: str,
        title: str,
        detail: str,
        *,
        source: str,
    ) -> dict[str, str]:
        return {
            "id": check_id,
            "status": status,
            "title": title,
            "detail": detail,
            "source": source,
        }

    def preview(
        self,
        *,
        tenant_id: str,
        window: str = "7d",
        now: float | None = None,
    ) -> dict[str, Any]:
        if window not in READINESS_WINDOWS:
            raise ValueError("invalid readiness window")
        generated_at = float(now if now is not None else time.time())
        observability = self.observability.snapshot(
            tenant_id=tenant_id,
            window=window,
            now=generated_at,
        )
        feedback = self._feedback_snapshot(tenant_id)
        evaluation = self._evaluation_snapshot()
        connections = self.connections.list()
        policy = self._policy_inventory(tenant_id)

        checks: list[dict[str, str]] = []

        latest = evaluation.get("latest")
        if not evaluation["available"]:
            checks.append(self._check(
                "gold_set_latest",
                "blocker",
                "Gold Set snapshot missing",
                "Run the deterministic Test Center before human release review.",
                source="evaluation",
            ))
        elif not bool((latest or {}).get("ok")):
            checks.append(self._check(
                "gold_set_latest",
                "blocker",
                "Latest Gold Set failed",
                f"{int((latest or {}).get('failed_case_count') or 0)} failed cases; replay drift={int((latest or {}).get('drift_case_count') or 0)}.",
                source="evaluation",
            ))
        else:
            checks.append(self._check(
                "gold_set_latest",
                "pass",
                "Latest Gold Set passed",
                f"{int((latest or {}).get('case_count') or 0)} cases; replay drift={int((latest or {}).get('drift_case_count') or 0)}.",
                source="evaluation",
            ))

        uncertain = int(observability["authority_workload"]["current_uncertain_actions"])
        checks.append(self._check(
            "uncertain_side_effects",
            "pass" if uncertain == 0 else "blocker",
            "No unresolved uncertain side effects" if uncertain == 0 else "Uncertain side effects require reconciliation",
            f"current_uncertain_actions={uncertain}",
            source="observability",
        ))

        action_blocking = int(feedback["action_blocking"])
        checks.append(self._check(
            "action_blocking_feedback",
            "pass" if action_blocking == 0 else "blocker",
            "No open action-blocking feedback" if action_blocking == 0 else "Open action-blocking feedback remains",
            f"action_blocking={action_blocking}",
            source="feedback",
        ))

        decision_relevant = int(feedback["decision_relevant"])
        if decision_relevant:
            checks.append(self._check(
                "decision_relevant_feedback",
                "warning",
                "Decision-relevant feedback remains open",
                f"decision_relevant={decision_relevant}; review before release.",
                source="feedback",
            ))
        else:
            checks.append(self._check(
                "decision_relevant_feedback",
                "pass",
                "No open decision-relevant feedback",
                "decision_relevant=0",
                source="feedback",
            ))

        safety = connections.get("safety") if isinstance(connections.get("safety"), dict) else {}
        safety_ok = (
            safety.get("business_tool_execution") is False
            and safety.get("secrets_exposed") is False
            and safety.get("authority_override") is False
        )
        checks.append(self._check(
            "connection_control_plane_safety",
            "pass" if safety_ok else "blocker",
            "Connection catalog remains observational" if safety_ok else "Connection control-plane safety invariant failed",
            "tools/list only; no business execution, secret exposure, or authority override" if safety_ok else "One or more connection safety invariants are not false.",
            source="connections",
        ))

        quality = observability["quality"]
        assistant_results = int(quality.get("assistant_results") or 0)
        evidence_gaps = int(quality.get("evidence_gap_results") or 0)
        if assistant_results == 0:
            checks.append(self._check(
                "quality_sample",
                "warning",
                "No assistant quality sample in selected window",
                f"window={window}; no runtime quality observations are available.",
                source="observability",
            ))
        elif evidence_gaps:
            checks.append(self._check(
                "evidence_gap_observation",
                "warning",
                "Evidence gaps observed in selected window",
                f"{evidence_gaps}/{assistant_results} assistant results; rate={_ratio_text(quality.get('evidence_gap_rate'))}.",
                source="observability",
            ))
        else:
            checks.append(self._check(
                "evidence_gap_observation",
                "pass",
                "No evidence gaps observed in selected window",
                f"{assistant_results} assistant results inspected.",
                source="observability",
            ))

        reliability = observability["reliability"]
        failed_jobs = int(reliability.get("failed") or 0)
        retry_jobs = int(reliability.get("retry_jobs") or 0)
        if failed_jobs or retry_jobs:
            checks.append(self._check(
                "runtime_reliability_observation",
                "warning",
                "Runtime failures or retries observed",
                f"failed_jobs={failed_jobs}; retry_jobs={retry_jobs}.",
                source="observability",
            ))
        else:
            checks.append(self._check(
                "runtime_reliability_observation",
                "pass",
                "No failed or retried jobs observed",
                f"terminal_jobs={int(reliability.get('terminal_jobs') or 0)}.",
                source="observability",
            ))

        checks.append(self._check(
            "policy_runtime_truth",
            "info",
            "Policy inventory captured without universal resolution claim",
            f"visible_versions={policy['visible_versions']}; request scope is required for deterministic resolution.",
            source="policy",
        ))

        blockers = [row for row in checks if row["status"] == "blocker"]
        warnings = [row for row in checks if row["status"] == "warning"]
        status = "ready_for_human_release_review" if not blockers else "blocked"

        return {
            "schema_version": 1,
            "generated_at": generated_at,
            "tenant_scope": tenant_id,
            "window": window,
            "status": status,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "checks": checks,
            "sources": {
                "evaluation": evaluation,
                "feedback": feedback,
                "observability": {
                    "window": observability["window"],
                    "quality": observability["quality"],
                    "reliability": observability["reliability"],
                    "authority_workload": observability["authority_workload"],
                },
                "connections": {
                    "scope": connections.get("scope"),
                    "count": connections.get("count"),
                    "safety": safety,
                },
                "policy": policy,
            },
            "authority": self.authority(),
            "methodology": {
                "hard_blockers": [
                    "latest Gold Set missing or failed",
                    "current uncertain side-effect action exists",
                    "open action-blocking feedback exists",
                    "connection control-plane safety invariant fails",
                ],
                "warnings_are_not_blockers": True,
                "success_rate_threshold": None,
                "evidence_gap_threshold": None,
                "readiness_means": "ready for human release review; not approved, published, promoted, merged, or deployed",
            },
        }

    def create_snapshot(
        self,
        *,
        tenant_id: str,
        window: str = "7d",
        now: float | None = None,
    ) -> dict[str, Any]:
        payload = self.preview(tenant_id=tenant_id, window=window, now=now)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        row_id = f"ready-{uuid.uuid4().hex[:16]}"
        created_at = float(payload["generated_at"])
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO release_readiness_snapshots(
                    id,tenant_id,window_key,content_hash,status,blocker_count,warning_count,snapshot_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    row_id,
                    tenant_id,
                    window,
                    digest,
                    payload["status"],
                    int(payload["blocker_count"]),
                    int(payload["warning_count"]),
                    encoded,
                    created_at,
                ),
            )
        return {
            "id": row_id,
            "content_hash": digest,
            "created_at": created_at,
            **payload,
        }

    def list_snapshots(self, *, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id,window_key,content_hash,status,blocker_count,warning_count,created_at
                FROM release_readiness_snapshots
                WHERE tenant_id=?
                ORDER BY created_at DESC,id DESC
                LIMIT ?
                """,
                (tenant_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_snapshot(self, snapshot_id: str, *, tenant_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT id,content_hash,snapshot_json,created_at
                FROM release_readiness_snapshots
                WHERE id=? AND tenant_id=?
                """,
                (snapshot_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        payload = json.loads(str(row["snapshot_json"]))
        return {
            "id": str(row["id"]),
            "content_hash": str(row["content_hash"]),
            "created_at": float(row["created_at"]),
            **payload,
        }
