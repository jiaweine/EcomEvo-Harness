from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFETY = (ROOT / "frontend/safety-guards.js").read_text(encoding="utf-8")


def test_active_turn_runtime_pulse_does_not_show_previous_turn_metrics():
    assert "function turnBusy()" in SAFETY
    assert "label === '处理中'" in SAFETY
    assert "'证据状态': '本轮查证中'" in SAFETY
    assert "'工具预算': '—'" in SAFETY
    assert "'自主步骤': '—'" in SAFETY
    assert "'停止原因': '本轮处理中'" in SAFETY
    assert "'运行模式': '受控运行中'" in SAFETY
    assert "等待本轮运行数据" in SAFETY


def test_action_execution_busy_state_is_not_mistaken_for_agent_turn_busy():
    assert "chip.innerHTML = '<i></i><b>执行中</b>'" in SAFETY
    turn_busy = SAFETY.split('function turnBusy()', 1)[1].split('function isFileDrag', 1)[0]
    assert "label === '处理中'" in turn_busy
    assert "执行中" not in turn_busy
