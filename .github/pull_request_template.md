## Problem and outcome

Describe the user-visible problem and the verified outcome.

## Runtime impact

- Components changed:
- Event/state/schema impact:
- Side effects and authority impact:
- Rollback or compatibility plan:

## Verification

- [ ] Regression test added or rationale provided
- [ ] `pytest -q`
- [ ] Gold Set / smoke gate where relevant
- [ ] Pressure or concurrency gate where relevant
- [ ] Chromium E2E where relevant
- [ ] Security and dependency audit
- [ ] README/docs updated where behavior changed

## Safety invariants

- [ ] No secrets or customer evidence are included
- [ ] Evidence-incomplete tasks cannot produce executable actions
- [ ] High-impact actions still require explicit approval
- [ ] Learning changes do not expand RBAC, Sandbox, Verifier, or tool authority
