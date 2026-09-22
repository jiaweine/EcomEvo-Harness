from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from typing import Any


WINDOW_SECONDS = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


class RoutingOffPolicyReadiness:
    """Tenant-scoped readiness audit for future routing off-policy evaluation.

    The current production router is deterministic UCB. This service therefore
    never fabricates a behavior propensity and never labels descriptive replay
    as a counterfactual value estimate. It only reports what the durable logs can
    support today and which statistical prerequisites remain missing.
    """

    MAX_ROWS = 10000

    def __init__(self, store) -> None:
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
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _positive_int(cls, value: Any) -> int | None:
        number = cls._number(value)
        if number is None or number <= 0 or abs(number - round(number)) > 1e-9:
            return None
        return int(round(number))

    @classmethod
    def _nonnegative_int(cls, value: Any) -> int | None:
        number = cls._number(value)
        if number is None or number < 0 or abs(number - round(number)) > 1e-9:
            return None
        return int(round(number))

    @staticmethod
    def _ratio(numerator: int | float, denominator: int | float) -> float | None:
        if denominator <= 0:
            return None
        return round(float(numerator) / float(denominator), 4)

    @classmethod
    def _stats(cls, values: list[float]) -> dict[str, Any]:
        clean = sorted(value for value in values if math.isfinite(value))
        if not clean:
            return {
                "samples": 0,
                "avg": None,
                "p50": None,
                "p95": None,
                "min": None,
                "max": None,
            }

        def percentile(q: float) -> float:
            index = max(0, min(len(clean) - 1, math.ceil(q * len(clean)) - 1))
            return clean[index]

        return {
            "samples": len(clean),
            "avg": round(sum(clean) / len(clean), 4),
            "p50": round(percentile(0.50), 4),
            "p95": round(percentile(0.95), 4),
            "min": round(clean[0], 4),
            "max": round(clean[-1], 4),
        }

    @classmethod
    def _feature_complete(cls, row: dict[str, Any]) -> bool:
        vector = row.get("feature_vector")
        if not isinstance(vector, list) or not vector:
            return False
        if any(cls._number(value) is None for value in vector):
            return False
        return (
            cls._number(row.get("advantage")) is not None
            and cls._number(row.get("cost")) is not None
        )

    def _rows(self, tenant_id: str, since: float) -> tuple[list[dict[str, Any]], bool]:
        with self.store._conn() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT e.id,e.conversation_id,e.type,e.payload,e.created_at,c.scene
                    FROM task_events e
                    JOIN conversations c ON c.id=e.conversation_id
                    WHERE c.tenant_id=? AND e.created_at>=?
                      AND e.type IN ('autonomy.decided','routing.policy.updated')
                    ORDER BY e.id ASC
                    LIMIT ?
                    """,
                    (tenant_id, since, self.MAX_ROWS + 1),
                ).fetchall()
            ]
        truncated = len(rows) > self.MAX_ROWS
        rows = rows[: self.MAX_ROWS]
        for row in rows:
            row["payload"] = self._json(row.get("payload"))
        return rows, truncated

    def snapshot(
        self,
        *,
        tenant_id: str,
        window: str = "7d",
        now: float | None = None,
    ) -> dict[str, Any]:
        if window not in WINDOW_SECONDS:
            raise ValueError("invalid routing off-policy window")

        now_ts = float(now if now is not None else time.time())
        since = now_ts - WINDOW_SECONDS[window]
        rows, truncated = self._rows(tenant_id, since)

        pending: dict[tuple[str, int], deque[dict[str, Any]]] = defaultdict(deque)
        decision_rounds = 0
        candidate_rows = 0
        selected_rows = 0
        full_feature_rounds = 0
        linked_updates = 0
        reward_linked_rounds = 0
        unpairable_decision_rounds = 0
        unpairable_update_events = 0
        explicit_propensity_rows = 0
        observed_round_credits: list[float] = []

        for event in rows:
            payload = event["payload"]
            event_type = str(event.get("type") or "")
            conversation_id = str(event.get("conversation_id") or "")
            step = self._nonnegative_int(payload.get("step"))

            if event_type == "autonomy.decided":
                decision_rounds += 1
                trace = payload.get("evogain")
                trace = trace if isinstance(trace, list) else []
                candidates = [item for item in trace if isinstance(item, dict)]
                candidate_rows += len(candidates)
                selected = [item for item in candidates if bool(item.get("selected"))]
                selected_rows += len(selected)
                feature_complete = bool(candidates) and all(
                    self._feature_complete(item) for item in candidates
                )
                if feature_complete:
                    full_feature_rounds += 1

                for item in selected:
                    propensity = self._number(item.get("behavior_propensity"))
                    if propensity is not None and 0.0 < propensity <= 1.0:
                        explicit_propensity_rows += 1

                if step is None:
                    unpairable_decision_rounds += 1
                    continue
                key = (conversation_id, step)
                pending[key].append(
                    {
                        "event_id": int(event.get("id") or 0),
                        "feature_complete": feature_complete,
                        "selected_count": len(selected),
                    }
                )
                continue

            if event_type != "routing.policy.updated":
                continue
            if step is None:
                unpairable_update_events += 1
                continue

            key = (conversation_id, step)
            queue = pending.get(key)
            if not queue:
                continue
            queue.popleft()
            linked_updates += 1
            credit = self._number(payload.get("mean_credit"))
            updated_calls = self._positive_int(payload.get("updated_calls"))
            if credit is not None and updated_calls is not None:
                reward_linked_rounds += 1
                observed_round_credits.append(credit)

        unmatched_decisions = (
            sum(len(queue) for queue in pending.values())
            + unpairable_decision_rounds
        )
        propensity_coverage = self._ratio(explicit_propensity_rows, selected_rows)
        feature_coverage = self._ratio(full_feature_rounds, decision_rounds)
        reward_coverage = self._ratio(reward_linked_rounds, decision_rounds)

        deterministic_behavior = {
            "family": "deterministic_ucb",
            "randomized_action_assignment": False,
            "logged_behavior_propensity": explicit_propensity_rows > 0,
            "selected_rows": selected_rows,
            "explicit_propensity_rows": explicit_propensity_rows,
            "propensity_coverage": propensity_coverage,
            "positivity_for_alternative_actions": False,
        }

        exact_behavior_replay = (
            decision_rounds > 0
            and not truncated
            and reward_linked_rounds == decision_rounds
            and unmatched_decisions == 0
        )
        replay_status = (
            "descriptive_current_behavior_only"
            if observed_round_credits
            else "unavailable"
        )
        if exact_behavior_replay:
            replay_reason = (
                "Every decision round in the untruncated window has a valid linked verifier-derived "
                "reward; this reproduces observed current behavior only, not a candidate-policy "
                "counterfactual value."
            )
        elif observed_round_credits:
            replay_reason = (
                "Some verifier-derived mean credit is observable for logged behavior, but reward "
                "linkage is incomplete or the event window is truncated; exact window replay is "
                "therefore unavailable."
            )
        else:
            replay_reason = (
                "No decision round in the selected window has a linked routing.policy.updated reward."
            )

        return {
            "generated_at": now_ts,
            "tenant_scope": tenant_id,
            "window": {
                "key": window,
                "since": since,
                "until": now_ts,
                "seconds": WINDOW_SECONDS[window],
            },
            "coverage": {
                "task_events": len(rows),
                "decision_rounds": decision_rounds,
                "candidate_rows": candidate_rows,
                "selected_rows": selected_rows,
                "full_feature_rounds": full_feature_rounds,
                "full_feature_round_coverage": feature_coverage,
                "reward_linked_rounds": reward_linked_rounds,
                "reward_linkage_coverage": reward_coverage,
                "linked_updates": linked_updates,
                "unmatched_decisions": unmatched_decisions,
                "unpairable_decision_rounds": unpairable_decision_rounds,
                "unpairable_update_events": unpairable_update_events,
                "truncated": truncated,
                "max_rows": self.MAX_ROWS,
            },
            "behavior_policy": deterministic_behavior,
            "current_behavior_replay": {
                "status": replay_status,
                "window_complete": exact_behavior_replay,
                "observed_round_credit": self._stats(observed_round_credits),
                "reason": replay_reason,
            },
            "candidate_counterfactual": {
                "status": "unavailable",
                "ready": False,
                "reason": (
                    "The current deterministic logger does not provide randomized overlap for "
                    "actions the behavior policy did not choose. Full feature traces alone do not "
                    "identify those missing counterfactual rewards."
                ),
            },
            "direct_method": {
                "status": "unavailable",
                "ready": False,
                "reason": (
                    "No separately validated outcome model for candidate routing actions is "
                    "configured; this service will not train an implicit model and present its "
                    "extrapolations as observed value."
                ),
            },
            "doubly_robust": {
                "status": "unavailable",
                "ready": False,
                "reason": (
                    "Doubly-robust estimation requires logged behavior propensity with target-policy "
                    "support plus a validated direct outcome model. The current deterministic UCB "
                    "contract does not satisfy those prerequisites."
                ),
            },
            "readiness": {
                "exact_behavior_replay": exact_behavior_replay,
                "full_feature_trace_available": full_feature_rounds > 0,
                "candidate_counterfactual_value": False,
                "doubly_robust": False,
            },
            "authority": {
                "read_only": True,
                "changes_routing": False,
                "changes_policy": False,
                "changes_runtime_skills": False,
                "approves_business_actions": False,
                "executes_tools": False,
            },
            "methodology": {
                "reward": "routing.policy.updated mean_credit derived from verifier leave-one-out harmonic credit",
                "pairing": "decision and reward events are paired tenant-locally only when step is an explicit non-negative integer, using conversation_id + step in durable event order",
                "feature_coverage": "a round is complete only when every logged candidate has a finite feature_vector, advantage, and cost",
                "propensity": "never inferred from utility, rank, activation, or deterministic UCB scores",
                "counterfactual_claims": "withheld when identification prerequisites are missing",
            },
        }
