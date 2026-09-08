# Visual regression

Fifteen screens, photographed and measured, so a change to the design system
can be made without guessing at what it did to the rest of the app.

## Running it

Needs the UI dev loop up — the SSH tunnel to the backend and the Vite server:

```
ssh -f -N -L 127.0.0.1:8795:100.64.0.10:8795 nas
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

## The guard that matters

`dev:uiqa` proxies to a tunnelled backend. If that tunnel is down, every screen
renders its empty state and a capture would "succeed" having photographed
nothing. Each state in `states.mjs` therefore declares the selectors that prove
it has real content, and the run fails if they are missing.

`intake` is captured on the backend origin, not through Vite — the dev server
only proxies `/api`, and that surface consumes the same tokens.
