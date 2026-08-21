from pathlib import Path

from scripts.eval_gate import load_cases


def test_gold_set_is_nonempty_and_covers_all_product_domains():
    root = Path(__file__).resolve().parents[1]
    cases = load_cases(root / "evals" / "gold_set.jsonl")
    domains = {case["domain"] for case in cases}
    assert domains == {
        "product_governance", "merchant_review", "aftersales", "risk_review", "content_audit"
    }
    assert any(case["expected_status"] == "completed" for case in cases)
    assert any(case["expected_status"] == "needs_evidence" for case in cases)
    assert len({case["id"] for case in cases}) == len(cases)
