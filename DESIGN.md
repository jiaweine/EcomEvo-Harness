---
version: 4.0
name: EcomEvo Unified AI Workbench Design System
description: "A calm commerce AI workspace using the shared interaction grammar of mature conversational AI products: light navigation, one centered work column, native system typography, a dominant composer, and contextual detail on demand."
reference_grammar:
  - "ChatGPT desktop/web: centered conversation, restrained chrome, dominant composer"
  - "Doubao: light navigation rail, direct task entry, quiet neutral surfaces"
  - "Codex: unified recent work, primary work surface, contextual detail when needed"
implementation_scope: "frontend presentation and interaction layout only"
---

# EcomEvo Unified AI Workbench Design System

## 1. Product thesis

EcomEvo is an AI workbench for complex commerce tasks. It should feel immediately familiar to someone who already uses ChatGPT, Doubao, or Codex.

The product must not look like an admin dashboard, monitoring console, KPI wall, or three-column enterprise template. Business complexity can exist in the runtime and detail views; the default customer surface remains conversational, calm, and focused.

The first visual question is always: **what does the user want to do next?**

## 2. Non-negotiable layout

Desktop uses two persistent regions:

1. **Light navigation rail** — product identity, new task, business entry points, recent work.
2. **Primary work surface** — the conversation/task itself.

A third information region may exist only as a **contextual drawer**. Progress, evidence, uploads, and confirmations must never permanently reduce the main reading width.

Default desktop geometry:

```css
--topbar: 56px;
--left: 260px;
--content: 780px;
--detail-drawer: 380px;
```

Rules:

- The main conversation and composer share the same horizontal center and width.
- The primary content column should normally remain between 720px and 800px.
- Do not create equal-weight left / center / right columns.
- Do not place dashboards or process maps beside the first-run greeting.
- The right detail drawer opens only through an explicit user action and must be keyboard accessible.
- On compact screens, both navigation and detail become drawers.

## 3. Typography

Use the platform-native sans-serif stack. Do not force a bundled Chinese webfont for the application shell.

```css
font-family:
  ui-sans-serif,
  -apple-system,
  BlinkMacSystemFont,
  "Segoe UI",
  "PingFang SC",
  "Hiragino Sans GB",
  "Microsoft YaHei",
  Arial,
  sans-serif;
```

This intentionally follows the rendering behavior users expect from modern AI desktop/web products: native metrics on macOS and Windows, clean CJK fallback, and no decorative display face.

Typography rules:

- Body / conversation: 14px, comfortable line height around 1.65–1.75.
- First-run greeting: approximately 27–32px, semibold, never marketing-scale.
- Task title in the top work bar: compact, approximately 12–14px.
- Sidebar labels: approximately 11–13px.
- Avoid uppercase microcopy and excessive letter spacing.
- Avoid font weights above 650 for normal UI hierarchy.

## 4. Color and surfaces

The default product is neutral and light.

```css
--canvas: #ffffff;
--sidebar: #f7f7f8;
--sidebar-active: #e7e7e9;
--surface-muted: #f2f2f2;
--line: #e5e5e5;
--ink: #1f1f1f;
--muted: #676767;
--interaction: #202020;
```

Brand color must not flood the interface. Interaction can be near-black in the main AI surface; semantic business states remain independent:

- success: green;
- warning: amber;
- danger: red.

Do not use purple/blue rails, gradients, or large tinted panels as the default visual identity.

## 5. Navigation rail

The navigation rail behaves like a modern AI product sidebar:

- light gray background;
- one compact “new task” action;
- short business entry labels;
- recent work below;
- active row uses a subtle neutral fill, not a colored stripe;
- secondary descriptions may be hidden when they add visual noise;
- system status can live quietly at the bottom.

The sidebar is supporting chrome, not the visual hero.

## 6. First-run surface

A new task should contain, in this order:

1. short greeting;
2. one sentence explaining what can be submitted;
3. a small set of lightweight prompt/task suggestions;
4. the composer.

Do not show a KPI dashboard, system topology, process diagram, or permanent evidence panel on the first screen.

A very small live-service indicator is acceptable if it reads like status text rather than a dashboard.

Quick-start cards are equal suggestions, not dashboard tiles. Use simple borders, neutral hover states, and short labels.

## 7. Composer

The composer is the main control of the product.

Required characteristics:

- horizontally aligned with the conversation column;
- rounded neutral shell, roughly 20–24px radius on desktop;
- subtle border and shadow only;
- textarea is visually dominant;
- attachments stay secondary;
- circular primary send action at the trailing edge;
- no separate title/header strip inside the composer;
- no loud brand gradient.

On an empty task, position the composer close enough to the greeting that the page reads as one task-entry experience, not a header plus distant footer form.

## 8. Conversation

Assistant responses should feel open and editorial rather than boxed into repeated cards.

- Assistant content sits directly on the white work surface.
- User messages may use a compact neutral bubble aligned to the right.
- Long answers preserve readable line length.
- Evidence, attachments, and confirmations may use cards only where their structure needs a boundary.
- Do not surround every response with borders and shadows.

## 9. Context drawer

Progress, evidence, confirmations, and uploaded materials belong in the contextual detail drawer.

The drawer:

- is closed by default on desktop;
- is opened from a clear detail control;
- traps focus while open;
- returns focus to the trigger when closed;
- uses a white surface and quiet separators;
- does not introduce a second visual theme.

The drawer may be information-dense because it is explicitly requested. The primary conversation surface must remain calm.

## 10. Top bar

The top bar is compact utility chrome.

Allowed:

- product identity aligned with the sidebar;
- search/command access;
- compact icon-only help / processing controls;
- workspace/avatar control;
- detail-drawer trigger.

Avoid a row of labeled enterprise-toolbar buttons. If a control is infrequent, use a compact icon or move it into a contextual surface.

## 11. Responsive behavior

At mobile widths:

- persistent sidebar disappears into a drawer;
- detail remains a drawer;
- main work surface occupies the viewport;
- greeting reduces to roughly 27px;
- quick suggestions become one column;
- composer keeps safe-area spacing and remains the primary action.

Desktop and mobile should feel like the same product, not separate themes.

## 12. Accessibility

- Preserve visible focus states.
- Drawers and dialogs must trap focus and restore it on close.
- Maintain semantic buttons, tabs, labels, and ARIA relationships.
- Do not communicate success/warning/danger through color alone.
- Keep controls comfortably targetable on touch screens.

## 13. Explicit do / do-not rules

### Do

- Use native system typography.
- Keep the main work column centered and constrained.
- Use neutral surfaces and minimal decoration.
- Make the composer the primary visual control.
- Put secondary detail behind explicit intent.
- Preserve business semantics and customer-readable language.

### Do not

- Do not restore a dark permanent sidebar as the default theme.
- Do not restore a permanent right inspector on desktop.
- Do not build the welcome page as a KPI/dashboard grid.
- Do not put a large process diagram beside the greeting.
- Do not use a bundled CJK font as the mandatory application font.
- Do not use large gradients, glass panels, or decorative brand color fields.
- Do not copy proprietary logos, exact icons, or distinctive branded components from reference products.

## 14. Product boundary

This design system changes presentation and interaction layout only. It must not change APIs, runtime planning behavior, permissions, action semantics, persistence, provider selection, verifier behavior, sandbox behavior, or backend data models.
