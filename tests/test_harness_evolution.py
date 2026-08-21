import asyncio

from ecomevo.runtime.harness_context import bind_harness_profile, current_harness_profile, reset_harness_profile
from ecomevo.runtime.harness_evolution import HarnessEvolutionOptimizer


def _catalog():
    return [
        {
            "tool": "merchant.inspect",
            "mode": "read-only",
            "purpose": "read merchant identity and authorization evidence",
            "evidence_tags": ["merchant_identity", "authorization"],
            "cost": 1.0,
        },
        {
            "tool": "evidence.search",
            "mode": "read-only",
            "purpose": "search uploaded evidence for merchant identity authorization",
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


def test_harness_profile_contains_only_cognitive_components(tmp_path):
    optimizer = HarnessEvolutionOptimizer(tmp_path / "runtime.db")
    profile = optimizer.profile("merchant_review", session_key="s-1")
    assert set(profile["components"]) == {"prompt", "tool", "memory", "delegation"}
    assert len(profile["component_ids"]) == 4
    assert all(row["status"] == "active" for row in profile["components"].values())
    assert "verifier" not in profile["components"]
    assert "sandbox" not in profile["components"]
    assert "authority" not in profile["components"]


def test_deterministic_tool_coordinate_uses_metadata_and_rejects_side_effect_tools(tmp_path):
    optimizer = HarnessEvolutionOptimizer(tmp_path / "runtime.db")
    candidate = asyncio.run(
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
    assert candidate is not None
    assert candidate["kind"] == "tool"
    assert candidate["status"] == "shadow"
    assert "merchant.inspect" in candidate["content"]["preferred_tools"]
    assert "refund.execute" not in candidate["content"]["preferred_tools"]
    assert candidate["authority"] == "cognition-only"

    # HarnessCompass/SBCO-style block coordinate constraint: do not jointly mutate a
    # second component while the first coordinate is under validation.
    second = asyncio.run(
        optimizer.propose(
            "merchant_review",
            trajectory={"goal": "another task", "missing": ["authorization"]},
            tool_catalog=_catalog(),
            reasoner=None,
        )
    )
    assert second is None


def test_shadow_component_promotes_from_verifier_posterior_not_fixed_run_count(tmp_path):
    optimizer = HarnessEvolutionOptimizer(tmp_path / "runtime.db")
    candidate = asyncio.run(
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
    assert candidate is not None
    parent_id = candidate["parent_id"]
    candidate_id = candidate["component_id"]

    promoted = False
    for index in range(40):
        optimizer.record_outcome(
            "merchant_review",
            [parent_id],
            verifier_score=0.05,
            evidence_complete=False,
            session_id=f"parent-{index}",
        )
        transitions = optimizer.record_outcome(
            "merchant_review",
            [candidate_id],
            verifier_score=0.95,
            evidence_complete=True,
            session_id=f"candidate-{index}",
        )
        if any(row["transition"] == "promoted" for row in transitions):
            promoted = True
            break
    assert promoted is True
    snapshot = optimizer.snapshot("merchant_review")
    active_tool = next(
        row for row in snapshot["components"]
        if row["kind"] == "tool" and row["status"] == "active"
    )
    assert active_tool["component_id"] == candidate_id


def test_harness_profile_context_is_task_local_and_resettable(tmp_path):
    optimizer = HarnessEvolutionOptimizer(tmp_path / "runtime.db")
    profile = optimizer.profile("merchant_review", session_key="s-context")
    token = bind_harness_profile(profile)
    try:
        assert current_harness_profile()["domain"] == "merchant_review"
        assert current_harness_profile()["component_ids"] == profile["component_ids"]
    finally:
        reset_harness_profile(token)
    assert current_harness_profile() == {}
