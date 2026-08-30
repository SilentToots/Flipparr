# SonicBoom UI system

SonicBoom uses semantic design tokens and reusable responsive components. Screens should compose these primitives instead of introducing local colors, spacing, motion, or loading behavior.

## Source of truth

- `src/design-tokens.css` contains primitive and semantic tokens.
- `src/components/` contains reusable interaction and display primitives.
- `src/styles.css` contains component and layout rules that consume the tokens.

## Responsive component contract

Components must work from the 320px application minimum through desktop widths without requiring a separate mobile implementation. Prefer intrinsic layouts (`minmax`, grid, flex, wrapping, and container-aware sizing) and add viewport breakpoints only when the content hierarchy must change.

Use these viewport bands consistently when a breakpoint is necessary:

- Compact: up to 620px
- Medium: 621px–900px
- Wide: above 900px

Touch controls use the semantic control-height tokens, and important actions must remain reachable without horizontal scrolling.

## Loading states

All asynchronous UI uses `LoadingIndicator`. Its size, motion, color inheritance, and reduced-motion behavior are controlled by design tokens. Do not add rotating icon classes or one-off keyframes.

Use semantic sizes for new work: `xs`, `sm`, `md`, `lg`, or `xl`. Numeric sizes are temporarily normalized by the component while existing call sites migrate.

## Buttons and statuses

New interactive actions use `Button`, including its `busy` state, semantic variant, size, icon, and label. New status labels use `StatusBadge` with a semantic tone. Legacy button and status classes remain temporarily supported while screens are migrated in complete, testable batches.

Do not put raw colors, spinner markup, or breakpoint-specific button copies into a feature screen. Extend the component API or its semantic tokens when a genuinely reusable state is missing.

## Framework policy

Do not mix a utility framework into individual screens. If Tailwind is adopted, configure its theme from the existing semantic CSS variables and migrate complete component boundaries. Until then, shared React components plus the token layer remain the styling API. This avoids maintaining competing systems during the migration.
