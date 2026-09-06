from __future__ import annotations

import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_event_store import BundledEventStore


READS = 1500
ROUNDS = 5
PRODUCTION_RATIO_LIMIT = 0.85


class TracedBundledEventStore(BundledEventStore):
    def __init__(self, path: str | Path):
        self._trace_enabled = False
        self.selects: list[str] = []
        super().__init__(path)
        self._trace_enabled = True

    def _conn(self):
        connection = super()._conn()
        if self._trace_enabled:
            connection.set_trace_callback(self._trace)
        return connection

    def _trace(self, sql: str) -> None:
        if sql.lstrip().upper().startswith("SELECT"):
            self.selects.append(" ".join(sql.split()))


def single_query_restore(
    store: BundledEventStore,
    session_id: str,
    seq: int | None = None,
) -> dict[str, Any] | None:
    q = (
        "SELECT s.seq,s.snapshot_blob,s.state_hash,s.event_hash,"
        "e.hash AS current_event_hash "
        "FROM snapshots AS s "
        "LEFT JOIN events AS e ON e.session_id=s.session_id AND e.seq=s.seq "
        "WHERE s.session_id=?"
    )
    params: list[Any] = [session_id]
    if seq is not None:
        q += " AND s.seq<=?"
        params.append(int(seq))
    q += " ORDER BY s.seq DESC LIMIT 1"

    with store._conn() as connection:
        row = connection.execute(q, params).fetchone()
    if not row:
        return None

    checkpoint_seq = int(row["seq"])
    if checkpoint_seq:
        if row["current_event_hash"] is None:
            return None
        event_hash = str(row["current_event_hash"])
    else:
        event_hash = "GENESIS"

    blob = str(row["snapshot_blob"])
    if not blob.startswith("json:"):
        return None
    state = json.loads(blob[5:])
    body = store._state_body(state)
    computed_state = hashlib.sha256(body.encode()).hexdigest()
    expected_state = str(row["state_hash"] or computed_state)
    expected_event = str(row["event_hash"] or event_hash)
    if computed_state != expected_state or event_hash != expected_event:
        return None
    return {
        **state,
        "_checkpoint": {
            "session_id": session_id,
            "seq": checkpoint_seq,
            "state_hash": expected_state,
            "event_hash": expected_event,
        },
    }


def seed(path: Path, *, session_id: str = "session") -> TracedBundledEventStore:
    store = TracedBundledEventStore(path)
    store._trace_enabled = False
    store.create_session(session_id, meta={"probe": True})
    store.append(session_id, "probe.one", {"value": 1})
    store.save_checkpoint(
        session_id,
        {"stage": "one", "belief": {"facts": {"value": 1}, "confidence": 0.2}},
    )
    store.append(session_id, "probe.two", {"value": 2})
    store.save_checkpoint(
        session_id,
        {"stage": "two", "belief": {"facts": {"value": 2}, "confidence": 0.4}},
    )
    store._trace_enabled = True
    store.selects.clear()
    return store


def compare_case(store: BundledEventStore, session_id: str, seq: int | None = None) -> bool:
    return store.restore_checkpoint(session_id, seq) == single_query_restore(store, session_id, seq)


def semantic_cases(root: Path) -> dict[str, bool]:
    normal = seed(root / "normal.db")
    latest = compare_case(normal, "session")
    seq_limited = compare_case(normal, "session", 1)

    genesis = TracedBundledEventStore(root / "genesis.db")
    genesis._trace_enabled = False
    genesis.create_session("zero")
    genesis.save_checkpoint("zero", {"stage": "zero", "belief": {"facts": {}}})
    genesis._trace_enabled = True
    genesis.selects.clear()
    seq_zero = compare_case(genesis, "zero")

    state_tamper = seed(root / "state-tamper.db")
    state_tamper._trace_enabled = False
    with state_tamper._conn() as connection:
        connection.execute(
            "UPDATE snapshots SET snapshot_blob=? WHERE session_id=? AND seq=?",
            ('json:{"stage":"tampered"}', "session", 2),
        )
    state_tamper._trace_enabled = True
    state_tamper.selects.clear()
    state_tamper_equal = compare_case(state_tamper, "session")
    state_tamper_rejected = state_tamper.restore_checkpoint("session") is None

    event_tamper = seed(root / "event-tamper.db")
    event_tamper._trace_enabled = False
    with event_tamper._conn() as connection:
        connection.execute(
            "UPDATE events SET hash=? WHERE session_id=? AND seq=?",
            ("tampered-event-hash", "session", 2),
        )
    event_tamper._trace_enabled = True
    event_tamper.selects.clear()
    event_tamper_equal = compare_case(event_tamper, "session")
    event_tamper_rejected = event_tamper.restore_checkpoint("session") is None

    missing_event = seed(root / "missing-event.db")
    missing_event._trace_enabled = False
    with missing_event._conn() as connection:
        connection.execute("DELETE FROM events WHERE session_id=? AND seq=?", ("session", 2))
    missing_event._trace_enabled = True
    missing_event.selects.clear()
    missing_event_equal = compare_case(missing_event, "session")
    missing_event_rejected = missing_event.restore_checkpoint("session") is None

    return {
        "latest_equal": latest,
        "seq_limited_equal": seq_limited,
        "seq_zero_genesis_equal": seq_zero,
        "state_tamper_equal": state_tamper_equal,
        "state_tamper_rejected": state_tamper_rejected,
        "event_tamper_equal": event_tamper_equal,
        "event_tamper_rejected": event_tamper_rejected,
        "missing_event_equal": missing_event_equal,
        "missing_event_rejected": missing_event_rejected,
    }


def query_shape(store: TracedBundledEventStore) -> dict[str, Any]:
    store.selects.clear()
    baseline = store.restore_checkpoint("session")
    baseline_sql = list(store.selects)
    store.selects.clear()
    candidate = single_query_restore(store, "session")
    candidate_sql = list(store.selects)
    return {
        "equal": baseline == candidate,
        "baseline_selects": len(baseline_sql),
        "candidate_selects": len(candidate_sql),
        "baseline_sql": baseline_sql,
        "candidate_sql": candidate_sql,
    }


def benchmark(store: TracedBundledEventStore) -> tuple[list[dict[str, Any]], float]:
    store._trace_enabled = False
    paired: list[dict[str, Any]] = []
    sink = 0
    for round_index in range(ROUNDS):
        order = ["baseline", "candidate"] if round_index % 2 == 0 else ["candidate", "baseline"]
        timings: dict[str, float] = {}
        for arm in order:
            started = time.perf_counter()
            for _ in range(READS):
                restored = (
                    store.restore_checkpoint("session")
                    if arm == "baseline"
                    else single_query_restore(store, "session")
                )
                if restored is not None:
                    sink += int(restored["_checkpoint"]["seq"])
            timings[arm] = time.perf_counter() - started
        paired.append(
            {
                "round": round_index + 1,
                "baseline_seconds": round(timings["baseline"], 6),
                "candidate_seconds": round(timings["candidate"], 6),
                "ratio": round(timings["candidate"] / max(timings["baseline"], 1e-12), 4),
            }
        )
    if sink <= 0:
        raise AssertionError("benchmark sink was not exercised")
    return paired, statistics.median(row["ratio"] for row in paired)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ecomevo-checkpoint-restore-") as tmp:
        root = Path(tmp)
        semantics = semantic_cases(root)
        measured = seed(root / "measure.db")
        shape = query_shape(measured)
        paired, median_ratio = benchmark(measured)

    failures: list[str] = []
    for name, ok in semantics.items():
        if not ok:
            failures.append(f"semantic case failed: {name}")
    if not shape["equal"]:
        failures.append("normal restore output changed")
    if shape["baseline_selects"] != 2:
        failures.append(f"baseline SELECT count {shape['baseline_selects']} != 2")
    if shape["candidate_selects"] != 1:
        failures.append(f"candidate SELECT count {shape['candidate_selects']} != 1")
    if median_ratio > PRODUCTION_RATIO_LIMIT:
        failures.append(
            f"median ratio {median_ratio:.4f} > production threshold {PRODUCTION_RATIO_LIMIT:.2f}"
        )

    report = {
        "ok": not failures,
        "semantic_cases": semantics,
        "query_shape": shape,
        "benchmark": {
            "reads_per_arm_per_round": READS,
            "rounds": ROUNDS,
            "paired": paired,
            "median_ratio": round(median_ratio, 4),
            "production_ratio_limit": PRODUCTION_RATIO_LIMIT,
        },
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
