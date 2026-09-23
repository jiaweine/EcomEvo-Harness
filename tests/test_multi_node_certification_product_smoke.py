from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_multi_node_certification_contract_product_smoke():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "multi_node_certification_contract_smoke.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "multi_node_certification_contract" in completed.stdout
    assert "complete_not_executed" in completed.stdout
    assert "multi_node_certification_passed" in completed.stdout
    assert "False" in completed.stdout
