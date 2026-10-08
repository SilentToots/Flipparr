# Offline reading for Flipparr — implementation plan

Written 2026-10-08 for execution by another model without this conversation.
Repository: `/Users/blee/Developer/Comic Arr Project` (branch `v1-forward`).
Backend: `app.py`, `catalog_store.py`, `access_policy.py`, tests `test_*.py`
(Python 3.13 venv; the system python3 is 3.9 and fails). Frontend:
`v1-prototype/` (React 18, Vite 6; `src/App.jsx` ~13k lines). Line numbers
are as of 2026-10-08 and drift — search by the names given.

## Context

Flipparr is a self-hosted comic catalog and reader (pre-release, production
track; see `AGENTS.md`). The owner reads mostly on an iPad and wants to read
with no connection to the home server: download issues at home, read on a
flight, have the reading place sync back. Today nothing works offline: pages
are served one at a time with `Cache-Control: private, no-cache`, there is no
service worker, no on-device store, and progress is a last-write-wins POST
with the server's clock.

**Product decisions (the owner's, 2026-10-08 — settled, do not reopen):**

- iPad first (iPhone too). A PWA installed to the Home Screen first; a
  native (Capacitor) shell is deferred behind a trigger, not ruled out —
  see Architecture.
- Downloadable: one issue; a run's unread issues ("Download the next N
  unread", N chosen in a sheet, default 5); a story arc or reading list
  (whole, in reading order). No automatic downloads.
- Pages stored at the reading size (`size=read`, 1800 px JPEG, ~10 MB an
  issue), byte for byte as the server sends them.
- Downloads belong to the profile that made them. On a shared iPad each
  profile sees only its own; a PIN'd profile can be unlocked offline (a hash
  of the PIN on the device, never the PIN); progress replays to the right
  profile on reconnect.
- 2 GB cap per device by default, changeable in Settings; when exceeded,
  the oldest *finished* issues are removed first, never one in progress; a
  download that cannot fit says so.

**Platform facts (checked 2026-10-06, record the sources with the code):**

- A Home Screen web app on iOS/iPadOS keeps its storage (origin quota ~60%
  of disk; exempt from Safari's 7-day script-storage eviction, which does
  apply to a plain Safari tab). Sources: MDN "Storage quotas and eviction
  criteria"; web.dev "Storage for the web".
- Background Fetch and Background Sync do not work on iOS/iPadOS web apps:
  downloads run only while the app is in the foreground. Sources:
  whatpwacando.today/background-fetch, firt.dev/notes/pwa-ios.
- The shell CSP has `img-src 'self' https: data:` (no `blob:`) and
  `worker-src 'self'`, `connect-src 'self'`: a same-origin service worker
  answering the page URLs from the Cache API needs no CSP change.

**Rules that bind every phase** (from `AGENTS.md`, `DESIGN_SYSTEM.md`, memory):

- Tests with every change; backend suites in both orders
  (`python -B -m unittest test_app test_catalog_store test_http_contract
  test_page_panels test_panel_benchmark test_content_rating
  test_no_undefined_names test_reading_list_formats test_torrent_client
  test_arc_catalog test_provider_evidence test_secret_hygiene`, then
  reversed); frontend `npm test` (node:test over `tests/*.test.mjs`),
  `npx eslint src tests`, `npx vite build`.
- Design tokens only in `styles.css` (`tests/design-system.test.mjs` fails
  otherwise); Phosphor icons only; mobile-first; reduced motion honoured;
  accessible status messages. For any `styles.css` change run
  `npm run visual:capture -- --out baseline` on HEAD, `--out current`
  after, `npm run visual:compare`, and `npm run layout:check`, against the
  scratch stack (`VISUAL_APP_ORIGIN=http://localhost:4199
  VISUAL_BACKEND_ORIGIN=http://127.0.0.1:8802`; recipe in
  `tests/visual/README.md`). New screens are added to
  `tests/visual/states.mjs` and audited by `npm run a11y:audit`.
- The rating gate is server-side and must hold offline: a page is only on
  the device if the gate answered 200 for this profile when it was
  downloaded; profiles' caches are separate; on reconnect the device asks
  which downloads this profile may no longer see and removes them.
- Never log secrets; never store the PIN; `support_safe` in log lines.
- The NAS Docker is the QA runtime. Deploy recipe (memory `flipparr-deploy`):
  config backup → `git archive HEAD` to a fresh `~/flipparr-build` →
  `docker build -t flipparr:candidate` → smoke import → SABnzbd download
  guard (0 downloading) → tag `flipparr:local` → `docker compose up -d
  --force-recreate flipparr` → `/healthz` shows the build. Deploy backend
  changes promptly; batch UI polish.
- Commit as `SilentToots <261538712+SilentToots@users.noreply.github.com>`;
  `git checkout .claude/launch.json` before committing; the owner pushes.

## Architecture

**A same-origin service worker at `/sw.js`, scope `/`, plus an IndexedDB
store, plus three small server routes.** Pages are kept in the Cache API
keyed by their exact `readUrl`, so `<img src={item.readUrl}>` in the reader
is unchanged and no `blob:` is needed; the HTTP cache stays `no-cache` so
the rating gate still runs for every view that is *not* a download.

**The page store is behind an adapter (decided 2026-10-08, after the owner
weighed a native shell).** `src/page-store.js` exports one interface the
rest of the code uses: `put(profileId, fileId, index, readUrl, response)`,
`has`, `delete(profileId, fileId)`, `deleteProfile`, `usage()`, and
`pageSrc(profileId, fileId, index, readUrl)` — what the reader puts in
`<img src>`. The web implementation (`src/page-store-web.js`) is the Cache
API plus the service worker below, and `pageSrc` returns `readUrl`
unchanged. Nothing outside the adapter may call `caches.*`. The reason: a
Capacitor shell was considered and deferred, not rejected. WKWebView has
no service worker outside App-Bound Domains and none from Capacitor's
custom scheme, so a native build would store pages in the Filesystem and
`pageSrc` would return `Capacitor.convertFileSrc(path)`; every other part
of this plan (routes, IndexedDB rows, engine, replay, profiles, eviction,
run/arc downloads) is shared verbatim. **Trigger for building the shell:**
the iPad proof at the end of Phase 2 shows foreground-only downloads or
storage eviction to be a real nuisance. It costs the owner a $99/yr Apple
Developer Program, signing and TestFlight, and me a Swift background
`URLSession` plugin (no production Capacitor plugin exists) and live-update
wiring (Capgo updater, "serve your own zip" mode, MPL-2.0 to be verified)
so the bundled UI tracks the server's. Not before the trigger.

Why not the other web option: IndexedDB blobs shown via
`URL.createObjectURL` need `blob:` in `img-src` and a rewrite of every
image element; the Cache API keyed by the real URL needs neither.

### The service worker (`v1-prototype/src/sw.js` → `dist/client/sw.js`)

- Built by a second Vite config `v1-prototype/vite.sw.config.mjs`:
  `build.emptyOutDir: false`, `build.outDir: "dist/client"`,
  `rollupOptions.input: "src/sw.js"`, `output.entryFileNames: "sw.js"`
  (no hash), `define: { __FLIPPARR_BUILD__: JSON.stringify(<git sha or
  Date.now()>) }`. `package.json` `"build": "vite build && vite build -c
  vite.sw.config.mjs"` — the main build must run first (it empties the
  folder). The Dockerfile (L11–13, L87) runs `npm run build` and copies
  `dist/client` → `/app/web`, so nothing else changes for the image; CI's
  `web` job runs `npm run build` too.
- Served by `handle_web_asset` (`app.py` ~L20350): root files get
  `Cache-Control: no-cache` (right for a SW); add `".js": "text/javascript"`
  to `WEB_ASSET_TYPES` (~L2379) so the type never depends on the host's
  mimetypes table (`_web_asset_body` appends `; charset=utf-8`). Note: a
  *missing* `/sw.js` falls back to `index.html` with 200 — the contract test
  must cover both the file present and absent.
- eslint: `eslint.config.mjs` gains an override `{ files: ["src/sw.js",
  "src/sw-routing.js"], languageOptions: { globals: globals.serviceworker } }`
  (`globals` ^17 is already a dev dependency).
- Registration in `src/main.jsx` after `window.load`, only when
  `"serviceWorker" in navigator`, `import.meta.env.PROD`, and the protocol is
  `https:` or the host is `localhost`/`127.0.0.1`:
  `navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache:
  "none" })`. On `updatefound` → `installed` while
  `navigator.serviceWorker.controller` exists, dispatch a `window` event
  `flipparr:update-ready`; `App` shows a toast "Flipparr was updated —
  Reload" (`showToast` ~L11988 gains an optional action); Reload posts
  `{type: "skip-waiting"}` to the worker and reloads on `controllerchange`.
- **Fetch rule, exact (pure function `routeFor({method, url, headers})` in
  `src/sw-routing.js`, tested in `tests/sw-routing.test.mjs`; the worker
  only applies it):**
  1. method not GET → network.
  2. pathname matches `^/api/v1/files/\d+/pages/\d+$` and
     `searchParams.get("size") === "read"` and the request has no header
     `x-flipparr-fetch: network` → **pages**: `caches.open("flipparr-pages-u"
     + activeProfile)` → `cache.match(request.url, {ignoreVary: true})`
     (full URL, `v=` included; `ignoreVary` because `send_image` answers
     with `Vary: Cookie` and `Cookie` is a forbidden header the Cache API
     cannot compare — without the option a match can miss for no visible
     reason) → hit: return it; miss: `fetch(request)` untouched. The worker
     never writes to this cache; the download engine is the only writer.
  3. any other pathname starting `/api/` → network only, never cached.
  4. pathname starting `/assets/` (hashed, immutable) → cache-first from
     `flipparr-shell-<build>`.
  5. navigations and `/`, `/index.html`, `/manifest.webmanifest`, `/brand/*`
     → **network-first**, falling back to the shell cache only when the
     network fails; offline with nothing cached → `Response.error()`. (Not
     stale-while-revalidate: with several deploys a day a bad shell would
     otherwise stay pinned on every device until a second reload, with no
     recovery short of clearing site data. The cost is one round trip on a
     cold start online.)
  6. anything else (`/healthz`, unknown paths) → network, untouched.
- `activeProfile`: the page writes `{key: "activeProfile", value: viewerId |
  null}` to the IndexedDB `settings` store at boot and in `enterProfile`
  (`App.jsx` ~L252); the worker reads it on its first fetch after start and
  on `message {type: "profile", id}`. A null profile makes rule 2 fall
  through to the network.
- Install: fetch `/index.html`, collect the `/assets/...` `src`/`href`
  values, cache them with `/`, `/manifest.webmanifest` and the `/brand/*`
  icons index.html names. Activate: delete other `flipparr-shell-*` caches;
  **never** touch `flipparr-pages-*`. `__FLIPPARR_BUILD__` in the cache name
  makes every build a byte-different worker so `updatefound` fires.

### The device store (IndexedDB `flipparr-offline`, version 1; `src/offline-db.js`)

| store | keyPath | indexes | row |
|---|---|---|---|
| `downloads` | `["profileId","fileId"]` | `profileId`; `["profileId","state"]`; `["profileId","lastReadAt"]` | `{profileId, fileId, seriesRunId, seriesTitle, issueNumber, filename, medium, readingDirection, cover (= pageUrls[0]), pageCount, fileSignature, pageUrls[], pagesDone, bytes, state: "queued"|"downloading"|"ready"|"failed"|"stale", downloadedAt, lastReadAt, finished, groupKey, error}` |
| `panels` | `["profileId","fileId","page"]` | `["profileId","fileId"]` | `{…, data}` — the `/panels` JSON as answered |
| `progressQueue` | `["profileId","fileId"]` (a later save replaces the row) | `profileId` | `{profileId, fileId, page, panel, at (ISO UTC, `new Date().toISOString()`), finished}` |
| `profiles` | `profileId` | — | `{profileId, name, colour, role, lock, pinLength, pinHash?, updatedAt}` |
| `settings` | `key` | — | `activeProfile`, `deviceSalt` (16 random bytes), `capBytes` (default 2 147 483 648), `persisted` |

- Cap accounting: usage = Σ `downloads.bytes` across all profiles (the cap
  is the device's). `navigator.storage.estimate()` only feeds the Settings
  usage bar. `navigator.storage.persist()` is requested on the first
  download; the answer is shown in Settings.
- `localStorage["flipparr.offline"] = {capBytes, persisted}` for the shell,
  written with `window.localStorage` directly (per device; `profileStorage()`
  would namespace it).
- Eviction (`evictionOrder` in `src/offline.js`): candidates are `state ===
  "ready" && finished`, oldest `lastReadAt` (then `downloadedAt`) first;
  never `queued`/`downloading`, never unfinished, never the issue being
  read. If evicting every candidate still cannot fit, the download is
  refused: "Needs X MB more than the cap allows".
- `fake-indexeddb` (Apache-2.0, 6.2.5) as a dev dependency for
  `tests/offline-db.test.mjs`; keep the wrapper thin so `offline.js` (pure)
  carries the logic.

### Server additions (all reader-class, all through the rating gate)

Facts to rely on: pages `GET /api/v1/files/{id}/pages` → `{fileId, pageCount,
pages: [{index, url, readUrl}]}` (`file_pages` ~L13979; `_page_url` ~L13693;
`_file_signature` ~L13594 = `mtime_ns`-hex + `size`-hex; `cached_page_members`
~L13664); page image `GET …/pages/{n}?size=read` → JPEG via
`render_file_page` ~L14850, `send_image` ~L20300 (`private, no-cache`, weak
ETag, `Vary: Cookie` — keep). Progress table `reading_progress(user_id,
file_id, page, panel, page_count, file_signature, started_at, finished_at,
updated_at)` PK (user_id, file_id); `set_reading_progress` (`catalog_store.py`
~L3989) upserts with `updated_at = _utc_now()`; `file_reading_progress`
~L14783; `POST /api/v1/files/{id}/progress` ~L19596 accepts `{page, panel?}`
| `{read: bool}` | `{page: null}`; `set_file_reading_progress` ~L14804.
Routing: `access_policy.py` `ROUTE_ACCESS` (file routes READER at L134–140;
unlisted `/api` routes are ADMIN); the dispatcher's rating gate `_run_visible`
~L18102 already covers every `/api/v1/files/{id}/…` path (404 for a limited
reader); the avatar POST ~L18862 shows the by-hand gate for an id in a body.
`send_json` ~L20327 sets `private, no-store`. Adding a reader route = the
regex in `ROUTE_ACCESS` + the branch in `_route_get`/`_route_post` with the
same regex + the sample in `NOT_ADMIN` of `test_http_contract.py` (~L1786),
or `RouteCensusTests` fails.

1. **`GET /api/v1/files/{id}/offline`** → `{fileId, fileSignature, pageCount,
   bytesEstimate, pages: [{index, readUrl}], series: {runId, title,
   issueNumber, filename, medium, readingDirection}, cover:
   "/api/v1/files/{id}/pages/0"}`. New `CatalogStore.file_offline_context
   (file_id)` (one query over `files`, `file_identities`, `series_runs`;
   `LookupError` if absent or `present = 0`) and `file_offline_manifest
   (file_id)` beside `file_pages`, reusing `file_pages`, `_file_signature`,
   `cached_page_members`, `store.file_reading_direction`. `bytesEstimate` = Σ
   sizes of reading-size cache files already rendered for this file under
   `reading_cache_dir()`, else `pageCount × 380_000`.
2. **`POST /api/v1/offline/check`** body `{files: [{fileId, fileSignature}]}`
   (≤ 500 entries, else 400) → `{files: [{fileId, status: "ok" | "changed" |
   "gone" | "hidden"}]}`. Per file: absent or `present = 0` → `gone`; a
   non-admin viewer with `max_rating` for whom `self._run_visible(f"/api/v1/
   files/{fid}/pages/0")` is false → `hidden`; signature ≠ `_file_signature
   (path)` → `changed`; else `ok`.
3. **`at` on the progress POST**: optional ISO-8601 time with a zone (a
   naive value → 400). Normalise `at = min(parsed → UTC, now)` so a clock
   ahead cannot lock out later writes. Thread `at` through
   `set_file_reading_progress` to `set_reading_progress`: when `at` is given,
   read the stored row's `updated_at`, parse both with
   `datetime.fromisoformat`, and **skip the write when the stored time is
   newer** (compare parsed datetimes in Python, not strings: `_utc_now()`
   omits the fraction when microseconds are 0); a kept write stores
   `updated_at = at`. Without `at` the write always wins, as before. The
   docstring says so.
4. `WEB_ASSET_TYPES[".js"] = "text/javascript"`.
4a. **`?vision=no` on `GET /api/v1/files/{id}/pages/{n}/panels`** (route
   ~L18518): when present, `allow_vision=False` and `vision_budget_allows`
   is **not** called. `file_page_panels` already takes `allow_vision`; the
   local tiers (ONNX detector, gutter finder) still run and their answer is
   stored as today, so a page read later online is upgraded by the model
   exactly as it is now (`upgrade` in `_file_page_panels` ~L14461). The
   download engine always sends it: a five-issue run download is ~110
   pages, and the owner's stated cost is a wrong result, not a spent
   budget — but he has not been asked whether a download should spend the
   model; this default spends nothing and the handoff names the question.
   Test: a reader with `visionForReaders` on and the flag set leaves
   `_VISION_SPEND` untouched; the admin with the flag set gets a stored row
   whose source is not `vlm`.
5. Tests: `NOT_ADMIN` gains `("GET", "/api/v1/files/1/offline"): "reader"`
   and `("POST", "/api/v1/offline/check"): "reader"`; a manifest test
   modelled on the page-list test (~L1216); `/sw.js` present → 200
   `text/javascript; charset=utf-8` + `no-cache`, absent → the shell; in
   `ReaderProfileHttpTests` (~L1976, `call()`): a reader whose `maxRating`
   is below a run's rating gets 404 from `/offline` and `hidden` from
   `/offline/check`, the admin gets `ok`; `changed` after rewriting the
   archive; `gone` for an unknown id. `test_catalog_store.py`: an older
   replayed save does not overwrite a newer one; a newer one does; no `at`
   wins. `test_app.py`: POST with an older `at` leaves the page unchanged;
   a naive `at` → 400.

## Phases, in order

### Phase 0 — server routes and `at` (1 session, deployable alone)
Everything under "Server additions". Proof: both suite orders green; deploy
to the NAS; `curl` the three routes as the admin and as a limited reader
(the owner's instance has reader profiles; never clear their records).

### Phase 1 — service worker, shell precache, update toast (1 session)
Files: `src/sw.js`, `src/sw-routing.js`, `vite.sw.config.mjs`,
`package.json`, `eslint.config.mjs`, `src/main.jsx`, `src/App.jsx`
(`showToast` action), `src/offline-db.js` (the `settings` read the worker
needs; plain IDB code importable by a worker), `tests/sw-routing.test.mjs`.
Proof: `npm run build` leaves `dist/client/sw.js`; served by `app.py` with
`FLIPPARR_WEB_ROOT=…/dist/client` on `http://localhost`: the worker is
active, the shell loads with the network set to offline (page images
absent); a rebuild shows the toast and Reload picks up the new assets;
`npm test`, eslint, build green; contract tests for `/sw.js`.

### Phase 2 — IndexedDB, download engine, single-issue download, Downloads surface (2 sessions)
Files: `src/offline.js` (pure: `planDownload(manifest, {capBytes, usageBytes,
downloads}) → {fits, bytes, evict, shortfall}`, `evictionOrder`,
`queueProgress` (coalesce per file), `mergeProgress(local, server)` (newer by
`at`/`updatedAt`), `downloadRow(manifest, profileId, groupKey)`,
`nextUnread(issues, n)`, `formatBytes`), `src/page-store.js` (the
adapter interface and the `pageStore` the app imports) +
`src/page-store-web.js` (Cache API implementation), `src/offline-db.js`,
`src/offline-downloads.js`, `src/profiles.js` (`READER_SURFACES` gains
`settings.offline`), `src/App.jsx`, `src/styles.css`, tests
`tests/offline.test.mjs`, `tests/offline-db.test.mjs`, `tests/profiles.test.mjs`.

- Engine (`src/offline-downloads.js`, in the page, never the worker):
  an `EventTarget` singleton with `enqueue(fileId, {groupKey})`, `cancel`,
  `remove`, `removeAll`, `resume`. Loop: first `queued` row of the active
  profile → `downloading` → `GET /api/v1/files/{id}/offline` (404 → `failed`
  "hidden", nothing stored; 401/403 → pause the queue) → `planDownload` →
  evict per plan (delete cache entries and rows) or `failed` "doesn't fit"
  → pages three at a time: `fetch(readUrl, {cache: "no-store", headers:
  {"x-flipparr-fetch": "network"}})`; on 200 `image/jpeg` → `pageStore.put
  (profileId, fileId, index, readUrl, response)` (the web adapter does
  `cache.put(readUrl, response)` into `flipparr-pages-u<profileId>`), add
  the body length to `bytes`, `pagesDone++`, emit `progress`; a 404 mid-way removes
  everything stored for the file (`failed` "hidden"); a network error
  retries after 1 s, 4 s, 16 s then leaves the row `queued` for `resume()`.
  Then panels: `GET …/pages/{n}/panels?vision=no` for every page into
  `panels` (a 4xx stores `{segmented: false, panels: []}`; a network failure
  does not block `ready` — panels are best-effort; the flag keeps a
  download from spending the vision budget, see server addition 4a). `state = "ready"`, `downloadedAt`.
  `resume()` on `visibilitychange` → visible, `online`, and boot.
  `navigator.storage.persist()` on the first enqueue.
- UI: `useDownloads()` hook (rows for the active profile, `byFile`, usage).
  `IssueMenu` (~L7782): "Download for offline" / "Remove download" /
  "Downloading… (cancel)". `GroupedIssueInventory` tiles and rows
  (~L7946/7953) and Keep Reading cards (`RecommendedView` ~L3232,
  `LibraryShelf` ~L3289): `<StatusBadge tone="green">Downloaded</StatusBadge>`.
  `ProfileView` (~L6012): a "Downloaded" shelf (`LibraryShelf`,
  `profile-history` cards, `onRead({id: fileId})`) with See all →
  `/profile/downloads` (`stateFromLocation`/`locationForState` ~L11292/
  L11306 learn it): rows with state, `formatBytes`, a progress bar
  "Downloaded N of M pages", Remove, Cancel, Re-download; the phone
  `ProfileSheet` (~L6070) gets the shelf. Settings: `SETTINGS_SECTIONS`
  (~L5432) gains `{id: "offline", label: "Offline reading", icon:
  DownloadSimple, group: "reading", detail: "What this device keeps for
  reading without a connection, and how much room it may take."}`;
  `SettingsView` block for `current === "offline"`: cap (`GlassSelect` 1/2/
  4/8 GB), usage bar (Σ bytes vs cap, `estimate()` beneath), "Remove all
  downloads" (danger, confirmed), "Kept when storage is low: yes/no" with a
  Request button; `/settings/offline` validates through `settingsSectionsFor`.
  New classes `.download-progress`, `.offline-usage-bar` — tokens only.
- Proof: unit tests; lint; build; on the local prod build download an
  issue — DevTools shows `flipparr-pages-u1` with `pageCount` entries keyed
  by the exact `readUrl`; reload offline → the Downloads page lists it;
  Remove empties the cache. Visual harness before/after at both viewports
  and `layout:check`.
- **iPad proof, before Phase 3 starts** (deploy Phases 0–2 to the NAS for
  it; the UI is deployable without the reader work): on the owner's iPad,
  installed to the Home Screen over https, download five issues; switch
  apps mid-download and come back (the queue resumes — note how much was
  lost); lock the iPad mid-download; leave it eight days and reopen (the
  rows and cache are still there); Settings shows `persisted`. Record the
  outcome in `docs/INSTALL_PROOFS.md`. **This is the decision point for
  the native shell** (see Architecture): if foreground-only downloads or
  eviction made this a chore, stop and raise it with the owner before
  Phase 3; if not, the PWA path continues.

### Phase 3 — the reader offline, and progress replay (1–2 sessions)
Files: `src/App.jsx` (`ReaderView` ~L9957–10974, `apiRequest` ~L648,
`loadCatalog` ~L12030, `App`), `src/offline.js`, `src/offline-progress.js`,
`tests/offline.test.mjs`.
- `useNetwork()` in `App`: `online` from `navigator.onLine` and the
  `online`/`offline` events; `apiRequest` sets `network = "offline"` on a
  fetch `TypeError` and `"online"` on any response. Distinct from
  `backendStatus === "offline"` (server down with connectivity).
- `loadCatalog`: when the failure is a `TypeError` and `!navigator.onLine`,
  do not show `DEMO_SERIES`; keep `catalog` null, set `network = "offline"`,
  pause polling until `online`. Library tab: offline, render the Downloaded
  shelf first and replace the `.backend-banner` (~L3217) with a
  `role="status"` line "You're offline — N downloaded issues are ready."
  Opening a non-downloaded issue: toast "This issue isn't downloaded.
  Connect to read it."
- `ReaderView` page-load effect (~L10104): first `getDownload(profileId,
  fileId)`; if `ready` → `list = pageUrls.map((readUrl, index) => ({index,
  readUrl: pageStore.pageSrc(profileId, fileId, index, readUrl), url:
  readUrl}))` with no network for pages (on the web `pageSrc` is the
  identity; it is the one seam a native build changes); the place =
  `mergeProgress(queue row, server GET when online)`; title and series from
  the row through a new `offlineTitle` fallback where `readingTitle`
  currently falls back to "Reading" (~L13170). Panels effect (~L10158):
  `getPanels` before `apiRequest`; offline with no row → `{segmented: false,
  panels: []}` (`readablePanels` then shows the whole page / quadrants).
  `lastReadAt` and `finished` are written to the row on every `keepPlace`.
- `keepPlace` (~L10293): always `putProgress({profileId, fileId, page, panel,
  at: new Date().toISOString(), finished})`; online, POST the same body the
  replay would send and delete the queue row on success. **Body for a queue
  row, exact:** `finished` → `{read: true, at}`; otherwise `{page, panel, at}`
  (`panel` omitted when null). `at` governs every form the route accepts
  (`{page, panel?}`, `{read}`, `{page: null}`) — the Phase 0 merge rule is
  applied in `set_file_reading_progress` before any of them writes, and the
  tests cover `{read: true, at}` with an older `at` as well as `{page, at}`.
  `src/offline-progress.js` `replayProgress()` on boot, `online`, and before
  `onProgressSaved`: rows in `at` order, POST each; 401/403 → stop and keep
  the rows; 404 → drop the row and mark the download `failed` "hidden";
  network error → stop. Replay only when `authStatus.viewer.id ===
  row.profileId`.
- Proof: node tests for `mergeProgress` and queue order; Playwright
  `tests/offline/offline.e2e.mjs` (Chromium against the prod build served
  by `app.py` on localhost): download an issue → `context.setOffline(true)`
  → `/?read=<id>` opens, three page turns, panel view shows stored panels,
  close → `setOffline(false)` → `GET …/progress` reports the page reached;
  an offline save with an older `at` after a newer online save does not
  regress the page.

### Phase 4 — profiles offline, and the rating re-check (1 session)
Files: `src/App.jsx` (picker ~L6155–6185, `switchToProfile` ~L13142,
`enterProfile` ~L252, `signOut` ~L12018, forget devices ~L7339),
`src/offline-pin.js`, `src/offline-db.js`, `tests/offline-pin.test.mjs`.
- The `profiles` store is refreshed from `GET /api/v1/profiles` whenever the
  picker loads online; a profile whose `lock` is no longer `pin` loses its
  `pinHash`.
- `src/offline-pin.js`: `hashPin(pin, salt)` = WebCrypto PBKDF2-SHA256,
  200 000 iterations, 256 bits, hex; `verifyPin`. The salt is
  `settings.deviceSalt` (made once with `crypto.getRandomValues`). After a
  successful online `POST /api/v1/profiles/switch` with a PIN, store
  `pinHash` on that profile's row. Module header cites the W3C WebCrypto
  spec (PBKDF2 `deriveBits`) and the OWASP Password Storage Cheat Sheet,
  and states plainly: this is device-local and unthrottled, weaker than
  the server's scrypt check (`check_profile_switch` ~L1034), acceptable for
  a 4–6 digit PIN on a device the household owns; password-locked profiles
  have no offline unlock; the PIN is never stored or logged.
- Offline switch: the picker lists the `profiles` store when offline; a
  PIN'd profile is checked with `verifyPin`; success → `putSetting
  ("activeProfile", id)`, `localStorage["flipparr.offlineSwitch"] = id`,
  `enterProfile(id)` (reload). The worker's cache name follows
  `activeProfile`, so another profile's pages are never served. On
  reconnect, if `authStatus.viewer.id !== cachedViewerId()` and
  `flipparr.offlineSwitch` is set, open the picker on that profile ("Enter
  your PIN to carry on as …") before any replay; a queue is replayed only
  under its own profile's cookie.
- Sign-out and forget-devices: delete every `flipparr-pages-*` cache and
  the `downloads`, `panels`, `progressQueue`, `profiles` stores (the shell
  cache stays).
- Reconnect check: on `online`, on boot when online, **and on
  `visibilitychange` → visible while online** (one POST; cache-first pages
  would otherwise keep showing a downloaded issue *online* after the admin
  lowered this profile's rating, until the next boot), `POST /api/v1/
  offline/check` with every `ready`/`stale` row; `hidden`/`gone` → remove
  cache entries and rows (toast "N downloads were removed: they're no longer
  available to this profile"); `changed` → `stale` with Re-download. The
  window between a rating change and the next check is stated in Risks.
- Proof: node tests for `hashPin` (a fixed vector checked against
  `node:crypto` `pbkdf2Sync`) and the offline picker list; Playwright: a
  reader with `maxRating` below a run → the download fails "hidden" and
  nothing is cached; the admin lowers a reader's rating → the reader
  reconnects → the download disappears; two profiles switched offline →
  each sees only its rows and the other's pages miss the cache.

### Phase 5 — run and arc downloads, eviction, Settings polish (1–2 sessions)
Files: `src/App.jsx` (`SeriesDrawer` ~L8521, `.comic-drawer-actions` /
`ReadRunButton` ~L8398, `ReadingListDrawer` ~L9013, `RunCollectionDrawer`
~L2160, the reader settings drawer), `src/offline.js`, `styles.css`.
- Run drawer: a secondary "Download" button beside Read → a sheet
  "Download the next N unread" (stepper 1–20, default 5) from `GET /api/v1/
  series/{id}/reading` + `nextUnread(issues, n)`; shows Σ `bytesEstimate`
  (one `/offline` GET per issue), what fits under the cap and what would be
  evicted; Confirm enqueues with `groupKey: "run:<id>"`.
- Arc and collection drawers: an Advanced card "Download this arc" (the
  `advanced-card` + `LoadingSpinner` busy pattern, ~L8786) over the list's
  items in reading order, `groupKey: "arc:<id>"`.
- Reader settings drawer: "Download this issue" / "Downloaded".
- Settings copy for eviction; "Remove finished downloads"; a cap change
  applies at the next download (say so on the card).
- Proof: node tests for `nextUnread` and `planDownload` with eviction;
  Playwright: cap 30 MB, download three issues, finish the first, download
  a fourth → the finished one goes, an unfinished one never; a fifth that
  cannot fit says so. Harness states `downloads`, `settings-offline`,
  `library-offline` (route `**/api/v1/catalog` to abort
  `internetdisconnected` and `context.setOffline(true)` after load) at
  both viewports; `a11y:audit` on them.

### Phase 6 — docs, harness, the iPad checklist (1 session)
- `docs/OPERATING.md`: "Offline reading" before "Known limitations": what
  the device keeps (pages at reading size, panels, places, profile names
  and a PIN hash), the cap, foreground-only downloads on iOS, Home Screen
  install needed for storage to persist, how to clear (Settings → Offline
  reading → Remove all; sign out). `docs/PRIVACY.md`: "On the device".
  `docs/RELEASE_TRACK.md`: the entry with its evidence. `DESIGN_SYSTEM.md`:
  the progress and usage bars. `README.md`: one line.
- iPad checklist (record the outcome in `docs/INSTALL_PROOFS.md`): install
  to the Home Screen over https; download five issues; airplane mode →
  open from the Home Screen, read, panel view, rotate, lock and unlock,
  switch profile with a PIN; leave the device idle eight days and reopen
  (storage still there); reconnect → progress on the server; the
  "Update ready" toast after a deploy.
- Deploy to the NAS (backend in Phase 0 already; the UI batched here).

## Risks and limits (say them in the docs and the commit)

- iOS/iPadOS web apps download only while in the foreground; the queue
  resumes on return.
- Storage can still be evicted under pressure even after `persist()`; a
  plain Safari tab loses everything after seven days unused.
- Downloading stores panels from the local tiers only (`?vision=no`): an
  issue read offline shows the detector's answer, not the model's, unless
  the page was already read online. **Open product question for the owner:**
  should a download spend the vision model instead (admin: unmetered;
  readers: 300 pages a day), given a five-issue run is ~110 pages?
- The rating gate is enforced at download time and re-checked on boot,
  reconnect and every return to the foreground; between an admin lowering
  a profile's rating and the next of those events, a downloaded issue is
  still readable on that device (cache-first). There is no server push.
- The `at` merge is last-write-wins by time; there is no three-way merge.
- The offline PIN hash is device-local and unthrottled; password-locked
  profiles cannot be unlocked offline.
- A replaced file leaves a stale download until reconnect marks it;
  cache-first means a changed page is not seen until Re-download.
- Offline there is no catalog: the Downloaded shelf, Keep Reading from
  downloads and the reader work; drawers and search need a connection.

## Verification, end to end

1. Backend suites both orders; `npm test`; eslint; `vite build`.
2. Phase proofs above, each on the scratch stack (`tests/visual/README.md`;
   the scratch library at `$SCRATCH/stack`, backend on 8802, vite on 4199
   with `FLIPPARR_API_ORIGIN=http://127.0.0.1:8802`).
3. `tests/offline/offline.e2e.mjs` end to end in Chromium (and WebKit where
   Playwright's WebKit supports the APIs; WebKit cannot emulate Safari's
   chrome or Home Screen storage rules — the iPad checklist covers those).
   **The offline e2e is the only harness that lets the worker register.**
   `tests/visual/capture.mjs` and `tests/a11y/audit.mjs` create their
   contexts with `serviceWorkers: "block"` (Phase 1 adds this the same
   commit the worker lands): the a11y audit points at 8802 serving the prod
   build, where `/sw.js` is real, and this project has twice mistaken a
   stale harness for a product failure — a worker serving yesterday's
   shell across captures would be a third.
4. Visual harness and `a11y:audit` on the new states.
5. NAS deploy and the iPad checklist, recorded.

---

# Appendix — Panel scanner plan (approved 2026-10-06; status 2026-10-07)

Done: Phase 0 (benchmark `tools/panel_benchmark.py`, `page_panel_history`,
fixtures), 1d (leftover pass on the default install), vision answers kept
in `page_vision_answers` (schema 67), panel fixes keyed by page fingerprint
with export/import (schema 66). Dropped after the benchmark: the relative
"valley" cut (split more panels than gutters it found) and the solid-band
rule (broke the vision path's coverage gate in production). Still open:
1c insets; dark pages crossed by light (`expectedFailure` fixtures in
`test_page_panels.py`); Phase 2 (vision asked for structure over local
candidates, more of the owner's pages as examples, record/replay fixture,
live checkpoint the owner judges) — gated on `page_vision_answers` filling
during the owner's reading; Phase 3 (editor: source badge, Ask the model,
merge/split). Rules: never spend the owner's vision key from tools or
tests; manual rows are sacred and the benchmark; a change that does not
move the aggregate or regresses white pages is dropped; any change to the
leftover pass or masks is checked against the vision path's coverage
(`test_the_leftover_pass_never_leaves_a_solid_band_uncovered`). Evidence in
`docs/PANELS.md`.
