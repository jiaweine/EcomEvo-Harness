from __future__ import annotations

from typing import Any

from .observability import QualityObservability
from .operator_activity import OperatorActivityLedger


class OperatorAwareQualityObservability(QualityObservability):
    """Quality read model extended with explicit operator active-time telemetry."""

    def __init__(self, store):
        super().__init__(store)
        # Read-only mode: route installation owns schema creation. Merely opening the
        # observability page must never mutate the product database.
        self.operator_activity = OperatorActivityLedger(store, ensure_schema=False)

    def snapshot(
        self,
        *,
        tenant_id: str,
        window: str = "7d",
        now: float | None = None,
    ) -> dict[str, Any]:
        result = super().snapshot(tenant_id=tenant_id, window=window, now=now)
        window_data = result["window"]
        activity = self.operator_activity.summarize(
            tenant_id=tenant_id,
            since=float(window_data["since"]),
            until=float(window_data["until"]),
        )

        verified = int(result["north_star"]["verified_decisions"])
        active_seconds = int(activity["active_seconds"])
        exact_hours = active_seconds / 3600.0
        display_hours = float(activity["operator_hours"])
        if activity["instrumented"]:
            result["north_star"]["operator_hours"] = {
                "available": True,
                "value": display_hours,
                "active_seconds": active_seconds,
                "active_users": int(activity["active_users"]),
                "bucket_seconds": int(activity["bucket_seconds"]),
                "coverage_complete": bool(activity["window_fully_covered"]),
                "coverage_rate": float(activity["window_coverage_rate"]),
                "coverage_seconds": float(activity["window_coverage_seconds"]),
                "measurement_started_at": float(activity["measurement_started_at"]),
                "definition": activity["definition"],
            }
            if not activity["window_fully_covered"]:
                result["north_star"]["verified_decisions_per_operator_hour"] = {
                    "available": False,
                    "reason": "operator active-time telemetry does not cover the full requested window; a full-window VDPH ratio would mix a complete numerator with a partial denominator",
                    "verified_decisions": verified,
                    "operator_hours": display_hours,
                    "coverage_rate": float(activity["window_coverage_rate"]),
                }
            elif active_seconds > 0:
                result["north_star"]["verified_decisions_per_operator_hour"] = {
                    "available": True,
                    "value": round(verified / exact_hours, 4),
                    "verified_decisions": verified,
                    "operator_hours": display_hours,
                    "definition": "verified decisions divided by server-observed operator active hours in the same window",
                }
            else:
                result["north_star"]["verified_decisions_per_operator_hour"] = {
                    "available": False,
                    "reason": "operator active-time telemetry is installed but no active buckets were observed in this window",
                    "verified_decisions": verified,
                    "operator_hours": 0.0,
                }
            result["telemetry_availability"]["operator_active_hours"] = {
                "available": True,
                "complete": bool(activity["window_fully_covered"]),
                "coverage_rate": float(activity["window_coverage_rate"]),
                "coverage_seconds": float(activity["window_coverage_seconds"]),
                "measurement_started_at": float(activity["measurement_started_at"]),
                "bucket_seconds": int(activity["bucket_seconds"]),
                "client_duration_accepted": False,
                "duplicate_bucket_write_suppressed": bool(
                    activity["duplicate_bucket_write_suppressed"]
                ),
                "retention": dict(activity["retention"]),
                "reason": (
                    None
                    if activity["window_fully_covered"]
                    else "telemetry started after the requested window began; observed hours are shown, but full-window VDPH is withheld"
                ),
                "definition": activity["definition"],
            }
        else:
            result["north_star"]["operator_hours"] = {
                "available": False,
                "reason": "operator active-time telemetry is not installed",
            }
            result["north_star"]["verified_decisions_per_operator_hour"] = {
                "available": False,
                "reason": "requires operator active-time telemetry",
            }
            result["telemetry_availability"]["operator_active_hours"] = {
                "available": False,
                "reason": "operator active-time telemetry is not installed",
            }

        by_date = {row["date"]: dict(row) for row in result.get("series") or []}
        for date, seconds in activity.get("daily_seconds", {}).items():
            by_date.setdefault(
                date,
                {
                    "date": date,
                    "jobs_terminal": 0,
                    "jobs_succeeded": 0,
                    "verified_decisions": 0,
                    "evidence_gaps": 0,
                },
            )
            by_date[date]["operator_active_seconds"] = int(seconds)
        for date, row in by_date.items():
            seconds = int(row.get("operator_active_seconds") or 0)
            day_hours = seconds / 3600.0
            row["operator_hours"] = round(day_hours, 6)
            row["operator_coverage_complete"] = bool(activity["window_fully_covered"])
            row["verified_decisions_per_operator_hour"] = (
                round(int(row.get("verified_decisions") or 0) / day_hours, 4)
                if activity["window_fully_covered"] and day_hours > 0
                else None
            )
        result["series"] = [by_date[date] for date in sorted(by_date)]

        result["operator_activity"] = activity
        result["methodology"]["operator_active_hours"] = activity["definition"]
        result["methodology"]["operator_active_hours_client_duration_accepted"] = False
        result["methodology"]["operator_active_hours_duplicate_bucket_write_suppressed"] = bool(
            activity["duplicate_bucket_write_suppressed"]
        )
        result["methodology"]["operator_active_hours_retention"] = dict(activity["retention"])
        result["methodology"]["operator_active_hours_window_coverage"] = (
            "Verified Decisions per Operator Hour is emitted only when the durable telemetry "
            "measurement start is at or before the requested window start; partial windows may "
            "show observed operator hours but never produce a full-window efficiency ratio"
        )
        result["methodology"]["operator_active_hours_limitation"] = activity["limitation"]
        return result
