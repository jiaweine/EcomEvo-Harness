from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ecomevo.runtime.bundled_event_store import BundledEventStore


BATCH_SIZE = 64
BATCHES = 96
WARMUP_BATCHES = 8
EXPERIMENTS = 5
POSITIVE_OPERATION_RATIO = 0.98


def _request(session_id: str, batch_index: int):
    return SimpleNamespace(
        session_id=session_id,
        event_type="tail-seek.probe",
        payload={"batch": batch_index, "session": session_id},
    )


class TracedStore(BundledEventStore):
    def __init__(self, path):
        self.statements: list[str] = []
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()
        connection.set_trace_callback(self.statements.append)
        return connection

    def reset_trace(self) -> None:
        self.statements.clear()


class CorrelatedTailStore(TracedStore):
    @staticmethod
    def _correlated_tails(connection, session_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique_ids = list(dict.fromkeys(str(session_id) for session_id in session_ids))
        if not unique_ids:
            return {}
        values = ",".join("(?)" for _ in unique_ids)
        rows = connection.execute(  # nosec B608 - bounded local placeholders only
            f"""WITH wanted(session_id) AS (VALUES {values})
                SELECT s.session_id,e.seq,e.hash
                FROM wanted AS w
                JOIN sessions AS s ON s.session_id=w.session_id
                LEFT JOIN events AS e
                  ON e.session_id=s.session_id
                 AND e.seq=(
                     SELECT e2.seq
                     FROM events AS e2
                     WHERE e2.session_id=s.session_id
                     ORDER BY e2.seq DESC
                     LIMIT 1
                 )""",
            unique_ids,
        ).fetchall()
        return {
            str(row["session_id"]): {"seq": row["seq"], "hash": row["hash"]}
            for row in rows
        }

    def _persist_append_group(self, batch):
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            ordered_ids = list(
                dict.fromkeys(str(request.session_id) for request in batch)
            )
            tails = self._correlated_tails(connection, ordered_ids)
            for session_id in ordered_ids:
                if session_id not in tails:
                    raise KeyError(f"unknown session: {session_id}")

            persisted = []
            for request in batch:
                session_id = str(request.session_id)
                event = self._append_in_transaction(
                    connection,
                    session_id,
                    request.event_type,
                    request.payload,
                    tail=tails[session_id],
                )
                persisted.append(event)
                tails[session_id] = {"seq": event.seq, "hash": event.hash}
            return persisted


def _prepare(store: TracedStore, session_ids: list[str]) -> None:
    for session_id in session_ids:
        store.create_session(session_id)
        store.append(session_id, "seed", {"session": session_id})


def _append_batches(
    store: TracedStore,
    session_ids: list[str],
    *,
    count: int,
    batch_offset: int,
) -> None:
    for batch_index in range(batch_offset, batch_offset + count):
        persisted = store._persist_append_group(
            [_request(session_id, batch_index) for session_id in session_ids]
        )
        if len(persisted) != len(session_ids):
            raise AssertionError("append group lost requests")


def _semantic_arm(store_cls, path: Path) -> dict[str, Any]:
    store = store_cls(path)
    store.create_session("empty")
    empty = store._persist_append_group([_request("empty", 0)])[0]

    store.create_session("repeat")
    seed = store.append("repeat", "seed", {"n": 0})
    repeated = store._persist_append_group(
        [_request("repeat", index) for index in (1, 2, 3)]
    )

    store.create_session("known")
    with pytest_raises_key_error():
        store._persist_append_group(
            [_request("known", 1), _request("missing", 2)]
        )
    with store._conn() as connection:
        known_count = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM events WHERE session_id='known'"
            ).fetchone()["n"]
        )

    return {
        "empty_seq": int(empty.seq),
        "empty_prev_hash": str(empty.prev_hash),
        "repeat_seed_seq": int(seed.seq),
        "repeat_seqs": [int(event.seq) for event in repeated],
        "repeat_chain_valid": bool(store.verify_chain("repeat")),
        "unknown_batch_rolled_back": known_count == 0,
    }


class pytest_raises_key_error:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError("expected KeyError")
        if not issubclass(exc_type, KeyError):
            return False
        return True


def _run_arm(store_cls, path: Path, session_ids: list[str]) -> dict[str, Any]:
    store = store_cls(path)
    _prepare(store, session_ids)
    _append_batches(
        store,
        session_ids,
        count=WARMUP_BATCHES,
        batch_offset=-WARMUP_BATCHES,
    )
    store.reset_trace()

    started = time.perf_counter()
    _append_batches(store, session_ids, count=BATCHES, batch_offset=0)
    operation = time.perf_counter() - started

    statements = [" ".join(statement.upper().split()) for statement in store.statements]
    transactions = sum(statement == "BEGIN IMMEDIATE" for statement in statements)
    legacy_tail_lookups = sum(
        "FROM SESSIONS AS S" in statement
        and "ORDER BY E.SEQ DESC LIMIT 1" in statement
        for statement in statements
    )
    correlated_tail_lookups = sum(
        statement.startswith("WITH WANTED(SESSION_ID) AS (VALUES")
        and "SELECT E2.SEQ" in statement
        and "ORDER BY E2.SEQ DESC" in statement
        for statement in statements
    )
    return {
        "operation_seconds": operation,
        "transactions": transactions,
        "legacy_tail_lookups": legacy_tail_lookups,
        "correlated_tail_lookups": correlated_tail_lookups,
        "chains_valid": all(store.verify_chain(session_id) for session_id in session_ids),
    }


def _query_plan(path: Path) -> list[str]:
    store = CorrelatedTailStore(path)
    session_ids = [f"plan-{index:02d}" for index in range(4)]
    _prepare(store, session_ids)
    values = ",".join("(?)" for _ in session_ids)
    with store._conn() as connection:
        rows = connection.execute(
            f"""EXPLAIN QUERY PLAN
                WITH wanted(session_id) AS (VALUES {values})
                SELECT s.session_id,e.seq,e.hash
                FROM wanted AS w
                JOIN sessions AS s ON s.session_id=w.session_id
                LEFT JOIN events AS e
                  ON e.session_id=s.session_id
                 AND e.seq=(
                     SELECT e2.seq
                     FROM events AS e2
                     WHERE e2.session_id=s.session_id
                     ORDER BY e2.seq DESC
                     LIMIT 1
                 )""",  # nosec B608 - diagnostic bounded placeholders only
            session_ids,
        ).fetchall()
    return [str(row["detail"]) for row in rows]


def main() -> int:
    failures: list[str] = []
    session_ids = [f"session-{index:02d}" for index in range(BATCH_SIZE)]
    paired_runs: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="ecomevo-tail-correlated-") as tmp:
        root = Path(tmp)
        baseline_semantics = _semantic_arm(TracedStore, root / "semantic-baseline.db")
        candidate_semantics = _semantic_arm(
            CorrelatedTailStore, root / "semantic-candidate.db"
        )
        if baseline_semantics != candidate_semantics:
            failures.append("correlated tail seek changed append semantics")

        plan = _query_plan(root / "query-plan.db")

        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "candidate")
                if experiment % 2 == 0
                else ("candidate", "baseline")
            )
            results = {}
            for arm in order:
                store_cls = TracedStore if arm == "baseline" else CorrelatedTailStore
                results[arm] = _run_arm(
                    store_cls,
                    root / f"{experiment}-{arm}.db",
                    session_ids,
                )
            baseline = results["baseline"]
            candidate = results["candidate"]
            ratio = candidate["operation_seconds"] / baseline["operation_seconds"]
            paired_runs.append(
                {
                    "experiment": experiment,
                    "order": list(order),
                    "ratio": ratio,
                    "baseline": baseline,
                    "candidate": candidate,
                }
            )

    expected_transactions = BATCHES
    expected_legacy = BATCHES * BATCH_SIZE
    expected_candidate = BATCHES
    for pair in paired_runs:
        index = int(pair["experiment"])
        baseline = pair["baseline"]
        candidate = pair["candidate"]
        if baseline["transactions"] != expected_transactions:
            failures.append(f"baseline[{index}] transaction count changed")
        if candidate["transactions"] != expected_transactions:
            failures.append(f"candidate[{index}] transaction count changed")
        if baseline["legacy_tail_lookups"] != expected_legacy:
            failures.append(
                f"baseline[{index}] tail lookups {baseline['legacy_tail_lookups']} != {expected_legacy}"
            )
        if candidate["correlated_tail_lookups"] != expected_candidate:
            failures.append(
                f"candidate[{index}] set lookups {candidate['correlated_tail_lookups']} != {expected_candidate}"
            )
        if candidate["legacy_tail_lookups"]:
            failures.append(f"candidate[{index}] used legacy tail statements")
        if not baseline["chains_valid"] or not candidate["chains_valid"]:
            failures.append(f"experiment {index} produced invalid event chain")

    paired_ratios = [float(pair["ratio"]) for pair in paired_runs]
    median_ratio = statistics.median(paired_ratios)
    positive = median_ratio <= POSITIVE_OPERATION_RATIO
    if not positive:
        failures.append(
            "correlated tail seek did not achieve meaningful fixed-input improvement: "
            f"{median_ratio:.4f}x > {POSITIVE_OPERATION_RATIO:.2f}x"
        )

    result = {
        "ok": not failures,
        "positive": positive,
        "batch_size": BATCH_SIZE,
        "warmup_batches_per_arm": WARMUP_BATCHES,
        "measured_batches_per_arm": BATCHES,
        "experiments": EXPERIMENTS,
        "paired_operation_ratios": [round(value, 4) for value in paired_ratios],
        "paired_operation_ratio_median": round(median_ratio, 4),
        "positive_ratio_threshold": POSITIVE_OPERATION_RATIO,
        "baseline_tail_statements_per_experiment": expected_legacy,
        "candidate_tail_statements_per_experiment": expected_candidate,
        "tail_statement_ratio": round(expected_candidate / expected_legacy, 4),
        "transactions_per_arm": expected_transactions,
        "query_plan": plan,
        "semantics": candidate_semantics,
        "pair_timings": [
            {
                "experiment": int(pair["experiment"]),
                "order": pair["order"],
                "baseline_seconds": round(
                    float(pair["baseline"]["operation_seconds"]), 6
                ),
                "candidate_seconds": round(
                    float(pair["candidate"]["operation_seconds"]), 6
                ),
                "ratio": round(float(pair["ratio"]), 4),
            }
            for pair in paired_runs
        ],
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
