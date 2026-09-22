from __future__ import annotations

import json
import sqlite3

from ecomevo.product.routing_quality import RoutingQualityControlTower


class _Store:
    def __init__(self, path):
        self.path = path

    def _conn(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db


def _payload(*, samples, reward, residual, cost, stagnated=False):
    return {
        "runtime": {
            "domain": "aftersales",
            "status": "completed",
            "stagnated": stagnated,
            "tool_cost_used": cost,
            "belief": {
                "facts": {
                    "routing_policy": {
                        "samples": samples,
                        "reward_ewma": reward,
                        "residual_ewma": residual,
                    }
                }
            },
        }
    }


def test_routing_quality_is_tenant_scoped_and_uses_durable_facts(tmp_path):
    store = _Store(tmp_path / "routing-quality.db")
    with store._conn() as db:
        db.executescript(
            """
            CREATE TABLE conversations(
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              scene TEXT NOT NULL
            );
            CREATE TABLE messages(
              id TEXT PRIMARY KEY,
              conversation_id TEXT NOT NULL,
              role TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at REAL NOT NULL
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
                ("a2", "tenant-a", "aftersales"),
                ("b1", "tenant-b", "aftersales"),
            ],
        )
        db.executemany(
            "INSERT INTO messages(id,conversation_id,role,payload,created_at) VALUES(?,?,?,?,?)",
            [
                ("m1", "a1", "assistant", json.dumps(_payload(samples=10, reward=0.6, residual=0.3, cost=2.0)), 900.0),
                ("m2", "a2", "assistant", json.dumps(_payload(samples=20, reward=0.7, residual=0.2, cost=3.0, stagnated=True)), 950.0),
                ("mb", "b1", "assistant", json.dumps(_payload(samples=999, reward=9.0, residual=9.0, cost=99.0)), 960.0),
            ],
        )
        db.execute(
            "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
            (
                "a2",
                "autonomy.decided",
                json.dumps(
                    {
                        "evogain": [
                            {
                                "tool": "order.inspect",
                                "selected": True,
                                "policy_activation": 0.4,
                                "reliability": 0.8,
                                "diversity_overlap": 0.1,
                                "advantage": 0.3,
                            },
                            {
                                "tool": "evidence.search",
                                "selected": False,
                                "reason": "abstain_nonpositive_advantage",
                                "policy_activation": 0.4,
                                "reliability": 0.5,
                                "diversity_overlap": 0.4,
                                "advantage": -0.1,
                            },
                        ]
                    }
                ),
                951.0,
            ),
        )
        db.execute(
            "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
            (
                "a2",
                "tools.completed",
                json.dumps(
                    {
                        "results": [
                            {
                                "tool": "order.inspect",
                                "ok": True,
                                "data": {"_evidence_tags": ["order", "tracking", "tracking"]},
                            },
                            {"tool": "evidence.search", "ok": False, "data": {}},
                        ]
                    }
                ),
                952.0,
            ),
        )
        db.execute(
            "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
            ("a2", "autonomy.stagnated", json.dumps({"step": 2}), 953.0),
        )
        db.execute(
            "INSERT INTO task_events(conversation_id,type,payload,created_at) VALUES(?,?,?,?)",
            (
                "b1",
                "tools.completed",
                json.dumps({"results": [{"tool": "secret.tool", "ok": False, "data": {}}]}),
                954.0,
            ),
        )

    snapshot = RoutingQualityControlTower(store).snapshot(
        tenant_id="tenant-a",
        window="24h",
        now=1000.0,
    )

    assert snapshot["tenant_scope"] == "tenant-a"
    assert snapshot["coverage"]["assistant_results"] == 2
    assert snapshot["routing_policy"]["domains"] == [
        {
            "domain": "aftersales",
            "observed_runs": 2,
            "latest_samples": 20,
            "latest_reward_ewma": 0.7,
            "latest_residual_ewma": 0.2,
            "residual_delta": -0.1,
            "reward_delta": 0.1,
        }
    ]
    assert snapshot["adaptive_routing"]["candidates_observed"] == 2
    assert snapshot["adaptive_routing"]["selected_candidates"] == 1
    assert snapshot["adaptive_routing"]["selection_rate"] == 0.5
    assert snapshot["adaptive_routing"]["activation"]["p50"] == 0.4
    assert snapshot["adaptive_routing"]["tool_reliability"]["min"] == 0.5
    assert snapshot["tool_quality"]["calls"] == 2
    assert snapshot["tool_quality"]["failed_call_rate"] == 0.5
    assert snapshot["tool_quality"]["tool_distribution"] == {
        "evidence.search": 1,
        "order.inspect": 1,
    }
    assert snapshot["tool_quality"]["evidence_tags_yielded"] == 2
    assert snapshot["tool_quality"]["evidence_tag_yield_per_call"] == 1.0
    assert snapshot["stagnation"]["stagnated_run_rate"] == 0.5
    assert snapshot["stagnation"]["stagnation_events"] == 1
    assert snapshot["cost_efficiency"]["tool_cost_per_completed_run"] == 2.5
    assert snapshot["authority"]["read_only"] is True
    assert snapshot["authority"]["changes_routing"] is False
    assert snapshot["authority"]["executes_tools"] is False
    assert "secret.tool" not in snapshot["tool_quality"]["tool_distribution"]


def test_routing_quality_rejects_unknown_window(tmp_path):
    service = RoutingQualityControlTower(_Store(tmp_path / "unused.db"))
    try:
        service.snapshot(tenant_id="tenant-a", window="90d")
    except ValueError as exc:
        assert "invalid routing quality window" in str(exc)
    else:
        raise AssertionError("expected invalid window to fail")
