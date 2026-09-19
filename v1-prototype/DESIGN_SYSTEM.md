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
- `tests/visual/` — `layout:check` (edges, alignment and placeholder fit at
  seven widths) and `visual:capture` / `visual:compare` (every screen at
  1440 and 375, with colour, type, spacing and contrast recorded per element).

## Brand

The brand is the F bubble mark (`FlipparrMark`), and only that: in the
sidebar's foot, on sign-in and setup, and anywhere else the app names itself. The
full wordmark is not used.

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

- **Buttons are capsules** (`--radius-pill`, since 2026-09-19, after Liquid
  Glass), of three kinds: **prominent** (`.primary-button`, the violet fill,
  one per view; the Pull button is this), **standard** (`.secondary-button` and
  `.ghost-button`: a translucent `--button-standard-bg` fill, no outline, so it
  reads on any surface) and **destructive** (`.danger-button`: red on the
  standard fill). Buttons in content stay solid.
- **Close and back** are glass circles in the dialog's corner
  (`DialogCloseButton`, 36px, 44px on a phone). On a phone a drawer's is a
  back arrow at the top left, because a drawer there is a page pushed over
  the last; above 640px it is the X.
- A button **nested inside another control** — a menu's row — is 8px inside
  its frame; a segment inside a capsule is a capsule.
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
- **Page tabs** (Pull List, Settings) are `SegmentedTabs`, after iOS 26's
  segmented control: a pill track on `--surface`, 14px semibold tabs 36px
  tall inside a 4px inset, and the glass thumb (`.glass-indicator`) sliding to
  the open one. A row wider than the screen scrolls inside the pill and the
  open tab is scrolled into view. A count rides the tab as a badge.
- **Drawer tabs** keep the underline look (16px regular, 44px tall); the run
  drawer's are the same glass on a violet bar. Selecting a tab never changes
  its size or weight.
- **Page headers** (`PageHeader`) are every page's top, at every width
  (since 2026-09-17; the violet app bar is gone). The page's name is a 24px
  bold `h1`, alone: nothing sits under a title, so every page's reads the
  same (the library's size is in the rail's foot and Settings › Library
  folders). On the right: the page's actions and one primary action, as glass
  controls, then the bell.
  Under them, an optional tools row (tabs, or view and sort). The header is
  sticky. Once the page moves it *condenses*: the title scales to 18px and,
  on a desktop, the header rises into its top padding.
  Its height never changes, so nothing under a scroll jumps. Content under it
  meets a scroll edge: solid to the header's bottom, then a 24px blurred fade.
  The header itself is never a glass panel.
  - `search="phone"`: the page's own field on a phone only, on a row of its
    own. On Comics it filters your comics as you type, and a query with no
    match offers the catalogs; on Discover it searches your library and the
    catalogs. Above 640px the sidebar's Search page does both.
  - `search="page"`: the field at every width -- the Search page's.
  - `narrow`: a form page's column (`--page-narrow`, 920px), so the header
    stops where its content does.
  - `leading`: a back button, before the title in the same cell.
  - Every first row shares one centre line (`--page-header-row`). Content starts at the header's own spacing, never with a
    top margin of its own.
- **Settings** (since 2026-09-19). Above 640px: one panel, the sections
  listed down its left (the open one a violet-soft pill, Library health's
  count as a red badge) and the open section's title, a line on what it
  holds, then its blocks as titled `SettingsCard`s with an optional action at
  the title's end. `/settings` opens Library health. On a phone it is two
  levels, as iPhone Settings: `/settings` is `SettingsIndex` (grouped rounded
  rows of an icon tile, the name, what is set and a chevron), and each
  section opens as its own page with a back button; tapping the Settings tab
  inside a section returns to the list.
- **The frame above 640px** (since 2026-09-19) follows the Apple TV app's
  sidebar. There is no top bar: each page's header carries its bell.
  - The **sidebar** is a Liquid Glass panel floating the full height of the
    window, inset `--sidebar-inset` from its edges, with 24px corners. Rows
    are 44px pills with an icon and a label; the open one is a filled violet
    pill. It leads with **Search**, its own page (`/search?q=`): your library
    and the catalogs, the results a phone sees on Discover. Before a search
    the page is its field, centred between a heading and a hint and focused
    on arrival, then **Recently Searched** (after the Apple TV app): cards
    of the searches run (a magnifier where the cover goes; tapping runs it
    again) and what was opened from results, newest first, kept in the viewer's
    browser (`recent-searches.js`), with Clear. With results the field moves
    to the header. Its foot holds the
    F mark beside the library's size and the scan control. It does not fold.
  - **Discover** above 640px is the week's releases only; its field and
    results are a phone's. A search crosses over with the width: `/search`
    on a phone opens as Discover's results, and Discover's results above
    640px open as the Search page.
  - A **phone** has no Search tab: its tab bar keeps four, and searches from
    Discover.
  - A **tablet** (641-900px) shows the rail instead: 76px, each icon over a
    10px label, as YouTube's mini guide, without the foot.
  - `layout:check` holds the sidebar's inset, Search first in the sidebar and
    absent from a phone's tab bar, and a tablet's rail showing no F mark.

## Glass and motion

A choice from a list is `GlassSelect`, never a native select: a capsule on
the standard fill showing the value, opening a glass menu (drawn over
everything, placed from the button, flipped above it when there is more room
there) with a check on the chosen option. It joins the dialog stack, so
Escape closes the menu and not the dialog under it; arrow keys, Home, End and
type-ahead move through it, and choosing or Escape returns focus to the
button. The notifications menu is the same glass.

Settings chosen from a few (grid or list, runs or collections) are
`GlassSegmented`: SegmentedTabs' track and sliding glass thumb, as radios.
Comics' tools row is two of those on the left and a Sort + Following
`.glass-capsule` at its end.

Liquid Glass is for the navigation layer only: page-header controls, the
search field, the tab bars, the drawer tabs and a drawer's top bar. Never cards, rows or covers,
and never glass on glass. `.glass-button` (a capsule, or a circle with
`--icon`), `.glass-button--primary` (one per page), `.glass-field`,
`.glass-capsule` (related controls in one pill: its segments are plain, never
glass on glass, divided by a hairline; a segment that is on fills violet) and
`.glass-menu` (a menu grown from the control that opens it, denser than a
control, `--glass-menu-bg`) use the `--glass-*` tokens: a tinted blur, a hairline, a rim of light along the top,
and a soft shadow. Where the browser cannot blur, the fill is solid. Under
`prefers-contrast: more` and `prefers-reduced-transparency` (Chromium) the
glass turns solid.

Motion tokens:
- `--motion-duration-morph` and `--motion-ease-morph`: one shape becoming
  another (the tab bar tucking away, the header condensing).
- `--motion-duration-press` and `--motion-ease-spring`: a press. The spring is
  a `linear()` curve, with a `cubic-bezier` fallback.
- **Drawer transitions.** A drawer slides in and out on its own; the cover
  does not fly between card and drawer (removed 2026-09-19 at the user's
  request -- it was unnecessary). A drawer's tab
  content slides in from the side the tab lies on. The scrim fades its colour
  rather than its opacity, since the drawer is inside it.
- **Sheets on a phone.** A dialog is a sheet from the bottom, 8px in from
  the screen's edges with 24px corners, over the tab bar, with a grabber
  (`SheetGrabber`): the sheet follows a finger on it, and leaves past a
  quarter of its height or on a flick, otherwise settling back. A sheet with
  `detents` taller than three-quarters of the screen opens at half height and
  is pulled up to full.
- Reduced motion collapses every duration.

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

Retired on 2026-09-19 at the user's request: the app has moved far enough
from it that it no longer describes the design. This document, the tokens and
the guard test are the reference; new screens follow the patterns here rather
than the file.

## Changing styles

1. `npm test` — the guard. A new value needs a token, or an allowlist entry in
   the test with its reason.
2. `npm run lint` and `npm run build`.
3. `npm run layout:check` against a running app (`VISUAL_APP_ORIGIN`): header,
   content and rail edges at five widths, and every placeholder's fit.
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
