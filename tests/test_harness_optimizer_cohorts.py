import asyncio

from ecomevo.runtime.harness_evolution import HarnessEvolutionOptimizer


def _catalog():
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
            "purpose": "search supplied evidence for identity authorization facts",
            "evidence_tags": ["merchant_identity"],
            "cost": 0.6,
        },
        {
            "tool": "refund.execute",
            "mode": "write",
            "purpose": "change business state",
            "evidence_tags": ["merchant_identity"],
            "cost": 1.0,
        },
    ]


def _candidate(optimizer):
    return asyncio.run(
        optimizer.propose(
            "merchant_review",
            trajectory={
                "goal": "review merchant identity and authorization",
                "missing": ["merchant identity", "authorization evidence"],
            },
            tool_catalog=_catalog(),
            reasoner=None,
        )
    )


def test_harmonic_reward_requires_both_quality_and_completeness(tmp_path):
    optimizer = HarnessEvolutionOptimizer(tmp_path / "runtime.db")
    assert optimizer.verifier_potential(0.0, 1.0) == 0.0
    assert optimizer.verifier_potential(1.0, 0.0) == 0.0
    balanced = optimizer.verifier_potential(0.8, 0.8)
    imbalanced = optimizer.verifier_potential(0.99, 0.25)
    assert balanced == 0.8
    assert imbalanced < balanced


def test_shadow_cannot_transition_from_single_arm_evidence(tmp_path):
    optimizer = HarnessEvolutionOptimizer(tmp_path / "runtime.db")
    candidate = _candidate(optimizer)
    assert candidate is not None

    transitions = optimizer.record_outcome(
        "merchant_review",
        [candidate["parent_id"]],
        verifier_score=0.01,
        evidence_complete=False,
        session_id="parent-only",
    )
    assert transitions == []

    snapshot = optimizer.snapshot("merchant_review")
    shadow = next(
        row for row in snapshot["components"]
        if row["component_id"] == candidate["component_id"]
    )
    assert shadow["status"] == "shadow"


def test_shadow_promotion_is_posterior_driven_and_excludes_write_tools(tmp_path):
    optimizer = HarnessEvolutionOptimizer(tmp_path / "runtime.db")
    candidate = _candidate(optimizer)
    assert candidate is not None
    assert "refund.execute" not in candidate["content"]["preferred_tools"]
    assert candidate["acceptance"]["fixed_run_threshold"] is False

    promoted = False
    for index in range(20):
        optimizer.record_outcome(
            "merchant_review",
            [candidate["parent_id"]],
            verifier_score=0.05,
            evidence_complete=False,
            session_id=f"incumbent-{index}",
        )
        transitions = optimizer.record_outcome(
            "merchant_review",
            [candidate["component_id"]],
            verifier_score=0.95,
            evidence_complete=True,
            session_id=f"candidate-{index}",
        )
        if any(row["transition"] == "promoted" for row in transitions):
            promoted = True
            transition = next(row for row in transitions if row["transition"] == "promoted")
            assert transition["candidate_exposures"] > 0
            assert transition["incumbent_exposures"] > 0
            assert transition["probability_superior"] >= 1.0 - optimizer.accept_risk
            break
    assert promoted is True
