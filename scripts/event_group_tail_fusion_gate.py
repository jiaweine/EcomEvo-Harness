from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from ecomevo.runtime.bundled_event_store import BundledEventStore


BATCH_SIZE = 64
BATCHES = 96
WARMUP_BATCHES = 8
EXPERIMENTS = 5
MAX_OPERATION_RATIO = 1.02


def _request(session_id: str, batch_index: int):
    return SimpleNamespace(
        session_id=session_id,
        event_type="tail-fusion.probe",
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


class BaselineStore(TracedStore):
    def _persist_append_group(self, batch):
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            tails = {}
            persisted = []
            for request in batch:
                if request.session_id not in tails:
                    tail = self._session_tail(connection, request.session_id)
                    if tail is None:
                        raise KeyError(f"unknown session: {request.session_id}")
                    tails[request.session_id] = tail
                event = self._append_in_transaction(
                    connection,
                    request.session_id,
                    request.event_type,
                    request.payload,
                    tail=tails[request.session_id],
                )
                persisted.append(event)
                tails[request.session_id] = {"seq": event.seq, "hash": event.hash}
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


def _run_arm(store_cls, path: Path, session_ids: list[str]) -> dict:
    store = store_cls(path)
    _prepare(store, session_ids)

    # Warm SQLite pages, statement preparation and the connection path before the
    # measured region. Warm-up writes are identical across arms and excluded from
    # both timing and statement counts.
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

    statements = [statement.upper() for statement in store.statements]
    transactions = sum(
        statement.strip() == "BEGIN IMMEDIATE" for statement in statements
    )
    legacy_tail_lookups = sum(
        "ORDER BY E.SEQ DESC LIMIT 1" in statement for statement in statements
    )
    set_tail_lookups = sum(
        statement.lstrip().startswith("WITH WANTED(SESSION_ID) AS (VALUES")
        for statement in statements
    )
    chains_valid = all(store.verify_chain(session_id) for session_id in session_ids)
    return {
        "operation_seconds": operation,
        "transactions": transactions,
        "legacy_tail_lookups": legacy_tail_lookups,
        "set_tail_lookups": set_tail_lookups,
        "chains_valid": chains_valid,
    }


def main() -> int:
    session_ids = [f"session-{index:02d}" for index in range(BATCH_SIZE)]
    paired_runs: list[dict] = []
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ecomevo-tail-fusion-gate-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "fused")
                if experiment % 2 == 0
                else ("fused", "baseline")
            )
            results = {}
            for arm in order:
                store_cls = BaselineStore if arm == "baseline" else TracedStore
                results[arm] = _run_arm(
                    store_cls,
                    root / f"{experiment}-{arm}.db",
                    session_ids,
                )
            baseline = results["baseline"]
            fused = results["fused"]
            paired_runs.append(
                {
                    "experiment": experiment,
                    "order": list(order),
                    "baseline_operation_seconds": baseline["operation_seconds"],
                    "fused_operation_seconds": fused["operation_seconds"],
                    "operation_ratio": (
                        fused["operation_seconds"] / baseline["operation_seconds"]
                        if baseline["operation_seconds"]
                        else 0.0
                    ),
                    "baseline": baseline,
                    "fused": fused,
                }
            )

    expected_transactions = BATCHES
    expected_legacy_lookups = BATCHES * BATCH_SIZE
    expected_fused_lookups = BATCHES

    for pair in paired_runs:
        index = int(pair["experiment"])
        baseline = pair["baseline"]
        fused = pair["fused"]
        if baseline["transactions"] != expected_transactions:
            failures.append(
                f"baseline experiment {index} transactions "
                f"{baseline['transactions']} != {expected_transactions}"
            )
        if fused["transactions"] != expected_transactions:
            failures.append(
                f"fused experiment {index} transactions "
                f"{fused['transactions']} != {expected_transactions}"
            )
        if baseline["legacy_tail_lookups"] != expected_legacy_lookups:
            failures.append(
                f"baseline experiment {index} tail lookups "
                f"{baseline['legacy_tail_lookups']} != {expected_legacy_lookups}"
            )
        if fused["set_tail_lookups"] != expected_fused_lookups:
            failures.append(
                f"fused experiment {index} set lookups "
                f"{fused['set_tail_lookups']} != {expected_fused_lookups}"
            )
        if fused["legacy_tail_lookups"] != 0:
            failures.append(
                f"fused experiment {index} used "
                f"{fused['legacy_tail_lookups']} legacy tail lookups"
            )
        if not baseline["chains_valid"]:
            failures.append(f"baseline experiment {index} produced invalid chain")
        if not fused["chains_valid"]:
            failures.append(f"fused experiment {index} produced invalid chain")

    paired_ratios = [float(pair["operation_ratio"]) for pair in paired_runs]
    operation_ratio = statistics.median(paired_ratios)
    if operation_ratio > MAX_OPERATION_RATIO:
        failures.append(
            "set-based tail lookup regressed paired fixed-input operation time: "
            f"{operation_ratio:.4f}x > {MAX_OPERATION_RATIO:.2f}x"
        )

    result = {
        "ok": not failures,
        "batch_size": BATCH_SIZE,
        "warmup_batches_per_arm": WARMUP_BATCHES,
        "measured_batches_per_arm": BATCHES,
        "experiments": EXPERIMENTS,
        "paired_operation_ratios": [round(value, 4) for value in paired_ratios],
        "paired_operation_ratio_median": round(operation_ratio, 4),
        "max_operation_ratio": MAX_OPERATION_RATIO,
        "baseline_tail_lookup_statements_per_experiment": expected_legacy_lookups,
        "fused_tail_lookup_statements_per_experiment": expected_fused_lookups,
        "tail_lookup_statement_ratio": round(
            expected_fused_lookups / expected_legacy_lookups, 4
        ),
        "transactions_per_arm_per_experiment": expected_transactions,
        "pair_timings": [
            {
                "experiment": int(pair["experiment"]),
                "order": pair["order"],
                "baseline_seconds": round(
                    float(pair["baseline_operation_seconds"]), 6
                ),
                "fused_seconds": round(float(pair["fused_operation_seconds"]), 6),
                "ratio": round(float(pair["operation_ratio"]), 4),
            }
            for pair in paired_runs
        ],
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
