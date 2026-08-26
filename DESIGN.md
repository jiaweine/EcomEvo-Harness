---
version: 2.0
name: EcomEvo Customer Service UI
description: "Customer-facing commerce service interface inspired primarily by Intercom's calm customer-service design language, with Claude-like editorial warmth and restrained Clay-like friendly accents. Internal Agent/Runtime terminology stays in code, never in customer-facing copy."
source_inspiration:
  - "VoltAgent/awesome-design-md · intercom"
  - "VoltAgent/awesome-design-md · claude"
  - "VoltAgent/awesome-design-md · clay"
implementation_scope: "frontend presentation only"
---

# EcomEvo Customer Service Design System

## 1. Product position

EcomEvo is presented to customers as a **business service assistant**, not as an Agent Runtime, AI research system, developer console, observability platform, or model-control interface.

Customers should understand the interface without knowing anything about LLMs, Agents, Runtime, Event Sourcing, Verifiers, Plugins, Providers, provenance, traces, planners, generations, contracts, or sandboxes.

The customer should be able to answer four questions at a glance:

1. What can I do here?
2. What is happening with my request?
3. Do I need to provide anything else?
4. Is there anything I need to confirm?

The product may remain technically sophisticated internally. The UI must translate that sophistication into simple service language.

## 2. Non-negotiable implementation boundary

This design system is a **presentation contract only**.

When implementing it:

- DO NOT change algorithms.
- DO NOT change Agent planning or execution logic.
- DO NOT change API routes, payload schemas, event types, IDs, status codes, permissions, authorization, sandbox behavior, verifier behavior, provider selection logic, persistence, or backend data models.
- DO NOT rename internal JavaScript variables merely to match customer copy.
- DO NOT rename backend fields such as `runtime`, `evidence`, `provider`, `plugin`, `trace`, `action`, or `contract_valid`.
- DO translate those concepts at the final presentation layer before the customer sees them.
- Tests may assert customer-visible wording, but business semantics must remain unchanged.

Internal engineering vocabulary is allowed in source code, logs, developer documentation, and admin-only diagnostics. It is not allowed in the normal customer experience.

## 3. Design inspiration

### Primary reference: Intercom

Use the public Intercom DESIGN.md in `VoltAgent/awesome-design-md` as the main visual reference.

Borrow these characteristics:

- warm cream canvas instead of cold enterprise gray;
- white product surfaces;
- charcoal primary text;
- thin warm-gray borders;
- modest 8–16px radii;
- product-led layout rather than decorative dashboard chrome;
- calm, helpful, conversational hierarchy;
- restrained shadows;
- one clear action hierarchy;
- generous reading comfort without becoming a marketing landing page.

Do **not** copy Intercom branding, logos, proprietary fonts, or Fin Orange as EcomEvo's brand identity.

### Secondary reference: Claude

Borrow only:

- calm editorial rhythm;
- comfortable reading width;
- human, non-technical tone;
- warm surfaces;
- lower visual aggression.

Do not turn EcomEvo into an editorial website or use large serif typography inside the application workspace.

### Accent reference: Clay

Borrow only small amounts of friendly energy for:

- welcome shortcuts;
- empty states;
- onboarding;
- soft category accents.

Do not use claymation, mascots, saturated rainbow grids, or playful illustration as the dominant application language.

## 4. Customer language principle

### Core rule

**Describe what the customer can do or what is happening to their request. Never describe how the internal system is implemented.**

Bad:

> Runtime is replanning after verifier failure.

Good:

> 正在重新核对信息，请稍候。

Bad:

> Evidence provenance is derived from tool results.

Good:

> 这条信息由系统根据已提交资料整理。

Bad:

> Authority required.

Good:

> 需要你确认后才能继续。

## 5. Required terminology map

Customer-facing surfaces must use the right column.

| Internal / technical term | Customer-facing term |
|---|---|
| Runtime | 服务 / 处理服务 |
| Agent | 助手 / 系统 |
| Planner | 处理计划 / 下一步安排 |
| Adaptive Planner | 自动安排下一步 |
| Recursive Agent | 并行处理 / 分步处理 |
| Event Trace | 办理进度 |
| Trace | 记录 / 编号 |
| Evidence | 相关资料 / 参考信息 |
| Evidence Audit | 资料说明 |
| Provenance | 来源 / 类型 |
| Original evidence | 你提供的 |
| Derived evidence | 系统整理的 |
| Authority | 需要确认 |
| Action | 操作 / 下一步 |
| Proposed action | 待确认操作 |
| Plugin | 功能 / 服务能力 |
| Plugin Runtime | 服务状态 |
| Contract / Contract Valid | 检查结果 / 服务正常 |
| Provider | 处理方式 / 可用服务 |
| Cognition engine | 处理方式 |
| Sandbox | 安全保护 |
| Verifier | 结果核对 |
| Generation | 版本 / omit if unnecessary |
| State | 当前信息 / 当前进度 |
| Goal | 你的问题 / 处理目标 |
| Replan | 重新核对 / 重新安排 |
| Rollback | 已恢复到安全状态 |
| Tool | 已接入资料 / 外部服务 |
| Event Sourced | omit |
| Graph / dependency graph | 服务组成, only when needed |

### Terms that should normally never appear to customers

- Runtime
- Agent Runtime
- Event Sourced
- Event Trace
- Graph
- Planner
- Verifier
- Plugin
- Contract
- Generation
- Sandbox
- Provenance
- Authority
- Cognition
- Recursive
- Tool Call
- MCP
- PTC
- Harness

If a term is required for a support or admin workflow, place it behind an explicitly technical/admin surface, not in normal customer navigation.

## 6. Voice and tone

Customer copy should be:

- clear;
- calm;
- specific;
- helpful;
- short;
- action-oriented;
- honest about missing information;
- explicit before important changes.

Avoid:

- academic language;
- AI hype;
- infrastructure language;
- unnecessary English labels;
- robotic status narration;
- claims of certainty when the data is incomplete;
- wording that makes automated recommendations sound like already-executed business decisions.

### Sentence patterns

Prefer:

- `请补充品牌授权书首页。`
- `这条信息来自你上传的资料。`
- `我们还缺少物流签收记录。`
- `需要你确认后才能继续。`
- `当前资料已经核对完成。`
- `你可以继续补充说明或上传资料。`

Avoid:

- `Verifier detected an evidence gap.`
- `Runtime entered needs_evidence state.`
- `Authority gate pending.`
- `Tool execution succeeded.`

## 7. Information architecture

Preserve the current three-area product structure, but present it in customer language.

### Left: business navigation

Purpose:

- choose a business scenario;
- start a new task;
- reopen a recent task.

Customer labels:

- 新建任务
- 业务场景
- 最近任务
- 服务状态

The left side must not look like a developer sidebar.

### Center: service workspace

Purpose:

- understand the request;
- communicate with EcomEvo;
- upload materials;
- read the result;
- continue the conversation.

The center is the primary customer experience.

### Right: processing details

Use exactly this mental model:

- 进度
- 相关资料
- 待确认
- 已上传

Do not label it `Evidence & Authority`, `Trace`, or `Control Plane`.

## 8. Final homepage copy

### Product name

**EcomEvo 业务服务助手**

### Welcome title

**今天想处理什么问题？**

### Welcome description

**把情况和相关资料告诉我，我会帮你核对重点、提示缺少的信息，并给出下一步建议。**

### Shortcut cards

#### 商品治理

Title: `核对商品`

Description: `检查商品信息、素材和资质`

#### 商家审核

Title: `审核商家`

Description: `核对主体、授权和相关风险`

#### 售后处理

Title: `处理售后`

Description: `结合订单和资料梳理处理建议`

#### 风险核查

Title: `核查异常`

Description: `核对异常交易和相关信息`

### Upload hint

Label: `可上传`

Types: `图片 · 视频 · 音频 · 文档 · 表格 · 日志`

## 9. Customer-facing process copy

The welcome-side process panel should say:

### 办理流程

1. **说明问题** — 告诉我们你想处理什么
2. **核对资料** — 整理与问题相关的信息
3. **补充信息** — 缺少内容时会及时提醒
4. **给出建议** — 把重点和下一步说清楚
5. **确认操作** — 重要变更由你决定是否继续

Footer:

`根据实际情况自动推进`

`重要操作会先征得你的确认`

Never show STATE / PLAN / ACT / GATE / EVOLVE on the customer surface.

## 10. Composer copy

Header:

**补充说明或资料**

Helper:

**可以随时继续添加**

Placeholder:

**例如：这笔退款该怎么处理？订单、物流、聊天记录和图片都在这里，请帮我核对清楚。**

Footer note:

**重要操作会先请你确认，再执行。**

The composer should feel like a helpful service input, not a command terminal.

## 11. Right panel copy

### Panel title

**处理详情**

Subtitle:

**进度 · 资料 · 待确认**

### Tab 1: 进度

Status label: `当前进度`

Default state: `等待开始`

Default description:

`描述问题或先上传资料，我会帮你继续处理。`

Progress section title:

`办理进度`

Follow-up section:

- title: `继续补充`
- helper: `你可以接着说明`

### Tab 2: 相关资料

Title:

**与结果相关的资料**

Description:

**这里会整理影响当前结果的资料和信息，并保留来源方便查看。**

Summary labels:

- 全部
- 你提供的
- 系统整理的

Per-item metadata:

- 来源
- 类型
- 编号

### Tab 3: 待确认

Title:

**需要你确认**

Description:

**涉及重要变更时，会先说明影响，再由你决定是否继续。**

Metadata labels:

- 状态
- 确认
- 影响

### Tab 4: 已上传

Title:

**你上传的资料**

Description:

**可以随时继续补充，后续处理会继续使用这些资料。**

## 12. Result-message writing format

Whenever practical, customer-visible assistant answers should use this reading order:

### 当前结果

Say the conclusion in plain language.

### 我参考了什么

Name the important submitted material or information that influenced the result.

### 还缺什么

Only show this section when information is actually missing.

### 下一步

Tell the customer exactly what they can do next.

Do not force these headings into every short reply. Use them when they improve understanding.

Do not expose chain-of-thought, hidden reasoning, internal planning steps, or tool-call narration.

## 13. Service status copy

The existing technical runtime modal may remain functionally backed by `/api/runtime`, but its normal customer surface should be simplified.

Navigation label:

**服务状态**

Title:

**当前服务是否正常**

Description:

**这里展示当前服务的可用情况，不影响你的任务内容和处理结果。**

Customer categories:

- 任务信息
- 智能处理
- 资料连接
- 安全保护

Customer states:

- 服务正常
- 部分服务需检查
- 正在准备
- 可用
- 待启用

Detailed plugin catalogs, API versions, generations, contract names, implementation sources, and package names should be hidden from the default customer surface.

## 14. Onboarding copy

Kicker:

**ECOMEVO 使用指南**

Subtitle:

**更简单地处理电商业务问题**

Title:

**把问题和资料交给我，重要操作由你确认。**

Lead:

**同一个任务里可以持续补充资料和追问，处理进度随时可看。**

Three steps:

1. **说明问题** — 告诉我们你遇到的情况和希望得到的结果。
2. **上传资料** — 加入图片、文档、表格或相关记录。
3. **查看并确认** — 先看处理建议，重要操作再由你决定。

Usage notes:

- 处理结果会尽量说明参考了哪些资料。
- 图片、音频和文档的处理能力以当前可用服务为准。
- 涉及重要业务变更时，不会在你不知情的情况下执行。

## 15. Visual theme

### Atmosphere

The application should feel:

- warm;
- calm;
- trustworthy;
- modern;
- helpful;
- customer-service oriented;
- professional without looking bureaucratic.

It should not feel:

- like an observability dashboard;
- like a terminal;
- like a SOC/NOC control room;
- like an AI research demo;
- like an admin console;
- like a marketing landing page.

## 16. Color system

Adapt Intercom's warm surface strategy while keeping an original EcomEvo accent.

```css
:root {
  --customer-canvas: #f5f1ec;
  --customer-surface: #ffffff;
  --customer-surface-soft: #eeeae4;
  --customer-ink: #171717;
  --customer-muted: #66625d;
  --customer-subtle: #8a857f;
  --customer-line: #d8d1c8;
  --customer-line-soft: #e9e3dc;

  --customer-accent: #2f67d8;
  --customer-accent-hover: #2457bd;
  --customer-accent-soft: #edf3ff;

  --customer-success: #1f8a52;
  --customer-warning: #b56a13;
  --customer-danger: #c74646;
}
```

### Color rules

- Warm cream is the application floor.
- White is the primary working surface.
- Charcoal is the main text color.
- EcomEvo blue is used for primary interaction, focus, links, and selected state.
- Do not use Intercom's Fin Orange as EcomEvo branding.
- Pastel colors may appear only as subtle category accents on onboarding/shortcut surfaces.
- Red is reserved for error/destructive states.
- Green is reserved for successful/complete states.
- Amber is reserved for warning or attention.

## 17. Typography

Use the existing CJK-first font stack.

```css
--font-ui: "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
  "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
  system-ui, -apple-system, "Segoe UI", sans-serif;
```

Do not import or redistribute Intercom's proprietary Saans font or Claude proprietary fonts.

Recommended hierarchy:

| Role | Size | Weight | Use |
|---|---:|---:|---|
| Welcome title | 34–44px | 600 | New task page |
| Task title | 24–28px | 600 | Current task |
| Section title | 16–20px | 600 | Main content sections |
| Card title | 14–16px | 600 | Service cards |
| Body | 14–16px | 400 | Customer reading |
| Helper | 12–14px | 400 | Secondary explanation |
| Caption | 11–12px | 400 | Time/source/metadata |

Do not use monospace typography on customer-facing labels unless displaying an actual identifier the customer may need.

## 18. Shape and elevation

Inspired by Intercom's modest card system:

- controls: 8–10px radius;
- cards: 10–14px radius;
- major modal/container: 16–18px radius;
- avoid pill-heavy UI;
- thin warm-gray borders are preferred;
- normal cards use little or no shadow;
- use soft shadow only for modal, composer, floating drawer, or a genuinely elevated surface.

Do not return to ultra-flat 2px enterprise rectangles everywhere. The customer interface should feel friendlier than the previous Carbon control-console treatment.

## 19. Welcome shortcuts

Use white cards on warm canvas.

A small category accent is allowed at the top edge:

- product: soft blue;
- merchant: soft lavender;
- after-sales: soft peach;
- risk: soft mint.

These are navigation cues, not semantic status colors.

Do not fill the entire cards with saturated colors.

## 20. Messages

Assistant messages:

- white surface;
- comfortable reading width;
- 14–16px body;
- 1.7–1.8 line height for Chinese;
- small EcomEvo identity line;
- subtle border;
- no technical provider/status narration unless the customer explicitly opens service information.

User messages:

- soft EcomEvo blue tint;
- dark text;
- moderate radius;
- clearly distinct from assistant messages without looking like a social messenger.

## 21. Accessibility

- visible keyboard focus;
- minimum 40px touch target on mobile;
- status meaning never relies only on color;
- readable Chinese text at all breakpoints;
- dialogs must trap focus;
- drawers must expose expanded state;
- reduced-motion preferences must continue to work;
- customer copy must remain understandable without icons.

## 22. Responsive behavior

### Desktop

Keep the existing left navigation + center task + right details layout.

### Medium screens

- right details may become a drawer;
- customer should still be able to see progress and confirmation state;
- do not expose hidden technical UI just because a layout collapses.

### Mobile

Priority order:

1. current task/result;
2. composer;
3. progress;
4. related materials;
5. confirmation actions;
6. navigation.

Do not let service-status diagnostics dominate mobile space.

## 23. Do

- Translate implementation complexity into simple customer outcomes.
- Explain exactly what is missing when information is incomplete.
- Tell the customer what happens next.
- Keep important confirmation boundaries visible.
- Preserve the customer's submitted materials and source visibility.
- Use warm surfaces and friendly spacing.
- Keep EcomEvo's blue as the interaction accent.
- Use real backend state; never invent customer-facing progress or metrics.

## 24. Do not

- Do not show academic or infrastructure vocabulary in normal UI.
- Do not use `Runtime`, `Agent`, `Plugin`, `Verifier`, `Trace`, `Authority`, `Contract`, or `Provenance` as customer labels.
- Do not rename backend/API concepts just to satisfy visual copy.
- Do not expose chain-of-thought or internal planning.
- Do not fabricate completion percentages, confidence values, or SLAs.
- Do not copy Intercom, Claude, or Clay branding 1:1.
- Do not use proprietary fonts or logos from reference products.
- Do not turn the app into a colorful marketing page.
- Do not hide a required customer confirmation behind visual minimalism.

## 25. Agent implementation prompt

Use this when an AI coding agent edits the EcomEvo frontend:

> Read `DESIGN.md` before editing customer-facing UI. Preserve all existing algorithms, API routes, backend schemas, runtime behavior, permissions, action confirmation semantics, event IDs, and business logic. Treat technical names such as Runtime, Evidence, Plugin, Provider, Verifier, Trace, Authority and Contract as internal implementation concepts. Translate them only at the presentation layer into plain customer language. Follow the warm Intercom-inspired surface system, CJK-first typography, EcomEvo blue interaction accent, modest 8–16px radii, white cards on warm cream canvas, and calm customer-service tone. Never expose hidden reasoning or technical orchestration details to customers.

## 26. Reference rationale

`VoltAgent/awesome-design-md` defines DESIGN.md as an agent-readable design contract containing visual theme, semantic colors, typography, components, layout, depth, responsive behavior, and guardrails.

For EcomEvo:

- **Intercom** is the primary reference because its product language is closest to customer service: warm canvas, quiet chrome, white product cards, readable hierarchy, and modest radii.
- **Claude** contributes warmth and reading comfort.
- **Clay** contributes only small friendly category accents for onboarding and shortcuts.

The result must remain recognizably EcomEvo rather than becoming a clone of any reference brand.
