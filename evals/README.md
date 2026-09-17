# Gold Set evaluation fixtures

`gold_set.jsonl` is the engineering copy of the deterministic business regression set used by `scripts/eval_gate.py`.

The executable product copy is packaged at `ecomevo/evals/gold_set.jsonl` so wheel/container installs can run the same evaluation without depending on the repository layout. Tests require both copies to have the same SHA-256 hash; changes to the Gold Set must update both in one pull request.

The shared evaluator lives in `ecomevo/evaluation.py`. The CLI gate and the admin Test Center call the same `validate()` logic rather than maintaining separate promotion criteria.

Each evaluation runs every case twice against an isolated temporary runtime database: once from fresh priors and once after routing/skill/evolution state has been persisted. Both passes must preserve evidence completeness, stop-state, tool-budget, event-chain, and side-effect authority invariants.

The admin Test Center is served at `/api/runtime/evaluations/ui`. Completed runs are stored as append-only snapshots in `evaluation.db`; the v1 API exposes no update/delete/promotion endpoint and never points the evaluator at the production runtime database.

This is a product safety/promotion gate, not a competitor or model benchmark.
