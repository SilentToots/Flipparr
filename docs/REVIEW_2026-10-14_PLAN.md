# Review of the week's deployments (2026-10-07 → 2026-10-14) — fix and optimisation plan

Repository `/Users/blee/Developer/Comic Arr Project`, branch `v1-forward`. Reviewed range
`3e3dfbf^..HEAD` (141 commits, 144 files, +29.4k/−10.8k). Production runs `25295ff`
(0.2.0, healthy, CI green). `a2c6ec5` (docs) is unpushed. Line numbers are as of
2026-10-14 and drift; search by the names given.

## Context

The week shipped three large things on top of 0.1.0: **Flipparr View** (the Capacitor
iPad/iPhone reader, with offline downloads as one server-built package per comic),
**reading state that belongs to the comic** (page fingerprints, progress/panels/covers
keyed to the issue, CBR→CBZ conversion of the library, schemas 68–77), and **library
integrity** (damaged-page check and bulk replace, one-copy-per-issue guard, libarchive
3.8.9). Three reviewers read the diff by area; I read production's own log for the same
week and confirmed every item ranked "fix now" by reading the cited lines myself.

**What production said (`/config/logs`, 7–14 Oct):**
- One incident: 12 Oct 21:00–00:00 UTC, the app downloading a run *page by page* put
  ~6,600 requests/hour through 32 Waitress threads (queue depth 160+; the 200-connection
  limit hit 1,146 times). The one-package download (`790b01e`, deployed 13 Oct 01:25)
  designed it away: 14 Oct shows 33 page renders and 116 package/offline requests.
  Root cause worth closing: the iOS background session sets no
  `httpMaximumConnectionsPerHost`, and a page render on a RAR is one `bsdtar -xOf`
  that decompresses a solid archive from the start for every page.
- Page renders took 5–20 s on *quiet* days too (10 Oct: 285 of 302 over 5 s; worst
  21.6 s; one `GET /api/v1/catalog` 20.1 s). Cause: the RAR per-page extraction above,
  3–4 archive reads per first panels request, and a catalog cache that is invalidated
  by every write (it stats the `-wal` file) and rebuilt behind one lock.
- 2 × `UNIQUE constraint failed: files.path` in the CBZ conversion at 15:12 UTC 13 Oct —
  before `4934a17` was deployed (18:08 UTC). Closed. The other conversion failures are
  the damaged-comic refusal working; verdicts from libarchive 3.6.2 are re-read only
  when someone runs the page check (see B9).
- The one red CI run (`c226c37`) was fixture comics with no real pages; fixed in `e416b9a`.

**Decisions for the owner (defaults chosen; say if you want the other):**
1. **A4 — what signing out of the app does.** Default: revoke *that device's* token
   only (per-token revocation, small schema bump). Alternative: bump the profile's
   `session_version`, which also signs the profile out of every other device and web
   session — today's logout does not do that.
2. **A1 — cross-profile eviction stays.** Default: the 2 GB cap is the device's, so a
   download by A may still remove B's *finished* comic — now from B's own folder, with
   a note B sees and a removal told to the server as B. Alternative: each profile may
   only evict its own comics (a profile can then be refused a download while the
   device has room).

**Rules that bind the work** (`AGENTS.md`, memory): tests with every change and both
suite orders; frontend `npm test`, `npx eslint src tests`, `vite build`; tokens only in
`styles.css`; evidence in tables not logs; never clear live records; deploy backend
and safety fixes promptly behind the config backup and SAB guard, batch UI polish;
commit as `SilentToots`, `git checkout .claude/launch.json` first, the owner pushes;
app releases (TestFlight) are the owner's to run via `tools/reader_app_release.sh`.

## Tier A — fix now (data, profile and safety defects)

Server items (A2, A5, A6, A7 and A4's server half) deploy to the NAS each as it lands.
App items (A1, A3, A4's app half) ship together as one TestFlight build the owner
uploads — build them first in one sitting, prove them in the simulator, then release.

**A1. App: making room deletes the wrong profile's comic.** `v1-prototype/src/reader-app/downloads.js:109-118`
plans with `this.store.allDownloads()` — `device-store.js:54-62` flattens every
`u<N>/downloads.json` with no profile on the rows — then `this.remove(evicted)` deletes
under `this.profileId`. Profile B's finished #42 is chosen; A's unfinished #42 is deleted
(or nothing is, the cap is exceeded, and the server is told A removed a comic it never
had). The download sheet (`ReaderApp.jsx:1134`) plans the same way. **Fix:** tag each
row with `profileId` in `allDownloads()`; `evictionOrder` returns `{profileId, fileId}`;
evict from *that* profile's folder and `downloads.json`. Cross-profile eviction stays
(the cap is the device's — the owner's decision of 2026-10-08) but becomes correct;
confirmed default, flagged under Decisions. Two consequences the fix must handle: (1)
`tellRemoved` has to speak as B, whose token may not be on the device — queue the
removal under B the way `replay-profiles.js` queues B's reading, sent when B's
credentials are next present, or the server's download record drifts; (2) B opens the
app to a missing comic — B's Downloads shows a one-line note "Removed to make room on
this device on <date>" (a row with `state: "evicted"` kept until B sees it) rather than
silence. **Test:** `tests/reader-app-downloads.test.mjs` — two profiles, cap forces
eviction, B's folder loses the comic, A's rows untouched, B's pending removal queued;
`allDownloads` is currently stubbed to `[]`, so the eviction path has never run in a
test.

**A2. Backend: a failed replacement's rollback puts the RAR back under the `.cbz` name.**
`app.py:9804` passes `Path(imported["destination"])` as `original_path` to
`_rollback_replacement_swap`; since `c226c37` a converted replacement is filed at
`X.cbz` while the original was `X.cbr`. If `_catalog_imported_issue` raises after the
swap, `app.py:8080` moves the quarantined RAR to `X.cbz` and
`restore_replacement_original` repoints the row without touching `extension`. The
first call site (`app.py:8294`) passes `replacement_original` correctly. **Fix:**
return `replacementOriginal` from `_import_selected_comic` and pass it at 9804.
**Test:** extend the rollback test (`test_app.py` ~L10803, currently original ==
destination) with a `.cbr` original and `.cbz` destination; assert the `.cbr` is back
at its own path and the row's `extension` is `.cbr`.

**A3. App: any 5xx throws away queued offline reading.** `transport.js:347-353,
360-368, 278-282`, `server.js:113-117`: every non-2xx is "refused" and the row is
dropped (`queueDrop`). A 502 from the proxy during a redeploy loses a flight's places
and ratings. Tests use 404 only. **Fix:** keep the row and stop this replay on
network errors, 5xx, 408, 429 **and 401/403** (an expired token or a PIN-locked profile
is this device's problem, not the data's — and A4's logout change produces exactly that
401 on another device holding a queue); drop only on definite refusals: 400, 404, 409,
422-class. **Test:** 503 and 401 leave the queue intact and stop the replay; 404 drops
the row.

**A4. App + server: profile switch and sign-out leak work and tokens across profiles.**
- `ReaderApp.jsx:258-259`: `/profiles/switch` swaps the shared client's token at once;
  `attach(B)` happens after `/auth/status`; A's running replay/`check()` continue with
  B's token — a child profile's `hidden` answer then deletes A's downloads
  (`downloads.js:433-467`). **Fix:** `attach(null)` and cancel A's engine before the
  switch; replay only under a client bound to the row's profile (as
  `replay-profiles.js` already does).
- `app.py:21015-21019` logout only expires the cookie; the app's bearer token stays
  valid 30 days; `ReaderApp.jsx:274-281` never removes `session@<origin>#<id>` from
  the Keychain, and the offline path returns early before it. **Fix (app side, no
  decision needed):** on sign-out remove that profile's kept session from the Keychain
  on both the online and offline paths. **Server side — the owner's call (see
  Decisions):** `session_version` is per profile, so bumping it on an app logout signs
  that profile out of *every* device, including the phone's web session — today's
  cookie logout does not. The alternative that matches today's behaviour is per-token
  revocation: give tokens an id (`jti`) and keep a small `revoked_tokens` table
  (schema bump, pruned at expiry) so one device's logout kills one token. Default in
  this plan: per-token revocation. **Test:** server — a token used after its own logout
  is 401 while the same profile's other token still works; app — the offline picker no
  longer lists the signed-out profile.

**A5. Backend: CBZ conversion has two crash windows that leave two copies, forever
skipped.** `app.py:7411-7426`: `os.replace(partial, target)` → `repoint_converted_file`
→ `os.replace(path, aside)`; a crash between steps leaves `X.cbr` and `X.cbz` both in
the library, the next scan adds the other as a copy, and every later run says
`kept("A CBZ of it is already there")` (`app.py:7377`). The partial
`.X.flipparr-convert.partial.cbz` (`app.py:7401`) does not match `IMPORT_PARTIAL`
(`app.py:11413`, digits only) and `scan_folder` `rglob`s dotfiles, so a SIGKILL
mid-copy leaves a half-zip the next scan catalogs. The auto-scan (`app.py:18215`)
never checks whether a conversion runs. **Fix:** (1) when the target exists and the
row still points at the `.cbr`, finish the repoint and move-aside instead of
`kept`; (2) name the partial `.X.flipparr-{pid}-{tid}.partial.cbz` so the filter
matches; (3) the auto-scan skips while `cbz_conversion_status().running`. **Tests:**
target present + row at `.cbr` → completed; partial name matches `IMPORT_PARTIAL`;
auto-scan deferred while converting.

**A6. Backend: the overnight panel worker re-runs every minute all night.**
`app.py:18458` backs off an hour only when `stopped in ("done", "cap")`; a night that
stopped `vision_unavailable`, `error` or `stopped` re-runs `overnight_panel_pass` every
60 s, each run writing an `overnight_panel_runs` row and walking every profile's
continue-reading and every planned page. No test covers the worker. **Fix:** back off
for every stop reason except `window_end`, with the reason's own interval
(`vision_unavailable` → 15 min, `error` → 1 h, `stopped` → until the next night).
**Test:** a fake store with a last run `error` two minutes old → the worker `continue`s.

**A7. Backend: "Keep one copy" moves the file before recording it.** `app.py:7706-7712`
`os.replace(source, target)` then `set_aside_duplicate`; a crash between leaves the
file in `.flipparr/duplicates` with its row `present=1` at the old path and no
`duplicate_resolutions` row (log line only — AGENTS.md: evidence in tables). **Fix:**
write the resolution row as `pending` first, move, then mark `done`; on move failure
delete the pending row. **Test:** move raises → no file moved, no resolution row.

## Tier B — correctness and hardening (one batched deploy; app items in one TestFlight)

**Web**
- **B1. `src/history-step.js:23-25`:** a same-key change (`?read=63` → `?read=64` via
  Read next; `?series=61` → `?series=62`) pushes with `state: null`, so closing replaces
  to `/library` and Back reopens 63 — the bug `b1358c1` fixed. **Fix:** same key on both
  sides → `{kind: "replace", state}` carrying `from`. Tests for both cases.
- **B2. Refresh on return blanks drawers.** `App.jsx:5507-5511` bumps the reading
  version on every return; `drawers/SeriesDrawer.jsx:74-83` `setReading(null)` on reset
  and on failure, `drawers/ReadingListDrawer.jsx:41-48` turns the arc drawer into an
  error while the server is down. **Fix:** keep data while refetching; on failure leave
  it. Add a request-sequence ref to `loadCatalog` (`App.jsx:5466-5490`) so an older
  catalog response cannot land after a newer one.
- **B3. Reader (`src/reader/ReaderView.jsx`):** the new-`fileId` reset (650-656) clears
  neither `pageFailures` nor `spreads` (Read next carries "Page 4 could not be loaded"
  into the next comic) → add both; the zoomed-rotation path (757-759) still fits by
  height → use `fittedSize`; pinch effect cleanup never resets `pinching`/`dragged`
  refs (1136-1230) → reset them; PanelEditor's drawn panel reads last frame's `draft`
  (241-249) → build from `drawnRect(origin, point)`; `tries >= PANEL_RETRIES` is two
  retries not three (419-421, 687) → fix the comment; `styles.css:4065`
  `.reader-page { max-height: 100dvh }` disagrees with the reader's
  `--reader-height` → `max-height: 100%`.
- **B4. Confirm steps.** `settings/cbz-conversion.jsx` starts with one click even when
  "Keep the originals" is off (default keeps them; deletion is opt-in — a confirm, not a
  redesign): reuse DuplicateCopies' two-step confirm and say "N originals will be
  deleted". `App.jsx:2489-2496` "Replace all damaged" queues N acquisitions unconfirmed
  and reads "all 1 damaged comics" → confirm + plural.

**Backend**
- **B5.** `catalog_store.py` `_move_personal_issue_data` (~4500-4510) keeps the target's
  rating and cover regardless of date (docstring says "the newer stands") and the
  merged-away rating is cascade-deleted unrecorded → compare `updated_at`, record the
  dropped one. `catalog_store.py:5932` superseded rename `#superseded-by-{file_id}`
  can collide on a second conversion → use the holder's id.
- **B6.** Package eviction race: `sweep_image_cache(..., interval=0)` (`app.py:15257`)
  can delete a package between `offline_package` saying ready and `send_file` opening
  it (`app.py:22564-22570`) → 500. Hold packages out of the sweep while a download row
  is `preparing`/`downloading` for them, or re-build on ENOENT and answer 503 +
  `Retry-After` as the build path does.
- **B7.** Damaged-check fallback `bsdtar -xOf path member` (`app.py:7088`) treats the
  member as a wildcard pattern: a page name with `[…]` would not match itself and be
  graded unreadable (not auto-replaced, but wrong). Check bsdtar's docs; if so pass
  `--no-wildcards`/escape, with a fixture whose page is `[cover].jpg`.

**App**
- **B8. Storage accounting and removal.** `offline.js:125-136`, `downloads.js:309-312`:
  failed rows count their estimate forever and their partial pages stay on disk
  (disk-full → permanent "Needs X MB more") → don't count `failed`, delete the folder on
  failure, let finished `stale` comics be evicted. `downloads.js:465-467`: `check()`
  `hidden`/`gone`, a 404 on a progress save, and "Remove finished downloads" can remove
  the comic open in the reader → defer `this.reading` until the reader closes (the test
  at `reader-app-downloads.test.mjs:162` claims this but tests an unfinished comic).
  `downloads.js:98-123`: two quick taps enqueue the same comic twice → an in-flight map
  per file. `downloads.js:259-265` `mark("package")` sets every page done without
  checking `event.pages` → require `event.pages === row.pageCount`; the "package left
  whole" recovery (`downloads.js:551-556`, Swift `unpack` 263-278) is dead code → delete.
  `downloads.js:408-410` "downloaded" notes retried forever on a 4xx → mark noted.
- **B9. Transport.** `transport.js:73-85, 103-106`: timeouts race a timer but never
  cancel the request, and each timeout starts its own 5 s probe → `AbortController` per
  request, one shared in-flight probe, and replay after a timeout whose probe
  succeeded. `server.js:47-67`: an https attempt slower than 4 s falls back to http and
  is saved permanently (password and token in clear on the LAN) → fall back only on
  refusal or certificate failure, and show "not encrypted" when it does.
  `FlipparrNative.swift:304`: set `httpMaximumConnectionsPerHost` (4) on the background
  session — the mechanism behind the 12 Oct flood; `fetchToFile` (Swift 157-161) gets
  the same redirect handling as the background session, which should compare scheme
  as well as host (473).
- **B10. Release workflow.** `.github/workflows/reader-app-release.yml:24-28, 45-78`,
  `tools/reader_app_release.sh:44-54`: `ref` accepts any ref including a fork PR's,
  whose `npm ci` then runs beside `AuthKey.p8` (Admin role) → allow only this repo's
  `v1-forward`, `main` and tags; build in a step with no secrets; `persist-credentials:
  false`; pin actions by sha; build number gains seconds or the run number (two runs in
  a minute collide); the dirty-tree check covers `package*.json`,
  `vite.reader-app.config.mjs`, `capacitor.config.json`, `public/`, `App.xcodeproj`.

## Tier C — performance (measure first, then the two cheapest wins)

- **C1. Panels route reads the archive 3–4 times for a first read** (`app.py:16064,
  16108, 16117, 16121` renders; `page_hashes` re-reads the member at `15921`) → use the
  stored `page_fingerprints` when present, render once and derive the backdrop and
  spread halves from that image. Expected: first-panels latency on RAR drops to one
  extraction; on CBZ it is already cheap.
- **C2. Catalog cache invalidated by every write.** `_catalog_stamp` (`app.py:19713-
  19723`) stats the `-wal`, so every page-turn save and every first-render fingerprint
  rebuilds the ~18 MB catalog behind `_CATALOG_BUILD_LOCK` (`19737`) — the leading
  candidate for the 20 s `/api/v1/catalog`. **Fix:** stamp from a store-side "catalog
  generation" counter bumped only by writes that change what the catalog shows (files,
  runs, identities, ownership), not by `reading_progress`/`page_fingerprints`/
  `comic_reading`. **Test:** one test enumerates every store write method and asserts
  which bump the generation and which do not (a write type missed here is a stale
  catalog, so the list is explicit, not inferred). **Proof:** time `/api/v1/catalog` on
  the NAS while a reader turns pages; before/after in `docs/PERFORMANCE.md`.
- **C3. Finish the move to CBZ.** The per-page RAR cost is the root of both the 12 Oct
  flood and the quiet-day 5–20 s renders. Report on the NAS how many library comics
  are still `.cbr` after the conversion run and why (damaged refusals vs not yet run);
  the damaged ones go through Replace, the rest through another conversion pass once
  A5 lands. New downloads are already stored as CBZ (`7a9d462`).
- **C4. Confirm in production** after C1–C2: queue notices and connection-limit lines
  per day stay at the 14 Oct level (46 / 0) through a week of app use; the slowest
  page render under 3 s.

## Tier D — tests, accessibility, copy (fold into the Tier B deploy where cheap)

- Tests that would have caught Tier A: engine eviction with real rows (A1); rollback
  with differing extensions (A2); 5xx replay (A3); profile-switch race (A4); worker
  back-off (A6). Also: `history-step` same-key cases; `refresh-on-return` hook
  (listener, `enabled`, cleanup); `host-images.test.mjs` misses `new Image()` (the
  PanelEditor preload at `ReaderView.jsx` bypasses the host and is broken in the app);
  `shared-modules.test.mjs` omits `settings/`, `search/`, `history-step.js`,
  `refresh-on-return.js`; the `reader-panels` visual state requires `.panel-view` but
  not `.scrim`.
- Accessibility: `frame/pullRefresh.jsx:53` live region mounted already filled → keep
  it mounted, change text; `library/shelves.jsx` `ReadState` `role="img"` inside a
  labelled button is never spoken → add the state to the button's label (DESIGN_SYSTEM
  promises "Read"/"42% read"); CBZ conversion progress not in a live region;
  `overnight-panels.jsx` no busy state and From = Until accepted; `duplicate-copies.jsx`
  and `cbz-conversion.jsx` hide the card on a failed load → show an error.
- Styles: literals outside tokens — `180ms cubic-bezier(.2,.8,.2,1)` ×8 (dim), `120px`,
  `minmax(150px, 1fr)`, `56px`, `--control-height-md: 44px`, `stroke-width: 2.5`; stale
  comments at `styles.css` ~4031 and ~4070 naming App.jsx. Decide whether the
  design-system test should also guard durations and sizes (today it checks colours).
- `recommended.js` `issueOfFile` fallback by number takes the first match (two issues
  with one number); the finishing of a volume writes a `comic_reading` row per
  collected issue with `last_read_at=now` (floods Keep Reading) — check with a volume.

## Open items the plan and commits already record (not new defects)

iPad QA pending for Flipparr View Phases 5–7 and the 30 MB-cap proof; XCTest for the
Swift plugin (none exists; the package parser is untested); App Review demo server not
built; live updates (open question 4) unanswered so Phase 8 unbuilt; the bell and
making arcs/collections not in the app; a stale composited layer over the page (known);
panel scanner on hold ~10-15 → 10-22.

## Verification

1. Backend: `python -B -m unittest test_app test_catalog_store test_http_contract
   test_page_panels test_panel_benchmark test_content_rating test_no_undefined_names
   test_reading_list_formats test_torrent_client test_arc_catalog test_provider_evidence
   test_secret_hygiene`, then the reverse order; the fulfillment drill
   (`tools/fulfillment_drill.py`) — all in the NAS container or CI, not the Mac's
   Python (the scratch venv is gone; recreate a 3.13 venv if a local run is wanted).
2. Frontend: `npm test` (380 today), `npx eslint src tests`, `npx vite build`, and for
   any `styles.css` change the visual harness at both viewports + `layout:check` on the
   scratch stack; `a11y:audit` for the live-region and label changes.
3. Tier A deploys: each behind the recipe in memory `flipparr-deploy` (config backup,
   `git archive` → `~/flipparr-build`, build, smoke, SAB guard = 0, recreate, `/healthz`
   build). A2/A5/A7 additionally: run the conversion status and the duplicates list
   afterwards and confirm no new copies appeared (read-only queries).
4. App fixes (A1, A3, A4, B8–B10): simulator against the scratch 8803 server (memory
   `reader-app-scratch`), then the owner's TestFlight upload; the two-profile eviction
   and the 5xx-replay cases on a real iPad with the cap set to 30 MB.
5. Performance (C1–C2): before/after timings recorded in `docs/PERFORMANCE.md`; the
   per-day log counts above re-run a week later.
