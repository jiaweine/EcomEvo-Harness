from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from ecomevo.runtime.bundled_event_store import BundledEventStore


BATCH_SIZE = 64
BATCHES = 24
EXPERIMENTS = 5


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
    store.reset_trace()


def _run_arm(store_cls, path: Path, session_ids: list[str]) -> dict:
    store = store_cls(path)
    _prepare(store, session_ids)
    started = time.perf_counter()
    for batch_index in range(BATCHES):
        persisted = store._persist_append_group(
            [_request(session_id, batch_index) for session_id in session_ids]
        )
        if len(persisted) != len(session_ids):
            raise AssertionError("append group lost requests")
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
    baseline_runs = []
    fused_runs = []
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ecomevo-tail-fusion-gate-") as tmp:
        root = Path(tmp)
        for experiment in range(EXPERIMENTS):
            order = ("baseline", "fused") if experiment % 2 == 0 else ("fused", "baseline")
            results = {}
            for arm in order:
                store_cls = BaselineStore if arm == "baseline" else TracedStore
                results[arm] = _run_arm(
                    store_cls,
                    root / f"{experiment}-{arm}.db",
                    session_ids,
                )
            baseline_runs.append(results["baseline"])
            fused_runs.append(results["fused"])

    expected_transactions = BATCHES
    expected_legacy_lookups = BATCHES * BATCH_SIZE
    expected_fused_lookups = BATCHES

    for index, run in enumerate(baseline_runs):
        if run["transactions"] != expected_transactions:
            failures.append(
                f"baseline experiment {index} transactions "
                f"{run['transactions']} != {expected_transactions}"
            )
        if run["legacy_tail_lookups"] != expected_legacy_lookups:
            failures.append(
                f"baseline experiment {index} tail lookups "
                f"{run['legacy_tail_lookups']} != {expected_legacy_lookups}"
            )
        if not run["chains_valid"]:
            failures.append(f"baseline experiment {index} produced invalid chain")

    for index, run in enumerate(fused_runs):
        if run["transactions"] != expected_transactions:
            failures.append(
                f"fused experiment {index} transactions "
                f"{run['transactions']} != {expected_transactions}"
            )
        if run["set_tail_lookups"] != expected_fused_lookups:
            failures.append(
                f"fused experiment {index} set lookups "
                f"{run['set_tail_lookups']} != {expected_fused_lookups}"
            )
        if run["legacy_tail_lookups"] != 0:
            failures.append(
                f"fused experiment {index} used "
                f"{run['legacy_tail_lookups']} legacy tail lookups"
            )
        if not run["chains_valid"]:
            failures.append(f"fused experiment {index} produced invalid chain")

    baseline_operation = statistics.median(
        run["operation_seconds"] for run in baseline_runs
    )
    fused_operation = statistics.median(
        run["operation_seconds"] for run in fused_runs
    )
    operation_ratio = fused_operation / baseline_operation if baseline_operation else 0.0
    if operation_ratio > 1.02:
        failures.append(
            f"set-based tail lookup regressed fixed-input operation time: "
            f"{operation_ratio:.4f}x > 1.02x"
        )

    result = {
        "ok": not failures,
        "batch_size": BATCH_SIZE,
        "batches_per_arm": BATCHES,
        "experiments": EXPERIMENTS,
        "baseline_operation_seconds_median": round(baseline_operation, 6),
        "fused_operation_seconds_median": round(fused_operation, 6),
        "operation_ratio": round(operation_ratio, 4),
        "baseline_tail_lookup_statements": expected_legacy_lookups,
        "fused_tail_lookup_statements": expected_fused_lookups,
        "tail_lookup_statement_ratio": round(
            expected_fused_lookups / expected_legacy_lookups, 4
        ),
        "transactions_per_arm": expected_transactions,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
