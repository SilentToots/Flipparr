# Releasing Flipparr

How a build is identified, what a release involves, and what an upgrade or a
rollback does to your data. For day-to-day operation see [OPERATING.md](OPERATING.md).

---

## 1. What identifies a build

Three numbers, and they answer different questions.

| | Where | Changes when |
| --- | --- | --- |
| **App version** | `APP_VERSION` in `app.py` | a release is cut, by hand |
| **Build stamp** | `FLIPPARR_BUILD`, a build argument | every image build — the commit that made it |
| **Schema version** | `SCHEMA_VERSION` in `catalog_store.py` | the catalog's shape changes |

A running instance reports the first two without authentication, so you can
identify it before signing in:

```bash
curl -s http://localhost:8787/healthz
```

```json
{ "status": "ok", "version": "0.1.0", "build": "6a7a4b2…", "catalog": "ok" }
```

`catalog` says whether the database can be read; when it cannot, the answer
is 503 and Docker's health check reports the container unhealthy.

The same pair is on the first log line at start-up. The build stamp is the one
that matters when something is wrong: it names the exact commit, where the app
version only names the release.

An image built without the argument reports `build: "source"`. That is expected
for a local build; a release image should never say it. `compose.build.yaml`
passes the argument through, so a stamped build from a checkout is:

```bash
FLIPPARR_BUILD=$(git rev-parse HEAD) docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

---

## 2. Cutting a release

1. **Decide the version.** Pre-1.0, raise the minor for user-visible change and
   the patch for fixes.
2. **Bump `APP_VERSION`** in `app.py`, as its own commit.
3. **Note whether `SCHEMA_VERSION` moved** since the last release. If it did,
   the release notes must say so — that single fact decides whether a rollback
   needs a restore.
4. **Point `compose.yaml` at the new tag** (`image:` default), as part of the
   same commit, so a fresh checkout installs the release it belongs to.
5. **Tag the commit and push the tag:** `git tag v0.1.0 && git push origin v0.1.0`.
   CI (`.github/workflows/v1-forward.yml`) runs both test orders, secret
   hygiene, the fulfillment drill, the web build from the lockfile, the image
   checks and the install drill on that commit, and then the `publish` job
   builds the image with the commit stamped in and pushes it as
   `ghcr.io/silenttoots/flipparr:0.1.0` and `:latest`. Pushes to the
   development branch publish `:edge` the same way. Nothing is published
   unless everything before it passed.
6. **Verify the published artefact, not just the build:**

   ```bash
   docker pull ghcr.io/silenttoots/flipparr:0.1.0
   docker run --rm --entrypoint printenv ghcr.io/silenttoots/flipparr:0.1.0 FLIPPARR_BUILD
   python3 -B tools/install_drill.py --image ghcr.io/silenttoots/flipparr:0.1.0 \
       --previous ghcr.io/silenttoots/flipparr:<the release before> --out ./install-drill
   ```

   The drill installs it clean, upgrades the previous release to it, rolls
   back by restoring the backup, and checks that a config folder it cannot
   write and a catalog from a newer build are refused with a plain message
   (docs/INSTALL_PROOFS.md).
7. **Write the release notes below**, including the migration line.

The package on GitHub is private until its owner makes it public; a release
anyone can install needs that done once.

### What makes the build reproducible

- **Base images are pinned by digest**, not tag. `node:22-bookworm-slim` moves;
  `node@sha256:83f487e0…` does not. Refresh them deliberately, as their own
  commit, so the change is visible in history.
- **`npm ci`, never `npm install`** — installs the lockfile exactly and fails if
  `package.json` has drifted from it.
- **Python dependencies are pinned** to exact versions in `requirements.txt`.
- **SQLite is built from a pinned source tarball** (the `sqlite-build` stage),
  checked against its published hash, so the image never depends on the base
  image's older library.
- **Build from a clean export of the commit**, never from a working folder
  that earlier builds were unpacked into: unpacking adds files but never
  removes the ones the repository has since dropped, and `COPY v1-prototype/`
  then ships them. `git archive <commit> | tar -x -C <empty folder>` and
  build there.

---

## 3. Upgrading

```bash
docker compose pull && docker compose up -d
```

The catalog migrates itself forward on start.

Your library is mounted read-write, and two things write to it: importing a
completed download, and replacing a damaged file — which moves the original you
replaced into `.flipparr/quarantine/` inside the library root rather than
deleting it. Scanning never writes; it reads sizes and modification times.

**Back up `/config` before every upgrade.** Not because upgrading is risky, but
because *rolling back* needs it — see below.

---

## 4. Migration and rollback

Migrations run forward automatically and only forward. Before a build raises
the schema it keeps the catalog as it was beside the database
(`flipparr.db.pre-v<N>`, the last three kept). There is no downgrade path, by
design: a build refuses to open a catalog newer than itself, before touching
it, rather than writing to a shape it does not understand:

```
Flipparr cannot start: This library's catalog is at schema 70, newer than this build's 64: it was last opened by a newer Flipparr. Run that version, or restore the backup taken before it.
```

That is what the previous image prints, and it exits, if you roll back to it
after an upgrade that raised the schema (from 0.1.0 on; earlier builds
started and answered errors instead).

**So a rollback across a schema change is: restore the backup, then run the
previous tag.** Not just the previous tag.

```bash
docker compose down
tar -xzf flipparr-backup-YYYY-MM-DD.tgz -C /path/to     # as the config folder's owner
FLIPPARR_IMAGE=ghcr.io/silenttoots/flipparr:<previous>  # in .env
docker compose up -d
```

Rolling back a release that did *not* raise the schema needs no restore — the
previous image opens the same database unchanged.

**If you have no backup**, nothing is lost that cannot be rebuilt: your comics
were never modified. Point a fresh `/config` at the same folder and re-scan.
You lose manual corrections, aliases and request history — not files.

---

## 5. Release notes

### Unreleased

**Schema:** 79 (adds `overnight_panel_runs`, `duplicate_resolutions.state`,
`revoked_sessions`). No change on disk.

- *Signing out ends that device's session on the server* (`revoked_sessions`):
  before, the sign-out only expired the cookie, and Flipparr View's token sat
  valid in a signed-out device's Keychain for its 30 days. The profile's other
  sign-ins -- a phone's, the web's -- stand; "sign out everywhere" is still
  the admin's.
- Web, from the same review (Tier B): Read next from the finish drawer no
  longer leaves the first comic behind in history (Back reopened it); coming
  back to the tab keeps a run's or an arc's drawer filled while it refetches,
  and keeps it when the server is away; an older catalog answer can no longer
  land over a newer one; the reader clears the last comic's failed pages and
  spreads when the next opens, fits a zoomed spread the same way on
  rotation, and a pinch cut off by a re-render no longer leaves swipes off;
  a drawn panel keeps its last sliver of movement; "Convert to CBZ" asks
  once more when originals will be deleted, and "Replace all damaged" asks
  before queuing every replacement.
- Server, from the same review (Tier B): a page whose name reads as a shell
  pattern (`[cover].jpg`) is read from a RAR -- bsdtar took the name as a
  pattern and called the page unreadable; a package built in the last
  fifteen minutes is never swept, and one swept between "ready" and its
  fetch is packed again with a wait, not a 500; where two merged issues
  both carry a profile's rating or a chosen cover, the newer stands (the
  target's stood whatever its date), and the one let go is said in the log.
- Performance, from the same review (Tier C): the catalog's cache and ETag
  are stamped with a catalog generation that moves only on writes to the
  tables the catalog is made from -- a page turn, a page's fingerprint or
  panels no longer move it, so a poll after a page turn is a 304 and not the
  whole 14 MB again (the build itself is 0.6 s on a 2,700-comic library;
  the payload was the cost); a first panel read extracts a page once, not
  three or four times, and takes its hash from the fingerprint the store
  already holds (docs/PERFORMANCE.md).
- Tests, accessibility and copy, from the same review (Tier D): the
  pull-to-refresh pill stays in the document so screen readers announce it;
  a shelf card's name carries its reading state ("Read", "42% read") where
  the sash inside the button was never spoken; the CBZ conversion says a
  start and a finish once, and both it and the duplicate-copies card say
  when they could not load instead of hiding; the overnight read's card
  shows it is saving and warns when From and Until are the same hour (no
  window); the panel editor's next-page preload goes through the host, so
  it works in Flipparr View too; a transition's duration must be a motion
  token and no curve may be written by hand (`npm test` enforces it; the
  panel dim and the glass indicator were rewritten in tokens); the harness
  now proves the dim in the panel-view capture, and the shared-module and
  host-image checks cover the settings and search screens.
- Flipparr View (needs the next TestFlight build): a download making room
  removes another profile's *finished* comic from that profile's own folder,
  leaves them a note shown when they next open the app, and tells the server
  as them -- it removed this profile's unfinished copy instead; places,
  ratings and marks waiting offline are kept through a busy or broken server
  (5xx, 408, 429) and a session to renew (401, 403), and dropped only on a
  refusal -- a 502 during a redeploy threw a flight's reading away; a profile
  switch holds the leaving profile's engine and queue until the next profile
  is attached, so nothing goes out under the wrong token; signing out
  removes the profile's kept session from the device on every path. Also
  (Tier B): a failed download leaves nothing on disk and counts for nothing
  against the limit (failed rows held their estimate forever); a finished
  comic whose server copy changed may make way; the comic open in the
  reader is never removed under it -- a check's "hidden" or "Remove finished"
  waits for it to close; two taps for one comic are one download; a
  package that unpacks short is a failure, not a whole comic; a note the
  server refuses is not asked again every launch; a request given up on is
  cancelled and one is-it-there check runs at a time; the background session
  opens four connections to the server at most; a redirect keeps the token
  only on the same host and scheme, for one-off fetches too; falling back to
  http is said on the connect screen. The release workflow ships only this
  repository's own branches and tags, keeps no checkout token, and the build
  number is to the second.

- Fixes from the review of the week's deployments (docs/REVIEW_2026-10-14_PLAN.md,
  Tier A): a replacement converted to CBZ that failed after its swap is
  rolled back to its own `.cbr` name, not a RAR under the `.cbz` one; a
  library CBZ conversion cut off between the CBZ landing and its record is
  finished the next run instead of leaving two copies, its copy in flight is
  named so a scan skips it, and scans wait while a conversion runs; a night
  of the overnight read that stopped on an error or a resting connector
  waits before it is tried again (it ran every minute); "Keep one copy"
  records a set-aside before the file moves, and an interrupted one is
  finished or dropped at the next start.

- *Overnight panel read:* between the hours set (midnight to 6 am by
  default, local time), the panels of the comics each profile is likely to
  open next are read ahead -- Keep Reading and each run's next issue, new
  arrivals, comics on Flipparr View devices -- an admin's by the vision
  assistant up to a nightly limit (Settings › Reader). Opening a page nothing
  has read yet still reads it on the spot. Flipparr View takes the new
  panels for comics already downloaded.
- Flipparr View: a comic still being packed shows Preparing, not 100%; a
  run's comics start downloading a few seconds apart.

### 0.2.0 — Flipparr View, reading that follows the comic, and a library kept whole

**Schema:** 76 (from 64). Upgrading keeps the catalog as it was, as
`flipparr.db.pre-v76`, beside it; rolling back to 0.1.0 means restoring that
copy or a `/config` backup,
as 0.1.0 refuses a newer catalog. Added: `page_panel_history`,
`page_vision_answers`, `offline_downloads` (what Flipparr View keeps on each
device), `cbz_conversions`, `page_fingerprints`, `comic_reading` (a
profile's reading of an issue or volume), `issue_cover_preferences`,
`archive_checks` (every page of a comic read, and what was found) and
`duplicate_resolutions` (which copy of an issue was kept, which set aside).

**Upgrading changes what is on disk only when you ask:** converting
existing CBR comics to CBZ, keeping one copy of an issue, and replacing a
damaged comic each start from a button and move files aside under
`.flipparr/` rather than deleting them.

**Settings and environment, new:** *Store new comics as CBZ* (on);
*Replace damaged comics automatically* (off). `FLIPPARR_CPUS` caps the CPUs
Flipparr sizes its work for (it reads the container's quota otherwise);
`FLIPPARR_PACKAGE_CACHE_MB` (4096) bounds the packed comics kept for
Flipparr View; `FLIPPARR_READING_CACHE_MB` now defaults to 4096
(OPERATING.md).

- *Flipparr View*, the reader-only iPad and iPhone app (TestFlight): the
  web's own screens, page and panel view, downloads in iOS's background
  session read offline, offline places, ratings, marks, reading lists and
  panel fixes sent with their time when back online, profile switching
  offline, run/arc/collection downloads, Settings. The server side: tokens
  in headers, `GET /api/v1/app`, offline manifests and checks, late saves
  with `at`, and the reader API pinned by a contract test.
- *Downloads that do not load the server:* a comic is packed once, at
  reading size, in the background lane and served whole and resumably
  (`GET /api/v1/files/{id}/package`); readers' work and bulk work run in
  CPU lanes sized from the machine.
- *Reading belongs to the comic, not the file* (docs/READING_STATE_PLAN.md):
  a place, chosen covers and backdrops, and panels (by the page's image)
  follow the issue into any copy, replacement or CBZ of it; merges,
  rebuilds and re-matches keep them; a place is found again by the page's
  fingerprint. Run, arc and collection counts count comics, not files.
- *CBZ:* comics Flipparr downloads as RAR are stored as CBZ, every page as
  it was; the existing library can be converted on the admin's word, each
  comic keeping its record.
- *Damaged comics:* every downloaded comic has its pages read before it is
  filed, and a damaged release is refused and the next tried; *Check every
  comic* reads the library's pages once and lists the damaged ones under
  Library health, with *Replace all damaged* (and the automatic setting).
- *One copy per issue:* an issue already in the library is never
  downloaded or filed again; Library health lists issues with two copies,
  the better picked, and keeps one on the admin's word.
- *libarchive 3.8.9* is built into the image: Debian's 3.6.2 refused pages
  of valid RAR comics (and so called them damaged), and stopped at the first
  bad page of a damaged one.
- Panel view: a benchmark against hand-fixed pages, fixes keyed to the page
  itself and exportable, the leftover pass on default installs, a softer dim.
  Reader: the place saved on leaving, a failed page offers Try again, a
  swipe from the phone's edge no longer closes the comic.
- Releases posted more than 90 days before an issue came out are refused
  for it. The log is kept in `/config/logs` (50 MB). Status marks mean one
  thing on every cover.

**Proven for this release:** the unit suites in both orders and the
fulfillment drill (12 scenarios) in CI; on the owner's library of 2,830
comics: the conversion (1,998 converted, every page byte-identical), the
damage check against unrar and libjpeg, and the owner's daily use since 0.1.0.

### 0.1.0 — first release

The first installable release of Flipparr, for one household on one Docker
host. Everything below is in `ghcr.io/silenttoots/flipparr:0.1.0`, built by
CI from the tagged commit and stamped with it (`/healthz` shows `build`).

**Schema:** 64. There is no earlier release to upgrade from; a pre-release
build's catalog is raised to 64 on first start and a copy of it kept beside
it (`flipparr.db.pre-v<old>`, the last three). Rolling back to a pre-release
build means restoring that copy or a `/config` backup -- an older build
refuses a newer catalog and stops.

**Running it:** the container runs as `PUID:PGID` (1000:1000 unless `.env`
says otherwise). Create `CONFIG_PATH` owned by that user before the first
start; otherwise the container says so and stops. The port is bound to
loopback by default; sign-in is off until you set a password under Settings
→ Security, so put a proxy or sign-in in front before anything else can
reach it (OPERATING.md, section 2).

**What is in it**

- *Library:* folders read where they are; filenames, embedded metadata and
  covers as first-class evidence; runs, issues and collected editions
  (editions opt-in) grouped and confirmed against GCD, Metron, Comic Vine
  and Open Library; file health shown as its own fact; the metadata
  workbench, cover picker and run tools behind Edit and Advanced.
- *Acquisition:* follow a run and its missing issues are searched through
  Prowlarr and fetched by SABnzbd, qBittorrent (only the wanted issues of a
  pack, the rest of the run riding along) or a direct-download site you
  enter; order of sources is yours; every download validated before import;
  replacement of damaged files without deleting the original first; what
  waits and why on Settings → System, with a bell notice after half an hour.
- *Reading:* the reader with page and panel views (panels found locally; an
  optional AI connector for hard pages, off by default), progress per
  profile, reading lists, story arcs (seeded from Metron and community
  lists, or made by hand, CBL in and out), collections, the pull list,
  Discover shelves, universal search, an A–Z rail.
- *Household:* Plex-Home-style profiles with PIN or password, age limits
  enforced on the server, readers' requests awaiting the admin, shared-device
  sign-out, notifications per profile.
- *Operations:* one JSON log line per event carrying the request id (also
  returned as `X-Request-Id`), kept in `/config/logs` across restarts and
  upgrades; `/healthz` with the build and the catalog's
  state; start-up refusals in one plain line; backups of `/config` restore
  everything but files; the container runs read-only with every capability
  dropped, under memory and pid limits.

**Proven for this release** (`docs/RELEASE_TRACK.md`, Gates 1–4): a clean
and a repeated intake of a real 2,820-file library; restarts, outages and
bad downloads in the fulfillment drill, with qBittorrent run end to end
against a real client; clean install, upgrade from a schema-28 build and
rollback in the install drill; performance on the real library on a NAS
volume; a security review of the household model; WCAG 2.2 AA audit of
every screen at four widths.

**Found and fixed while proving it** (so you do not meet them): a
qBittorrent that did not have the `comics` category, or runs in manual
torrent management, saved packs where Flipparr never looked -- the grab now
makes the category and adds torrents under automatic management; a clean
scan on a NAS volume took ten minutes for want of a kept database connection
(now under twenty seconds); a newer catalog opened by an older build came up
"healthy" with every page a 500 (now refused before a byte is touched).

**Known limitations** are listed in [OPERATING.md](OPERATING.md#7-known-limitations).
The ones worth reading before you start: GCD's anonymous tier allows roughly
25 series an hour, so a large library wants a Metron token; the whole
catalog is one answer to the browser, fine to about 10,000 files; collected
editions have weaker metadata and are never acquired automatically.
