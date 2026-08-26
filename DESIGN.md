---
version: 2.0
name: EcomEvo Customer Service UI
description: "Customer-first commerce service workspace inspired by Intercom conversational clarity, Claude editorial calm, and a small amount of Clay warmth. Plain-language, reassuring, action-oriented, CJK-first."
source_inspiration: "VoltAgent/awesome-design-md · Intercom + Claude + Clay"
---

# EcomEvo Design System

## 1. Product character

EcomEvo is presented to customers as a clear, trustworthy business service workspace.

Customers should never need to understand how the underlying AI, agent, runtime, verifier, planner, plugin, model, trace, event sourcing, or post-training system works. The product may use these internally, but the interface must translate them into ordinary business language.

The customer should understand four things within a few seconds:

1. What can I do here?
2. What is happening with my request now?
3. Is anything missing from me?
4. What should I do next?

The interface should feel helpful, calm, modern, and professional — closer to a premium customer-service product than a developer console.

## 2. Reference direction

Use a blended design direction based on public references in `VoltAgent/awesome-design-md`:

- **Intercom** for friendly blue, conversational UI patterns, approachable service interactions, and obvious next actions.
- **Claude** for clean editorial hierarchy, comfortable reading width, calm spacing, and human-feeling copy presentation.
- **Clay** only for restrained warmth in the welcome page, onboarding, empty states, and selected starter cards.

Do not make the product look like the source brands and do not use their logos or proprietary identity. These are design references only.

Do not use VoltAgent's terminal-native, developer-first visual language for customer-facing screens.

## 3. Customer-language rule

This is a hard product rule.

### Never expose these words in normal customer UI

- Runtime
- Agent
- Plugin
- Verifier
- Planner
- Trace
- Event Trace
- Event Sourced
- Provenance
- Authority
- Contract
- Generation
- Sandbox
- Model
- Provider
- Tool Call
- Belief State
- Recursive Agent
- Harness
- Graph
- Cognition
- Execution Plane
- Safety Plane

These words may exist in source code, developer documentation, logs, admin diagnostics, and internal APIs. They should not appear in the normal customer journey.

### Translate system concepts into customer concepts

| Internal concept | Customer-facing wording |
|---|---|
| Runtime | 服务 / 处理服务 |
| Runtime health | 服务状态 |
| Agent execution | 正在处理 |
| Event Trace / Trace | 办理进度 / 处理记录 |
| Evidence | 相关资料 / 判断依据 |
| Provenance | 来源 |
| Authority | 待您确认 |
| Action | 下一步 / 待确认操作 |
| Plugin | 功能 / 服务组件 |
| Provider / Model | 处理方式 |
| Contract valid | 服务正常 |
| Blocked | 暂时无法继续 |
| Needs evidence | 需要补充资料 |
| Derived evidence | 系统整理 |
| Uploaded evidence | 您提交的 |
| Asset | 资料 / 已上传资料 |
| Verification | 核对 |

## 4. Writing principles

Customer copy must be:

- short;
- specific;
- action-oriented;
- free of engineering vocabulary;
- reassuring without sounding childish;
- explicit when customer action is required.

Prefer:

> 还需要一份品牌授权书，请补充上传。

Over:

> Evidence insufficient. Verification is blocked.

Prefer:

> 我们正在核对您提交的资料。

Over:

> Runtime is executing the verification pipeline.

Prefer:

> 请确认是否继续退款。

Over:

> Authority required for the proposed action.

## 5. Tone

Use warm professional Chinese.

Good tone:

- “我们已收到您的资料。”
- “还需要补充一项信息。”
- “您可以继续上传，之前的内容会保留。”
- “这一步会影响订单状态，请您确认后再继续。”

Avoid:

- academic explanation;
- implementation details;
- exaggerated AI language;
- “智能体正在自主推理” type copy;
- overly formal legalistic language unless legally required;
- cute or childish wording in serious disputes or risk cases.

## 6. Core information architecture

The existing three-column structure may remain, but customer semantics change.

### Left: business navigation

Purpose: let customers choose what they want to handle.

Preferred labels:

- 商品问题
- 商家认证
- 售后处理
- 风险问题
- 内容问题
- 最近办理

Do not describe architecture or system layers here.

### Center: request and conversation

Purpose: explain the current result in plain language and let the customer continue the request.

A result should normally follow this reading order:

1. **当前结果** — what we know now.
2. **还需要您补充** — only when something is missing.
3. **判断依据** — concise supporting information.
4. **下一步** — what the customer can do now.

Not every response needs all four sections. Never create empty sections.

### Right: status and supporting information

Use these tabs:

- **进度**
- **资料**
- **待确认**
- **已上传**

Do not use “轨迹 / 证据 / 执行 / Authority / Assets”.

## 7. Welcome page

### Primary headline

> 您好，今天想处理什么？

### Supporting copy

> 提交问题和相关资料，我们会帮您核对信息、说明当前结果，并告诉您下一步怎么做。

### Starter cards

Use customer goals, not system capabilities:

- **商品问题** — 核对商品信息、素材或资质
- **商家认证** — 查看主体、授权或认证问题
- **售后处理** — 处理退款、履约或责任争议
- **风险问题** — 核对异常交易或账户问题

Cards should feel like Intercom-style service entry points: clear labels, short descriptions, restrained blue accent, no technical badges.

## 8. Service overview

The welcome overview should not show architecture metrics such as active plugins, contracts, generations, or execution graphs.

Preferred customer overview:

- **服务状态** — 正常 / 暂时繁忙
- **最近办理** — number of recent requests shown
- **待您确认** — number of current pending confirmations when available
- **资料支持** — 图片、文档、表格等

Only show real data. Never invent SLA, completion percentage, confidence score, success rate, or processing speed.

## 9. Progress

Use customer-readable stages such as:

- 已提交
- 正在核对资料
- 需要补充资料
- 正在处理中
- 等待您确认
- 已完成

If a request is blocked, explain the reason immediately.

Example:

> 缺少品牌授权书首页，请补充上传后继续。

Do not show internal step names, route-node IDs, planner stages, event IDs, or model states in the customer view.

## 10. Supporting information

Call customer-visible evidence **资料** or **判断依据**.

Each item may show:

- title;
- short relevance explanation;
- source: `您提交的` / `系统整理`;
- related file name when useful;
- a short reference number only when customers may need it for support.

Do not show the labels `SOURCE`, `PROVENANCE`, or `TRACE`.

Preferred summary labels:

- 资料总数
- 您提交的
- 系统整理的

## 11. Confirmations

High-impact business actions must be easy to understand.

Every confirmation should answer:

1. What will happen?
2. What will it affect?
3. Can it be undone?
4. What does the customer need to choose?

Use labels like:

- 待您确认
- 确认继续
- 暂不处理
- 会影响订单状态
- 仅更新当前记录

Do not use “authority”, “execution control”, or “proposed action”.

## 12. Composer

The input area is a service request box, not an AI command console.

### Label

> 描述您的问题

### Supporting text

> 可以继续补充图片、文档、表格或其他相关资料。

### Placeholder

> 例如：这笔退款是否符合条件？订单、物流和聊天记录我都可以补充。

### Primary action

> 发送

### Attachment action

> 添加资料

Do not use “目标”“指令”“多模态输入”“Prompt”“任务命令”等 technical phrasing in normal customer UI.

## 13. Service settings

Customer-facing settings should use:

- **处理方式** instead of Provider / Model / Engine;
- **自动选择** instead of Auto orchestration;
- **系统状态** instead of Runtime plugins;
- **服务正常** instead of Contract valid / Healthy.

Advanced implementation details should be hidden from normal customers whenever role-based UI allows it.

## 14. Visual theme

The visual system should be friendlier than the former Carbon operations console.

### Main qualities

- light warm-neutral canvas;
- white content surfaces;
- friendly service blue as primary accent;
- dark navy/charcoal text rather than pure technical black everywhere;
- 8–12px normal corner radius;
- subtle borders and soft shadow only where useful;
- generous reading space in the center;
- compact but comfortable right-side panels;
- no terminal aesthetic;
- no engineering-status dashboards on the customer homepage.

## 15. Color system

```css
:root {
  --ink: #1f2430;
  --ink-2: #3f4652;
  --muted: #6b7280;
  --muted-2: #8a919d;

  --canvas: #f7f8fb;
  --paper: #ffffff;
  --soft: #f3f6fb;
  --soft-blue: #edf4ff;

  --line: #e5e9f0;
  --line-strong: #d5dbe5;

  --accent: #2864dc;
  --accent-hover: #1f55c7;
  --accent-soft: #eaf1ff;

  --success: #1f8a55;
  --success-soft: #eaf7f0;
  --warning: #b36b00;
  --warning-soft: #fff6e5;
  --danger: #c63d43;
  --danger-soft: #fff0f1;

  --radius-sm: 8px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --shadow-small: 0 1px 3px rgba(31, 36, 48, 0.06);
}
```

Do not copy exact source-brand colors mechanically. EcomEvo owns its visual identity.

## 16. Typography

CJK-first UI stack:

```css
--font-ui: "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
  "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
  system-ui, -apple-system, "Segoe UI", sans-serif;
```

Recommended hierarchy:

| Role | Size | Weight |
|---|---:|---:|
| Welcome title | 34–40px | 600 |
| Page title | 22–28px | 600 |
| Section title | 16–18px | 600 |
| Body | 14–16px | 400 |
| Supporting text | 13–14px | 400 |
| Caption | 12px | 400–500 |

Avoid monospace in normal customer UI. Reserve it for hidden/internal diagnostics or short reference numbers.

## 17. Buttons and controls

Primary actions:

- solid service blue;
- white text;
- 8–10px radius;
- clear hover and focus;
- plain-language verb label.

Secondary actions:

- white or soft-blue surface;
- neutral border;
- explicit label.

Avoid icon-only actions when the meaning is not universal.

## 18. Cards

Use cards to make choices and supporting information easier to scan.

Good uses:

- service starter card;
- recent request;
- missing-material notice;
- confirmation request;
- uploaded file;
- important supporting information.

Avoid turning every sentence into a card.

Clay-inspired color may appear in onboarding or empty states only, and should be soft rather than saturated.

## 19. Answer presentation

Assistant answers should read like customer service, not AI analysis.

Preferred pattern:

**当前结果**

我们已经核对了您提交的订单和物流信息，目前更支持……

**还需要您补充**

请补充品牌授权书首页，图片需完整清晰。

**判断依据**

订单记录显示……；物流记录显示……

**下一步**

补充资料后我们会继续处理，您无需重新提交问题。

Use headings only when they improve scanning. Keep short answers short.

## 20. Accessibility and trust

- visible keyboard focus;
- minimum 40px touch target on mobile;
- no state represented by color alone;
- plain explanation for disabled actions;
- destructive actions require explicit wording;
- never imply an action has happened before confirmation succeeds;
- never fabricate confidence scores or service metrics.

## 21. Responsive behavior

Desktop:

- left navigation;
- main service conversation;
- right status/detail panel.

Mobile:

- conversation is primary;
- navigation and details become drawers;
- `待确认` state must remain easy to find;
- do not expose internal architecture just because space is limited.

## 22. Do

- Write for a customer who has never heard of agents or runtimes.
- Tell the customer what is happening now.
- Tell them exactly what is missing.
- Make the next action obvious.
- Keep serious cases calm and professional.
- Use Intercom-like conversational clarity.
- Use Claude-like reading hierarchy.
- Use Clay-like warmth sparingly.

## 23. Do not

- Do not expose academic or engineering jargon.
- Do not present internal architecture as a product feature.
- Do not show execution graphs to customers.
- Do not turn the homepage into a system dashboard.
- Do not use terminal styling.
- Do not add fake analytics.
- Do not overuse English uppercase labels.
- Do not use developer-facing abbreviations when a Chinese phrase works.
- Do not copy Intercom, Claude, or Clay 1:1.

## 24. Agent prompt guide

Before editing customer-facing frontend files, read this `DESIGN.md` and `docs/CUSTOMER_COPY.md`.

Implementation prompt:

> Refine EcomEvo as a customer-facing commerce service product. Use Intercom-inspired conversational clarity, Claude-inspired reading hierarchy, and restrained Clay warmth. Translate all implementation concepts into plain customer language. Preserve existing backend behavior, permissions, evidence integrity, confirmation requirements, accessibility, and business capabilities. Do not expose Runtime, Agent, Plugin, Trace, Provenance, Authority, Provider, Contract, Planner, Verifier, or other engineering terms in the normal customer journey.
