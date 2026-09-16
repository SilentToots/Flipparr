# Flipparr UI system

Screens are built from tokens and a few shared components. A value that is not
a token needs a written reason, and `tests/design-system.test.mjs` — run by
`npm test` and in CI — fails when one lands without it. That test is the
enforcement; this file is the explanation.

## Where things live

- `src/design-tokens.css` — every token: palette, semantic colours, type,
  spacing, radii, motion, component values. The only file that may write a
  colour.
- `src/styles.css` — component and layout rules, consuming the tokens.
- `src/components/` — `Button`, `StatusBadge`, `LoadingIndicator`.
- `tests/design-system.test.mjs` — the guard. Its allowlists name each
  exception and say why.
- `tests/visual/` — `figma:check` (the design file's values, asserted against
  the running app) and `visual:capture` / `visual:compare` (every screen at
  1440 and 375, with colour, type, spacing and contrast recorded per element).

## Type

Inter, bundled (`@fontsource-variable/inter`, the optical-size cut, OFL-1.1 —
the licence ships at `/licenses/Inter-OFL-1.1.txt`). Do not rely on a system
font: phones without Inter rendered a narrower one, and layouts tuned here broke
there.

Seven sizes, each at least 2px from the next, so two sizes side by side read as
a deliberate difference:

| Token | Size | For |
| --- | --- | --- |
| `--text-2xs` | 10px | Badges and chips, the phone's two-up card metadata, the tab bar's count. **Only** these — never running text. |
| `--text-xs` | 12px | Captions, metadata, `<small>` (its default here), tab-bar labels |
| `--text-sm` | 14px | Secondary copy and every control |
| `--text-md` | 16px | Body, emphasis, and every tab |
| `--text-lg` | 18px | Sub-headings |
| `--text-xl` | 20px | Section and drawer headings |
| `--text-2xl` | 24px | Page and dialog titles |

`--text-input` (16px) is a platform constant, not a step: iOS zooms the page
when a focused field is smaller. Use it on fields only.

Weights: `--weight-regular` 400, `--weight-medium` 500, `--weight-semibold`
600, `--weight-bold` 700, `--weight-black` 900. Line heights:
`--leading-none` 1, `--leading-tight` 1.2, `--leading-snug` 4/3 (12px text on a
16px line), `--leading-normal` 1.45, `--leading-relaxed` 1.55.

## Spacing and layout

Padding, margin and gap use `--space-N`, named for its value: every 2px from 2
to 16 for the insides of controls, then 20, 24, 28, 32, 40, 48, 64, 80, 96. A
name never drifts from its number; the guard holds them equal.

When a value falls between steps, an odd one goes to the neighbouring multiple
of 4 and an even one to the step below. Two exceptions are allowed: `1px`
hairlines, and the 3px vertical inset of a badge (12px of content in an 18px
chip). A negative bleed is `calc(var(--space-N) * -1)` and must equal the
padding it cancels.

Lengths that belong to a component and are used by more than one rule are named
once: `--appbar-height`, `--sidebar-width`, `--sidebar-width-collapsed`,
`--mobile-nav-height` (what every phone screen clears at the bottom). An indent
that lines up with a row's text is computed from the row's parts — see
`--request-cover` and `--source-icon` — not written as a number.

## Corners

`--radius-4`, `--radius-6`, `--radius-8`, `--radius-12`, `--radius-16`,
`--radius-pill`; `50%` only for a true circle.

- **Buttons are 8px**, the design's control radius.
- A button **nested inside another control** — a toggle's segment, a menu's
  row — is 4px inside its 8px frame.
- Pills stay pills, circles stay circles.
- Covers and thumbnails are 4px, cards 8px, dialogs and sheets 16px.

## Colour

Three layers: a palette (`--color-*`), semantic tokens (`--bg`, `--surface*`,
`--line*`, `--text`, `--muted*`, the accents and their `-soft`, `-border` and
`-text` steps), and component tokens (`--button-*`, `--badge-*`, the scrims,
washes and shadows). `styles.css` writes no colour of its own; the only
literals the guard accepts there are black as a mask stencil and the tab
glass's parametric white.

Surfaces follow iOS's dark mode, because zinc greys close to black vanish on
an iPhone's OLED screen: the page is black, a card or the rail is gray 6
(`--surface`, #1c1c1e), anything raised off a card or tinted on it is gray 5
(`--surface-raised`, `--surface-soft`, #2c2c2e), and hovers and lines are gray 4
(`--surface-hover`, `--line`, #3a3a3c). Use `--surface-hover` for any hover or
focus fill, never `--surface-soft`.

Every text colour clears WCAG AA (4.5:1) on every surface it can sit on.

- `--muted` (#b4b4bb) and `--muted-weak` (#9e9ea8, 4.82:1 at worst) are for
  text; neither is held to AA on a hover fill.
- `--chrome-label` (the design's #71717a) is **not** text: progress and switch
  tracks, and icon-only buttons, which need 3:1 and get 3.52 on a card.

Alpha layers have jobs, not values: `--wash-subtle` (a panel lifted off its
background), `--wash-hover` and `--wash-active` (on dark chrome),
`--shade-hover` and `--shade-subtle`, `--scrim`, `--scrim-medium`,
`--scrim-strong`, and `--shadow-faint`, `--shadow-soft`, `--shadow-strong`.

## Components

- **Badges** have one look everywhere: a filled pill, 10px bold, 18px tall,
  the accent at 40% (`--badge-violet-bg`, `--badge-muted-bg`) or solid
  (`--badge-green-bg`, `--badge-amber-bg`, `--badge-red-bg`), with white type.
  New status labels use `StatusBadge` with a tone.
- **Tabs** have one look: 16px regular, 44px tall, 24px apart, muted until
  selected, then the text colour and a violet underline. A row wider than the
  screen scrolls sideways. The run drawer's tabs are the same type on a violet
  bar with a glass pill. Selecting a tab never changes its size or weight.
- **Buttons**: new actions use `Button`, including its `busy` state.
- **Loading**: all asynchronous UI uses `LoadingIndicator`; no one-off
  spinners or keyframes.
- **Empty states** use `.empty-state`: an icon, a heading and a centred,
  padded message.
- **The phone tab bar** is a 360px pill whose four tabs share its width —
  24px icons, 12px labels, 56px-tall tabs — so the glass on the open one is a
  capsule. It is inset 8px on every side, in an 8px strip. Scrolling tucks it
  into a 64px circle at the left gutter.

## Breakpoints

Three, written as a `max-width` on one side and a `min-width` a pixel later on
the other:

- **Phone**, up to 640px (`min-width: 641px` above it).
- **Tablet**, up to 900px: the nav is an icon rail (`min-width: 901px` above
  it).
- **Wide**, above 900px, with one exception at `max-width: 1100px` for wide
  content.

Components that size themselves use container queries instead; those are not
breakpoints. Components must work from 320px up without a separate mobile
implementation.

## The Figma file

The Figma file is where a screen starts, not a gate on every change. Where the
app deliberately differs, record it in `tests/visual/figma/node-map.json` —
`ignoreProps` for a style, `ignoreSize` for an icon, a `false` and a `_note` for
a frame dimension — saying what the file says, why the app differs, and what is
still asserted. Then `npm run figma:generate`.

## Changing styles

1. `npm test` — the guard. A new value needs a token, or an allowlist entry in
   the test with its reason.
2. `npm run lint` and `npm run build`.
3. `npm run figma:check` against a running app (`FLIPPARR_UI_ORIGIN`). It
   needs a real library behind the app, so it runs locally rather than in CI.
4. `npm run visual:capture -- --out baseline` before the change and
   `--out current` after, then `npm run visual:compare`. It fails when
   contrast gets worse or the layout moves; read its list of changed
   properties, which should contain only what you meant to change. Point it
   elsewhere with `VISUAL_APP_ORIGIN` and `VISUAL_BACKEND_ORIGIN`.

## Framework policy

Do not mix a utility framework into individual screens. If Tailwind is adopted,
configure its theme from these tokens and migrate complete component
boundaries. Until then, the shared components and the token layer are the
styling API.
