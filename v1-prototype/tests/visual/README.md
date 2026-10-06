# Visual regression

Nineteen screens, photographed and measured at two widths — 33 captures — so
a change to the design system can be made without guessing at what it did to
the rest of the app.

## Running it

Run it against a scratch backend on this machine, with a small library made
for it (below). The shared QA library changes under a capture — someone
reads, something arrives — and since profiles, a capture cannot sign in to
production at all; opening the bell also marks that profile's news read on
the server, which is nobody's to do on a library they share.

From the repository root, with the Python 3.13 venv the backend tests use:

```
export FLIPPARR_DATABASE=/path/to/scratch/config/flipparr.db
export FLIPPARR_PROVIDER_CONFIG=/path/to/scratch/config/metadata-providers.json
export FLIPPARR_ACQUISITION_CONFIG=/path/to/scratch/config/acquisition-services.json
export FLIPPARR_SETTINGS_CONFIG=/path/to/scratch/config/settings.json
export FLIPPARR_AUTH_CONFIG=/path/to/scratch/config/auth.json
export FLIPPARR_LIBRARY_ROOT=/path/to/scratch/comics
export FLIPPARR_SAB_COMPLETE_ROOT=/path/to/scratch/completed
python -B app.py serve --port 8801
```

and from `v1-prototype/`, a Vite server proxying `/api` to it:

```
FLIPPARR_API_ORIGIN=http://127.0.0.1:8801 npx vite --port 4199 --strictPort
```

Point the scripts at both — `VISUAL_APP_ORIGIN` for every state and
`VISUAL_BACKEND_ORIGIN` for `intake`, which is served by the backend itself:

```
export VISUAL_APP_ORIGIN=http://localhost:4199
export VISUAL_BACKEND_ORIGIN=http://127.0.0.1:8801
```

(The tunnelled QA library still works for `layout:check`, which only reads:
`ssh -f -N -L 127.0.0.1:8795:<backend-address>:8795 <your-ssh-host>` and
`npm run dev:uiqa`, and the defaults point there.)

Then, from `v1-prototype/`:

```
node tests/visual/capture.mjs --out baseline     # on the commit you are changing from
# ...make the change...
node tests/visual/capture.mjs --out current
node tests/visual/compare.mjs baseline current
```

Captures are gitignored. They are ~20MB a run and regenerate deterministically
in about two minutes — the same library captured twice diffs at zero pixels
on every state — so the scripts are tracked and the output is not. To compare
against an older commit, check it out and capture again.

## What the library needs

Each state declares the selectors that prove it photographed something, and
several of them can only be satisfied by data. A scratch library that passes
every state has:

- **At least three runs, one of them with two or more readable issues of
  more than one page.** `reader-finish` opens the last page of a run's first
  issue and needs a next one to offer, and `library-in-progress` needs a
  page left to read. The reader renders the pages, so the archives need real
  images in them, not placeholder bytes: a `.cbz` of three small PNGs per
  issue is enough (a one-page issue can never be in progress).
- **One followed run** for `library-following`, and **one run in progress** —
  started, with issues still unread — for `library-in-progress`. The same run
  can be both. Scan the folder with `metadataMode: "local"` (`POST
  /api/v1/scans`) so nothing is asked of a provider, then `POST
  /api/v1/series/<id>/monitoring` with `{}` to follow it and `POST
  /api/v1/files/<fileId>/progress` with `{"page": 1}` to be part-way through
  its first issue (pages count from 0; the page must exist in the file).
- **No reader's request waiting.** A pending `member_requests` row opens the
  Pull List on its Requests tab — a queue row on desktop, a swipe card on a
  phone — which is a different page from the Wanted tab the baseline holds,
  even though `pull-list` accepts either as content.
- **No acquisition requests**, so the Pull List photographs its "All caught
  up" empty state and stays the same from one run to the next. Against a live
  library that tab changes on its own; read its row with that in mind.
- **Sign-in off, and one profile.** A fresh Playwright context carries no
  session. With sign-in off and only the admin, every request is the admin's
  and the grid opens straight away; a household with more than one profile
  opens on "Who's reading?" instead, and every state fails on the picker.
- **No metadata or acquisition services configured.** Discover's release
  shelves are stubbed (`stubs.mjs`) and the rest of the app says the
  catalogs are not connected, which is a stable thing to photograph.

The baseline was last regenerated (2026-09-28) against three runs — Saga
2012 ×3, Paper Girls 2015 ×2, Wolverine 2020 ×2, six pages each — with Saga
followed and its first issue on page 2.

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
than `dev:uiqa`; the defaults are that dev server and the tunnel it proxies.

## The guard that matters

`dev:uiqa` proxies to a tunnelled backend. If that tunnel is down, every screen
renders its empty state and a capture would "succeed" having photographed
nothing. Each state in `states.mjs` therefore declares the selectors that prove
it has real content, and the run fails if they are missing.

The same guard catches the harness's own drift. One page serves every state,
and the Comics grid remembers its view in localStorage under a key namespaced
by profile (`flipparr.u1.library`), so `capture.mjs` clears that key before
each state — `library-list` would otherwise leave a page of rows behind for
every state after it that waits for a card, which is exactly what happened
when the key gained its profile prefix and the script went on clearing the old
name. A required selector that a refactor renamed (`.notifications-menu`
became `.notifications-popover`) fails the same way, loudly, rather than
photographing the closed bell.

`intake` is captured on the backend origin, not through Vite — the dev server
only proxies `/api`, and that surface consumes the same tokens.

## The Figma file (retired)

The app was checked against the Figma file (`figma:check`) until 2026-09-19,
when the user retired it: the app had moved far enough from the file that it
no longer described the design. `DESIGN_SYSTEM.md` and the guard test are the
reference now, with `layout:check` and `visual:compare` for what renders. The
generator, node map and captured responses are in git history.
