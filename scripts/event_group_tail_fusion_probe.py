from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import writer_profile_gate as writer_profile
from ecomevo.runtime.bundled_event_store import _GroupedAppend


DOMAIN = "merchant_review"
BATCH_SIZES = (32, 64)
CAUSAL_BATCHES = 24
EXPERIMENTS = 3
RUNTIME_TASKS = 32


class TailMetricMixin:
    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._tail_metric_lock = threading.RLock()
        self._tail_lookup_statements = 0
        super().__init__(path, profile)

    def reset_tail_metrics(self) -> None:
        with self._tail_metric_lock:
            self._tail_lookup_statements = 0

    def _conn(self):
        connection = super()._conn()
        connection_id = id(connection)

        def trace(statement: str) -> None:
            self._writer_profile.trace(connection_id, statement)
            normalized = " ".join(statement.strip().upper().split())
            baseline_tail = (
                "FROM SESSIONS AS S" in normalized
                and "LEFT JOIN EVENTS AS E" in normalized
                and "ORDER BY E.SEQ DESC LIMIT 1" in normalized
            )
            fused_tail = (
                normalized.startswith("WITH WANTED(SESSION_ID) AS (VALUES")
                and "LATEST AS" in normalized
                and "JOIN SESSIONS AS S" in normalized
            )
            if baseline_tail or fused_tail:
                with self._tail_metric_lock:
                    self._tail_lookup_statements += 1

        # ProfiledEventStore installed its own callback. Replace it with a combined
        # callback so the diagnostic preserves writer attribution while counting tails.
        connection.set_trace_callback(trace)
        return connection

    def tail_lookup_statements(self) -> int:
        with self._tail_metric_lock:
            return int(self._tail_lookup_statements)


class BaselineTailStore(TailMetricMixin, writer_profile.ProfiledEventStore):
    pass


class FusedTailStore(TailMetricMixin, writer_profile.ProfiledEventStore):
    """Diagnostic set-based session-tail lookup; queueing remains unchanged."""

    @staticmethod
    def _group_tails(connection, session_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique_ids = list(dict.fromkeys(session_ids))
        if not unique_ids:
            return {}
        values = ",".join("(?)" for _ in unique_ids)
        rows = connection.execute(
            f"""WITH wanted(session_id) AS (VALUES {values}),
                latest AS (
                    SELECT e.session_id,MAX(e.seq) AS seq
                    FROM events AS e
                    JOIN wanted AS w ON w.session_id=e.session_id
                    GROUP BY e.session_id
                )
                SELECT s.session_id,latest.seq,e.hash
                FROM wanted AS w
                JOIN sessions AS s ON s.session_id=w.session_id
                LEFT JOIN latest ON latest.session_id=s.session_id
                LEFT JOIN events AS e
                  ON e.session_id=latest.session_id AND e.seq=latest.seq""",  # nosec B608 -- bounded local placeholders only
            unique_ids,
        ).fetchall()
        return {
            str(row["session_id"]): {"seq": row["seq"], "hash": row["hash"]}
            for row in rows
        }

    def _persist_append_group(self, batch):
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            ordered_ids = list(dict.fromkeys(str(request.session_id) for request in batch))
            tails = self._group_tails(connection, ordered_ids)
            # Preserve the base implementation's first-unknown-session failure order.
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


def make_request(session_id: str, index: int, *, event_type: str = "probe.event") -> _GroupedAppend:
    loop = asyncio.get_running_loop()
    return _GroupedAppend(
        session_id=session_id,
        event_type=event_type,
        payload={"index": index, "session_id": session_id},
        future=loop.create_future(),
    )


def stage(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == name), None)


def session_projection(store, session_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    with store._conn() as connection:
        rows = connection.execute(
            "SELECT seq,event_type,payload_json FROM events WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
    return [
        (int(row["seq"]), str(row["event_type"]), dict(json.loads(row["payload_json"])))
        for row in rows
    ]


def event_count(store, session_id: str) -> int:
    with store._conn() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM events WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return int(row["n"])


def seed_sessions(store, count: int, *, prefix: str) -> list[str]:
    session_ids = [f"{prefix}-{index:03d}" for index in range(count)]
    for session_id in session_ids:
        store.create_session(session_id)
        store.append(session_id, "seeded", {"session_id": session_id})
    return session_ids


async def semantic_probe(root: Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    projections: dict[str, Any] = {}
    for mode, store_type in (("baseline", BaselineTailStore), ("fused", FusedTailStore)):
        profile = writer_profile.WriterProfile()
        store = store_type(root / f"semantic-{mode}.db", profile)

        empty = f"{mode}-empty"
        store.create_session(empty)
        first = store._persist_append_group([make_request(empty, 1, event_type="first")])[0]

        repeated = f"{mode}-repeated"
        store.create_session(repeated)
        store.append(repeated, "seeded", {"index": 0})
        repeated_events = store._persist_append_group(
            [make_request(repeated, index, event_type="repeat") for index in (1, 2, 3)]
        )

        known = f"{mode}-known"
        store.create_session(known)
        store.append(known, "seeded", {"index": 0})
        before = event_count(store, known)
        unknown_error = None
        try:
            store._persist_append_group(
                [
                    make_request(known, 1, event_type="rollback"),
                    make_request(f"{mode}-missing", 2, event_type="rollback"),
                    make_request(known, 3, event_type="rollback"),
                ]
            )
        except Exception as exc:
            unknown_error = type(exc).__name__
        after = event_count(store, known)

        results[mode] = {
            "empty_session_first_seq": int(first.seq),
            "empty_session_prev_hash": str(first.prev_hash),
            "same_session_seqs": [int(event.seq) for event in repeated_events],
            "same_session_chain_valid": bool(store.verify_chain(repeated)),
            "unknown_error": unknown_error,
            "unknown_batch_rolled_back": before == after,
        }
        projections[mode] = {
            "empty": session_projection(store, empty),
            "repeated": session_projection(store, repeated),
            "known": session_projection(store, known),
        }

    comparable = {
        mode: {
            key: value
            for key, value in result.items()
            if key not in {"empty_session_prev_hash"}
        }
        for mode, result in results.items()
    }
    return {
        "baseline": results["baseline"],
        "fused": results["fused"],
        "behavior_equal": comparable["baseline"] == comparable["fused"],
        "projection_shapes_equal": {
            key: [(seq, event_type, payload.get("index")) for seq, event_type, payload in projections["baseline"][key]]
            == [(seq, event_type, payload.get("index")) for seq, event_type, payload in projections["fused"][key]]
            for key in projections["baseline"]
        },
    }


async def causal_arm(root: Path, mode: str, batch_size: int, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    store_type = BaselineTailStore if mode == "baseline" else FusedTailStore
    store = store_type(root / f"causal-{batch_size}-{experiment}-{mode}.db", profile)
    session_ids = seed_sessions(store, batch_size, prefix=f"{mode}-{batch_size}-{experiment}")

    profile.reset()
    store.reset_tail_metrics()
    started = time.perf_counter()
    for batch_index in range(CAUSAL_BATCHES):
        batch = [
            make_request(session_id, batch_index, event_type="causal.append")
            for session_id in session_ids
        ]
        writer_profile._timed(
            profile,
            "event.group_commit",
            lambda batch=batch: store._persist_append_group(batch),
        )
    wall = time.perf_counter() - started
    report = profile.report(CAUSAL_BATCHES)
    row = stage(report, "event.group_commit")
    chain_valid = all(store.verify_chain(session_id) for session_id in session_ids)
    expected_events = 1 + CAUSAL_BATCHES
    counts_valid = all(event_count(store, session_id) == expected_events for session_id in session_ids)
    return {
        "mode": mode,
        "batch_size": batch_size,
        "experiment": experiment,
        "transactions": int(row["transactions"]) if row else 0,
        "writer_hold_ms_total": float(row["writer_hold_ms_total"]) if row else 0.0,
        "operation_ms_total": float(row["operation_ms_total"]) if row else 0.0,
        "wall_seconds": round(wall, 4),
        "tail_lookup_statements": store.tail_lookup_statements(),
        "chain_valid": chain_valid,
        "event_counts_valid": counts_valid,
    }


def build_engine(db: Path, profile: writer_profile.WriterProfile, mode: str):
    sandbox = writer_profile.ActionSandbox()
    store_type = BaselineTailStore if mode == "baseline" else FusedTailStore
    events = store_type(db, profile)
    skills = writer_profile.ProfiledSkills(db, profile)
    harness = writer_profile.ProfiledHarness(db, profile, sandbox=sandbox)
    engine = writer_profile.EcomEvoEngine(
        db,
        plugin_overrides={
            "event.store": events,
            "memory.skills": skills,
            "evolver.harness": harness,
            "sandbox.action": sandbox,
        },
    )
    engine.autonomy.policy.routing = writer_profile.ProfiledRouting(db, profile)
    return engine, events


async def runtime_arm(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, events = build_engine(root / f"runtime-{experiment}-{mode}.db", profile, mode)
    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up chain invalid")

    profile.reset()
    events.reset_tail_metrics()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, RUNTIME_TASKS)
    wall = time.perf_counter() - started
    report = profile.report(RUNTIME_TASKS)
    row = stage(report, "event.group_commit")
    tx = int(row["transactions"]) if row else 0
    hold = float(row["writer_hold_ms_total"]) if row else 0.0
    return {
        "mode": mode,
        "experiment": experiment,
        "transactions": tx,
        "writer_hold_ms_total": hold,
        "writer_hold_ms_per_tx": round(hold / max(1, tx), 4),
        "wall_seconds": round(wall, 4),
        "tail_lookup_statements": events.tail_lookup_statements(),
        "chains_valid": all(summary.event_chain_valid for summary in summaries),
        "unattributed_transactions": int(report["unattributed_transactions"]),
    }


def median(rows: list[dict[str, Any]], key: str) -> float:
    return float(statistics.median(float(row[key]) for row in rows))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    causal: dict[int, dict[str, list[dict[str, Any]]]] = {
        size: {"baseline": [], "fused": []} for size in BATCH_SIZES
    }
    runtime: dict[str, list[dict[str, Any]]] = {"baseline": [], "fused": []}

    with tempfile.TemporaryDirectory(prefix="ecomevo-event-tail-fusion-") as tmp:
        root = Path(tmp)
        semantics = await semantic_probe(root)
        if not semantics["behavior_equal"] or not all(semantics["projection_shapes_equal"].values()):
            failures.append("set-based tail lookup changed append semantics")
        for mode in ("baseline", "fused"):
            result = semantics[mode]
            if result["empty_session_first_seq"] != 1 or result["empty_session_prev_hash"] != "GENESIS":
                failures.append(f"{mode}: empty-session append semantics changed")
            if result["same_session_seqs"] != [2, 3, 4] or not result["same_session_chain_valid"]:
                failures.append(f"{mode}: same-session batch ordering/hash chain changed")
            if result["unknown_error"] != "KeyError" or not result["unknown_batch_rolled_back"]:
                failures.append(f"{mode}: unknown-session rollback semantics changed")

        for batch_size in BATCH_SIZES:
            for experiment in range(EXPERIMENTS):
                order = ("baseline", "fused") if experiment % 2 == 0 else ("fused", "baseline")
                for mode in order:
                    causal[batch_size][mode].append(
                        await causal_arm(root, mode, batch_size, experiment)
                    )
                for mode in ("baseline", "fused"):
                    row = causal[batch_size][mode][-1]
                    if row["transactions"] != CAUSAL_BATCHES:
                        failures.append(
                            f"{mode} batch{batch_size}[{experiment}] tx changed: "
                            f"{row['transactions']} != {CAUSAL_BATCHES}"
                        )
                    if not row["chain_valid"] or not row["event_counts_valid"]:
                        failures.append(f"{mode} batch{batch_size}[{experiment}] chain/count invalid")
                baseline_lookups = causal[batch_size]["baseline"][-1]["tail_lookup_statements"]
                fused_lookups = causal[batch_size]["fused"][-1]["tail_lookup_statements"]
                if baseline_lookups != CAUSAL_BATCHES * batch_size:
                    failures.append(
                        f"baseline batch{batch_size}[{experiment}] tail selects: "
                        f"{baseline_lookups} != {CAUSAL_BATCHES * batch_size}"
                    )
                if fused_lookups != CAUSAL_BATCHES:
                    failures.append(
                        f"fused batch{batch_size}[{experiment}] tail selects: "
                        f"{fused_lookups} != {CAUSAL_BATCHES}"
                    )

        for experiment in range(EXPERIMENTS):
            order = ("baseline", "fused") if experiment % 2 == 0 else ("fused", "baseline")
            for mode in order:
                row = await runtime_arm(root, mode, experiment)
                runtime[mode].append(row)
                if not row["chains_valid"]:
                    failures.append(f"runtime {mode}[{experiment}] invalid event chain")
                if row["unattributed_transactions"]:
                    failures.append(
                        f"runtime {mode}[{experiment}] unattributed tx: "
                        f"{row['unattributed_transactions']}"
                    )

    comparisons: dict[str, Any] = {}
    for batch_size in BATCH_SIZES:
        baseline = causal[batch_size]["baseline"]
        fused = causal[batch_size]["fused"]
        comparisons[str(batch_size)] = {
            "tail_lookup_statement_ratio": round(
                median(fused, "tail_lookup_statements")
                / max(1.0, median(baseline, "tail_lookup_statements")),
                4,
            ),
            "writer_hold_total_ratio": round(
                median(fused, "writer_hold_ms_total")
                / max(0.001, median(baseline, "writer_hold_ms_total")),
                4,
            ),
            "operation_total_ratio": round(
                median(fused, "operation_ms_total")
                / max(0.001, median(baseline, "operation_ms_total")),
                4,
            ),
            "wall_ratio": round(
                median(fused, "wall_seconds")
                / max(0.0001, median(baseline, "wall_seconds")),
                4,
            ),
            "transactions_baseline": int(median(baseline, "transactions")),
            "transactions_fused": int(median(fused, "transactions")),
        }

    runtime_comparison = {
        "writer_hold_per_tx_ratio": round(
            median(runtime["fused"], "writer_hold_ms_per_tx")
            / max(0.001, median(runtime["baseline"], "writer_hold_ms_per_tx")),
            4,
        ),
        "wall_ratio": round(
            median(runtime["fused"], "wall_seconds")
            / max(0.0001, median(runtime["baseline"], "wall_seconds")),
            4,
        ),
        "tail_lookup_statements_ratio": round(
            median(runtime["fused"], "tail_lookup_statements")
            / max(1.0, median(runtime["baseline"], "tail_lookup_statements")),
            4,
        ),
        "median_transactions_baseline": int(median(runtime["baseline"], "transactions")),
        "median_transactions_fused": int(median(runtime["fused"], "transactions")),
    }

    return {
        "ok": not failures,
        "semantics": semantics,
        "causal_batches": CAUSAL_BATCHES,
        "batch_sizes": list(BATCH_SIZES),
        "experiments": EXPERIMENTS,
        "causal_comparison": comparisons,
        "runtime_tasks": RUNTIME_TASKS,
        "runtime_comparison": runtime_comparison,
        "runtime": runtime,
        "failures": failures,
    }


def main() -> int:
    result = asyncio.run(main_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
