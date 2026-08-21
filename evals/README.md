# Gold Set evaluation fixtures

`gold_set.jsonl` is a deterministic business regression set used by `scripts/eval_gate.py`.

The gate runs every case twice against the same persisted runtime database: once from fresh priors and once after routing/skill/evolution state has been persisted. Both passes must preserve evidence completeness, stop-state, tool-budget, event-chain, and side-effect authority invariants.

This is a product safety/promotion gate, not a competitor or model benchmark.
