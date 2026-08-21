# Contributing to EcomEvo

Thank you for improving EcomEvo. Changes are reviewed as product-runtime changes, not only as model or prompt changes.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,e2e,security]'
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## Before opening a pull request

```bash
python -m compileall -q ecomevo
pytest -q
python scripts/eval_gate.py
python scripts/e2e_smoke.py
python scripts/pressure_gate.py
pip-audit --strict --progress-spinner off .
bandit -q -r ecomevo -ll
```

Run the Chromium gate when the change affects UI, WebSocket behavior, task lifecycle, or browser-visible state.

## Pull request expectations

- Start from current `main` and keep one coherent change per pull request.
- Explain the user problem, architecture impact, failure semantics, rollback plan, and verification evidence.
- Add a regression test for every bug fix.
- Do not weaken evidence, RBAC, approval, Sandbox, Verifier, lease fencing, or event-chain checks to make a test pass.
- Never commit secrets, real customer evidence, generated runtime databases, or provider/MCP credentials.
- Update README and the relevant document when behavior, configuration, verification, or production boundaries change.

## Architecture invariants

The model is not the business-state controller. Tool calls cross the Runtime boundary; evidence-incomplete work cannot be presented as verified; high-impact actions remain separate from cognition; and execution outcomes must be durably audited. Harness evolution may patch cognitive components, but not authority components.

Security reports must follow [SECURITY.md](SECURITY.md), not the public issue tracker.
