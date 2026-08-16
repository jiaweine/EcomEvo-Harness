# EcomEvo UI Design System

## Design read

**Operate-mode B2B e-commerce decision desk for high-frequency operators and reviewers.** The visual language is precise, editorial-industrial and restrained: dark ink navigation, paper-like work surfaces, one cobalt action color, semantic colors only for state, and fast state-explaining motion.

Design dials used for this product:

- Variance: **5/10** — authored enough to feel specific, familiar enough for daily operations.
- Motion: **3/10** — only feedback, continuity and state changes.
- Density: **6/10** — information-rich without shrinking Chinese text into illegibility.

## Product principles

1. **The task leads.** Navigation and chrome recede behind the current decision.
2. **Evidence before decoration.** Borders, proximity and typography create hierarchy; cards are used only when an object is actually independent.
3. **One action color.** Cobalt is reserved for primary action, current selection and progress. Success/warning/error are semantic only.
4. **Readable Chinese first.** Core product text stays at 12–14px or above. 8–10px text is metadata only.
5. **No AI-template visual tells.** No purple/blue mesh gradients, no excessive glassmorphism, no equal feature-card wall, no glow as hierarchy.
6. **High-impact actions are visually explicit.** Approval and rejection must never look like ordinary navigation.
7. **Motion explains state.** Routine transitions: 140–240ms with `cubic-bezier(.16,1,.3,1)`. No ornamental page-load choreography.
8. **Responsive behavior is structural.** Desktop uses three regions; tablet turns the inspector into a drawer; mobile turns both rails into drawers while preserving task input.

## Tokens

- Navigation: `#0D1728`
- Canvas: `#F4F5F7`
- Paper: `#FFFFFF`
- Ink: `#172033`
- Primary cobalt: `#245EDB`
- Success: `#14845D`
- Warning: `#A45C00`
- Danger: `#B42318`
- Border: `#E1E5EB`

Radii are intentionally restrained: 6–10px for controls and bounded objects. Pills are reserved for counts/status when their compact shape communicates state.

## Typography

Use one UI family with native Chinese support:

`ui-sans-serif, SF Pro Text, Segoe UI Variable Text, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif`

Display roles use the same family’s display variant where available. Product typography uses a tight hierarchy rather than exaggerated marketing scale.

## Motion

- hover/focus: 120–160ms
- normal state transition: 160–240ms
- drawers/overlays: 200–260ms
- exit should be no slower than entrance
- `prefers-reduced-motion` keeps state feedback but removes spatial choreography

## Anti-patterns

Do not introduce:

- random category colors for decoration
- nested bordered cards when proximity is enough
- 9px body copy
- full-saturation inactive states
- generic blue-purple gradients or glows
- bouncing/elastic transitions
- custom non-standard form behavior purely for visual novelty
