# GrowthEvo Product Surface v1

GrowthEvo is the product layer on top of the existing EcomEvo Harness runtime. It turns the repository's causal decision, event sourcing, evaluation, shadow/canary and policy capabilities into an enterprise growth control plane.

## Run

```bash
python -m pip install -e .
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

Open:

- `http://localhost:8000/growth` — GrowthEvo responsive product UI / mobile PWA
- `http://localhost:8000/docs` — FastAPI schema
- `http://localhost:8000/` — existing EcomEvo business workspace

## What ships in this slice

### Product UI

The new `/growth` surface implements the first production-shaped GrowthEvo cockpit:

- Incremental Revenue / Incremental Profit / Incremental ROI / avoided-touch cost as first-class KPIs.
- `NO_TREATMENT` remains visible in the data model instead of being hidden behind a response-propensity score.
- Incrementality trend, channel contribution, active Campaign / Experiment / Canary table.
- Actionable Opportunity Map cards with evidence tier, uplift and support.
- Approval inbox and governed campaign draft flow.
- Growth Agent sidecar that produces structured candidate artifacts rather than chat-only text.
- Command palette (`⌘/Ctrl + K`).
- Responsive mobile layout with bottom navigation, full-screen Agent drawer and installable PWA shell.

The visual language intentionally stays close to Linear / Stripe / Ramp / modern data tooling: warm-white surfaces, one primary accent, low shadows, fine dividers, dense typography and restrained status colors.

### Backend control plane

`ecomevo.product.growth_center.GrowthCenter` adds a tenant-scoped SQLite/WAL product store for:

- daily incremental metrics;
- campaign / experiment candidates;
- approvals;
- Agent + Harness runs;
- evidence-facing decision logs.

The API is installed by `ecomevo.api.growth_routes`:

| Endpoint | Purpose |
|---|---|
| `GET /api/growth/dashboard` | KPI, series, campaign, approval and active-run snapshot |
| `GET /api/growth/opportunities` | causal opportunity cards |
| `GET/POST /api/growth/campaigns` | list/create candidate Campaigns |
| `POST /api/growth/campaigns/{id}/canary-request` | move a Draft into approval, never directly into external execution |
| `GET /api/growth/approvals` | approval queue |
| `POST /api/growth/approvals/{id}/decision` | approver-only Canary decision |
| `GET /api/growth/agent-runs` | Harness run list |
| `POST /api/growth/agent/plan` | compile a goal into governed candidate artifacts |
| `GET /api/growth/decisions` | decision/evidence log including NO_TREATMENT |

No route in this product surface sends a push, email, SMS, ad, coupon or other external side effect. The enforced progression is:

```text
Candidate
  -> Causal Evaluation
  -> Policy
  -> Guardrail
  -> Approval
  -> Canary
  -> existing governed connector/runtime authority
```

That boundary is deliberate: the LLM/Agent is allowed to propose objects, but it does not acquire production authority by producing a persuasive answer.

## Data mode

The first boot lazily seeds a **reference workspace** per tenant so the full UI and governance loop are inspectable without external credentials. The API returns `data_mode=reference_seed` and the UI labels it explicitly.

This is not presented as real business evidence. When warehouse/event connectors are wired, the reference metrics can be replaced without changing the page contract.

## 2025–2026 research decisions

The implementation follows recent work where it materially changes product architecture rather than merely adding paper names to a README.

### Causal decisioning and OPE

1. **Treatment Effect Estimation for Optimal Decision-Making — NeurIPS 2025**
   - https://proceedings.neurips.cc/paper_files/paper/2025/hash/c248154176c08147e82c0b30961604f7-Abstract-Conference.html
   - Key product implication: a CATE estimator that is excellent at effect estimation is not automatically optimal at the decision boundary. GrowthEvo therefore separates effect/evidence from the final policy objective and surfaces decision-oriented evidence.

2. **STITCH-OPE — NeurIPS 2025**
   - https://proceedings.neurips.cc/paper_files/paper/2025/hash/0a85f2414e354f9d61ffea5705a8bbf4-Abstract-Conference.html
   - Key product implication: off-policy evaluation must be treated as a first-class gate for policies that cannot be safely trialed at full traffic.

3. **Model Selection for Off-policy Evaluation — NeurIPS 2025**
   - https://proceedings.neurips.cc/paper_files/paper/2025/hash/3d4dc72d715bd6415d356293079adf3d-Abstract-Conference.html
   - Key product implication: OPE itself has model-selection risk; Evidence UI must retain method/version metadata rather than showing one unexplained score.

4. **Improved Offline Contextual Bandits with Second-Order Bounds — COLT 2025**
   - https://proceedings.mlr.press/v291/ryu25a.html
   - Key product implication: propensity/support and variance-aware confidence matter in small-data regimes; GrowthEvo keeps them in the decision/evidence contract.

### Agent memory and controlled evolution

1. **MemBench — ACL Findings 2025**
   - https://aclanthology.org/2025.findings-acl.989/
   - Product implication: memory evaluation must cover effectiveness, efficiency and capacity, not only retrieval demos.

2. **Agentic Memory — ACL 2026**
   - https://aclanthology.org/2026.acl-long.981/
   - Product implication: store/retrieve/update/summarize/discard are explicit memory operations. GrowthEvo's long-term architecture should model memory operations as governed tools, not hidden prompt concatenation.

3. **Memory-R1 — ACL 2026**
   - https://aclanthology.org/2026.acl-long.583/
   - Product implication: ADD / UPDATE / DELETE / NOOP-style memory actions support a future learned memory policy while preserving an auditable operation boundary.

4. **EvoMemBench — 2026**
   - https://arxiv.org/abs/2605.18421
   - Product implication: there is no universally best memory method and long-context baselines remain competitive. GrowthEvo therefore does not hard-wire one memory vendor or silently promote every reflection into durable memory.

## Open-source engineering references

These projects are references for architecture and interaction patterns; this PR does not vendor/copy their source code.

- **LangGraph** — resilient, long-running stateful agent orchestration: https://github.com/langchain-ai/langgraph
- **Temporal** — durable workflow/execution semantics: https://github.com/temporalio/temporal
- **OpenTelemetry GenAI semantic conventions** — agent/model/tool/memory trace naming: https://github.com/open-telemetry/semantic-conventions-genai
- **Mem0** — production-oriented agent memory layer: https://github.com/mem0ai/mem0
- **Letta** — stateful agent/memory architecture: https://github.com/letta-ai/letta
- **shadcn/ui** — accessible, composable application UI patterns: https://github.com/shadcn-ui/ui
- **TanStack Query** — server-state/cache interaction model: https://github.com/TanStack/query
- **Apache ECharts** — dense analytical visualization patterns: https://github.com/apache/echarts

For v1, the web surface intentionally remains dependency-light and uses the repository's existing static frontend packaging. This avoids introducing a second Node build/runtime before the product contracts stabilize. The component hierarchy, state boundaries and chart contracts are designed so they can later move to React/shadcn/TanStack/ECharts without changing the backend API.

## Next implementation slices

The current PR establishes a vertical slice rather than pretending every navigation item is already production-complete. The next slices should connect existing EcomEvo primitives behind the new product IA:

1. Evidence Center: CATE / support / propensity / OPE / locked holdout visualizations.
2. Harness Trace: OpenTelemetry-compatible spans for context, memory, model and tool operations.
3. Memory Center: explicit ADD/UPDATE/DELETE/NOOP governance, TTL, conflict and replay evaluation.
4. Campaign Builder: strategy -> creative -> experiment -> execution object graph.
5. Durable workflow adapter: migrate side-effecting long-running marketing workflows to a durable workflow engine or extend the current durable job implementation with equivalent retry/fencing/version guarantees.
6. Warehouse/event integrations: replace `reference_seed` metrics with real tenant data and maintain freshness/evidence provenance.

## Tests

`tests/test_growth_center.py` covers:

- incremental-first dashboard seed contract;
- Draft -> Approval -> Canary state transition;
- Agent goal -> structured candidates without side effects;
- FastAPI product route wiring.
