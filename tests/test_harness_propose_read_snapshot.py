import asyncio
import json

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer


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
            "tool": "refund.execute",
            "mode": "write",
            "purpose": "change business state",
            "evidence_tags": ["merchant_identity"],
            "cost": 1.0,
        },
    ]


def _trajectory(label: str = "one"):
    return {
        "goal": f"review merchant identity and authorization {label}",
        "missing": ["merchant identity", "authorization evidence"],
    }


class TracingBundledHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.immediate_begins = 0
        super().__init__(path)
        self.immediate_begins = 0

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection

    def replay_count(self, domain: str) -> int:
        with self._conn() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM harness_replay_cases WHERE domain=?",
                    (domain,),
                ).fetchone()[0]
            )


def test_cold_proposal_keeps_bootstrap_and_records_replay_once(tmp_path):
    optimizer = TracingBundledHarness(tmp_path / "runtime.db")

    candidate = asyncio.run(
        optimizer.propose(
            "merchant_review",
            trajectory=_trajectory(),
            tool_catalog=_catalog(),
            reasoner=None,
        )
    )

    assert candidate is not None
    assert candidate["status"] == "shadow"
    assert optimizer.replay_count("merchant_review") == 1
    # Cold/bootstrap plus final candidate insertion remain writer-protected.
    assert optimizer.immediate_begins >= 2


def test_existing_shadow_probe_adds_no_writer_beyond_replay_evidence(tmp_path):
    optimizer = TracingBundledHarness(tmp_path / "runtime.db")
    first = asyncio.run(
        optimizer.propose(
            "merchant_review",
            trajectory=_trajectory("first"),
            tool_catalog=_catalog(),
            reasoner=None,
        )
    )
    assert first is not None
    assert optimizer.replay_count("merchant_review") == 1

    optimizer.immediate_begins = 0
    second = asyncio.run(
        optimizer.propose(
            "merchant_review",
            trajectory=_trajectory("second"),
            tool_catalog=_catalog(),
            reasoner=None,
        )
    )

    assert second is None
    # Replay evidence is a real durable write, so one isolated proposal still needs one
    # immediate writer transaction. The existing-shadow read probe itself must not add a
    # second BEGIN IMMEDIATE reservation.
    assert optimizer.immediate_begins == 1
    assert optimizer.replay_count("merchant_review") == 2


class CoordinatedReasoner:
    def __init__(self):
        self.calls = 0
        self.ready = asyncio.Event()

    async def chat(self, **_kwargs):
        self.calls += 1
        if self.calls >= 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=2)
        return json.dumps(
            {
                "kind": "delegation",
                "hypothesis": "add one bounded review role",
                "edits": [
                    {
                        "op": "add",
                        "field": "roles",
                        "value": ["evidence_reviewer"],
                    }
                ],
            }
        )


def test_concurrent_candidate_race_still_creates_at_most_one_shadow(tmp_path):
    optimizer = TracingBundledHarness(tmp_path / "runtime.db")
    # Initialize the domain without creating a shadow so both proposal tasks can take the
    # new read snapshot before either reaches the final writer recheck.
    optimizer.profile("merchant_review", session_key="bootstrap")
    optimizer.immediate_begins = 0

    async def run():
        reasoner = CoordinatedReasoner()
        return await asyncio.gather(
            optimizer.propose(
                "merchant_review",
                trajectory=_trajectory("a"),
                tool_catalog=_catalog(),
                reasoner=reasoner,
            ),
            optimizer.propose(
                "merchant_review",
                trajectory=_trajectory("b"),
                tool_catalog=_catalog(),
                reasoner=reasoner,
            ),
        )

    results = asyncio.run(run())

    assert sum(row is not None for row in results) == 1
    assert optimizer.replay_count("merchant_review") == 2
    snapshot = optimizer.snapshot("merchant_review")
    shadows = [row for row in snapshot["components"] if row["status"] == "shadow"]
    assert len(shadows) == 1
