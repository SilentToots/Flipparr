# Visual regression

Fifteen screens, photographed and measured, so a change to the design system
can be made without guessing at what it did to the rest of the app.

## Running it

Needs the UI dev loop up — the SSH tunnel to the backend and the Vite server:

```
ssh -f -N -L 127.0.0.1:8795:<backend-address>:8795 <your-ssh-host>
npm run dev:uiqa                      # or preview_start name flipparr-ui
```

Then, from `v1-prototype/`:

```
node tests/visual/capture.mjs --out baseline     # on the commit you are changing from
# ...make the change...
node tests/visual/capture.mjs --out current
node tests/visual/compare.mjs baseline current
```

Captures are gitignored. They are ~20MB a run and regenerate deterministically
in about 40 seconds, so the scripts are tracked and the output is not — to
compare against an older commit, check it out and capture again.

## What it checks

`capture.mjs` writes a full-page screenshot per screen, plus:

- **`sweep.json`** — every element's `color`, `backgroundColor`, `border*Color`,
  `outlineColor`, `boxShadow`, `fill` and `stroke`, keyed by a structural path.
  A picture says something moved; this says *which selector and which value*.
- **`summary.json`** — the distinct colours actually rendered, every text below
  WCAG AA, and every border under 1.15:1 against what is behind it.

`compare.mjs` diffs two captures and **fails on the things a colour change must
never cause**, rather than on change itself:

- a screen that reflowed (the images differ in size)
- an element that appeared or disappeared
- new text below AA contrast
- a border that stopped being visible

Drift in colour is expected while the palette migrates and is reported, not
failed. The diff images in `diff/` are there to be looked at once per screen.

A commit that removes or adds a component reflows on purpose. Declare it with
`--allow-structural` and the first three become warnings; contrast and borders
stay a hard gate, because no structural change licenses unreadable text.

To diff one commit rather than the whole migration, stash the change, capture
under any name, restore it, capture again — `*/` under `tests/visual/` is
ignored, so a capture can be called whatever the comparison needs.

## Two viewports

Every state is captured at 1440×900 and again at 375×812 as `<name>@phone`,
because most of the rules that differ between the two sit behind
`max-width: 640px` and a desktop capture never exercises them. A state marks
itself `phone: false` when the phone has no equivalent screen, and can give
`phoneRequire` when its proof-of-content selectors differ there.

The sweep records type (size, weight, line height), padding, margin, gap and
corner radius beside the
colours, so a type change shows up in the report as exactly which elements
moved and from what to what.

`VISUAL_APP_ORIGIN` and `VISUAL_BACKEND_ORIGIN` point the run somewhere other
than `dev:uiqa`. Against a live library, the Pull List changes between runs on
its own; read its row in the report with that in mind.

## The guard that matters

`dev:uiqa` proxies to a tunnelled backend. If that tunnel is down, every screen
renders its empty state and a capture would "succeed" having photographed
nothing. Each state in `states.mjs` therefore declares the selectors that prove
it has real content, and the run fails if they are missing.

`intake` is captured on the backend origin, not through Vite — the dev server
only proxies `/api`, and that surface consumes the same tokens.

## The Figma file (retired)

The app was checked against the Figma file (`figma:check`) until 2026-09-19,
when the user retired it: the app had moved far enough from the file that it
no longer described the design. `DESIGN_SYSTEM.md` and the guard test are the
reference now, with `layout:check` and `visual:compare` for what renders. The
generator, node map and captured responses are in git history.
