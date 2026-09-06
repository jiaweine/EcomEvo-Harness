from __future__ import annotations

import asyncio
import json
import threading
from collections import Counter

import pytest

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer
from ecomevo.runtime.harness_optimizer import HarnessEvolutionOptimizer


DOMAIN = "merchant_review"
OTHER_DOMAIN = "returns"


class TracingHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.immediate_begins = 0
        self.component_updates = 0
        self.outcome_inserts = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            normalized = statement.strip().upper()
            with self._trace_lock:
                if normalized.startswith("BEGIN IMMEDIATE"):
                    self.immediate_begins += 1
                if normalized.startswith("UPDATE HARNESS_COMPONENTS SET ALPHA="):
                    self.component_updates += 1
                if (
                    "INSERT INTO HARNESS_COMPONENT_OUTCOMES" in normalized
                    or "INSERT INTO \"HARNESS_COMPONENT_OUTCOMES\"" in normalized
                ):
                    self.outcome_inserts += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_trace(self) -> None:
        with self._trace_lock:
            self.immediate_begins = 0
            self.component_updates = 0
            self.outcome_inserts = 0


def _catalog() -> list[dict]:
    return [
        {
            "tool": "merchant.inspect",
            "mode": "read-only",
            "purpose": "read merchant identity authorization evidence",
            "evidence_tags": ["merchant_identity", "authorization"],
            "cost": 1.0,
        },
        {
            "tool": "evidence.search",
            "mode": "read-only",
            "purpose": "search supplied evidence",
            "evidence_tags": ["merchant_identity"],
            "cost": 0.6,
        },
    ]


def _component_rows(harness, domain: str) -> dict[str, dict]:
    return {
        row["component_id"]: row
        for row in harness.snapshot(domain).get("components", [])
    }


def test_fused_outcome_uses_one_update_and_insert_and_preserves_evidence_order(tmp_path):
    harness = TracingHarness(tmp_path / "runtime.db")
    profile = harness.profile(DOMAIN, session_key="target")
    other = harness.profile(OTHER_DOMAIN, session_key="other")
    target_ids = list(profile["component_ids"])
    other_id = str(other["component_ids"][0])
    requested = [target_ids[2], target_ids[0], target_ids[2], "missing", other_id, target_ids[3]]
    expected_ids = [target_ids[2], target_ids[0], target_ids[3]]

    before_target = _component_rows(harness, DOMAIN)
    before_other = _component_rows(harness, OTHER_DOMAIN)
    harness.reset_trace()

    transitions = harness.record_outcome(
        DOMAIN,
        requested,
        verifier_score=0.84,
        evidence_complete=False,
        evidence_completeness=0.6,
        session_id="fusion-order",
        meta={"probe": "fusion-order"},
    )

    assert transitions == []
    assert harness.immediate_begins == 1
    assert harness.component_updates == 1
    assert harness.outcome_inserts == 1

    after_target = _component_rows(harness, DOMAIN)
    after_other = _component_rows(harness, OTHER_DOMAIN)
    for component_id, before in before_target.items():
        expected_delta = 1 if component_id in expected_ids else 0
        assert int(after_target[component_id]["uses"]) == int(before["uses"]) + expected_delta
    assert int(after_other[other_id]["uses"]) == int(before_other[other_id]["uses"])

    with harness._conn() as connection:
        rows = connection.execute(
            "SELECT component_id,verifier_score,evidence_complete,meta_json "
            "FROM harness_component_outcomes WHERE session_id=? ORDER BY id",
            ("fusion-order",),
        ).fetchall()
    assert [str(row["component_id"]) for row in rows] == expected_ids
    assert len(rows) == 3
    expected_reward = harness.verifier_potential(0.84, 0.6)
    for row in rows:
        assert float(row["verifier_score"]) == pytest.approx(expected_reward)
        assert int(row["evidence_complete"]) == 0
        meta = json.loads(row["meta_json"])
        assert meta["probe"] == "fusion-order"
        assert meta["raw_verifier_score"] == pytest.approx(0.84)
        assert meta["evidence_completeness"] == pytest.approx(0.6)
        assert meta["reward"] == pytest.approx(expected_reward)
        assert meta["reward_method"] == "verifier_harmonic_potential"


def test_large_component_selection_falls_back_without_bind_limit_regression(tmp_path):
    harness = BundledHarnessEvolutionOptimizer(tmp_path / "runtime.db")
    profile = harness.profile(DOMAIN, session_key="large")
    valid_id = str(profile["component_ids"][0])
    before = _component_rows(harness, DOMAIN)[valid_id]
    requested = [valid_id, *(f"missing-{index}" for index in range(129)), valid_id]

    transitions = harness.record_outcome(
        DOMAIN,
        requested,
        verifier_score=0.75,
        evidence_complete=True,
        session_id="large-fallback",
    )

    assert transitions == []
    after = _component_rows(harness, DOMAIN)[valid_id]
    assert int(after["uses"]) == int(before["uses"]) + 1
    with harness._conn() as connection:
        rows = connection.execute(
            "SELECT component_id FROM harness_component_outcomes "
            "WHERE session_id=? ORDER BY id",
            ("large-fallback",),
        ).fetchall()
    assert [str(row["component_id"]) for row in rows] == [valid_id]


async def _shadow_signature(harness) -> dict:
    candidate = await harness.propose(
        DOMAIN,
        trajectory={
            "goal": "review merchant identity and authorization",
            "missing": ["merchant identity", "authorization evidence"],
        },
        tool_catalog=_catalog(),
        reasoner=None,
    )
    assert candidate is not None

    promotion_round = None
    transition_signature: list[dict] = []
    for index in range(40):
        await harness.record_outcome_async(
            DOMAIN,
            [candidate["parent_id"]],
            verifier_score=0.05,
            evidence_complete=False,
            session_id=f"parent-{index}",
        )
        transitions = await harness.record_outcome_async(
            DOMAIN,
            [candidate["component_id"]],
            verifier_score=0.95,
            evidence_complete=True,
            session_id=f"candidate-{index}",
        )
        if transitions:
            transition_signature = [
                {
                    "kind": row.get("kind"),
                    "transition": row.get("transition"),
                    "probability_superior": row.get("probability_superior"),
                    "candidate_exposures": row.get("candidate_exposures"),
                    "incumbent_exposures": row.get("incumbent_exposures"),
                }
                for row in transitions
            ]
        if any(row.get("transition") == "promoted" for row in transitions):
            promotion_round = index
            break

    statuses = Counter(
        (str(row["kind"]), str(row["status"]))
        for row in harness.snapshot(DOMAIN).get("components", [])
    )
    return {
        "promotion_round": promotion_round,
        "transition_signature": transition_signature,
        "status_counts": dict(statuses),
    }


def test_fused_outcome_matches_base_shadow_posterior_transition(tmp_path):
    async def exercise():
        base = HarnessEvolutionOptimizer(tmp_path / "base.db")
        fused = BundledHarnessEvolutionOptimizer(tmp_path / "fused.db")
        return await _shadow_signature(base), await _shadow_signature(fused)

    base, fused = asyncio.run(exercise())
    assert fused == base
    assert fused["promotion_round"] == 1
    assert fused["transition_signature"] == [
        {
            "kind": "tool",
            "transition": "promoted",
            "probability_superior": 0.9504,
            "candidate_exposures": 2,
            "incumbent_exposures": 2,
        }
    ]
