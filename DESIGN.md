---
version: 3.0
name: EcomEvo Customer Workbench Design System
description: "A premium commerce-service workbench built around Obsidian navigation, Porcelain work surfaces, and a single Iris interaction accent. The system combines Linear-like restraint, Stripe-like information clarity, Intercom-like service flow, and Shopify Admin-like merchant usability without copying any brand identity."
source_inspiration:
  - "VoltAgent/awesome-design-md · linear.app"
  - "VoltAgent/awesome-design-md · stripe"
  - "VoltAgent/awesome-design-md · intercom"
  - "Shopify Admin / Polaris interaction principles"
implementation_scope: "frontend presentation only"
---

# EcomEvo Customer Workbench Design System

## 1. Product idea

EcomEvo is a **business service workbench for complex commerce tasks**.

Customers should feel that they are working inside one calm, capable place where a difficult issue can be submitted, supported with materials, reviewed, continued, and confirmed. The interface should feel more like a mature commercial product than an AI demo, admin template, engineering console, or generic chat application.

A customer should understand four things at a glance:

1. What am I handling?
2. What is the current result or progress?
3. What information is still needed?
4. Is there anything that requires my confirmation?

The sophistication may live underneath. The surface must stay simple, legible, and trustworthy.

## 2. Non-negotiable product boundary

This design system changes presentation only.

Do not change:

- algorithms or planning behavior;
- API routes or payload schemas;
- event types or identifiers;
- permissions or authorization;
- business action semantics;
- persistence or recovery behavior;
- provider selection behavior;
- verifier or sandbox behavior;
- backend data models.

Internal names may remain in source code. Translate them only at the final customer-facing layer.

Never expose chain-of-thought, hidden planning, tool-call narration, implementation topology, or internal orchestration details to normal customers.

## 3. Design thesis

### Obsidian × Porcelain × Iris

EcomEvo has three visual responsibilities:

- **Obsidian** anchors navigation, identity, and the compact process map.
- **Porcelain** carries reading, working, evidence, and conversation.
- **Iris** identifies selection, focus, primary action, and system continuity.

This is not a dark theme and not a purple theme. Dark surfaces are structural anchors. Iris is deliberately scarce. Most of the product remains neutral.

### What is borrowed from references

**Linear** contributes restraint: one chromatic accent, precise hairlines, disciplined spacing, quiet density, and strong dark/light surface hierarchy.

**Stripe** contributes transaction clarity: primary actions are unmistakable, numbers and state information are easy to scan, and secondary chrome never competes with the task.

**Intercom** contributes service workflow: navigation on the left, the task in the center, contextual details on the right, and customer-language progress instead of technical narration.

**Shopify Admin / Polaris** contributes merchant usability: consistent interaction cues, semantic state colors, durable information architecture, and accessible operational density.

Do not copy proprietary logos, fonts, illustrations, brand colors, or recognizable component compositions from any reference.

## 4. Visual hierarchy

The desktop hierarchy must be obvious before reading any text:

1. **Center workspace** — primary task and result.
2. **Left navigation** — stable product/brand anchor.
3. **Right inspector** — contextual progress, materials, confirmations.
4. **Top utility bar** — search and infrequent controls.
5. **Composer** — available at all times, elevated only enough to remain findable.

The three columns must never carry equal visual weight.

The center is the place to think and act. The left is the place to navigate. The right is the place to verify context.

## 5. Core palette

```css
:root {
  --customer-canvas: #f4f5f8;
  --customer-canvas-elevated: #f8f8fa;
  --customer-surface: #ffffff;
  --customer-surface-soft: #f7f7f9;
  --customer-surface-tint: #f1f1ff;

  --customer-ink: #191a1e;
  --customer-ink-2: #34363d;
  --customer-muted: #686c76;
  --customer-subtle: #9296a0;

  --customer-line: #dedfe5;
  --customer-line-soft: #ececf1;
  --customer-line-strong: #c9cbd3;

  --customer-accent: #5b5bd6;
  --customer-accent-hover: #4b4bbd;
  --customer-accent-pressed: #3f3fa8;
  --customer-accent-soft: #efefff;
  --customer-accent-line: #d9d9ff;

  --customer-sidebar: #15161a;
  --customer-sidebar-2: #1c1e23;
  --customer-sidebar-3: #24272e;
  --customer-sidebar-line: #2a2d34;
  --customer-sidebar-ink: #f5f6f8;
  --customer-sidebar-muted: #9ca1ac;

  --customer-success: #23845b;
  --customer-warning: #a76818;
  --customer-danger: #c54545;
}
```

### Color rules

- Porcelain gray is the application floor, not beige and not blue-gray enterprise chrome.
- White is the primary reading surface.
- Obsidian is limited to navigation and deliberately elevated process surfaces.
- Iris is the only product interaction accent.
- Do not use iris to decorate every card or data row.
- Green means success/completion only.
- Amber means attention/warning only.
- Red means error/destructive risk only.
- Category colors must never look like status colors.
- Dense repeated content should become more neutral, not more colorful.

## 6. Typography

Use the existing CJK-first stack:

```css
--font-ui: "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
  "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
  system-ui, -apple-system, "Segoe UI", sans-serif;
```

No proprietary reference-brand fonts.

### Recommended hierarchy

| Role | Size | Weight | Notes |
|---|---:|---:|---|
| Welcome title | 38–50px | 600–650 | tight negative tracking, calm not heroic |
| Task title | 24–29px | 600–650 | one dominant line when possible |
| Result heading | 17–21px | 600–650 | clear editorial sections |
| Section title | 14–16px | 600–650 | inspector and cards |
| Body | 14–15px | 400–450 | Chinese line-height 1.65–1.8 |
| Helper | 11–13px | 400–500 | never lower contrast than accessibility allows |
| Caption | 9.5–11px | 450–550 | metadata only |

Rules:

- Use negative tracking only on large display/task headings.
- Do not use monospace for normal customer labels.
- Avoid fake-bold Chinese text everywhere; weight is part of hierarchy.
- Prefer short labels and breathing room over smaller type.

## 7. Spacing and shape

Use a disciplined 4px-derived rhythm.

Typical spacing:

- 4px micro gap;
- 8px compact control gap;
- 12px component internal gap;
- 16px normal card padding;
- 24px section separation;
- 32–36px workspace side padding;
- 48–68px first-run vertical breathing room.

Radii:

- micro/control: 6–9px;
- buttons/inputs: 8–10px;
- normal cards: 11–14px;
- elevated major panels: 16–20px.

Avoid both extremes:

- not square enterprise rectangles;
- not pill-everything consumer UI.

## 8. Elevation

Most surfaces use borders, not shadows.

Use shadow only when depth communicates behavior:

- composer;
- modal;
- mobile drawer;
- primary process panel;
- a card lifting on hover.

Recommended shadows:

```css
--customer-shadow-xs: 0 1px 2px rgba(19,20,24,.05);
--customer-shadow-sm: 0 8px 24px rgba(19,20,24,.07), 0 1px 3px rgba(19,20,24,.05);
--customer-shadow-lg: 0 24px 70px rgba(19,20,24,.14), 0 4px 14px rgba(19,20,24,.06);
```

Do not stack shadow + saturated border + colored fill on the same ordinary card.

## 9. Left navigation

The left rail is the product's visual anchor.

Required behavior:

- Obsidian background.
- Bright EcomEvo identity at top.
- One clear iris primary new-task action.
- Navigation rows are quiet by default.
- Selected state uses a restrained iris edge/inner cue, not a full bright fill.
- Secondary descriptions remain legible but subdued.
- Recent tasks are denser than business-scene navigation.
- Auto-save and confirmation language sits quietly at the bottom.

The left rail should feel closer to a premium professional tool than a conventional admin sidebar.

## 10. Top utility bar

The top bar is glass-like only in restraint, never decorative.

- near-white surface;
- subtle blur is allowed;
- one-pixel soft divider;
- search/command entry is centered and visually quiet;
- utility actions are secondary;
- no gradient hero chrome;
- no strong brand color across the entire bar.

On mobile, the bar becomes neutral light chrome so the dark navigation exists only when the drawer is open.

## 11. Center workspace

The center canvas should feel slightly cooler and more spacious than the right inspector.

Use:

- `#f4f5f8` base;
- extremely subtle iris radial atmosphere near the first-run content;
- white cards for actual reading results;
- task header as a near-white translucent boundary;
- enough empty space to make complex information feel manageable.

Do not fill unused workspace with decorative gradients, illustrations, blobs, grids, or noise textures.

## 12. First-run composition

Do not use a generic four-equal-card dashboard.

The first-run page uses a **bento hierarchy**:

- one primary commerce shortcut anchors the grid;
- two secondary shortcuts stack beside it;
- one wide shortcut closes the group;
- the main card may use a subtle iris-tinted atmosphere;
- other cards remain neutral;
- hover lifts by only 1–2px.

The large anchor card should use its space intentionally: title/content sits with editorial confidence instead of floating in a large empty rectangle.

Beside the shortcuts, the handling-flow panel is dark and compact. It is an orientation surface, not a dashboard and not technical topology.

## 13. Task process panel

The process panel uses Obsidian because it is structurally elevated from ordinary content.

- 16–18px radius;
- thin dark border;
- restrained shadow;
- compact numbered steps;
- one active step with iris emphasis;
- no neon glow;
- no animated node graph;
- no engineering vocabulary.

It should read as “this is how your request will move”, not “this is how the runtime works”.

## 14. Composer

The composer is always available but must not dominate the screen.

- white surface;
- 16px radius desktop;
- subtle border;
- medium low-opacity shadow;
- iris focus ring only while active;
- attachments use neutral gray controls;
- the send button is the only filled iris action in the composer;
- keyboard hint is visually subordinate.

Never make the whole composer permanently iris-outlined.

## 15. Messages and results

Assistant result:

- white reading card;
- subtle border;
- 14px+ body;
- 1.7–1.8 Chinese line-height;
- clear editorial headings;
- no provider/runtime narration in the main reading flow.

User message:

- soft iris tint;
- dark text;
- moderate asymmetric message radius;
- clearly distinct from the result without looking like a social chat bubble.

Long results should use this reading order when useful:

1. 当前结果
2. 主要依据 / 我参考了什么
3. 还缺什么
4. 风险关注
5. 下一步

Do not force every section into short answers.

## 16. Right inspector

The right side is contextual, not co-equal with the task.

It should feel lighter than both the center result and the dark navigation.

- almost-white background;
- compact tabs;
- no heavy card chrome around every block;
- progress first;
- evidence/materials second;
- confirmation clearly separated;
- uploaded assets remain easy to revisit.

Tabs:

- 进度
- 资料
- 待确认
- 已上传

Use color sparingly. Counts are neutral unless they require attention.

## 17. Evidence and materials

Dense evidence lists must remain visually quiet.

Do not repeat a bright vertical brand stripe on every row.

Distinguish source type with the small evidence icon:

- customer-provided: subtle success-tinted icon;
- system-organized: subtle iris-tinted icon.

The evidence title and description carry more weight than metadata.

Metadata such as source/type/id is secondary and must never visually overpower the material itself.

## 18. Confirmation actions

Important actions are intentionally more explicit than ordinary UI.

- Explain the impact first.
- Then expose approve/reject controls.
- Do not hide confirmation behind minimalism.
- Use red only for actual destructive/reject/error meaning.
- Iris may identify the affirmative primary action, but it does not imply that the action has already executed.

## 19. Semantic states

Status must never rely on color alone.

Examples:

- success: green + completed wording/check;
- warning: amber + attention wording;
- failure: red + explicit failure text;
- awaiting customer: iris soft chip + clear “待补资料 / 待确认” language;
- neutral waiting: gray + plain-language explanation.

Never use fabricated confidence, completion percentages, SLAs, or progress values that are not backed by real state.

## 20. Customer language

Core rule:

**Describe what the customer can do, what happened, what is missing, and what happens next. Do not narrate implementation.**

Preferred terms:

| Internal concept | Customer surface |
|---|---|
| Runtime | 服务 / 处理服务 |
| Agent | 助手 / 系统 |
| Planner | 下一步安排 |
| Trace | 办理进度 / 记录 |
| Evidence | 相关资料 / 判断依据 |
| Provenance | 来源 |
| Authority | 需要确认 |
| Action | 操作 / 下一步 |
| Plugin | 功能 / 服务能力 |
| Provider | 处理方式 |
| Verifier | 结果核对 |
| Replan | 重新核对 / 重新安排 |
| Rollback | 已恢复到安全状态 |

Normal customer UI should not display:

- Runtime
- Agent Runtime
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

## 21. Required product copy anchors

Product name:

**EcomEvo 业务服务助手**

First-run title:

**您好，今天想处理什么？**

First-run description:

**提交问题和相关资料，我们会帮您核对信息、说明当前结果，并告诉您下一步怎么做。**

Right inspector title:

**办理详情**

Composer title:

**描述您的问题**

High-impact boundary:

**涉及真实业务变更时，会先请您确认。**

## 22. Motion

Motion should confirm state, not entertain.

Allowed:

- hover lift 1–2px;
- 150–220ms control transitions;
- modal/drawer entrance;
- small progress/status pulse when real work is active.

Avoid:

- looping gradients;
- floating background elements;
- large parallax;
- bouncy card animation;
- animated decorative graphs.

Respect `prefers-reduced-motion`.

## 23. Responsive behavior

### Desktop

Keep three areas:

- dark left navigation;
- primary center workspace;
- light right inspector.

### Medium screens

- right inspector becomes a drawer;
- left navigation may remain or become a drawer depending on width;
- center task stays primary.

### Mobile

Priority:

1. current task/result;
2. composer;
3. progress;
4. materials/evidence;
5. confirmation;
6. navigation.

On mobile:

- dark nav appears only as an opened drawer;
- normal top chrome stays light;
- first-run bento collapses into a single-column list;
- touch targets remain at least ~40px where practical;
- inspector typography must stay readable without horizontal compression.

## 24. Accessibility

Required:

- visible keyboard focus;
- accessible contrast on light and dark surfaces;
- status not encoded by color alone;
- dialogs trap focus;
- drawers expose expanded state;
- controls have clear accessible names;
- Chinese text remains readable at every breakpoint;
- no essential information in decorative pseudo-elements;
- reduced-motion preferences remain functional.

## 25. Do

- Use one clear brand accent.
- Build hierarchy with surface polarity before adding color.
- Make dense information quieter as density increases.
- Give the primary task more breathing room than secondary chrome.
- Keep source visibility and confirmation boundaries obvious.
- Let typography and spacing carry premium quality.
- Use real product state and real materials.
- Capture real browser screenshots when judging visual changes.

## 26. Do not

- Do not return to a full warm-beige application canvas.
- Do not make all three columns the same color and visual weight.
- Do not use a generic equal-size dashboard-card grid for the welcome state.
- Do not put a colored rail on every evidence row.
- Do not turn the app into a dark theme overall.
- Do not use gradients as decoration across normal cards.
- Do not use multiple saturated accent colors.
- Do not overuse pills.
- Do not expose technical orchestration vocabulary.
- Do not copy Linear, Stripe, Intercom, Shopify, or any reference 1:1.
- Do not use proprietary fonts, icons, or logos from reference products.

## 27. Review standard

A visual change is not finished because the CSS looks reasonable.

Before merging a meaningful UI change, review real browser captures at:

- desktop overview;
- desktop result/evidence state;
- mobile task/details state.

Ask:

1. Is the primary task obvious in one second?
2. Does any secondary surface compete with it?
3. Is brand color used intentionally rather than repeatedly?
4. Does dense content become calmer, not noisier?
5. Does the layout still feel premium at 100% zoom rather than only in a mockup?
6. Can a customer understand progress and confirmation without knowing implementation details?

## 28. Agent implementation prompt

> Read `DESIGN.md` before editing customer-facing UI. Preserve all algorithms, API routes, backend schemas, runtime behavior, permissions, action-confirmation semantics, event IDs, persistence, and business logic. Follow EcomEvo's Obsidian × Porcelain × Iris system: dark navigation as the brand anchor, cool porcelain work surfaces, one restrained iris interaction accent, light contextual right inspector, asymmetric first-run bento composition, and semantic success/warning/danger colors that remain distinct from brand. Use typography, spacing, hairlines, and surface hierarchy before adding decoration. Keep customer language non-technical. Review real Browser E2E screenshots before considering a meaningful visual change complete.
