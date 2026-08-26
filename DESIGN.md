---
version: 1.0
name: EcomEvo Carbon Operations UI
description: "Enterprise decision-runtime interface adapted from IBM Carbon principles for EcomEvo. Dense, evidence-first, restrained, auditable, CJK-first, and operational rather than decorative."
source_inspiration: "VoltAgent/awesome-design-md · IBM / Carbon"
---

# EcomEvo Design System

## 1. Product character

EcomEvo is an enterprise commerce decision runtime, not a marketing site and not a consumer chat app. The interface must feel like a serious operating console for product governance, merchant review, after-sales adjudication, risk review, content audit, evidence inspection, controlled actions, and runtime observability.

The visual hierarchy should communicate five things immediately:

1. what task is active;
2. what evidence exists;
3. what the runtime is doing;
4. what is uncertain or blocked;
5. which real-world actions still require human authority.

Prefer clarity, traceability, density, and stable spatial structure over novelty, glassmorphism, large decorative gradients, oversized cards, or playful motion.

## 2. Design direction

Use an EcomEvo adaptation of IBM Carbon rather than a literal IBM clone.

Core qualities:

- light working surfaces with a dark operational navigation rail;
- one confident blue accent for primary actions, focus, selection, and active runtime states;
- neutral gray surfaces for hierarchy instead of decorative shadows;
- thin borders and section dividers;
- compact enterprise spacing;
- small corner radii rather than pill-heavy UI;
- typography-led hierarchy;
- data, state, provenance, and authority always visible when relevant;
- motion only for state transition, progress, feedback, and focus.

Do not add IBM logos, IBM branding, or copy IBM website composition. Carbon is the design-system reference; EcomEvo remains the brand.

## 3. Color system

Map these roles onto the existing CSS custom properties whenever possible instead of creating duplicate token families.

```css
:root {
  --nav: #161616;
  --nav-2: #262626;
  --nav-3: #393939;

  --ink: #161616;
  --ink-2: #393939;
  --muted: #525252;
  --muted-2: #6f6f6f;

  --canvas: #f4f4f4;
  --paper: #ffffff;
  --paper-warm: #ffffff;
  --soft: #f4f4f4;
  --soft-blue: #edf5ff;

  --line: #e0e0e0;
  --line-strong: #c6c6c6;

  --accent: #0f62fe;
  --accent-2: #0043ce;
  --accent-soft: #edf5ff;

  --success: #198038;
  --success-soft: #defbe6;
  --warning: #b28600;
  --warning-soft: #fff8e1;
  --danger: #da1e28;
  --danger-soft: #fff1f1;

  --shadow: none;
  --shadow-small: 0 1px 2px rgba(0, 0, 0, 0.08);
  --radius: 6px;
}
```

### Color rules

- Blue is the primary interactive color. Do not introduce a second brand accent for normal UI.
- Use red only for destructive/error states, amber for warning/attention, and green for verified/safe/success states.
- Do not use gradients for ordinary cards, buttons, navigation, status panels, or evidence surfaces.
- Do not use color alone to communicate state. Pair it with text, icon, label, position, or border treatment.
- The right-side Evidence & Authority panel should be neutral by default; semantic color appears only on the specific state being communicated.

## 4. Typography

EcomEvo is CJK-first. Do not force IBM Plex Sans onto Chinese text.

Preferred UI stack:

```css
--font-ui: "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
  "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
  system-ui, -apple-system, "Segoe UI", sans-serif;

--font-mono: "SFMono-Regular", "Cascadia Code", "Roboto Mono",
  ui-monospace, monospace;
```

Use typography and spacing before heavy font weight.

Recommended roles:

| Role | Size | Weight | Use |
|---|---:|---:|---|
| Page title | 24–28px | 600 | Current task |
| Welcome display | 34–44px | 600 | Empty/new task state only |
| Section title | 16–20px | 600 | Runtime / evidence / action sections |
| Body | 14–16px | 400 | Primary reading |
| Dense body | 13–14px | 400 | Side panels and metadata |
| Label | 12px | 500–600 | Eyebrows, tabs, compact labels |
| Caption | 11–12px | 400 | Provenance, timestamps, secondary metadata |
| Runtime value | 11–13px mono | 500–600 | IDs, latency, versions, scores |

Avoid ultra-light Chinese text. Avoid excessive bold. Long answer content should stay comfortable to read and should not inherit dashboard-density line height.

## 5. Spacing and density

Base spacing scale:

- 4px: micro gap;
- 8px: compact control gap;
- 12px: dense component padding;
- 16px: standard component spacing;
- 24px: section spacing;
- 32px: major workspace spacing;
- 48px: empty-state / hero spacing only.

Operational screens should favor 8 / 12 / 16 / 24. Do not spread every card apart with 24–32px gaps.

The existing three-column mental model is intentional:

- left: scene navigation and recent tasks;
- center: task conversation and working state;
- right: trajectory, evidence, actions, and assets.

Do not turn this into a generic single-column chat experience.

## 6. Shape, borders, and elevation

EcomEvo should be flatter than a typical SaaS dashboard.

- Primary radius: 4–6px.
- Larger containers may use up to 8px when the current layout needs softer grouping.
- Avoid 12–20px rounded cards except for an intentionally distinct modal or mobile container.
- Avoid pill buttons unless the information is truly a compact status/tag.
- Prefer `1px solid var(--line)` over drop shadows.
- Use shadows only for temporary floating layers such as command palette, modal, dropdown, or mobile drawer.
- Do not stack border + strong shadow + tinted background on the same normal card.

## 7. Navigation

### Left rail

The left rail is operational chrome and should remain dark.

- background: `--nav` / `--nav-2`;
- active item: darker/lighter local surface plus a blue edge or blue state marker;
- inactive icons and metadata: low-contrast gray;
- active scene name: white;
- use square/compact scene rows rather than floating rounded tiles;
- do not add large colorful icons per business scene.

### Top bar

Keep the top bar light and quiet. Search/command is a utility, not a hero CTA.

### Right control plane

Tabs should feel like instrument controls:

- clear selected state;
- compact height;
- neutral surface;
- bottom/edge indicator preferred over filled pill tabs;
- action count badges are small and semantic.

## 8. Buttons

### Primary

- blue background `#0f62fe`;
- white text;
- 4–6px radius;
- no gradient;
- no glow;
- hover `#0050e6` or darker blue;
- active `#0043ce`.

### Secondary

Use dark neutral or white with a strong neutral border depending on context.

### Tertiary / ghost

Use for utility actions such as refresh, share, open details, copy, or local panel actions.

### Destructive

Use red only when the action is actually destructive or irreversible.

High-impact commerce actions must visually distinguish:

1. proposed action;
2. impact scope;
3. required authority;
4. confirm / reject controls.

Do not visually style a proposed action as already completed.

## 9. Inputs and composer

The composer is a task command surface, not a social-chat bubble.

- neutral white/light-gray surface;
- 1px border;
- strong blue focus ring;
- compact attachment actions;
- no floating glass panel;
- upload busy state must remain visible;
- send action can be primary blue;
- pending evidence/assets should be visibly grouped with the composer.

The provider selector must read as configuration/engine state, not as a promotional brand selector.

## 10. Cards and panels

Use cards only when they group a meaningful operational unit.

Good card purposes:

- evidence item;
- runtime step;
- proposed real-world action;
- asset;
- provider/runtime component;
- compact status summary.

Bad card purposes:

- wrapping every paragraph;
- wrapping each tiny metric independently;
- decorative marketing tiles inside the working console.

For most operational groups, use a white surface, thin divider, and internal spacing instead of a floating shadow card.

## 11. Evidence design

Evidence is a first-class product object.

Every evidence component should make provenance easy to inspect. Where data exists, prefer this structure:

- evidence title / type;
- source;
- captured or observed time;
- relation to current conclusion;
- confidence or verification state;
- expandable raw detail / excerpt;
- link or trace identifier when available.

Strong evidence and weak clues must not look identical. Use labels and hierarchy, not only color.

## 12. Runtime / Agent state

Runtime visualization should look like an execution trace, not an AI magic animation.

Prefer states such as:

- queued;
- running;
- waiting for evidence;
- verifying;
- blocked;
- waiting for authority;
- complete;
- rolled back;
- replanning.

Show progress only when the number is meaningful. Otherwise show discrete step state.

Use monospace selectively for trace IDs, versions, latency, scores, model/runtime metadata, and event identifiers.

## 13. Status semantics

| State | Treatment |
|---|---|
| Active / running | IBM blue + motion only on the active element |
| Verified / safe | Green + verified label/icon |
| Warning / incomplete | Amber + explanatory text |
| Error / blocked | Red + recovery explanation |
| Neutral / pending | Gray |
| Human authority required | Strong neutral/blue boundary; do not imply success |

## 14. Motion

Motion is state-bound.

Allowed:

- short panel entrance;
- focus/hover transition;
- active runtime pulse;
- determinate progress;
- upload feedback;
- modal/drawer transition.

Avoid:

- continuous decorative animation;
- ambient floating blobs;
- gradient animation;
- large parallax effects;
- bouncing cards;
- motion that obscures traceability.

Respect `prefers-reduced-motion`.

## 15. Responsive behavior

Desktop is the primary operational layout. Preserve information hierarchy as width shrinks.

- Desktop: left rail + center workspace + right control plane.
- Medium screens: right control plane may become a drawer; left navigation remains compact.
- Mobile: navigation and control plane become explicit drawers; center task stays primary.
- Minimum touch target: 40px for primary mobile actions, 36px for dense desktop controls.
- Do not hide evidence/action state merely to make the layout look clean.

## 16. Accessibility

- Visible keyboard focus is mandatory.
- All state colors require textual or structural reinforcement.
- Maintain readable contrast on dark navigation.
- Do not use 10px text for important business information.
- Modal focus must be trapped and restored correctly.
- Drawers must expose expanded/collapsed state.
- Respect reduced motion.

## 17. Existing EcomEvo components

When modifying the current frontend, preserve these product concepts unless the feature itself changes:

- `.app-shell` three-column runtime layout;
- `.leftbar` business-scene navigation;
- `.workspace` central task surface;
- `.rightbar` Evidence & Authority control plane;
- `.task-head` active task identity;
- `.quick-grid` new-task starters;
- `.agent-map` / runtime path visualization;
- `.composer` multimodal task input;
- `.status-card`, `.progress-list`, `.evidence-list`, `.action-list`, `.asset-list`;
- runtime plugin control plane;
- command palette;
- accessibility and reduced-motion behavior.

Do not redesign away product capabilities simply to match a reference screenshot.

## 18. Migration priorities

When polishing existing CSS, make changes in this order:

1. normalize design tokens;
2. remove unnecessary gradients and strong shadows;
3. reduce oversized corner radii;
4. align selected/active states to one blue accent;
5. normalize button/input heights and borders;
6. improve evidence/action hierarchy;
7. tighten inconsistent spacing;
8. preserve CJK readability;
9. verify responsive drawers and keyboard focus;
10. only then consider decorative polish.

## 19. Do

- Make the product look trustworthy under high-stakes commerce decisions.
- Keep dense information scannable.
- Use dividers and alignment as hierarchy.
- Make evidence and authority boundaries obvious.
- Reuse the existing semantic CSS variables.
- Preserve product functionality before visual novelty.
- Keep blue as the single brand/action accent.

## 20. Do not

- Do not turn EcomEvo into a purple Linear clone.
- Do not use Shopify-style cinematic marketing surfaces inside the workspace.
- Do not use Claude-style warm editorial treatment for operational panels.
- Do not use glassmorphism.
- Do not add random gradients.
- Do not use huge radii everywhere.
- Do not make every component a floating card.
- Do not hide operational metadata to achieve minimalism.
- Do not display fake metrics or decorative progress.
- Do not introduce vendor logos into the product UI unless the feature explicitly requires vendor identity.

## 21. Agent prompt guide

Before editing any EcomEvo frontend file, read this `DESIGN.md` and inspect the existing component/interaction behavior first.

A good implementation prompt is:

> Refine the EcomEvo frontend using `DESIGN.md`. Preserve the current information architecture, behaviors, accessibility, runtime states, evidence model, and authority controls. Apply the Carbon-inspired token system, flatter surfaces, restrained radii, single-blue accent, CJK-first typography, and enterprise operational density. Do not redesign it as a marketing page or generic chat UI.

## 22. Reference

This design direction was selected from the public `VoltAgent/awesome-design-md` collection. The IBM entry is used as a systems-design reference because its Carbon-style enterprise structure, structured blue palette, flat surfaces, and data-oriented hierarchy fit EcomEvo's decision-runtime product better than consumer, editorial, or cinematic alternatives.
