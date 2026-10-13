# Offline reading for Flipparr — implementation plan

Written 2026-10-08 for execution by another model without this conversation;
rewritten the same day around a native reader app (the first draft was a
Home Screen web app — see "Decisions"). Repository: `/Users/blee/Developer/
Comic Arr Project` (branch `v1-forward`). Backend: `app.py`,
`catalog_store.py`, `access_policy.py`, tests `test_*.py` (Python 3.13 venv;
the system python3 is 3.9 and fails). Frontend: `v1-prototype/` (React 18,
Vite 6; `src/App.jsx` ~13.2k lines). Line numbers are as of 2026-10-08 and
drift — search by the names given.

## Context

Flipparr is a self-hosted comic catalog and reader (pre-release, production
track; see `AGENTS.md`). The owner reads mostly on an iPad and wants to read
with no connection to the home server: download issues at home, read on a
flight, have the reading place sync back. Today nothing works offline: pages
are served one at a time with `Cache-Control: private, no-cache`, nothing is
stored on the device, and progress is a last-write-wins POST with the
server's clock.

## Decisions

**Product decisions (the owner's, 2026-10-08 — settled, do not reopen):**

- iPad first (iPhone too). A PWA installed to the Home Screen first; a
  native (Capacitor) shell is deferred behind a trigger, not ruled out —
  see Architecture. *(Superseded the same day — see the next block.)*
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

**Phase 2 decisions (the owner's, 2026-10-08):** the panel editor stays
web-only; App ID `com.silenttoots.flipparr.view`; he joins the Apple
Developer Program now (not at Phase 7). Enrolment can take a day or two;
Phase 2 works in the simulator meanwhile. Capacitor's CLI sends usage
telemetry by default: run `npx cap telemetry off` once per machine.

**Native reader app (the owner's, 2026-10-08 — settled):**

- Offline reading is a **native iPad/iPhone reader app** (Capacitor), not a
  Home Screen web app. Reason: a web app's storage can still be evicted
  under pressure and is opaque to manage, and its downloads stop whenever it
  leaves the screen.
- The app is **reader-only**: connect to a server, sign in, pick a profile,
  browse what to read (Keep Reading, runs, arcs and reading lists), read
  with panel view, download, and sync the place. Search, Discover,
  requests, acquisition, library management, settings beyond the app's own
  and every admin surface stay in the web app.
- **One codebase.** The reader is moved out of `App.jsx` into its own
  module; the web app and the reader app both import it. A reader fix is
  one commit that reaches both builds.
- The web app stays a connected app: no service worker, no offline mode.

**Open questions for the owner (not decided; the executing session asks
before the phase that needs the answer):**

1. ~~Should a download spend the vision model?~~ **Answered 2026-10-08: no;**
   downloads use the local finder (`?vision=no`). Originally: should a download spend the vision model on panels?
   The plan defaults to `?vision=no` (local tiers only; the model's answer
   arrives when the page is later read online). A five-issue run is ~110
   pages; admin is unmetered, readers 300 pages a day.
2. ~~Is the panel editor web-only at first?~~ **Answered 2026-10-08: yes.**
   The app's reader hides the editor (`canEditPanels: false`); panels are
   fixed in the web app.
3. ~~Keep a record of downloads?~~ **Answered 2026-10-08: yes** --
   `offline_downloads` (schema 68), `GET/POST /api/v1/offline/downloads`,
   `POST /api/v1/offline/downloads/remove`. Originally: should the server keep a record of downloads (who,
   which file, when, removed when)? `AGENTS.md` (2026-10-07) says evidence
   a later analysis depends on goes in the catalog, never only on a device
   or in a log. If he will ever want to know how offline reading is used,
   that is a table (`offline_downloads`, its migration, a reader, a test);
   otherwise nothing is kept server-side. The plan leaves it out until he
   answers.
4. *Phase 8 only:* live updates of the app's UI from the household's own
   server — yes or no, after reading Apple's rule quoted under "Platform
   facts". The plan ships without them.

## Platform facts (checked 2026-10-08; record the sources with the code)

- **Capacitor** is at major version 8 (capacitorjs.com/docs; MIT). The app
  is served from `capacitor://localhost` on iOS; files on disk are shown in
  the web view through `Capacitor.convertFileSrc(path)`.
- **Cross-origin:** every request from `capacitor://localhost` to a
  household's server is cross-site. Flipparr's session is a cookie
  (`app.py` `_set_cookie` ~L17969, `SameSite=Lax`) and `_same_origin`
  (~L17888) refuses a state change whose `Origin` is not the `Host`. So the
  app can use neither the cookie nor `<img src>` pointing at the server, even
  online. Phase 0 adds a bearer token.
- **CapacitorHttp** (core plugin, off by default; `plugins.CapacitorHttp.
  enabled: true`) patches `fetch`/`XMLHttpRequest` to native requests (no
  CORS). Its docs warn that large payloads over the bridge cause problems
  and point to file plugins for files: JSON goes through it, **page images
  never do**.
- **Background downloads:** iOS keeps a background `URLSession` transfer
  running while the app is suspended and relaunches the app to deliver
  completion (`application(_:handleEventsForBackgroundURLSession:
  completionHandler:)`). A force-quit cancels it. Plugins checked:
  `@capacitor/file-transfer` (official) is foreground-only; Capawesome's
  File Transfer (background `URLSession`, Aug 2026) is **Insiders-only**,
  a paid licence key, unusable in a publicly distributed build; Capgo's
  Downloader says "under development and not yet ready for production use".
  **So Flipparr writes its own plugin** (Phase 2), under the repo's licence.
  Sources: capawesome.io/blog/announcing-the-capacitor-file-transfer-plugin,
  capgo.app/docs/plugins/downloader, capacitorjs.com/docs/apis/http.
- **Web storage inside the app:** Apple does not document the eviction rules
  for a WKWebView's website data under a custom scheme. **Nothing that
  matters is kept in IndexedDB or localStorage**: rows and pages are files
  in the app container under `Library/` (iOS does not purge it as it does
  `Library/Caches`, and the Files app does not show it).
- **Backups:** iOS includes `Library/` (except `Library/Caches`) in iCloud
  and device backups by default. Apple's data-storage guidance asks for
  re-downloadable data to be kept out of backups, and 2 GB of pages in
  every backup is wrong anyway. The plugin sets `isExcludedFromBackup`
  (`URLResourceValues`; developer.apple.com, "isExcludedFromBackupKey") on
  the `flipparr/` folder, and sets it again at launch, because Apple notes
  some file operations reset it. `@capacitor/filesystem` cannot set it.
  To confirm on a device in Phase 3: the app's backup size in Settings ›
  iCloud stays small after five downloads.
- **App Store rules** (developer.apple.com/app-store/review/guidelines,
  read 2026-10-08):
  - 4.2 Minimum Functionality: "elevate it beyond a repackaged website" —
    a native reader with background downloads and offline reading is the
    case for it, and it is the reason the app is not a web view of the site.
  - 2.5.2: apps may not "download, install, or execute code which
    introduces or changes features or functionality of the app." Live UI
    updates (Capgo, Appflow) rely on an exception for code run by WebKit
    that does not change the app's primary purpose, in the Developer
    Program License Agreement 3.3.1(B). **That wording was seen only in
    secondary sources (the agreement is behind sign-in) — unverified.**
    Hence open question 4 and no live updates by default.
  - App Review needs a working server and account to review a client: the
    release phase provides a demo server (see Phase 7).
- **App Transport Security:** a household server on the LAN is often plain
  `http`. `Info.plist` sets `NSAllowsLocalNetworking` (LAN and `.local`
  names) and nothing broader; a public `http` address or a self-signed
  certificate is refused, and the connect screen says so ("Use the https
  address, or a name on your local network").
- **Signing:** the Apple Developer Program ($99/yr), the team ID,
  certificates and provisioning profiles are the owner's. None of them
  enters git (`AGENTS.md`: no private credentials); the Xcode project reads
  the team from an untracked `Signing.local.xcconfig`, and `.gitignore`
  lists it.

## Rules that bind every phase (from `AGENTS.md`, `DESIGN_SYSTEM.md`, memory)

- Tests with every change; backend suites in both orders
  (`python -B -m unittest test_app test_catalog_store test_http_contract
  test_page_panels test_panel_benchmark test_content_rating
  test_no_undefined_names test_reading_list_formats test_torrent_client
  test_arc_catalog test_provider_evidence test_secret_hygiene`, then
  reversed); frontend `npm test` (node:test over `tests/*.test.mjs`),
  `npx eslint src tests`, `npx vite build`, and from Phase 2 the reader-app
  build too.
- Design tokens only in `styles.css` (`tests/design-system.test.mjs` fails
  otherwise); Phosphor icons only; mobile-first; reduced motion honoured;
  accessible status messages. For any `styles.css` change run
  `npm run visual:capture -- --out baseline` on HEAD, `--out current`
  after, `npm run visual:compare`, and `npm run layout:check`, against the
  scratch stack (`VISUAL_APP_ORIGIN=http://localhost:4199
  VISUAL_BACKEND_ORIGIN=http://127.0.0.1:8802`; recipe in
  `tests/visual/README.md`). New web screens go into
  `tests/visual/states.mjs` and `npm run a11y:audit`.
- New components in their own files, not added to `App.jsx` (`AGENTS.md`:
  reusable components rather than one monolith).
- The rating gate is server-side and must hold offline: a page is only on
  the device if the gate answered 200 for this profile when it was
  downloaded; profiles' files are separate; on reconnect the device asks
  which downloads this profile may no longer see and removes them.
- Never log secrets or tokens; never store the PIN; `support_safe` in log
  lines. The bearer token is kept in the iOS Keychain, never in a file, web
  storage, a URL or a log.
- **Panel hold:** panel-finder and vision changes are on hold for the
  owner's data review (~2026-10-15 to 10-22; memory `panel-scanner-plan`).
  Phase 0's `?vision=no` is inert until something sends it. The app sends
  it from Phase 3: until the review is done, Phase 3 onward is tested
  against the scratch stack, never production.
- The NAS Docker is the QA runtime. Deploy recipe (memory `flipparr-deploy`):
  config backup + saved `docker logs` → `git archive HEAD` to a fresh
  `~/flipparr-build` → `docker build -t flipparr:candidate` → smoke import
  → SABnzbd download guard (0 downloading) → tag `flipparr:local` →
  `docker compose up -d --force-recreate flipparr` → `/healthz` shows the
  build. Deploy backend changes promptly; batch UI polish.
- Commit as `SilentToots <261538712+SilentToots@users.noreply.github.com>`;
  `git checkout .claude/launch.json` before committing; the owner pushes.

## Architecture

### One codebase, two builds

```
v1-prototype/
  src/reader/           the reader module (moved out of App.jsx in Phase 1)
  src/App.jsx           the web app; imports src/reader
  src/reader-app/       the app's own shell; imports src/reader
  reader-app.html       the app's entry document
  vite.reader-app.config.mjs   → dist/reader-app
  capacitor.config.json        webDir: dist/reader-app
  ios/                  the Capacitor iOS project (Xcode), plugin source
```

- `npm run build` stays the web build the Dockerfile uses; it does not build
  the app. `npm run build:reader-app` builds `dist/reader-app`;
  `npm run ios:sync` runs it and `npx cap sync ios`. CI runs
  `build:reader-app` (catches a reader change that breaks the app build)
  but does not build Xcode.
- **How a reader change reaches each build:** the web on the next NAS
  deploy; the app on its next build. Phase 7 makes "build and upload to
  TestFlight" one script (`tools/reader_app_release.sh`), so the lag is a
  command, not a project. Live updates are Phase 8 and only if the owner
  says yes to open question 4.
- **The reader API is a contract.** The app in someone's hands may be older
  than their server. Reader-class routes the app uses change only by
  addition; `test_http_contract.py` gains `ReaderAppContractTests` pinning
  the fields the app reads (below), and `/api/v1/app` (Phase 0) reports
  `apiVersion` so an app can say "Update Flipparr View to read from this
  server" instead of failing obscurely.

### The reader module (`src/reader/`)

`ReaderView` (`App.jsx` ~L9957–10974) already takes a short prop list —
`fileId, title, medium, directionOverride, startPage, behind, onFinish,
onProgressSaved, onOpenRun, onClose` (rendered at ~L13209) — and its pure
logic is already in `src/reader.js` and `src/panel-editor.js`. It moves to
`src/reader/ReaderView.jsx` with its helpers, and everything it reached
from `App.jsx`'s scope becomes an explicit dependency through one context,
`ReaderHost`:

| `ReaderHost` member | web | app |
|---|---|---|
| `api(path, options)` | `apiRequest` (cookie, same origin) | native fetch with `Authorization: Bearer` |
| `pageSource(fileId, index, readUrl)` → `Promise<string>` | resolves `readUrl` unchanged | the downloaded file's `convertFileSrc` URL; online and not downloaded, the plugin fetches the page to a rolling temp file first |
| `canEditPanels` | the admin, as today | `false` (open question 2) |
| `toast(message)` | `showToast` | the app's toast |
| `progressSink(fileId, body)` | POST as today | the queue (Phase 4) then POST |

`pageSource` is the one place the builds differ for pages. It is async in
both so the reader's preloading treats them alike; on the web it resolves
immediately. The web app must behave exactly as before Phase 1 — that is
the phase's proof.

### Authentication for the app (Phase 0 — built 2026-10-08)

- Every app request sends `Flipparr-Client: app`. Its session goes as
  `Authorization: Bearer <token>` and a shared device's token as
  `Flipparr-Device: <token>`. A session or device token issued on any
  request (sign-in, a profile switch, a new PIN, the admin's first reader)
  comes back in the response headers `Flipparr-Session` / `Flipparr-Device`
  instead of `Set-Cookie`; an empty `Flipparr-Session` means the sign-in
  ended. No body changes, so every route that re-issues a session already
  works for the app (`_set_cookie` diverts, `send_response` writes them).
- The tokens are the same signed tokens the cookies carry and follow the
  same rules as the web: the admin signing in makes a shared device (a
  device token, and a `v3` session); a profile picked on a shared device is
  `v3`, so "forget shared devices" ends it; a reader signing in on their own
  phone is `v2`, as on the web. Session versions end them as they end cookies.
- An app request reads no cookies at all (a native cookie jar may keep
  some): one request, one identity. A browser request's `Authorization`
  header is never read (a reverse proxy's Basic credentials stay harmless).
- The cross-site check is skipped for app requests: it guards cookies, an
  app request reads none, and `Flipparr-Client` forces a CORS preflight that
  the server never grants (OPTIONS answers 501 with no `Access-Control-*`;
  `test_no_other_site_may_send_an_app_request` pins it), so another site's
  page cannot send an app request at all. Cookie requests are checked as
  before. No CORS headers are added.
- Tests: `test_flipparr_reader_signs_in_with_tokens_in_headers_never_cookies`
  and `…_as_the_admin_and_gets_the_shared_device` (`ReaderProfileHttpTests`).
- Not done: sessions do not slide. An app token expires 30 days after it was
  issued, like a cookie; the app signs in again (Phase 2 handles the 401).

### The native plugin (`ios/App/App/FlipparrTransfer/`, Phase 2)

One Swift plugin, `FlipparrTransfer`, MIT like the repo, ~300 lines:

- `fetchToFile({url, path, token})` → `{path, bytes, status, contentType}`:
  a foreground `URLSession` data task written to `path` (a page for reading
  online, or one page of a download while the app is in front). Never puts
  the image over the bridge.
- `enqueue({id, url, path, token})`, `cancel({id})`, `list()` and events
  `transferDone`/`transferFailed`: a background `URLSession` (identifier
  `flipparr.downloads`) download task moved to `path` on completion;
  survives suspension, delivered on relaunch through the `AppDelegate`
  hook. Resume data kept on failure.
- `excludeFromBackup({path})`: sets `isExcludedFromBackup` on the folder;
  called at launch for `Library/flipparr/`.
- The token is passed per call from the Keychain wrapper and set as a
  header; it is never logged.
- Tests: XCTest for path handling and the done/failed mapping against a
  `URLProtocol` stub; the JS side behind an interface with an in-memory fake
  for `node:test`.

### The device store (files in `Library/flipparr/`)

```
Library/flipparr/
  servers.json                      {serverUrl, deviceSalt, capBytes}
  profiles.json                     [{profileId, name, colour, role, lock, pinLength, pinHash?, updatedAt}]
  u<profileId>/downloads.json       rows, below
  u<profileId>/progress-queue.json  [{fileId, page, panel, at, finished}]
  u<profileId>/files/<fileId>/<index>.jpg
  u<profileId>/files/<fileId>/panels.json
  tmp/pages/                        the rolling cache for online reading (≤ 200 MB, oldest first)
```

- A download row: `{fileId, seriesRunId, seriesTitle, issueNumber,
  filename, medium, readingDirection, pageCount, fileSignature, pageUrls[],
  pagesDone, bytes, state: "queued"|"downloading"|"ready"|"failed"|"stale",
  downloadedAt, lastReadAt, finished, groupKey, error}`.
- `src/reader-app/device-store.js` is the only module that touches
  `@capacitor/filesystem` (MIT). Every JSON write is atomic (write
  `<name>.tmp`, then rename). The logic is pure in
  `src/reader-app/offline.js` and tested with an in-memory store.
- One server per install for now (`servers.json` has one entry); a second
  server is "Sign out and connect elsewhere", which removes everything.
- Cap accounting: usage = Σ `bytes` of every profile's rows (the cap is the
  device's); the temp page cache is separate and capped at 200 MB.
- Eviction (`evictionOrder` in `offline.js`): candidates are `state ===
  "ready" && finished`, oldest `lastReadAt` (then `downloadedAt`) first;
  never queued or downloading, never unfinished, never the issue being
  read. If evicting every candidate still cannot fit: "Needs X MB more than
  the cap allows".

### Server additions (Phase 0; all reader-class, all through the rating gate)

Facts to rely on: pages `GET /api/v1/files/{id}/pages` → `{fileId, pageCount,
pages: [{index, url, readUrl}]}` (`file_pages` ~L13979; `_page_url` ~L13693
puts `v=<_file_signature>` in every page URL, so a replaced file gets new
URLs; `_file_signature` ~L13594 = `mtime_ns`-hex + `size`-hex). Page images
`GET …/pages/{n}?size=read` → JPEG via `render_file_page`, `send_image`
~L20300 (`private, no-cache`, weak ETag, `Vary: Cookie` — keep). Progress
table `reading_progress(user_id, file_id, page, panel, page_count,
file_signature, started_at, finished_at, updated_at)` PK (user_id, file_id);
`set_reading_progress` (`catalog_store.py` ~L3989) upserts with `updated_at
= _utc_now()`; `POST /api/v1/files/{id}/progress` (~L19596) accepts `{page,
panel?}` | `{read: bool}` | `{page: null}` via `set_file_reading_progress`
~L14804. The rating gate `_run_visible` (~L18102) matches
`/api/v1/files/(\d+)(?:/.*)?`, so any new route under a file id is gated
with no extra code; a route with ids in its body is gated by hand. Adding a
reader route = the regex in `access_policy.py` `ROUTE_ACCESS` + the branch
in `_route_get`/`_route_post` + the sample in `NOT_ADMIN` of
`test_http_contract.py` (~L1786), or `RouteCensusTests` fails. The reader
routes the app needs already exist (`access_policy.py` L91–155): `/reading`,
`/reading/runs`, `/reading/lists`, `/series/{id}/reading`,
`/reading-lists/{id}`, `/files/{id}/pages…`, `/progress`, `/panels`, covers.

1. **Bearer auth and `client: "app"`** — see "Authentication for the app".
2. **`GET /api/v1/app`** at `PUBLIC`, the level `/api/v1/auth/status` uses,
   so the connect screen can recognise a Flipparr server before signing in:
   `{name: "Flipparr", apiVersion: 1, version, build, authMethod}` for anyone
   (`/healthz` already shows the build publicly). `READER_API_VERSION` in
   `app.py`.
3. **`GET /api/v1/files/{id}/offline`** → `{fileId, fileSignature,
   pageCount, bytesEstimate, filename, run: {id, title, startYear, medium} |
   null, kind, issueNumber, volumeNumber, readingDirection, pages: [{index,
   readUrl}]}` (built; the field set is pinned by the test). New
   `CatalogStore.file_offline_context(file_id)` (one query over `files`,
   `file_identities`, `series_runs`; `LookupError` if absent or
   `present = 0`) and `file_offline_manifest(file_id)` beside `file_pages`,
   reusing `file_pages`, `_file_signature`, `cached_page_members`,
   `store.file_reading_direction`. `bytesEstimate` = Σ sizes of
   reading-size cache files already rendered for this file under
   `reading_cache_dir()`, else `pageCount × 380_000`.
4. **`POST /api/v1/offline/check`** body `{files: [{fileId, fileSignature}]}`
   (≤ 500 entries, else 400) → `{files: [{fileId, status: "ok" | "changed" |
   "gone" | "hidden"}]}`. Per file: absent or `present = 0` → `gone`; a
   non-admin viewer for whom `self._run_visible(f"/api/v1/files/{fid}/
   pages/0")` is false → `hidden`; signature ≠ `_file_signature(path)` →
   `changed`; else `ok`.
5. **`at` on the progress POST**: optional ISO-8601 time with a zone (a
   naive value → 400). Normalise `at = min(parsed → UTC, now)` so a clock
   ahead cannot lock out later writes. Thread `at` through
   `set_file_reading_progress` to `set_reading_progress`: when `at` is
   given, read the stored row's `updated_at`, parse both with
   `datetime.fromisoformat`, and **skip the write when the stored time is
   newer** (compare parsed datetimes, not strings: `_utc_now()` omits the
   fraction when microseconds are 0); a kept write stores `updated_at = at`.
   It governs every body form (`{page, panel?}`, `{read}`, `{page: null}`).
   Without `at` the write always wins, as before. The web app keeps sending
   no `at`.
6. **`?vision=no` on `GET /api/v1/files/{id}/pages/{n}/panels`** (~L18518):
   `allow_vision=False` and `vision_budget_allows` is not called. The local
   tiers still run and their row is stored as today; a later online read
   upgrades an `auto`/`model` row to the model's answer (`upgrade` in
   `_file_page_panels` ~L14461, checked 2026-10-08).
7. Tests: `NOT_ADMIN` gains the new routes; a manifest test modelled on
   the page-list test (~L1216); in `ReaderProfileHttpTests` (~L1976): a
   reader whose `maxRating` is below a run's rating gets 404 from
   `/offline` and `hidden` from `/offline/check`, the admin gets `ok`;
   `changed` after rewriting the archive; `gone` for an unknown id.
   `test_catalog_store.py`: an older replayed save does not overwrite a
   newer one; a newer one does; no `at` wins; `{read: true, at}` with an
   older `at` leaves the row. `test_app.py`: a naive `at` → 400.
   `?vision=no`: a reader with `visionForReaders` on leaves `_VISION_SPEND`
   untouched; the admin gets a stored row whose source is not `vlm`.
   Built 2026-10-08: the field sets of `/app` and `/files/{id}/offline` are
   pinned in their tests. `ReaderAppContractTests` for `/reading`,
   `/reading/runs`, `/reading/lists`, `/series/{id}/reading`, `/pages`,
   `/panels` and `/progress` is written in Phase 2, pinning exactly the
   fields the app's code reads (unknown until then).

## Phases, in order

### Phase 0 — server: bearer auth, `/app`, offline routes, `at`, `?vision=no` (1–2 sessions)
Everything under "Server additions". Deployable alone at any time (nothing
sends the new forms yet). After the deploy, also check that openresty does not
answer a preflight itself (the cross-site skip for app requests depends on
it): `curl -si -X OPTIONS https://<server>/api/v1/profiles/switch -H "Origin:
https://evil.invalid" -H "Access-Control-Request-Method: POST" -H
"Access-Control-Request-Headers: flipparr-client"` must show no
`Access-Control-Allow-*` header; if one appears, the skip goes back to
"only when the identity came from a bearer token" before the app ships. Proof: both suite orders green; NAS deploy;
`curl` with a bearer token as the admin and as a limited reader (the
owner's instance has reader profiles; never clear their records).

### Phase 1 — move the reader into `src/reader/` (1 session, no behaviour change)
**Built 2026-10-08.** `src/reader/ReaderView.jsx` holds the reader, its settings
drawer and the panel editor; `src/reader/host.js` is `ReaderHost` with `api`
and `canEditPanels` (the web app provides them in `App`). The dialog stack and
phone sheets every drawer shares moved to `src/dialogs.jsx`, and `SettingsCard`,
`Toggle`, `HeaderToggle` to `src/components/SettingsControls.jsx`, because the
reader needs them and the app will too. `pageSource` and `progressSink` are
**not** in the host yet: they are added in Phase 2 with the app's
implementation and tests, so that this phase changes nothing the web app
renders (an async page source would change when the `<img>` gets its `src`).
Proof: the harness's new `reader-page` and `reader-panels` states (routed
place, settings and panels, so a capture writes nothing) and `reader-finish`
are 0 px from the baseline at both widths; the other differences traced to
the scratch library changing and to the phone tab highlight's run-to-run
timing (a second capture of the same build matched HEAD); `layout:check`
clean; 281 frontend tests; in the browser, the settings drawer opens with the
admin's panel tools and Escape closes only it. Still owed: the owner's iPad
gesture check after the next deploy.

- `ReaderView` and what only it uses move to `src/reader/`; `ReaderHost`
  context with the web implementations; `App.jsx` renders it as before.
- Proof: before the move, on HEAD, capture the reader states
  (`visual:capture -- --out baseline` including the reader and panel-view
  states in `tests/visual/states.mjs`; add them first if missing); after,
  `--out current`, `visual:compare` with no differences, `layout:check`;
  `npm test`, eslint, build; on the iPad against the scratch stack: page
  turns, pinch, panel view, rotation, the scrubber, finish → the finish
  drawer — the gesture work of 2026-10-07/08 (`81f6ed7a`, `2270f714`,
  `53ebd73a`) must feel identical. Batch the web deploy with the next UI
  deploy; it changes nothing visible.

### Phase 2 — the app shell, reading online (2 sessions)
**Built 2026-10-08, in the simulator; the iPad check waits for the
owner's Developer Program enrolment.** What was built, and where it differs
from the list below:
- `src/reader-app/`: `server.js` (the client), `secure-store.js` (Keychain),
  `images.js` (every image through `FlipparrNative.fetchToFile`; an online
  cache per profile in `Library/flipparr/online/u<id>`, trimmed to 200 MB;
  the token only for the household's own server), `ReaderApp.jsx`
  (connect, sign in, "Who's reading?", Keep Reading, the runs with comics,
  a run's issues, the shared reader). Library browsing reads
  `/api/v1/catalog`, filtered to runs with comics; a lighter endpoint can
  come later if its size matters on the iPad.
- `pageSource` arrived here, optional in the host: `usePageSrc` returns the
  address itself where the host has none, so the web reader is unchanged
  (reader states 0 px). `progressSink` waits for Phase 4.
- The keychain is in the app's own plugin, not a third-party one.
- `ProfileAvatar`, `ProfileTiles` and `PinPad` moved to
  `src/components/ProfileTiles.jsx`, shared by both pickers.
- On sign-in the app takes the profile's reader settings from the server,
  as the web app does: without it the reader used a phone's default (panel
  view on).
- `ReaderAppContractTests` is
  `test_reader_api_1_keeps_every_field_flipparr_reader_reads`.
- A session that ends while the app is open (a 401 on the home or run
  screen) re-asks `/auth/status` and lands on "Who's reading?" on a shared
  device, else sign-in; sign-out on a shared iPad does the same. Checked in
  the simulator by ending the admin's sessions from the server mid-use.
- CI builds the app's bundle (`npm run build:reader-app`); it does not run
  the Xcode build.
- Seen on the iPad Pro 13-inch simulator: connect, sign in, home and the
  reader in panel view; covers grow above 640px (180-220px cards).
- Not yet: the finish drawer (`onFinish` does nothing in the app; the last
  page marks the comic read); the reader settings drawer is reachable but
  untested in the app; `reader-app.css` is not read by
  the design-system test (its exceptions are noted in the file; the file
  went in Phase 3b, when the app took the web's own screens and styles).
  Phase 3's
  background-download delegate must drop `Authorization` on a redirect to
  another host, so the token reaching only the household's server holds by
  construction (fetchToFile today relies on Flipparr never redirecting).
- Capacitor 8 project: `capacitor.config.json` (`appId:
  "com.silenttoots.flipparr.view"` — the owner's choice, 2026-10-08,
  permanent once on the App Store; `appName: "Flipparr View"`; webDir
  `dist/reader-app`; `CapacitorHttp.enabled: true`), `ios/` generated by
  `npx cap add ios` (Swift Package Manager: no CocoaPods on the build Mac),
  `Signing.local.xcconfig` untracked and passed to `xcodebuild -xcconfig`
  so the tracked project names no team, `Info.plist`
  `NSAllowsLocalNetworking`, `PrivacyInfo.xcprivacy` (required for
  submission; declares file timestamps and no tracking).
- `src/reader-app/`: `main.jsx`, `ConnectScreen` (server address → `GET
  /api/v1/app`; ATS refusal explained), `SignIn`, `ProfilePicker` (reuse the
  web picker's component if it can be moved out cleanly; else a small one
  styled from the same tokens), `Home` (Keep Reading from `/reading`, runs
  from `/reading/runs`, arcs and lists from `/reading/lists`), `RunView`
  (`/series/{id}/reading`), and the shared `ReaderView` with the app's
  `ReaderHost`. `keychain.js` wraps a Keychain plugin (choose an MIT one, or
  add `getToken`/`setToken` to `FlipparrTransfer`; record the choice).
- `FlipparrTransfer.fetchToFile` for online pages into `tmp/pages/`.
- The app uses the web's design tokens and Phosphor icons; it is laid out
  for iPad first and iPhone, mobile-first, reduced motion honoured.
- Proof: on the owner's iPad (a development build over Xcode): connect to
  production, sign in, pick a PIN'd profile, read an issue online in page
  and panel view; a limited reader cannot open a run above its rating;
  sign out removes the token from the Keychain. `npm test` covers
  `ReaderHost` (app) against fakes.

### Phase 3 — downloads (2 sessions)
- `FlipparrTransfer` background `enqueue` + the `AppDelegate` hook;
  `src/reader-app/downloads.js` (an `EventTarget` engine): first `queued`
  row → `downloading` → `GET /files/{id}/offline` (404 → `failed`
  "hidden", nothing stored; 401 → pause and ask to sign in again) →
  `planDownload` (evict per plan or `failed` "doesn't fit") → `enqueue`
  every page to `u<id>/files/<fileId>/<index>.jpg`; on `transferDone`
  count `bytes` and `pagesDone`; a 404 mid-way removes the file's folder
  (`failed` "hidden"); then `GET …/panels?vision=no` for every page into
  `panels.json` (best-effort: failures do not block `ready`). Re-checked
  on launch and on return to the foreground; `list()` reconciles with the
  plugin after a relaunch.
- Single-issue Download in the issue's menu and the reader's settings;
  `Downloads` screen (state, `formatBytes`, "Downloaded N of M pages",
  Remove, Cancel, Re-download); Settings: cap (1/2/4/8 GB), usage bar,
  "Remove all downloads" (confirmed).
- **Panel hold:** this is the first build that sends `?vision=no`. Its
  iPad proof runs against the scratch stack (the owner's iPad pointed at
  the Mac's scratch backend over the LAN), not production, until the
  owner's panel review is done.
- Proof: node tests for `planDownload`, `evictionOrder`, `downloadRow`;
  XCTest for the plugin; on the iPad: download five issues, switch apps
  and lock the iPad mid-download (it carries on), relaunch (rows match the
  files), Remove empties the folder.
- **Built 2026-10-08** (iPad simulator against the scratch stack):
  - Native side: the background session lives in `FlipparrNative.swift`
    (`FlipparrDownloads`). It moves each finished page into place itself,
    so pages that land while the app is suspended are on disk at relaunch.
  - Engine: `src/reader-app/downloads.js`, with the rules in `offline.js`
    and the files in `device-store.js`. Downloads are re-checked at launch
    and on return to the foreground, and each comic is finished once only.
  - Pages are read from disk when downloaded (`images.js`), with no page
    request to the server.
  - Panels are fetched with `?vision=no` into `panels.json`. While online,
    the reader still asks the server for panels, since they may have been
    corrected since the download. The local copy is read offline (Phase 4).
  - The server keeps one row per download in `offline_downloads` (schema 68).
    Remove closes it with a reason; a 403/404 mid-download closes it as
    `hidden`.
  - Proven in the simulator: download, then Remove (the folder is emptied
    and the server row is closed as `removed`), then download again (a new
    row). Leaving and returning to the app mid-download works.
  - Differences from the list above:
    - Download is a button on each issue in the run screen, not in the
      issue's menu or the reader's settings.
    - Remove also cancels a download in progress, so there is no separate
      Cancel.
    - A failed download offers Try again. Re-downloading a comic whose file
      changed on the server (`/offline/check`) is not built yet.
    - The cap control and usage line are on the Downloads screen. "Remove
      all downloads" and a usage bar are not built yet.
  - Still to prove: XCTest for the plugin, and the five-issue download with
    the iPad locked mid-download, on a real iPad. Both wait for the Developer
    Program enrolment.

### Phase 3b — one UI: the app built from the web's components (2–3 sessions)
The owner's decisions, 2026-10-08:
- **Parity.** The app uses the web app's cards, shelves, drawers, page
  header and tab bar, not patterns of its own. The Phase 2 screens were
  stand-ins and are removed.
- **Screens.** Recommended (the web's shelves) and the Library tabs
  (Library, Collections, Story arcs, Reading list), with the run,
  collection, story-arc and reading-list drawers, plus Downloads. There is
  no Discover, Search, Requests or Settings.
- **Timing.** This happens now, before Phase 4, so the offline states are
  built once.
- **Name.** The app is **Flipparr View**. The App ID
  (`com.silenttoots.flipparr.view`) and the code paths (`reader-app/`,
  `ReaderApp.jsx`) are unchanged.

How:
- Same move as Phase 1: the components leave `App.jsx` for shared
  modules. Shared modules import nothing from `App.jsx`, and the web shows
  no change (visual compare 0 px at 1440 and 375, `layout:check`).
- Images go through the host. `CoverArt` uses the reader's `pageSource`
  mechanism, because the app has no cookie for a plain `<img src>`.
- The host gets a `canManage` flag. Library management (edits, merges,
  covers, requests) is shown only where it is true. Reading actions a
  profile has on the web (rate, mark read, reading list, follow) work in
  the app too.
- Drawers keep the web's history behaviour (`historyStep`): Back closes,
  and swipe dismisses.
- Downloads become a slot on the issue card, which the web leaves empty.
  The Downloads screen uses the web's list rows and `SettingsControls`.
- Three commits, each 0 px on the web:
  1. Covers, cards, shelves and the A–Z rail.
  2. The drawers and their hooks.
  3. The Library tabs, page header and tab bar, with nav items from the
     host.
- **Built 2026-10-08** (iPad simulator against the scratch stack):
  - Shared modules, each 0 px on the web against a baseline taken first:
    - `api.js`: `apiRequest`, with a transport the app replaces.
    - `host.js`: `Host`, `useImageSrc`, `useManages`.
    - `viewer.js`, `editions.js`.
    - `components/`: CoverArt, glass, Toast.
    - `library/`: cards, shelves, AlphaRail, LibraryView, RecommendedView,
      and `actions.js` (reading actions and what a Read button opens).
    - `drawers/`: run, collection, story-arc and finish drawers, with
      their frame, inventory and group screens.
    - `frame/`: Nav, PageHeader, header.
  - `App.jsx` went from 11,270 lines to about 6,800. A test keeps shared
    modules from importing it.
  - The visual capture now includes the Story arcs tab and the collection
    and story-arc drawers.
  - The app's signed-in screen is the web's: Nav (Comics, Downloads), the
    Comics page and its tabs, the four drawers, the reader and the toast.
  - Downloads appear in two places:
    - as items in an issue's "…" menu (Download, Cancel or Remove
      download, Try again);
    - in the issue tile's badge, where nothing else is shown.
  - The Downloads page uses the web's Settings pattern: the PageHeader,
    settings cards, a glass select and rows.
  - The stand-in screens and `reader-app.css` are gone.
  - Host settings that hide controls in the app:
    - `canManage: false` hides Files, Advanced, Edit, Pull, Scan and the
      admin's counts.
    - Request follow / Request missing, New collection / New story arc /
      Import and Add profile show only where the host passes a handler.
    - The bell shows only where the host has one.
  - `styles.css`: the sidebar and the drawer bar add the safe-area insets,
    because the app runs edge to edge (`viewport-fit=cover`). In a browser
    the insets are 0.
  - Proven in the iPad simulator:
    - Recommended shelves, the Library grid, the run drawer (hero toned
      from the cover, rating, reading list, Issues) and Download from an
      issue's menu (the server records it).
    - The Downloads page, and reading a downloaded comic with no page
      requests.
- **Not yet, and open:**
  - Drawer history: in the app, Back does not close a drawer. The web
    ties drawers to the address (`historyStep`). The app's drawers close
    with their X and the swipe.
  - The bell (notifications) in the app.
  - Making arcs and collections in the app.
  - A downloaded issue that is in progress still shows "In progress", not
    "Downloaded". Reading state wins the badge.
- **Also checked, after review:**
  - The collection and story-arc drawers in the app: Restart and Reading
    state only. Edit, Arrange, Add, Share, Export and Delete are web tools:
    a collection's need onSave, and an arc's need `groupTools`.
  - As a reader profile (switched from the header's menu): only that
    profile's place is shown, and there is no Request follow or Edit.
    Switching profiles forgets the last profile's cached answers.
  - Provider covers are fetched without the household's token (`images.js`
    sends it only to the server's own origin).
  - The reader API contract covers the shared screens (2026-10-08):
    `test_reader_api_1_keeps_the_shapes_the_shared_screens_read` reads
    every GET answer they use, as a reader with the app's headers. It keeps
    each answer's key sets (objects, and the first level of lists and
    id-keyed maps; 48 shapes) in `reader_api_1_shapes.json`. Adding a key
    is free. Losing one fails the test, unless READER_API_VERSION is
    bumped. `FLIPPARR_WRITE_READER_SHAPES=1` records a deliberate addition.
  - The phone (iPhone 18 Pro simulator, portrait): the web's phone layout,
    with a tab bar that hugs Comics and Downloads, centred; the drawer as a
    full page clear of the Dynamic Island.

### Phase 4 — reading offline and progress replay (1–2 sessions)
- Offline identity: with no connection the app has no `/auth/status`. The
  active profile is the last one used (`servers.json`), its name and role
  from `profiles.json`; the app never builds a viewer from nothing (the web
  app's "no gate" fallback at `App.jsx` ~L12012 is not copied).
- `useNetwork()`: online from CapacitorHttp responses and `navigator.onLine`;
  offline, Home shows the Downloaded shelf first with a `role="status"`
  line "You're offline — N downloaded issues are ready."; opening one that
  is not downloaded: "This issue isn't downloaded. Connect to read it."
- The reader opens a `ready` download with no network: `pageSource`
  returns the file; panels from `panels.json` (none → `{segmented: false,
  panels: []}`); the place = `mergeProgress(queue row, server GET when
  online)`; `lastReadAt` and `finished` written to the row on every save.
- `progressSink`: always append to the queue with `at: new Date().
  toISOString()`; online, POST and drop the row on success. **A live online
  save sends no `at`; only a replayed queue row does.** Every `at` is stored
  as the row's time and the web sends none, so an iPad whose clock runs ten
  minutes behind would otherwise have its live saves skipped for ten
  minutes after any web save. Body: finished
  → `{read: true, at}`, else `{page, panel, at}` (`panel` omitted when
  null). `replayProgress()` on launch, reconnect and foreground: rows in
  `at` order; 401 → stop and keep; 404 → drop the row and mark the
  download `failed` "hidden"; network error → stop. A queue is replayed
  only with its own profile's token.
- Proof: node tests for `mergeProgress` and queue order; on the iPad in
  airplane mode: open from cold, read three issues in page and panel view,
  rotate, lock and unlock; reconnect → the server's `/progress` shows the
  place; an offline save older than a newer online one does not regress it.
- **Panel hold:** as in Phase 3, no proof against production before the
  owner's panel review is done.
- **Built 2026-10-08** (iPad simulator; stopping the scratch server stood
  in for losing the connection):
  - The shared reader is unchanged. Offline lives in the app's transport
    (`src/reader-app/transport.js`, rules in `offline-reading.js`; node
    tests for both). That replaced the `progressSink` / `pageSource` hooks
    planned above.
    - When a request fails to connect or times out (15 s), the app marks
      itself offline once and answers from the device. It asks
      `/api/v1/app` again on return to the foreground and every 30 s.
    - A downloaded comic's page list and panels come from its row and
      `panels.json`. A page without panels is read whole.
    - The library's reading answers (catalog, shelves, reading list, arcs,
      a run's reading) are kept per profile in `u<id>/answers/`, so the
      Comics page and drawers draw offline, as they last were.
    - Saves go into `u<id>/progress-queue.json`: one row per comic, the
      latest wins. A live save carries no `at`; the row is dropped once the
      server has it. On reconnecting, launching and foregrounding, the
      queue is replayed oldest first, with `at`.
    - Replay handling: a 404 drops the row and the download (as hidden); a
      401 stops and keeps it.
    - A waiting save and the server's place: the later one wins.
    - Covers fetched before show from the device's cache.
  - Opening offline: the last signed-in status (`session.json`, no token)
    opens the app as that profile instead of at the connect screen. When
    the server answers again, the app asks `/auth/status` again.
  - UI, from the web's patterns:
    - the `backend-banner` under each page's header ("You're offline — N
      downloaded issues are ready"), through a host `notice` slot;
    - a Downloaded shelf on Recommended (first while offline, after Keep
      Reading otherwise), through a host `shelves()` slot;
    - "Downloaded" as a green mark beside an issue's number, where a locked
      correction's shield sits (host `issueMark`), so it shows whatever
      reading's badge says;
    - offline, a comic that isn't downloaded says "This issue isn't
      downloaded. Connect to read it.";
    - switching profiles and signing out say they need the server.
  - An error boundary shows what went wrong, with Start again, instead of
    a blank window. It found the one bug of the session: two kept answers
    (`/reading/lists` and `/reading-lists`) shared a file name.
  - Proven:
    - Downloaded as a reader profile; stopped the server and relaunched
      cold. The app opened as that profile with the banner and the
      Downloaded shelf first, and the library and covers came from the
      device.
    - Read the downloaded comic and turned a page: the save waited in the
      queue with its time.
    - Started the server: within one probe the queue emptied, and the
      server held the place timed when it was made offline. The banner
      went, and Keep Reading showed it.
    - Offline, a comic that isn't downloaded gives the message above.
  - Found in review and fixed:
    - A comic's place is kept from every progress answer and successful
      save, so offline it opens where the server last had it. Before
      this, it opened at page 1 once its queue had been sent.
    - Removals made offline wait in `u<id>/removals.json` and go to the
      server's record on reconnecting or launching.
    - The sidebar's Sign out says it needs the server, as the header's
      menu does.
  - Also proven in the simulator:
    - Panel view offline from `panels.json`: the comic opened at page 2,
      panel 2.
    - Locking and unlocking mid-read; the next step was still queued.
    - A removal made offline closed the server's record (`removed`) within
      one probe of the server returning.
  - Not yet: airplane mode and rotation on a real iPad (Developer
    Program).
  - Known limits:
    - Kept answers are the last seen, so a run's drawer offline can show
      its state from before, while Keep Reading is current.
    - The 15 s request timeout is a tunable: on a slow link a large
      catalog answer could flip the app offline until the next probe.
    - "An older offline save does not undo a newer web save" is proven by
      the server's tests and the transport's merge test, not live.

### Phase 5 — profiles offline and the rating re-check (1 session)
- **The owner, testing on his iPad (2026-10-09):** offline, rating an issue
  or run fails with "could not be reached", though the app asks for it.
  Instead, ratings should wait on the device, like saved places, and be
  sent on reconnecting. Marking read or unread and the reading-list
  bookmark should work the same way. They go in the transport's queue,
  each with its `at`; the screen shows the change at once, and the server
  keeps the latest.
  Built 2026-10-09:
  - Server: `at` on `POST /issues|series/{id}/rating`, on `POST
    /me/reading-list/{kind}/{id}` (and `?at=` on its DELETE, which has no
    body), and on `POST /series/{id}/reading` and `/reading-lists/{id}/reading`,
    threaded to each comic's progress. A late write is kept only if the
    stored row is not newer (`CatalogStore._stored_is_newer`). Limit, said
    in the code: a cleared rating or removed entry leaves no row, so a late
    rating or addition made before that clear still lands (no tombstones).
  - App: `src/reader-app/offline-changes.js`, one row per thing changed in
    `u<id>/change-queue.json`, sent after the places, oldest first. Waiting
    changes and waiting places are laid over every answer the screens draw
    from (`withChanges`, `withPlaces`): ratings on the catalog, entries on
    the reading list, marks on a run's drawer, its arcs and the cards'
    summaries. Online, a change goes straight to the server and only waits
    if the server stops answering.
- `profiles.json` is refreshed from `GET /api/v1/profiles` whenever the
  picker loads online; a profile whose `lock` is no longer `pin` loses its
  `pinHash`.
- `src/reader-app/offline-pin.js`: `hashPin(pin, salt)` = WebCrypto
  PBKDF2-SHA256, 200 000 iterations, 256 bits, hex; `verifyPin`; the salt is
  `deviceSalt`. After a successful online switch with a PIN, store
  `pinHash` on that profile. The module header cites the W3C WebCrypto
  spec and the OWASP Password Storage Cheat Sheet and says plainly: device
  local and unthrottled, weaker than the server's scrypt check
  (`check_profile_switch` ~L1034), acceptable for a 4–6 digit PIN on a
  household's device; password-locked profiles cannot be unlocked offline.
- Offline switch: the picker lists `profiles.json`; success sets the active
  profile; each profile reads only its own `u<id>/` folder. On reconnect
  the profile switches online with its PIN before any replay (each profile
  has its own token in the Keychain, keyed by profile id; a missing or
  expired one asks for the PIN).
- Sign out and "forget shared devices" (detected as 401 on every token):
  remove every `u<id>/` folder, `profiles.json` and the tokens.
- Reconnect check on launch, reconnect and foreground: `POST
  /offline/check` with every `ready`/`stale` row; `hidden`/`gone` → remove
  the files and rows ("N downloads were removed: they're no longer
  available to this profile"); `changed` → `stale` with Re-download.
- Proof: node test for `hashPin` against `node:crypto` `pbkdf2Sync`; on the
  iPad: two profiles switched offline each see only their own downloads; the
  admin lowers a reader's rating → the reader reconnects → the download
  disappears.
- Built 2026-10-09 (iPad QA pending):
  - `offline-pin.js` and `offline-profiles.js` as above; the salt is a
    Keychain item per server (`pinsalt@<origin>`), not `servers.json`.
  - Each profile's session is kept as `session@<origin>#<id>` in the
    Keychain with its `u<id>/status.json`; switching offline swaps the
    token the client carries (`client.useSession`) and opens from the kept
    status. Signing out still needs the server.
  - Forgotten device (it stops being shared): the kept sessions and
    `profiles.json` go. Downloads are not wiped, a change from the plan:
    a reader's own phone also reads as "not shared", and wiping there would
    lose their downloads on a plain session expiry; each profile's
    downloads are put to `/offline/check` when it next signs in.
  - `/offline/check` on launch, reconnect and foreground (`Downloads.check`):
    `hidden`/`gone` removed with a toast, `changed` → `stale` (still read
    offline from the copy kept, Download again in Downloads).
  - Open: a profile's waiting changes and places are sent only when that
    profile is next active online; sending every profile's with its kept
    session on reconnect is a follow-up.
  - Open: one issue marked read offline shows on its row in the run's
    drawer and its arcs, but the run's card count and Read badge
    (`/reading/runs`) catch up only on reconnecting; a whole run marked
    does update the card.

- **Panels for downloads, changed 2026-10-09 (the owner):** a download takes
  only the panels the server already keeps (`?stored=only`,
  `stored_page_panels`): a person's fix, the vision model's reading, an
  earlier automatic one. Nothing is worked out for it, so downloads add no
  rows to the panel data. Offline, a page with none kept reads whole
  (`whole: true`), not in quadrants; online, the reader asks the server as
  before, vision and all. `?vision=no` stays for older builds. With this,
  the reader is built against the owner's production library, and the panel
  scanner work is paused until the reader is solid.

### Phase 6 — run and arc downloads, eviction (1–2 sessions)
- **The owner, testing on his iPad (2026-10-09):** Download is buried
  under an issue's "…" menu, and even knowing it was there he could
  hardly find it. Download must be visible at the top level of every
  drawer:
  - "Download" for the whole run, collection or story arc, in the drawer's
    own actions (beside Read, or with the bookmark in its top bar);
  - a download control on each issue that can be seen without opening a
    menu.
  The design follows the drawer pattern shared with the web. Its slots
  are what the app adds; the web shows none of them.
- Run view: "Download" beside Read → a sheet "Download the next N unread"
  (stepper 1–20, default 5) from `/series/{id}/reading` + `nextUnread
  (issues, n)`; shows Σ `bytesEstimate`, what fits and what would be
  evicted; `groupKey: "run:<id>"`.
- Arc and reading list: "Download this arc", in reading order,
  `groupKey: "arc:<id>"`.
- Settings: eviction explained; "Remove finished downloads"; a cap change
  applies at the next download.
- Built 2026-10-09 (iPad QA pending):
  - Host slots `groupDownload(group)` and `issueDownload(issue)` (host.js).
    `ReadWithDownload` puts a round Download right of the drawer's Read
    button in the run, story arc and collection drawers (the owner moved it
    there from beside the bookmark, 2026-10-09). Each issue tile has a control at its top right, and
    each row one beside the menu, always shown: download, a spinner while it
    downloads, a green check once here. The "Downloaded" mark beside the
    number is gone, as the control says it. The web draws none of these
    (visual compare: 0 px at both widths).
  - `DownloadSheet`: the next unread in the drawer's order (`groupFiles`,
    `nextUnread`; a collection's runs in its order). A run or collection
    defaults to 5, an arc to every unread one. It shows the size and what
    makes way (`planDownloads`), or how many fit. `downloadMany` starts
    them under `groupKey` `run:|arc:|collection:<id>` and stops at the first
    that cannot fit.
  - Downloads: "Remove finished downloads (N)".
  - A comic opened online downloads behind the reader (`groupKey:
    "opened"`; the owner, 2026-10-09), so it can be finished offline. It
    counts against the limit, and one that cannot fit is read online with a
    word saying so. Always on for now; a switch belongs in Phase 7's
    Settings, beside "Download the next issue too" (off by default, the
    owner's call 2026-10-09: when on, opening a comic also downloads the
    next unread issue of its run or arc, as Kindle can).
  - Offline, the controls still show what is here, and a press says to
    connect.
- Proof: node tests for `nextUnread` and `planDownload` with eviction; on
  the iPad with a 30 MB cap: three issues, finish the first, download a
  fourth → the finished one goes, an unfinished one never; a fifth that
  cannot fit says so.

### Phase 7 — release path and docs (1 session)
- **The owner, testing on his iPad (2026-10-09):** Settings is a top-level
  section in Flipparr View, beside Comics and Downloads, built from the
  web's own Settings pieces:
  - Reader: the web's Settings › Reader controls (panel view and the
    rest), which follow the profile.
  - Storage: the download limit and usage, with a line on the Downloads
    page pointing to it, and the online page cache (≤ 200 MB) with Clear.
  - Account: the server and profile in use, switch profile, sign out, and
    Use another server.
  - About: app version and build, the server's version, open-source
    licences and the privacy notice (the App Store needs these anyway).
  Built 2026-10-09 (iPad QA pending): `AppSettingsPage` in ReaderApp.jsx,
  a third nav item (id `app-settings`, so the web's Library health count
  never rides on it):
  - Reading: "Download comics you open" (on) and "Download the next issue
    too" (off), kept per device in `settings.json`; a line pointing to the
    reader's own settings for panel view.
  - Storage: the limit (moved from Downloads, which now points here and
    keeps "Remove finished downloads"), usage, and the online page cache
    measured and cleared through `trimFolder`.
  - Account: who is reading from which server, Switch profile, Sign out.
  - About: the web's own, moved to `src/settings/about.jsx` and shared,
    with a Flipparr View card (its build, `__VIEW_BUILD__`, and the
    server's version from `/api/v1/app`). Its local links point at the
    server, since in the app they would replace the app's page. "In your
    browser" becomes "On this iPad". The reader app's build now ships
    `public/` (licences).
- **The owner, testing on his iPad (2026-10-09):** there is no way to
  refresh the library in Flipparr View:
  - pull to refresh, the web's own, moved out of `App.jsx` into a shared
    piece;
  - a refresh in the sidebar's footer, where an admin's Scan sits on the
    web.
  Built 2026-10-09; the app's pull refetches the library, the reading
  answers and the lists, and sends what was waiting.
- **From the owner's iPad (2026-10-09), locked mid-download:** a 40-page
  comic's pages all arrived in 2.3 s over home Wi-Fi, before the lock. But
  the step after them, one `/pages/{n}/panels?vision=no` request per page,
  runs in the app's JavaScript, which iOS pauses when the iPad locks. The
  server log shows the gap. The fix: the panels go through the background
  session too, as files beside the pages, so a whole comic finishes while
  locked or in another app. Only the server's "downloaded" note waits for
  the app to open. Retest with Settings › Developer › Network Link
  Conditioner on a slow profile, so pages are still arriving at the lock.
  Built 2026-10-09: each page's panels are a transfer of their own
  (`<profile>:<file>:p<n>`, `panels-<n>.json`), gathered into
  `panels.json` when the comic is complete; a page whose panels fail is
  read whole and does not fail the download
  (`tests/reader-app-downloads.test.mjs`).
  Proven on the owner's iPad the same day: a 40-page comic (335 MB at
  source) started at 14:25:21 under Network Link Conditioner, the iPad
  locked after six pages; iOS carried on one transfer at a time, a page
  then its panels, and the last panels landed at 14:31:44 still locked.
  Opening the app at 14:44 showed it downloaded and sent the server's note
  within the second.
- `tools/reader_app_release.sh`: `npm run ios:sync`, `xcodebuild archive`,
  upload to TestFlight with an App Store Connect API key read from the
  owner's environment (never the repo). One command per release.
  Written 2026-10-09; first upload to TestFlight the same day, by the owner
  from his own terminal with his key (after a fix, 48b86d8). It needs an App Store Connect record for
  `com.silenttoots.flipparr.view` and an API key (`ASC_KEY_ID`,
  `ASC_ISSUER_ID`, `ASC_KEY_PATH`). It refuses a dirty tree, numbers the
  build by UTC time, and writes its logs outside the repo.
- App Review needs a server: a demo Flipparr with public-domain comics
  (e.g. Digital Comic Museum titles whose licence permits it — verify each)
  and a review account, described in the App Review notes; it is not the
  owner's production instance.
- Docs: `docs/OPERATING.md` "Flipparr View (iPad and iPhone)": connecting,
  what the device keeps (pages at reading size, panels, places, profile
  names and a PIN hash), the cap, sign-out removes everything;
  `docs/PRIVACY.md` "On the device"; `docs/RELEASE_TRACK.md` the entry and
  its evidence; `docs/INSTALL_PROOFS.md` the iPad checklist; `README.md`
  one line; `DESIGN_SYSTEM.md` the app's screens and the progress and
  usage bars.
- iPad checklist (recorded): TestFlight install; connect over https;
  download a five-issue run, lock the iPad, come back (finished); airplane
  mode → read, panel view, rotate, switch profile with a PIN; leave idle
  eight days (sessions last 30 days, `_SESSION_TTL_SECONDS`) and reopen
  (downloads still there); reconnect → progress on the server.

### Releasing from anywhere (2026-10-11)

`.github/workflows/reader-app-release.yml`: Actions -> Release Flipparr View
-> Run workflow (the GitHub app works on a phone), with the branch, tag or
commit to ship (default `v1-forward`). It runs lint and the tests, then
`tools/reader_app_release.sh` on GitHub's macOS 26 runner (Xcode 26.6). The
archive is unsigned there and the upload signs it with Apple's
cloud-managed distribution certificate, so the App Store Connect key needs
the Admin role. Secrets: `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8`,
`DEVELOPMENT_TEAM`. Only what is pushed can be shipped. A dispatchable
workflow must be on the default branch (`main`) to show its button.

### Phase 7b — the owner's TestFlight UI notes (2026-10-10)

Twelve notes from build 48b86d8. Decided with him: hide what is not owned,
covers show status only (actions on a long press), search the library
only. Built (`4cf987c`..`835c36a`):
- the reader host (`readerOnly`, `readerCatalog`), offline only what is
  here (`readable`);
- `CoverStatus` and long-press menus;
- the nav's Downloading count and Offline status;
- Downloads as a cover shelf with a storage gauge;
- library search shared from the web;
- spacing on the app's screens (`.reader-app`).
Found in the simulator and fixed: the https try hung against an http-only
server (4 s limit now). The web's capture is unchanged.

Then (same day), from his follow-ups:
- Settings in sections beside the one open, the web's layout shared
  (`settings/shell.jsx`, `9957a70`).
- His Infuse, Apple Books and Kindle examples (`baeb5ab`, `3471127`; he
  chose these three of four, not the floating download bar):
  - download marks: a comic on its way dims with a ring and percentage,
    Kindle's check once here; issue rows and a drawer's Download go cloud,
    ring with a stop square, nothing (`ProgressRing`, `batchProgress`);
  - reading state on covers: a Read sash, a percentage tag;
  - the continue-reading bar (`ContinueBar`).
- Device checks still owed (simulator taps were unreliable): tapping a
  ring to stop it (row and drawer), a batch's ring filling live, and the
  phone layout of the bar (above the tab bar, under drawers, the toast in
  the same spot).

- **Keeping the library current (the owner, 2026-10-11):** neither host is
  told when the server changes; both fetch again. iOS keeps the app
  suspended for days, so issues downloaded overnight stayed hidden until a
  pull. Now:
  - coming back to the app or a web tab fetches the library and the story
    arcs again (and on the web where every comic is read to), at most once
    a minute (`src/refresh-on-return.js`, shared);
  - the app's pull to refresh works. It never fired in the app: Capacitor
    sets the web view's `bounces = false`, so the page never stretched and
    the shared pull never saw a pull. `FlipparrViewController` turns the
    rubber band back on (always, so a short page can be pulled too), as
    the web has it from a Home Screen.
  Push (the server waking the app) would need Apple's notification
  service per household server; not for the first release.

- **Panel fixes offline (the owner, 2026-10-12):** the 2026-10-09 build
  fixed panels only while online ("a later step"). Now the admin's Fix
  panels stays offline: a page's fix (`PATCH …/pages/{n}/panels`) waits in
  the change queue as `panels:<file>:<page>`, the reader reads it at once
  (`withChanges`), the downloaded comic's `panels.json` keeps it, and it is
  sent as a PATCH on reconnecting -- the last fix of a page standing, as
  the server keeps a person's fix (no `at` there: a fix made on the web
  meanwhile is overwritten by the one sent later). Reading a page again
  and Scan again need the server's finder, so offline they say Flipparr
  could not be reached.

- **One package per comic (the owner, 2026-10-12;** `3f0e4d8`, `91d3596`,
  `7a9d462`). Production's log that evening: one profile's downloads asked
  for 5,481 pages and 4,652 panel answers in four hours, one request each.
  Each RAR page was a bsdtar scan of the whole archive plus a re-encode (p50
  1.5 s). The 32 threads filled (peak 33, Waitress queue 99), a catalog took
  20 s and the owner's panel reads 19-23 s, past the app's 15 s; the app
  took the server for gone and hid Fix panels. Measured on the NAS: a whole
  RAR unpacks in 0.2-0.9 s; a page resizes to reading size in 0.15 s; the
  library is 71% RAR (2,019 of 2,831), pages JPEG, median 1.4 MB and 3,056
  px; the reading cache sat full at 2 GB. What changed, after how Plex and
  Komga hand over files rather than work per request:
  - The server packs a comic once: reading-size pages in a stored zip from
    one pass over the archive, in a background CPU lane, kept
    (`build_offline_package`; `GET /files/{id}/package`, resumable, 503 +
    Retry-After while packing). Every page's kept panels come in one answer
    (`GET /files/{id}/panels?stored=only`).
  - The app downloads the package and the panels: two transfers, not two a
    page. iOS asks again after Retry-After by itself, resumes a dropped
    transfer, and the package is unpacked into the comic's folder as it
    lands. A server that does not pack keeps the page-by-page path, and an
    older app build keeps asking page by page.
  - CPU lanes sized from the machine (`FLIPPARR_CPUS`, the container's
    quota read): readers half, bulk work a quarter; the panel detector's
    threads shared out, no spinning.
  - New downloads are stored as CBZ (`convertNewToCbz`, on); the library's
    existing comics are left as they are.
  - Load test, scratch server (18-core Mac), a 120-page comic: 120 page
    requests at once held a catalog request up to 2.9 s; six package
    downloads at once, 24 ms at most (median 2 ms). To confirm on the NAS
    after the deploy: server_queue notices and peak concurrency while a run
    downloads.

### Phase 8 — live updates (only if the owner answers yes to open question 4)
- `@capgo/capacitor-updater` (MPL-2.0; using it unmodified is compatible
  with an MIT app — record the check) in manual mode: `download`, `next`,
  `notifyAppReady` (an update that does not call it is rolled back).
- The household's server serves the reader-app bundle that matches its own
  build (built into the Docker image). **Bundles are signed** by the release
  pipeline and verified against a public key compiled into the app before
  activation: otherwise a compromised household server could run any code
  inside the app with the plugin's file and token access. Unsigned or
  mismatched → keep the bundled UI.
- A bundle declares the native plugin version it needs; the app refuses one
  newer than its native layer.

## Known issues

- **A piece of the reader left over the page (owner, 2026-10-08, iPhone,
  Home Screen web app).** Reading in panel view, the app was left for a
  video, the phone was turned to landscape, and the app was reopened from
  its icon: a panel of the comic sat over the Comics page's tabs, and
  survived page turns, rotation and closing the reader; only quitting the
  app cleared it. A second shot showed the reader short of the screen with
  the library under it. Not reproduced by the owner or in the iOS 27
  simulator, in Safari, as a Home Screen web app or in Flipparr View,
  with the same steps. Surviving the reader's own unmount points at WebKit
  keeping a stale composited layer (the panel flight is a transform
  animation) rather than at the reader's markup. If it comes back, note
  whether a panel was zoomed and how long the app was away. The reader is
  shared with Flipparr View, so a fix lands in both.

## Risks and limits (say them in the docs and the commits)

- Background downloads stop if the person force-quits the app (iOS cancels
  the session); they resume on the next launch.
- The app trails the server's reader by one TestFlight build unless Phase 8
  is approved.
- A second codebase does not exist, but a second *build* does: a reader
  change must pass the app build in CI and the iPad check before an app
  release.
- Panels stored by a download come from the local tiers only unless open
  question 1 says otherwise.
- The rating gate is enforced at download time and re-checked on launch,
  reconnect and foreground; between an admin lowering a profile's rating
  and the next of those, a downloaded issue stays readable on that device.
- The `at` merge is last-write-wins by the device's clock (clamped to the
  server's now): a device whose clock runs behind can lose a save to an
  older one from another device. There is no three-way merge.
- The offline PIN hash is device-local and unthrottled; password-locked
  profiles cannot be unlocked offline.
- A replaced file leaves a stale download until reconnect marks it.
- Offline there is no catalog: Home's Downloaded shelf and the reader work;
  run views and lists need a connection.
- One server per install.

## Verification, end to end

1. Backend suites both orders; `npm test`; eslint; `vite build`;
   `build:reader-app`; XCTest for the plugin.
2. Phase 1's before/after visual comparison and the iPad gesture check.
3. Each phase's iPad proof, against production only where the panel hold
   allows (scratch stack otherwise).
4. Phase 7's checklist, recorded in `docs/INSTALL_PROOFS.md`.

## Not covered here

Panel-scanner work is planned in memory `panel-scanner-plan` and recorded in
`docs/PANELS.md`; it is on hold until the owner's data review
(~2026-10-15 to 10-22). This plan does not change the panel finder.
