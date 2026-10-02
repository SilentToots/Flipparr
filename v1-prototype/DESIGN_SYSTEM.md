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
  standard fill). Buttons in content stay solid. A drawer counts as its own
  view: a run's drawer carries one prominent button (Read, in the band above
  its tabs) whatever the page behind it carries.
- **Close and back** are glass circles in the dialog's corner
  (`DialogCloseButton`, 36px, 44px on a phone). On a phone a drawer's is a
  back arrow at the top left, because a drawer there is a page pushed over
  the last; above 640px it is the X.
- A button **nested inside another control** — a menu's row — is 8px inside
  its frame; a segment inside a capsule is a capsule.
- Pills stay pills, circles stay circles.
- Covers and thumbnails are 4px. **Cards have no outline** (since
  2026-09-19): a card is a fill one step lighter than what it sits on --
  `--surface` on the page, `--surface-raised` inside a panel, the drawer's
  tone (`--art-band`) inside a toned drawer -- and corners nest: a 24px panel
  or drawer holds 16px cards, which hold 12px rows. Dialogs and sheets are
  16px on a desktop and 24px as a phone sheet. Pull List, Settings and the
  drawers all follow this; state that a border once carried is a dot.

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
  Under them, an optional tools row (tabs). The header is
  sticky. Once the page moves it *condenses*: the title scales to 18px and,
  on a desktop, the header rises into its top padding.
  Its height never changes, so nothing under a scroll jumps. Content under it
  meets a scroll edge (since 2026-09-22): the header's band is the
  sidebar's frost -- grey 6 at 72% over a 12px blur (`--scroll-edge-bg`,
  `--scroll-edge-blur`) -- so covers passing beneath the title and the tools
  show through softened, then a 24px fade below it. The blur layer exists
  from the start with no tint and stops at the header's edge, so a page at
  rest shows nothing; scrolling only changes its colour and reach, since a
  blur switched on as content arrived read as the band loading in. It is not the glass
  material's dressing: no hairline, no rim of light, no shadow, and the
  glass controls sitting on it are not glass on glass. Where transparency is
  reduced the band is solid. On a phone the header is fixed across the
  screen's top (the page keeps `--page-header-height` clear below it) and,
  condensed, carries the tint and blur itself while the band keeps only the
  fade: a blur on a sticky element stops painting on iOS during a fast
  scroll, and nested blurs render black. iOS 26 Safari also colours the
  status-bar zone by sampling that element (its own background and blur,
  not a pseudo-element's), so the frost continues up into the zone with no
  seam. The tab bar's strip stops 4px short of the bottom edge for the same
  reason in reverse: sampled there, its transparency became an opaque black
  bar.
  - `search="page"`: the Search page's own field, at 900px and below (above,
    the sidebar's field is search's). No other page has a field: on a phone
    every header carries a search button (`.appbar-search`, the bell's glass
    circle, left of it) that opens Search; `searchButton={false}` leaves it
    off Search itself.
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
- **Acquisition services** (since 2026-09-30) are grouped by what they do:
  the **Download order** first, then **Search** (Prowlarr), **Download
  clients** (SABnzbd, qBittorrent) and **Direct downloads** (DirectSite, then
  FlareSolverr, which only serves it). The order is a numbered list, one row
  per source with its client's Ready / Not connected pill; a source whose
  client is missing stays in the list, muted, and is skipped. Rows are
  dragged by a `DotsSixVertical` handle at their start: only the handle
  takes the touch (`touch-action: none`), so a swipe on the row still
  scrolls, and the lifted row gets a surface and the glass shadow ringed in
  `--line-strong` rather than a border, so nothing shifts. On the focused
  handle the arrow keys move a source a place at a time, since a drag must
  never be the only way (WCAG 2.5.7). The order saves on drop, and an
  `aria-live` line says which source is tried first.
- **Comics' tabs** (since 2026-10-01), after Plex and Komga: `SegmentedTabs`
  in the header's tools row -- Recommended, Library, Collections, Story arcs
  and Reading list. `/library` is Recommended, the page's home,
  which a tap on Comics always returns to; `/library/all` is the grid, the
  only tab with View & sort. **A tab's own actions sit at the far end of the
  tab row** (Notion's and Linear's views; the owner, 2026-10-01): View & sort on
  Library, New collection on Collections, New story arc and Import on Story
  arcs -- the title row keeps only what is the whole app's (search, bell,
  profile). They never take a row of their own: a `header-tools` container
  query shows them as their icons alone, square and named, when the row is
  narrower than 720px (one button) or 900px (two), and on a tight tablet row
  the tabs scroll a little instead. On a phone the tabs fill the row, so View
  & sort stays by the bell and New/Import sit above the grid -- a pair one
  to a column, a single button (New collection, a reader's New story arc)
  across both (`TabAction`,
  `.page-header-tab-actions`). A search on any tab shows the library's matches.
  Recommended is shelves in Discover's frame (`.release-shelf` /
  `.shelf-row` / `.pull-card`): Keep Reading (the issue to carry on with,
  its card carrying `.profile-history-progress`), Recently Released,
  Recently Added Issues and Recently Added Runs; a shelf with "See all" makes
  its title a link with a caret. A See all sorts the Library tab, never
  filters it: a filter left behind hid a later search.
- **Collections** (since 2026-10-01) are the household's own groups of runs,
  after Plex's; a series family is no longer called a collection anywhere
  (Library's Show menu says "Series families", a run's tab "Family"). A
  collection is a run card (`.series-card.collection-card`) with the
  collection mark -- MDI's bookmark-box-multiple -- in the cover's corner on
  `--scrim-strong`, its years and run count for a byline, and a muted
  "Collection" badge. The cards fill the Collections tab and lead the Library
  grid (View & sort: "Show collections in Library"). Its drawer is the comic
  drawers' frame, as a run's and an arc's are (the owner, 2026-10-01): the top
  bar (bookmark, and Edit for the admin), the cover over its own blurred art
  with "Collection • years • N runs" and status badges, then the big Read
  button every comic drawer leads with -- for the run read last unless it is
  finished, else the first unfinished in the collection's order (its own read
  target: Continue, Begin or Restart). Overview has the summary and a Runs
  shelf with *Add runs* under it (the admin's; the Set ratings list,
  choosing). The admin's tabs are Overview / *Arrange* / Advanced: Arrange is
  the Order dropdown (Your order / Title / Year -- a `GlassSelect`, not a
  segmented slider) over the `DragOrderList` (a finger lifts a row by holding its handle still for 250ms -- until then the handle scrolls like the rest of the row; a mouse drags at once) with Remove per row; Advanced is
  the counts and Delete. A reader's are Overview / Runs (the grid). A comic
  drawer with three tabs or fewer gives each an equal share of the bar; four
  still spread edge to edge. Edit (the pencil)
  is one screen: name, summary and cover (upload, or a run's cover as tiles),
  one Save. A run's drawer lists its collections as an "In Collections" row,
  and its Edit sheet ticks them. Every `.form-field textarea` is the filled
  field the inputs are (it was the browser's own box until 2026-10-01).
- **Every drawer is the comic drawer** (evaluated 2026-10-01 against the run
  drawer): top bar (actions only, the title appears once scrolled), hero with
  ownership, one action band (Read, or the drawer's one primary -- Add &
  follow, Request, Pull), glass tabs, body cards. Discover's drawers, the
  reader's finish drawer and the series family drawer use it too; a tab with
  nothing in it is not offered. Edit is one screen everywhere: the fields and
  short lists (a run's alternate titles, an arc's name) are on it, and only a
  picker (a cover, a backdrop, an arc's order) steps in, its name in the top
  bar rather than an "Editing" heading. A backdrop that will not load is left
  out, not shown broken.
- **Story arc and collection cards** (the owner's mock, 2026-10-02) are built as
  a run's: name, the byline (the publisher first when every run in it --
  and, for an arc, every issue, which must all be the library's -- is one
  house, then the years), one type badge (Story arc,
  Collection; no "N runs inside", which the size repeated), then under
  the rule its size, "13 Runs | 29 Issues" (runs, the app's word, not
  series), the owned bar only while something is missing, and "Made by" for
  an arc a profile made ("you", or its maker's name). Collections are always
  the admin's, so they name no maker. Run cards keep their status badges.
  Their drawers' headers say the same: the byline is the publisher (when one)
  and the years, and *Story arc* / *Collection* is the first badge, as on the
  card -- runs, collections and arcs share their information and patterns
  wherever that makes sense (the owner, 2026-10-02).
- **The header's cover leads** (2026-10-01): on a phone it is 140x196 from
  a 390px screen up (120x168 on a 320px one, so the badges still fit), and
  the copy beside it stays shorter than it -- a run's stars moved out of the
  header to a *Your rating* row at the top of Overview. Status chips centre
  their text both ways: equal room either side unless an icon leads, and
  1px under the text, which lifts Inter's lower-case to the optical middle.
- **Reading list** (since 2026-10-01) is each profile's own: the runs, story
  arcs and collections it added to come back to -- nothing it is merely in
  the middle of, which is Recommended's Keep Reading. The bookmark is its
  mark (Follow took the eye): a toggle in the run and arc drawers' top bar
  beside Follow, and a labelled button in a collection's drawer. Its tab is
  the Library grid's cards, newest addition first (or Title A-Z), each with
  a `.card-remove` glass button in the cover's corner; a run or arc read to
  its end steps out until new issues arrive. With four tabs a phone scrolls
  the tab row, and the open tab is brought into view once the font has
  loaded.
- **Set ratings** (since 2026-09-30) is the bulk tool behind Settings ›
  Profiles › Ratings: a modal of filters (what has no rating, what one
  limited profile can see now, or everything; a publisher, one per house
  however it is spelled; a title), a list of runs to tick (a small cover,
  the title and year, the publisher and where its rating came from), and an
  action row held at the sheet's foot -- the count, a rating or "What was
  found", Rate. On a phone the row stacks the picker over the count and
  Rate, inside the sheet's 16px inset. After rating, an `aria-live` line
  says who no longer sees the runs, or now does.
- **The frame above 640px** (since 2026-09-19) follows the Apple TV app's
  sidebar. There is no top bar: each page's header carries its bell.
  - The **sidebar** is a Liquid Glass panel floating the full height of the
    window, inset `--sidebar-inset` from its edges, with 24px corners. Rows
    are 44px pills with an icon and a label; the open one is a filled violet
    pill. Above 900px it leads with **search's field** (since 2026-10-01, as
    Messages and Figma have it): a filled field, not glass -- a blur in the
    sidebar's blur -- on every page. `/` and Cmd/Ctrl-K focus it. Its foot
    holds the F mark beside the library's size and the scan control. It does
    not fold.
  - **Search** is one page, `/search?q=`, at every width, for every profile.
    Typing shows "In Your Library" as you go -- collections, runs and saved
    story arcs, no network -- under a "Results for “x”" heading; Enter, or
    the "Search the catalogs" row (the arc row's look), asks the catalogs
    and lists Story Arcs and New Matches. A profile kept out of Discover
    searches its library only. Before a query: a hint and **Recently
    Searched** (after the Apple TV app): cards of the searches run (a
    magnifier where the cover goes; tapping runs it again) and what was
    opened from results, newest first, kept in the viewer's browser
    (`recent-searches.js`), with Clear. Esc clears, then leaves the field;
    clearing goes back to the page search was opened from.
  - **Discover** is the week's releases at every width; it has no field.
  - A **phone** has no Search tab: its tab bar keeps four, and every
    header's search button opens Search full screen, its field focused and
    a back button leading.
  - A **tablet** (641-900px) shows the rail instead: 76px, each icon over a
    10px label, as YouTube's mini guide, without the foot. It leads with a
    Search item, since a field has no room there.
  - `layout:check` holds the sidebar's inset, its field first above 900px,
    Search first on the rail, no search in a phone's tab bar but its button
    in every header, and a tablet's rail showing no F mark.

## Glass and motion

A choice from a list is `GlassSelect`, never a native select: a capsule on
the standard fill showing the value, opening a glass menu (drawn over
everything, placed from the button, flipped above it when there is more room
there) with a check on the chosen option. It joins the dialog stack, so
Escape closes the menu and not the dialog under it; arrow keys, Home, End and
type-ahead move through it, and choosing or Escape returns focus to the
button.

The bell (2026-09-27) opens a panel of the same glass on a larger screen,
placed from the bell's own position and kept 16px inside the window; on a
phone it is the standard sheet at the screen's full height. It has two halves:
"Needs you" (worked out from the library; a row is dismissed one at a time,
and comes back if the thing changes) and the news (kept per profile on the
server, so every device agrees; cleared by the row or Clear all, which never
touches what needs you). Focus opens on the heading, not on Clear all. A row opens what it is about;
clearing is its own control. News carries a cover when it has one and the
time in a phone's words ("5m", "Yesterday"), and a row new since the bell
was last opened has a violet dot. Titles wrap; nothing is truncated.

Settings chosen from a few (grid or list, runs or collections) are
`GlassSegmented`: SegmentedTabs' track and sliding glass thumb, as radios.
Comics has no tools row: one *View & sort* button in the header on every
width (the owner, 2026-09-29) opens the same controls -- scope, grid or list,
sort, the Following / In progress / arc-grouping switches -- as a sheet from
the bottom on a phone and as a drawer from the right above 640px
(`LibraryViewSheet`). The button carries a dot while anything is off its
default.

Liquid Glass is for the navigation layer only: page-header controls, the
search field, the tab bars, the drawer tabs and a drawer's top bar. Never cards, rows or covers,
and never glass on glass. One written exception: a run card's reading badge
(`.series-card-mark`) is black glass over the cover, so it reads on any art
without hiding it (the owner, 2026-09-28). `.glass-button` (a capsule, or a circle with
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
- `--motion-duration-sheet` (500ms), `--motion-duration-sheet-exit` (400ms)
  and `--motion-ease-sheet` (`cubic-bezier(.32, .72, 0, 1)`, Vaul's curve for
  an iOS sheet): a phone sheet arriving, leaving and settling back, and its
  backdrop fading with it. Brisk at first and slow to settle, never
  overshooting -- the press spring on something the size of the screen read
  as a jolt (2026-09-27). A sheet flicked away leaves at the finger's speed.
- **Drawer transitions.** A drawer slides in and out on its own; the cover
  does not fly between card and drawer (removed 2026-09-19 at the user's
  request -- it was unnecessary). A drawer's tab
  content slides in from the side the tab lies on. The scrim fades its colour
  rather than its opacity, since the drawer is inside it.
- **Text fields** are filled (`--field-bg`, a shade lighter than a standard
  button's fill), 12px corners, no outline; focus turns the edge violet. On a
  phone a `.field-group` is one rounded panel of rows, each a label and its
  field, divided by hairlines, with one focus ring around the panel.
- **A switch** is iOS's (`--switch-*`): a 51x31 track with a 27px knob inset
  2, violet when on, and the knob stretches while pressed (holding its right
  edge when on). Settings rows, a drawer's Follow band and the phone's View &
  sort sheet all use it; each keeps its own off colour.
- **The toast** is a glass capsule centred at the foot of the page (over the
  content area above 640px, above the tab bar on a phone). It springs up,
  sinks away after about three seconds, and a new one replaces it.
- **Reader profiles** (since 2026-09-25) are drawn as a coloured disc with
  white initials (`.profile-avatar`, 32px, 80px as `--lg`), in one of eight
  profile colours (`--profile-*` tokens, each a 700 shade so the initials meet
  AA). The colour is given, never chosen: a new profile gets the first one
  nobody has, and a picture is how a profile is made its own. "Who's reading?" (`.profile-picker`) is the sign-in page's shape: the
  F mark, the question, and the profiles as avatar tiles, a locked one marked
  under its name; a PIN or password is asked for on the same card. In
  Settings, the admin's Profiles list uses rows of avatar, name and a line of
  facts (`.profile-row`, the fill one step up from the card), and a profile is
  edited in a drawer (since 2026-09-30; the centred modal outgrew an iPad's
  screen): the series drawer's frame from the right, full width with Back on
  a phone, its cards raised to `--surface-raised`, and Remove and Save held at
  its foot. Every modal is bounded to the window too (`100dvh` less 40px)
  and scrolls inside. Who is reading is the header's avatar, which
  opens Your profile; the sidebar's foot no longer repeats it (2026-09-29).
- **Readers' requests** (since 2026-09-27) use the Pull List's card
  (`.request-card` with `.member-request`): cover, title, what was asked
  ("The whole run", "Issues 1, 2, 3"), who and when (a 24px
  `.profile-avatar--sm`), and a status badge -- amber waiting, violet
  approved, green in the library, muted declined or cancelled, red when an
  approval could not be carried out. Its text wraps, since a decline reason
  is a sentence. The admin answers under the card (Decline, then Approve as
  the primary); a reader's Pull reads "Request" and their Follow "Request
  follow", settling on "Requested" / "Follow requested" with an hourglass --
  a follow and a pull are different asks, and the words say which. The admin's queue is the Pull List's first
  tab while any request exists; a reader's tab bar names the same place
  "Requests".
- **Find release's set-aside rows are takeable** (since 2026-09-29). Under
  the candidates, a `<details>` (`.release-set-aside-more`, "3 more were set
  aside") folds the releases the matcher refused, each row
  (`.release-set-aside li`) the whole name wrapped, its reason, and for the
  admin a ghost "Take anyway". That button never sends anything: it opens an
  inline confirm in the row (`.release-take-confirm`, focus moved to it, the
  copy `aria-live`) that says what taking it means -- "Set aside: Not this
  series. Taking it imports it as Saga #4. The file still has to be a
  readable comic." -- with Cancel and the primary "Take it". With no
  candidates the same list stands open inside the empty state, as before. A
  DirectSite row without a solver reads "Not fetchable yet", disabled, as its
  candidates do.
- **A release row says how it travels** (since 2026-09-30): its fact line
  reads "PublicTracker · Torrent · 30 seeders · 14.5 GB" (`releaseTransport`), and
  its button names the client that takes it -- "Send to SABnzbd", "Send to
  qBittorrent", or "Download" for DirectSite (`releaseSendLabel`). A torrent
  pack adds "Only the issues wanted are downloaded from it" to its reasons,
  because its size is the whole pack's. A torrent row with no client
  connected reads "Not fetchable yet" with the reason in its hint, as a
  DirectSite row without a solver does.
- **Age ratings** (since 2026-09-27) are one scale everywhere -- Everyone,
  Teen, Teen+, Mature -- whatever the source said. A run's shows in its drawer
  as a badge in the rating colours; its Edit sheet has an "Age rating" card
  (a GlassSelect, since five choices do not fit across a phone) with Auto,
  saying where the found rating came from. A profile's limit is "What they can
  read" in its editor: a highest rating (a GlassSelect: No limit, Up to
  Everyone, Up to Teen, Up to Teen+), then, only once limited, Unrated comics
  and Discover as switches. Settings, Profiles has a Ratings card: how many
  runs are rated and from where, Look again, and the switch that lets the
  vision connector read covers (a `Toggle` that cannot be used yet is
  `disabled` and says why in its line).
- **Story arcs** (since 2026-09-27) are found by Discover's search in their
  own section, as rows (`.arc-row`: an icon, the name, "Story arc") since an
  arc has a name but no cover until opened. An arc opens in Discover's drawer:
  its cover, "Story arc - N issues - the series", one pull option ("Pull all 12
  issues", "Pull 3 missing issues", "Request" for a reader), and its issues in
  reading order as the run drawer's rows without the checkbox.
- **Deciding requests** (since 2026-09-27) takes the device's shape, chosen by
  input (`(hover: hover) and (pointer: fine)`), not width. Touch: a deck, one
  card at a time with the next peeking under it (`.request-swipe-card`) --
  swipe right approves, left declines, an Approve/Decline stamp fading in as
  it is dragged, and the same two buttons under the card. Mouse and keyboard:
  a list (`.request-queue-row`) with Decline and Approve on each row and keys
  (↑↓ or J/K, A, D, Z) hinted above it -- Seerr's requests list, a moderation
  queue's keys. Either way a decision is held five seconds in an Undo bar
  (`.request-undo`, Gmail's) before it is sent; a decline offers "Add a
  reason". Each card carries Metron's rating as a badge (green Everyone,
  violet Teen, amber Teen Plus, red Mature, muted "Not rated"), the genres,
  and the story, four lines with More.
- **The reader's settings** (`.reader-settings-drawer`, since 2026-09-24) open
  in the app's drawer, not a popover: a page pushed over the reader on a
  phone with the back arrow, a 420px side panel above it elsewhere, Back and
  Escape closing it, titled "Reader settings". Inside, Settings' own cards
  (`.settings-card`, raised, no outline, 16px): Screen, with the two sliders
  in rows; and Read by panel, whose switch sits in the card's header
  (`.toggle-row--header`, the pill alone) with everything it opens beneath
  it only while on -- the three choices as bare rows (`.toggle-row` without
  an explanation) and the two tools as the run drawer's Advanced rows
  (`.advanced-card`), one sentence each, the button under its line.
- **The panel editor** (`.panel-editor`, since 2026-09-24) is the reader's
  chrome over the page at fit size: a top bar with Done, the page number with
  its save status under it (Saving… / Saved / Unsaved changes) and a ‹ ›
  pager capsule, the page
  with each panel drawn as a violet box numbered at its top-right in reading
  order (the selected one white-edged with eight 22px handles), and a bottom
  bar with the hint and the actions -- Add panel, Set order, Delete, and
  Back to automatic on a page a person has corrected. Drag a box to move it,
  a handle to resize, an empty stretch of page to draw one; Set order numbers
  the boxes in the order they are tapped. The editor pages through the issue
  itself -- the capsule, or the keyboard's left and right with no panel
  selected (with one selected they nudge it; PageUp/PageDown always turn) --
  and a page is saved as it is left, by a turn or by Done; a page whose save
  failed is not left. Opened from the reader's settings
  ("Fix panels on this page"); what is saved is the person's until they let
  it go, and the automatic tiers never touch it.
- **The pull-to-refresh pill** (`.pull-refresh`, since 2026-09-22) is the
  same capsule at the top of the page, under the phone's safe area, and only
  in the installed app: a Home Screen web app has no browser chrome and so
  none of Safari's reload, so a pull past 80px of the phone's own rubber
  band shows "Release to refresh", and letting go there reloads. A Safari
  tab keeps Safari's.
- **Sheets on a phone.** A dialog is a sheet from the bottom, 8px in from
  the screen's edges with 24px corners, over the tab bar, with a grabber
  (`SheetGrabber`). It slides up as it opens. On a sheet with nothing to
  lose (`pullAnywhere`: the bell, the profiles, View & sort) a finger pulling
  down anywhere on it -- once it has clearly moved down, with everything under
  it scrolled to its top (`sheetPullDecision`, `sheet.js`) -- moves the sheet
  with it, as iOS does; a sheet holding a form pulls only by its grabber, so a
  stray drag cannot throw the edits away. Past a quarter of its height or on a
  flick it leaves, otherwise it settles back. However it is closed (Done, the
  backdrop, Escape, the back gesture) it slides down and its backdrop fades
  (`slideSheetAway`) rather than vanishing. A sheet with `detents` taller
  than three-quarters of the screen opens at half height and is pulled up to
  full, by the grabber. The bell and the profile menu are sheets on a phone
  and glass menus above 640px. Your profile is a sheet on a phone (it has no
  tab, and as a page left the tab bar nothing to fold into) and a page above
  640px. The tab bar folds only into a tab the phone shows (`canTuck`).
- **Choosing a profile** looks the same everywhere (`ProfileTiles`), after
  Plex's "Who's watching?": a disc with the name under it in regular weight,
  the one reading now named in white and marked "Reading now", and Add as a
  plain grey disc with a thin plus. On a phone -- the picker and the header's
  sheet -- they are three to a row across the full width, each disc as wide as
  its column, its initials scaled to it; above 640px the picker centres them
  and the header's menu lays them from the left.
- **Reading state (2026-09-28).** Once an issue is owned, its badge slot (where
  Missing / Waiting sit on an unowned one) says how it stands: "In progress"
  (violet, over the progress bar) or "Read" (green); unread is plain, as in
  Komga, Plex and Panels. Changing it is never a tap on the cover -- that
  opens the comic -- but a "..." menu (`IssueMenu`, `ActionMenu`): revealed on
  hover on a desktop, always shown to a finger, and opened by a long press on
  a tile. It holds Read / Continue, Mark as read and Mark as unread -- both
  offered while an issue is part-read, so a place someone else left on your
  profile can be cleared -- and the admin's Edit. The run's own "..." in its group header marks
  the whole run, as does the Advanced tab. A run's card carries the same
  words, centred over its cover as an issue's are, as a black glass pill
  (`--scrim-strong` over `--glass-blur`, white type, a step larger): "In
  progress" on a run that has been started, "Read" with a check once every
  issue is, nothing before (a bare count of what was left said nothing). The
  drawer's hero repeats it as a status badge, with how many are read. A
  story arc -- issues saved to read in order across runs -- is a card in the
  Comics' *Story arcs* tab with the same badge, and its drawer lists the issues in order, each
  saying whether it is here, on the way or missing, and whether it is read.
  On the Runs shelf a run kept only for an arc -- not followed, every owned
  issue an arc's -- is folded into the arc's card (no badge says so; its size already counts every run it spans), as a
  confirmed collection folds its runs; View & sort's *Story arcs* switch,
  "Collect story arc issues", turns it off, per profile. A search looks through
  everything, folded or not.
- **Story arcs tab** (since 2026-10-01, the owner: making an arc was buried in
  a run's issue menus). Comics' tabs are Recommended · Library · Collections ·
  Story arcs · Reading list (`/library/arcs`); the Story arcs scope left View &
  sort. The tab leads with *New story arc* (and *Import* for the admin), as
  Collections leads with *New collection*. On a phone the pair is a two-column
  grid matching the covers' columns below (`.arc-tools--pair`), worded *New
  arc* / *Import arc* so each fits a 320px screen's 138px column. A new, empty arc opens its drawer
  with `ArcIssuePickerModal` on top -- Spotify's empty playlist asking what to
  put in it: find a run, tick its issues or a *From # / To #* range, *Add*,
  and go on to another run without leaving; issues already in the arc are
  ticked and fixed. An arc's drawer offers *Add issues* under Read for
  whoever may change it. A run's Issues tab has *Select* (beside the run's
  menu): a tap ticks an issue, its read/menu/edit buttons step aside, and a
  floating bar says how many, with *Select all* and *Add to story arc*.
- **Hand-made story arcs** (since 2026-10-01). Any profile makes its own arc:
  *New story arc* on the Story arcs tab, *Add issues* inside it, *Select* in a
  run, or *Add to story arc…* in any issue's "…" menu, owned or missing, which opens
  `ArcPickerModal` -- the arcs this profile may change, latest first, with
  *Added* on one that holds the issue, and a name field to start a new one.
  An added issue goes on the end (Komga, Spotify). The arc drawer's Edit ›
  Reading order is the shared `DragOrderList` (handle drag, arrow keys), each
  row with a "…" menu (`ArcOrderRowMenu`): *Move to top* / *Move to bottom* --
  the way through a long arc -- and Remove. A menu, not three 44px buttons,
  so a phone row keeps ~170px for the issue's name. *Sort by release date*
  (its own line under the note, so Cancel and Done stay together) orders
  everything by cover date once, to fine-tune from; it is not a live sort. A hand-made arc is its maker's until
  shared (Advanced › *Share with the household*, a `FollowSwitch`); cards and
  the drawer say "Made by you" (the drawer adds " · shared") or "Made by
  Sam", the same words everywhere since 2026-10-02, and nothing on
  the household's arcs. Only the maker edits a hand-made arc; the admin may
  stop sharing or delete a shared one. Advanced is every profile's now, and
  holds *Export as a reading list* (CBL or JSON). A hand-made arc never folds
  runs into its card.
- **Uploaded covers for arcs and collections** (since 2026-10-01). An arc's
  Edit › Cover leads with *Upload an image*; the picture becomes the cover
  and stays among the choices as "Your picture". A collection's Edit has
  *Upload* beside its Cover choice; the picture leads (as "Your uploaded
  picture") until another cover is chosen and saved, which removes it. Both
  are made a bounded JPEG on the server, as a run's uploaded cover is.
- **Arrivals.** Nothing snaps in: a page fades as it opens (`.page-view`,
  keyed by the view -- a fade only, and `animation-fill-mode: backwards`,
  because a transform here would make the page the containing block for every
  fixed thing inside it), content fades in where its loading placeholder was, and
  a grid's first screenful of cards arrives in order (`--card-index`, 24ms
  apart, capped at twelve). All of it is mount-time animation, so there is no
  state to keep.
- **Counters.** A number that changes under the reader counts to its new
  value (`useCountUp`, `count-up.js`): fast first, settling at the end, 220ms
  plus 12ms a step to a 900ms cap. It starts at the first number it is given,
  so a library's size does not run up from zero on every load.
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
