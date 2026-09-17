from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from typing import Any


WINDOW_SECONDS = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


class QualityObservability:
    """Read-only tenant-scoped quality and runtime observability.

    Metrics are derived from durable SQLite facts only. Missing telemetry such as
    token cost or operator active hours is reported as unavailable instead of being
    estimated. This service never mutates routing, authority, policy, or task state.
    """

    MAX_ROWS = 5000

    def __init__(self, store):
        self.store = store

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
    def _ratio(numerator: int, denominator: int) -> float | None:
        if denominator <= 0:
            return None
        return round(numerator / denominator, 4)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(max(0.0, float(value)) for value in values)
        index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
        return round(ordered[index], 3)

    @staticmethod
    def _missing_evidence(runtime: dict[str, Any]) -> list[Any]:
        belief = runtime.get("belief") if isinstance(runtime.get("belief"), dict) else {}
        missing = runtime.get("missing_evidence")
        if not isinstance(missing, list):
            missing = belief.get("missing_evidence")
        return missing if isinstance(missing, list) else []

    @classmethod
    def _quality_flags(cls, payload: dict[str, Any]) -> dict[str, bool]:
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else None
        runtime_completed = str(runtime.get("status") or "") == "completed"
        missing = bool(cls._missing_evidence(runtime))
        grounding_sufficiency = str((grounding or {}).get("evidence_sufficiency") or "")
        grounding_blocks = grounding is not None and grounding_sufficiency not in {"", "sufficient"}
        evidence_gap = (
            str(runtime.get("status") or "") == "needs_evidence"
            or missing
            or grounding_sufficiency in {"insufficient", "conflicted"}
        )
        verified = runtime_completed and not missing and not grounding_blocks
        return {
            "verified": verified,
            "evidence_gap": evidence_gap,
            "has_grounding": grounding is not None,
            "grounding_sufficient": grounding_sufficiency == "sufficient",
        }

    def _rows(self, tenant_id: str, since: float) -> dict[str, list[dict[str, Any]]]:
        with self.store._conn() as db:
            jobs = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT j.id,j.conversation_id,j.status,j.attempts,j.created_at,j.updated_at,c.scene
                    FROM conversation_jobs j
                    JOIN conversations c ON c.id=j.conversation_id
                    WHERE c.tenant_id=? AND j.created_at>=?
                    ORDER BY j.created_at DESC,j.id DESC
                    LIMIT ?
                    """,
                    (tenant_id, since, self.MAX_ROWS),
                ).fetchall()
            ]
            assistants = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT m.id,m.conversation_id,m.payload,m.created_at,c.scene
                    FROM messages m
                    JOIN conversations c ON c.id=m.conversation_id
                    WHERE c.tenant_id=? AND m.role='assistant' AND m.created_at>=?
                    ORDER BY m.created_at DESC,m.id DESC
                    LIMIT ?
                    """,
                    (tenant_id, since, self.MAX_ROWS),
                ).fetchall()
            ]
            actions = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT a.id,a.status,a.requires_confirmation,a.side_effect,a.risk_level,
                           a.created_at,a.updated_at,c.scene
                    FROM actions a
                    JOIN conversations c ON c.id=a.conversation_id
                    WHERE c.tenant_id=? AND a.created_at>=?
                    ORDER BY a.created_at DESC,a.id DESC
                    LIMIT ?
                    """,
                    (tenant_id, since, self.MAX_ROWS),
                ).fetchall()
            ]
            conversations = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT id,scene,created_at,updated_at
                    FROM conversations
                    WHERE tenant_id=? AND created_at>=?
                    ORDER BY created_at DESC,id DESC
                    LIMIT ?
                    """,
                    (tenant_id, since, self.MAX_ROWS),
                ).fetchall()
            ]
            current = db.execute(
                """
                SELECT
                  SUM(CASE WHEN a.status='proposed' AND a.requires_confirmation=1 THEN 1 ELSE 0 END) AS proposed,
                  SUM(CASE WHEN a.status='uncertain' THEN 1 ELSE 0 END) AS uncertain
                FROM actions a
                JOIN conversations c ON c.id=a.conversation_id
                WHERE c.tenant_id=?
                """,
                (tenant_id,),
            ).fetchone()
        for row in assistants:
            row["payload"] = self._json(row.get("payload"))
        return {
            "jobs": jobs,
            "assistants": assistants,
            "actions": actions,
            "conversations": conversations,
            "current": [dict(current)] if current else [{"proposed": 0, "uncertain": 0}],
        }

    @staticmethod
    def _series_bucket(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(float(ts)))

    def snapshot(
        self,
        *,
        tenant_id: str,
        window: str = "7d",
        now: float | None = None,
    ) -> dict[str, Any]:
        if window not in WINDOW_SECONDS:
            raise ValueError("invalid observability window")
        now_ts = float(now if now is not None else time.time())
        since = now_ts - WINDOW_SECONDS[window]
        rows = self._rows(tenant_id, since)
        jobs = rows["jobs"]
        assistants = rows["assistants"]
        actions = rows["actions"]
        conversations = rows["conversations"]
        current = rows["current"][0]

        job_status = Counter(str(row.get("status") or "unknown") for row in jobs)
        terminal_jobs = [row for row in jobs if row.get("status") in {"succeeded", "failed"}]
        latencies = [
            max(0.0, float(row.get("updated_at") or 0) - float(row.get("created_at") or 0))
            for row in terminal_jobs
            if row.get("updated_at") is not None and row.get("created_at") is not None
        ]
        retried = sum(int(row.get("attempts") or 0) > 1 for row in jobs)

        quality_rows = []
        grounding_rows = 0
        verified = 0
        evidence_gap = 0
        for row in assistants:
            flags = self._quality_flags(row["payload"])
            quality_rows.append((row, flags))
            verified += int(flags["verified"])
            evidence_gap += int(flags["evidence_gap"])
            grounding_rows += int(flags["has_grounding"])

        action_status = Counter(str(row.get("status") or "unknown") for row in actions)
        confirmation_required = sum(bool(row.get("requires_confirmation")) for row in actions)
        uncertain_incidents = sum(str(row.get("status") or "") == "uncertain" for row in actions)
        side_effect_actions = sum(bool(row.get("side_effect")) for row in actions)
        scene_counts = Counter(str(row.get("scene") or "general") for row in jobs)
        conversation_scenes = Counter(str(row.get("scene") or "general") for row in conversations)

        series: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"jobs_terminal": 0, "jobs_succeeded": 0, "verified_decisions": 0, "evidence_gaps": 0}
        )
        for row in terminal_jobs:
            bucket = self._series_bucket(float(row.get("updated_at") or row.get("created_at") or now_ts))
            series[bucket]["jobs_terminal"] += 1
            if row.get("status") == "succeeded":
                series[bucket]["jobs_succeeded"] += 1
        for row, flags in quality_rows:
            bucket = self._series_bucket(float(row.get("created_at") or now_ts))
            series[bucket]["verified_decisions"] += int(flags["verified"])
            series[bucket]["evidence_gaps"] += int(flags["evidence_gap"])

        return {
            "generated_at": now_ts,
            "tenant_scope": tenant_id,
            "window": {
                "key": window,
                "since": since,
                "until": now_ts,
                "seconds": WINDOW_SECONDS[window],
            },
            "north_star": {
                "verified_decisions": verified,
                "operator_hours": {
                    "available": False,
                    "reason": "operator active-hours telemetry is not instrumented",
                },
                "verified_decisions_per_operator_hour": {
                    "available": False,
                    "reason": "requires operator active-hours telemetry",
                },
            },
            "quality": {
                "assistant_results": len(assistants),
                "verified_decisions": verified,
                "verified_rate": self._ratio(verified, len(assistants)),
                "evidence_gap_results": evidence_gap,
                "evidence_gap_rate": self._ratio(evidence_gap, len(assistants)),
                "grounding_instrumented_results": grounding_rows,
                "grounding_coverage_rate": self._ratio(grounding_rows, len(assistants)),
            },
            "reliability": {
                "jobs": len(jobs),
                "terminal_jobs": len(terminal_jobs),
                "succeeded": int(job_status.get("succeeded", 0)),
                "failed": int(job_status.get("failed", 0)),
                "queued": int(job_status.get("queued", 0)),
                "running": int(job_status.get("running", 0)),
                "success_rate": self._ratio(int(job_status.get("succeeded", 0)), len(terminal_jobs)),
                "retry_jobs": retried,
                "retry_rate": self._ratio(retried, len(jobs)),
                "end_to_end_latency_seconds": {
                    "definition": "conversation_jobs.created_at to terminal updated_at; includes queue time",
                    "samples": len(latencies),
                    "p50": self._percentile(latencies, 0.50),
                    "p95": self._percentile(latencies, 0.95),
                },
            },
            "authority_workload": {
                "actions_in_window": len(actions),
                "confirmation_required": confirmation_required,
                "confirmation_required_rate": self._ratio(confirmation_required, len(actions)),
                "side_effect_actions": side_effect_actions,
                "uncertain_incidents": uncertain_incidents,
                "current_waiting_approval": int(current.get("proposed") or 0),
                "current_uncertain_actions": int(current.get("uncertain") or 0),
            },
            "distribution": {
                "job_scenes": dict(sorted(scene_counts.items())),
                "new_conversation_scenes": dict(sorted(conversation_scenes.items())),
                "job_statuses": dict(sorted(job_status.items())),
                "action_statuses": dict(sorted(action_status.items())),
            },
            "throughput": {
                "new_conversations": len(conversations),
                "completed_runs": len(terminal_jobs),
                "successful_runs": int(job_status.get("succeeded", 0)),
            },
            "series": [
                {"date": date, **metrics}
                for date, metrics in sorted(series.items())
            ],
            "telemetry_availability": {
                "token_usage": {
                    "available": False,
                    "reason": "provider token usage is not durably recorded",
                },
                "provider_cost": {
                    "available": False,
                    "reason": "provider billing/cost ledger is not instrumented",
                },
                "operator_active_hours": {
                    "available": False,
                    "reason": "operator active-hours telemetry is not instrumented",
                },
            },
            "methodology": {
                "verified_decision": "runtime status completed with no missing evidence; when claim grounding exists, evidence_sufficiency must also be sufficient",
                "evidence_gap": "runtime needs_evidence/missing evidence or grounding insufficient/conflicted",
                "read_only": True,
                "changes_authority": False,
            },
        }
