from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import pressure_gate as base


_ORIGINAL_EVENT_STORE_PROBE = base.run_event_store_contention_probe
_PERFORMANCE_FAILURE_PREFIXES = (
    "event-store contention wall time exceeded",
    "event-store throughput fell below",
    "event-store append p99 exceeded",
)


def run_event_store_contention_probe(
    root: Path,
    events: int = 256,
    workers: int = 16,
    experiments: int = 3,
) -> dict[str, Any]:
    """Keep the existing envelopes, but judge hosted-runner performance by a median.

    The base probe still validates sequence uniqueness, hash-chain integrity, checkpoint
    binding, and session inspection on every independent SQLite database. Only the three
    timing/throughput verdicts are aggregated across samples so one noisy-neighbor spike
    cannot fail an otherwise healthy PR.
    """
    if experiments < 3 or experiments % 2 == 0:
        raise ValueError("experiments must be an odd integer >= 3")

    failures: list[str] = []
    samples: list[dict[str, Any]] = []
    for experiment in range(experiments):
        sample_root = root / f"event-store-contention-sample-{experiment}"
        sample_root.mkdir(parents=True, exist_ok=True)
        row = _ORIGINAL_EVENT_STORE_PROBE(sample_root, events=events, workers=workers)

        for failure in row["failures"]:
            if not str(failure).startswith(_PERFORMANCE_FAILURE_PREFIXES):
                failures.append(f"sample={experiment}: {failure}")

        samples.append(
            {
                "wall_seconds": float(row["wall_seconds"]),
                "throughput_events_per_second": float(row["throughput_events_per_second"]),
                "append_latency_ms": {
                    key: float(value) for key, value in row["append_latency_ms"].items()
                },
            }
        )

    wall = statistics.median(sample["wall_seconds"] for sample in samples)
    throughput = statistics.median(
        sample["throughput_events_per_second"] for sample in samples
    )
    latency = {
        key: statistics.median(sample["append_latency_ms"][key] for sample in samples)
        for key in ("p50", "p95", "p99")
    }

    # Preserve the exact existing regression envelopes. #39 healthy hosted-runner samples
    # were ~760-950 events/s with p99 below 200ms, while rejected #41 was ~105 events/s
    # with p99 above 1s. Median sampling removes transient host noise without weakening
    # those boundaries for sustained regressions.
    if wall >= 1.6:
        failures.append(f"event-store contention median wall time exceeded 1.6s: {wall:.3f}s")
    if throughput < 160.0:
        failures.append(
            "event-store median throughput fell below 160 events/s: "
            f"{throughput:.1f}"
        )
    if latency["p99"] >= 800.0:
        failures.append(
            "event-store median append p99 exceeded 800ms: "
            f"{latency['p99']:.3f}ms"
        )

    return {
        "name": "event_store_contention",
        "events": events,
        "workers": workers,
        "experiments": experiments,
        "aggregation": "median",
        "wall_seconds": round(wall, 4),
        "throughput_events_per_second": round(throughput, 1),
        "append_latency_ms": {key: round(value, 3) for key, value in latency.items()},
        "samples": samples,
        "failures": failures,
    }


base.run_event_store_contention_probe = run_event_store_contention_probe


if __name__ == "__main__":
    raise SystemExit(base.main())
