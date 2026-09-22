from __future__ import annotations

import json
import sqlite3

from ecomevo.product.routing_off_policy import RoutingOffPolicyReadiness


class _Store:
    def __init__(self, path):
        self.path = path

    def _conn(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db


def _trace(tool: str, *, selected: bool = True, complete: bool = True):
    row = {
        "tool": tool,
        "selected": selected,
        "advantage": 0.25 if selected else -0.1,
        "cost": 0.4,
    }
    if complete:
        row["feature_vector"] = [1.0, 0.4, 0.2]
    return row


def _event(db, conversation_id, event_type, payload, created_at):
    db.execute(
        "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
        (conversation_id, event_type, json.dumps(payload), created_at),
    )


def test_off_policy_readiness_is_tenant_scoped_and_withholds_unidentified_estimates(tmp_path):
    store = _Store(tmp_path / "routing-off-policy.db")
    with store._conn() as db:
        db.executescript(
            """
            CREATE TABLE conversations(
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              scene TEXT NOT NULL
            );
            CREATE TABLE task_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              type TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at REAL NOT NULL
            );
            """
        )
        db.executemany(
            "INSERT INTO conversations(id,tenant_id,scene) VALUES(?,?,?)",
            [
                ("a1", "tenant-a", "aftersales"),
                ("b1", "tenant-b", "aftersales"),
            ],
        )

        _event(
            db,
            "a1",
            "autonomy.decided",
            {
                "step": 0,
                "evogain": [
                    _trace("order.inspect", selected=True),
                    _trace("evidence.search", selected=False),
                ],
            },
            900.0,
        )
        _event(
            db,
            "a1",
            "routing.policy.updated",
            {"step": 0, "updated_calls": 1, "mean_credit": 0.25},
            901.0,
        )
        _event(
            db,
            "a1",
            "autonomy.decided",
            {"step": 1, "evogain": [_trace("shipment.lookup", complete=False)]},
            902.0,
        )
        _event(
            db,
            "a1",
            "routing.policy.updated",
            {"step": 1, "updated_calls": 1, "mean_credit": 0.5},
            903.0,
        )
        _event(
            db,
            "a1",
            "autonomy.decided",
            {"step": 2, "evogain": [_trace("merchant.profile")]},
            904.0,
        )

        _event(
            db,
            "b1",
            "autonomy.decided",
            {"step": 0, "evogain": [_trace("secret.tool")]},
            905.0,
        )
        _event(
            db,
            "b1",
            "routing.policy.updated",
            {"step": 0, "updated_calls": 1, "mean_credit": 9.0},
            906.0,
        )

    snapshot = RoutingOffPolicyReadiness(store).snapshot(
        tenant_id="tenant-a",
        window="24h",
        now=1000.0,
    )

    assert snapshot["tenant_scope"] == "tenant-a"
    assert snapshot["coverage"]["decision_rounds"] == 3
    assert snapshot["coverage"]["candidate_rows"] == 4
    assert snapshot["coverage"]["selected_rows"] == 3
    assert snapshot["coverage"]["full_feature_rounds"] == 2
    assert snapshot["coverage"]["full_feature_round_coverage"] == 0.6667
    assert snapshot["coverage"]["reward_linked_rounds"] == 2
    assert snapshot["coverage"]["reward_linkage_coverage"] == 0.6667
    assert snapshot["coverage"]["linked_updates"] == 2
    assert snapshot["coverage"]["unmatched_decisions"] == 1

    behavior = snapshot["behavior_policy"]
    assert behavior["family"] == "deterministic_ucb"
    assert behavior["randomized_action_assignment"] is False
    assert behavior["logged_behavior_propensity"] is False
    assert behavior["propensity_coverage"] == 0.0
    assert behavior["positivity_for_alternative_actions"] is False

    replay = snapshot["current_behavior_replay"]
    assert replay["status"] == "descriptive_current_behavior_only"
    assert replay["observed_round_credit"]["samples"] == 2
    assert replay["observed_round_credit"]["avg"] == 0.375
    assert replay["observed_round_credit"]["max"] == 0.5

    assert snapshot["candidate_counterfactual"]["status"] == "unavailable"
    assert snapshot["direct_method"]["status"] == "unavailable"
    assert snapshot["doubly_robust"]["status"] == "unavailable"
    assert snapshot["readiness"]["exact_behavior_replay"] is False
    assert snapshot["current_behavior_replay"]["window_complete"] is False
    assert snapshot["readiness"]["doubly_robust"] is False
    assert snapshot["authority"]["read_only"] is True
    assert snapshot["authority"]["changes_routing"] is False
    assert snapshot["authority"]["executes_tools"] is False
    assert "secret.tool" not in json.dumps(snapshot)


def test_off_policy_readiness_handles_empty_history(tmp_path):
    store = _Store(tmp_path / "routing-off-policy-empty.db")
    with store._conn() as db:
        db.executescript(
            """
            CREATE TABLE conversations(
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              scene TEXT NOT NULL
            );
            CREATE TABLE task_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              type TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at REAL NOT NULL
            );
            """
        )

    snapshot = RoutingOffPolicyReadiness(store).snapshot(
        tenant_id="tenant-a",
        window="7d",
        now=1000.0,
    )
    assert snapshot["coverage"]["decision_rounds"] == 0
    assert snapshot["coverage"]["full_feature_round_coverage"] is None
    assert snapshot["behavior_policy"]["propensity_coverage"] is None
    assert snapshot["current_behavior_replay"]["status"] == "unavailable"
    assert snapshot["readiness"]["exact_behavior_replay"] is False


def test_off_policy_readiness_rejects_unknown_window(tmp_path):
    service = RoutingOffPolicyReadiness(_Store(tmp_path / "unused.db"))
    try:
        service.snapshot(tenant_id="tenant-a", window="90d")
    except ValueError as exc:
        assert "invalid routing off-policy window" in str(exc)
    else:
        raise AssertionError("expected invalid window to fail")


def test_off_policy_pairing_rejects_missing_fractional_and_negative_steps(tmp_path):
    store = _Store(tmp_path / "routing-off-policy-invalid-step.db")
    with store._conn() as db:
        db.executescript(
            """
            CREATE TABLE conversations(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,scene TEXT NOT NULL);
            CREATE TABLE task_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,type TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL
            );
            """
        )
        db.execute("INSERT INTO conversations(id,tenant_id,scene) VALUES(?,?,?)", ("a1","tenant-a","aftersales"))
        created_at = 900.0
        for step in (None, 0.5, -1):
            decision = {"evogain": [_trace("order.inspect")]}
            update = {"updated_calls": 1, "mean_credit": 0.25}
            if step is not None:
                decision["step"] = step
                update["step"] = step
            _event(db, "a1", "autonomy.decided", decision, created_at)
            _event(db, "a1", "routing.policy.updated", update, created_at + 0.1)
            created_at += 1.0
        _event(db, "a1", "autonomy.decided", {"step": 2, "evogain": [_trace("evidence.search")]}, created_at)
        _event(db, "a1", "routing.policy.updated", {"step": 2, "updated_calls": 1, "mean_credit": 0.5}, created_at + 0.1)

    snapshot = RoutingOffPolicyReadiness(store).snapshot(tenant_id="tenant-a", window="24h", now=1000.0)
    assert snapshot["coverage"]["decision_rounds"] == 4
    assert snapshot["coverage"]["reward_linked_rounds"] == 1
    assert snapshot["coverage"]["unpairable_decision_rounds"] == 3
    assert snapshot["coverage"]["unpairable_update_events"] == 3
    assert snapshot["coverage"]["unmatched_decisions"] == 3
    assert snapshot["current_behavior_replay"]["observed_round_credit"]["samples"] == 1
    assert snapshot["readiness"]["exact_behavior_replay"] is False


def test_exact_behavior_replay_requires_complete_untruncated_reward_linkage(tmp_path):
    store = _Store(tmp_path / "routing-off-policy-exact.db")
    with store._conn() as db:
        db.executescript(
            """
            CREATE TABLE conversations(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,scene TEXT NOT NULL);
            CREATE TABLE task_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,type TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL
            );
            """
        )
        db.execute("INSERT INTO conversations(id,tenant_id,scene) VALUES(?,?,?)", ("a1","tenant-a","aftersales"))
        _event(db, "a1", "autonomy.decided", {"step": 0, "evogain": [_trace("order.inspect")]}, 900.0)
        _event(db, "a1", "routing.policy.updated", {"step": 0, "updated_calls": 1, "mean_credit": 0.25}, 901.0)

    service = RoutingOffPolicyReadiness(store)
    complete = service.snapshot(tenant_id="tenant-a", window="24h", now=1000.0)
    assert complete["coverage"]["reward_linkage_coverage"] == 1.0
    assert complete["coverage"]["truncated"] is False
    assert complete["readiness"]["exact_behavior_replay"] is True
    assert complete["current_behavior_replay"]["window_complete"] is True

    service.MAX_ROWS = 1
    truncated = service.snapshot(tenant_id="tenant-a", window="24h", now=1000.0)
    assert truncated["coverage"]["truncated"] is True
    assert truncated["readiness"]["exact_behavior_replay"] is False
    assert truncated["current_behavior_replay"]["window_complete"] is False
