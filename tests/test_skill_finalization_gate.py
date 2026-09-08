from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def gate():
    path = Path(__file__).resolve().parents[1] / "scripts" / "skill_finalization_gate.py"
    spec = importlib.util.spec_from_file_location("skill_finalization_gate_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def samples(gate, walls=None, lag=0.1):
    walls = [1.0] * len(gate.PAIR_ORDERS) if walls is None else walls
    result = []
    for order, wall in zip(gate.PAIR_ORDERS, walls):
        pair = {"order": list(order)}
        for mode in order:
            pair[mode] = {
                "mode": mode,
                "tasks": gate.TASKS,
                "writer_transactions": gate.TASKS,
                "policy_updates": gate.TASKS,
                "wall_seconds": 0.2 * (wall if mode == "async" else 1),
                "heartbeat_ms": {"max": 100 * (lag if mode == "async" else 1)},
                "failures": [],
            }
        result.append(pair)
    return result


def test_original_wall_limit_stays_inclusive(gate):
    assert gate.summarize_samples(samples(gate, [1.75] * 6))["ok"]


def test_unrounded_wall_failure_is_not_hidden_by_display_precision(gate):
    result = gate.summarize_samples(samples(gate, [1.75001] * 6))
    assert not result["ok"]
    assert any("wall regression" in failure for failure in result["failures"])


def test_all_pairs_participate_in_median(gate):
    result = gate.summarize_samples(samples(gate, [1, 1.1, 1.2, 2.4, 2.5, 2.6]))
    assert not result["ok"]
    assert result["comparison"]["async_to_sync_wall_ratio"] == 1.8
    assert len(result["samples"]) == 6


def test_one_semantic_failure_cannot_be_averaged_away(gate):
    data = samples(gate)
    data[0]["async"]["failures"] = ["durable update mismatch"]
    result = gate.summarize_samples(data)
    assert not result["ok"]
    assert "sample 1: durable update mismatch" in result["failures"]


def test_one_writer_count_mismatch_fails(gate):
    data = samples(gate)
    data[0]["async"]["writer_transactions"] -= 1
    result = gate.summarize_samples(data)
    assert not result["ok"]
    assert not result["comparison"]["writer_transactions_equal"]


def test_original_lag_limit_is_enforced(gate):
    assert gate.summarize_samples(samples(gate, lag=0.35))["ok"]
    assert not gate.summarize_samples(samples(gate, lag=0.35001))["ok"]


def test_incomplete_or_reordered_data_fails_closed(gate):
    data = samples(gate)
    assert not gate.summarize_samples(data[:-1])["ok"]
    data[0]["order"].reverse()
    assert not gate.summarize_samples(data)["ok"]


def test_measurement_finishes_every_fixed_pair(gate, monkeypatch):
    calls = []
    expected = samples(gate)

    async def measure(mode, root):
        index = int(root.name)
        calls.append((index, mode))
        return expected[index][mode]

    monkeypatch.setattr(gate, "measure", measure)
    result = asyncio.run(gate.main_async())
    assert result["ok"]
    assert calls == [(i, mode) for i, order in enumerate(gate.PAIR_ORDERS) for mode in order]
    assert len(result["samples"]) == 6
