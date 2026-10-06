# Flipparr release track

Status: pre-release, on a production track. Rewritten 2026-10-03 for the one
codebase Flipparr has.

## Decision

Flipparr is built for public distribution: a self-hosted, single-household
Docker application anyone may install for a comic library on a NAS-connected
host, with its data on a local Docker volume and one or more mounted library
roots. One admin; optional reader profiles.

Public distribution is a constraint from the start. Release artifacts contain
only code, assets, dependencies and data Flipparr may redistribute, with the
required notices (see `NOTICE` and Settings → About). Provider dumps, user
credentials and download sources are never release assets; the operator
configures each source they use.

**History.** A from-scratch catalog rewrite was tried between 2026-08-29 and
2026-09-03 and abandoned in favour of carrying the original application
forward; it was removed from the repository on 2026-10-03. Its gate
checkpoints were proven on that rewrite, not on this image, so they do not
count below. The findings from it that apply here are kept under
[Carried-forward findings](#carried-forward-findings).

## Initial supported release

- One instance per household, Docker on a single host, local persistent data.
- One or more mounted library roots, read where they are.
- One publication run groups Issues and collected editions with independent
  ownership; neither format fulfils or depends on the other.
- **Collected editions are opt-in (default off).** When on, they are local
  intake, grouping, metadata, health and manual import/replacement content
  with a lower-reliability notice. Flipparr never promises missing-volume
  catalogues or automatic Volume acquisition (decision 2026-09-02: two bounded
  live checks found no credible source).
- Issues are the only automatic monitoring, wanted, search and download
  target: Prowlarr search; SABnzbd, qBittorrent or a direct-download site the
  operator enters; validated import, replacement and recovery.
- Provider outages degrade enrichment, never local library availability.
- Desktop and phone layouts for every core workflow.

**Household reader profiles (amended 2026-09-25).** Plex-Home style: each
profile keeps its own history, place and ratings, chosen on a shared device
("Who's reading?", optional PIN) or signed into with its own password. Every
route has an access class and is the admin's unless listed
(`access_policy.py`). Readers' requests wait for the admin; content limits
hide runs above a profile's age rating server-side. Multi-tenant public
hosting is out of scope.

Public Internet exposure without a reverse proxy and authentication,
distributed workers, clustering, PostgreSQL and Redis are not
initial-release requirements.

## Architecture rules for release work

- **End-to-end progress:** follow the prioritization principles in
  [AGENTS.md](../AGENTS.md#end-to-end-progress-and-prioritization).
- **Outcome-first UX:** every capability is designed as the user's whole task,
  from trigger to success or recovery, in product language, never leaving a
  status without an explanation and a next action. Styling follows
  `v1-prototype/DESIGN_SYSTEM.md`.
- **Idempotent boundaries:** scan, enrichment, request, download
  reconciliation and import can be repeated after interruption without
  duplicating entities or losing state.
- **Durable background work:** work survives restarts and records state,
  attempts and actionable failure details.
- **Versioned change:** schema changes are numbered (`SCHEMA_VERSION` in
  `catalog_store.py`) with tested forward migration; rollback is
  backup/restore (see `docs/RELEASE.md`).
- **Safe files:** an original is never deleted or replaced until the new file
  is validated and the destination verified.
- **Targeted hardening in place**, not a rewrite: the HTTP layer onto a
  production server, the `app.py` / `catalog_store.py` split, an era guard at
  match time, a "merge runs" action.

## Release definition of done

A capability may be merged before every gate passes, but is not
release-ready until its gates pass.

### Catalog and data integrity

- A clean intake and a repeated intake of the real library produce the same
  runs, assignments and separate Issue / edition ownership.
- A file cannot be assigned twice; duplicate runs fail closed and are
  user-fixable.
- Owning one format never fulfils, suppresses or creates requests for the
  other.
- Provider refreshes never overwrite corrections or move files silently.
- Foreign-key and integrity checks pass.

### Workflow reliability

- Scan, metadata, search, download, validation, import, replacement and retry
  resume safely after a container restart.
- Provider rate limits and outages, Prowlarr / SABnzbd / qBittorrent failures,
  malformed archives, permission errors and full disks have tested, visible
  recovery paths.
- Every queued or failed item shows its state, history, the next automatic
  action and any action the user actually has to take.

### Installation and operations

- A documented clean install works on a NAS-style setup from release
  artifacts alone (`docs/OPERATING.md`).
- Paths, permissions, UID/GID, secrets and mounts are checked at setup.
- Backup, restore, upgrade from the previous release and rollback are
  exercised against representative data.
- Health endpoint, structured logs with request ids, support-safe
  diagnostics.
- The image is built and tested in CI from pinned bases, runs as a non-root
  user and excludes development files and secrets.

### Security, privacy, UX and supportability

- No secret reaches source, images, responses, logs or exception text;
  rotation and exposure response are documented. A disclosure is a release
  blocker.
- The applicable OWASP ASVS controls are verified for the supported
  trusted-network deployment.
- What leaves the instance, and to whom, is stated in Settings → About and
  `docs/PRIVACY.md`; optional sharing (every page to a vision model, the
  community lists' GitHub fetch, visitor addresses in the log) is off until
  turned on.
- Core workflows are keyboard operable with visible focus, labels, contrast
  and reflow at the documented breakpoints, and respect reduced motion.
- Setup, providers, import, backup/restore, troubleshooting and known
  limitations are documented; a versioned image, release notes, migration and
  rollback notes are published.

## Delivery gates

Status as of 2026-10-06: Gates 1–4 passed; `v0.1.0` published as `ghcr.io/silenttoots/flipparr:0.1.0` and running in production.

### Gate 1 — Release foundation

In place: CI on every push (`.github/workflows/v1-forward.yml`) running the
backend suites in two orders, the web tests, the build and the secret check;
pinned base images; non-root user; health check; structured logs with request
ids; numbered schema migrations.

**HTTP layer (2026-10-04):** served by Waitress instead of Python's
`http.server`, which Python documents as not for production. Measured with
300 stalled connections: `http.server` grew to 300 threads and dropped none;
Waitress stayed at 18 threads, answered a real request in 0.02 s and dropped
all 300 after its 30 s timeout (Cheroot, also evaluated, bounded its threads
but let the stalls starve real requests). The routes run unchanged through a
WSGI adapter (`app._WSGIHandler`) and the 152 HTTP contract tests pass on it;
`TransportTests` keep the stalled-client, keep-alive, large-body and
server-header behaviour in CI. SIGTERM now lets running requests finish.

**Job inspection (2026-10-05):** Settings → System and
`/api/v1/system/status` show each background worker's state, last activity and
last problem, the work waiting (downloads, wanted issues, metadata, provider
cooldowns, the last scan) and the recent warnings and errors; a supervisor
restarts a worker that dies (five times an hour at most) and every crash is
logged. Two loops that could end or fail silently -- metadata on one
exception, imports swallowing every error -- were fixed. A diagnostics file,
scrubbed of credentials and service addresses, is the support artefact.
HEAD requests, answered 501 before, now answer as GET without a body.

Gate 1's listed items are in place; its proofs for this image are the CI run
and the NAS checks recorded in the commits.

### Gate 2 — Catalog correctness

The original application's intake, grouping, filename parsing and naming are
the baseline. The era guard and "merge runs" are in place and tested.

**Measured (2026-10-05):** `tools/intake_checkpoint.py` on the owner's real
library -- 2,821 files, local evidence only (no provider calls), read-only, in
a throwaway container, two clean intakes into separate databases and a rescan,
against a copy of production's catalog as a reference (not as truth). Outputs
that name the owner's comics stay outside the repository.

| Stage | Baseline | After the fixes |
|---|---|---|
| Inventory: files healthy | 100% | 100% |
| Discovery: enough local evidence to identify | 98.3% | 100% |
| Grouping: numbered issue or volume | 97.6% | 99.9% |
| Grouping: runs merging several eras | 11 | 0 |
| Grouping: runs with exactly production's files | 181 of 218 | 206 of 229 |
| Ownership: issues credited | 2,493 | 2,649 |
| Intervention: items needing a decision | 0 | 0 |
| Two clean intakes / rescan, files that differ | 0 / 0 | 0 / 0 |
| Owner's spot-check of 40 random files | -- | 40 of 40 correct |

Fixed: a run's year stated by its folder, a `V2011`, or a year before the
number now separates relaunches (exactly; ComicInfo's Volume guides with the
catalog's slack, since some taggers write the issue's year there), and
`v05`-style manga volumes are numbered. Opening production's catalog with the
new build moves no file (checked on a copy before deploying).

Remaining, user-fixable and none wrong-era: 23 runs are part of a larger run
in production -- leading-article and embedded-title variants ("The X" beside
"X", "X (2020-)") and spin-offs, which "merge runs" repairs. A file whose
number appears only in its ComicInfo comes out unnumbered (none in this
library).

### Gate 3 — Fulfillment correctness

Prowlarr through SABnzbd, qBittorrent or a direct download, through validated
import and request reconciliation, is in daily use.

**Proven (2026-10-05):** [FULFILLMENT_PROOFS.md](FULFILLMENT_PROOFS.md) gives
each restart, outage and bad-download scenario, what Flipparr does, and the
test or drill scenario that proves it -- including the qBittorrent gates set
on 2026-09-30 (outage behaviour, a torrent never restarted as a direct
fetch, the read-only torrents mount). A survey of the pipeline found faults
behind those promises, fixed with a failing-first test each:

- an import cut off by a restart left a half-written copy that the next
  scan catalogued, and a direct download stuck at "importing" for good;
- a download client that dropped the line or answered half a reply failed
  the download for good (a torrent was then deleted by the hourly sweep),
  and SABnzbd refusing the API key gave every download up as lost;
- a Prowlarr outage counted against every issue, pushing its next search out
  by up to a day, and a search by hand during one parked the issue for good;
- outages were told to nobody: Settings -> System now names a silent service
  at once and every admin's bell does after 30 minutes; a refused
  qBittorrent password is retried under its ban threshold;
- a password-protected torrent or direct download reached the library;
- a missing torrents mount was checked nowhere.

**Reviewed the same day.** A second look at that work found five flaws in
it, fixed in 924bf7a3: one issue Prowlarr errors on held up every search
behind it; the RAR/7-Zip password check read only the archive tool's last
line and its test had invented the message; the qBittorrent sign-in retry
was still banned, an hour later (qBittorrent's failure count never runs
down); a refused qBittorrent password was told to nobody; trouble was worded
wrongly or left listed.

`tools/fulfillment_drill.py` runs the real server beside fake SABnzbd and
Prowlarr, with a SIGKILL mid-download and mid-search; it runs in CI. In the
release image on the NAS (build 924bf7a3): 7 of 7 scenarios held -- against
1 of the first 6 for the build before Gate 3 (fcf15e1c), and 6 of 7 for the
first round (24c7809e); 1,136 tests OK; opening production's catalog moved
no file.

Not covered end to end: qBittorrent itself (its behaviour is proven by unit
tests against a scripted client, not a running one); an encrypted RAR (the
check is proven with the real archive tool on an encrypted zip, as nothing
free writes an encrypted RAR); and a clean install and upgrade exercising
the torrents mount, which moves to Gate 4 with the rest of install and
upgrade.

### Gate 4 — Release candidate

**Install, upgrade, backup/restore and rollback (2026-10-05):** proven by
`tools/install_drill.py` with real containers, in CI and on the NAS (5 of 5
against the build of 2026-09-07, schema 28, upgraded to this one, schema 64);
see [INSTALL_PROOFS.md](INSTALL_PROOFS.md). What that work fixed first: an
older image opening a newer catalog, and a config folder the container could
not write, both came up "healthy" with every page a 500 (the schema check ran
after the tables were touched, and `/healthz` never looked at the catalog);
only the move to schema 49 kept a pre-upgrade copy; nothing was published, so
the documented upgrade and rollback could not be followed. Now: the start-up
checks refuse with one plain line and a non-zero exit; a newer catalog is
refused before a byte is touched; every schema upgrade keeps a copy (the last
three); `/healthz` reports the catalog and goes 503 without it; CI publishes
`ghcr.io/silenttoots/flipparr` on release tags (and `:edge` from the branch)
and compose pulls it; the owner's proxy network, time zone and group are out
of the shipped compose file.

**Performance on the real library (2026-10-06):** measured on an isolated
copy of production's catalog with the library mounted read-only
([PERFORMANCE.md](PERFORMANCE.md)). Three findings, fixed: a clean scan of
the 2,820-file library took 628 s with the database on the NAS volume
because every operation opened and closed its own SQLite connection (18.6 s
with a connection kept per thread and `synchronous=NORMAL` under WAL); the
story-arcs grid took up to 2 s for want of an index on `file_issue_links`
(20–30 ms); and a page polled the whole 18 MB catalog every 5 s while any
request was queued (now an `ETag` and a 304). The catalog payload's size --
about 6 KB a file, held whole in the server's cache -- is the next ceiling,
past roughly 10,000 files, and is recorded as such.

**Security (2026-10-06):** three independent reviews of the code against the
household model (identity and access; files, archives and fetching; the HTTP
layer and the container), every finding verified in the code and fixed with
a test -- DNS rebinding in household mode, sessions surviving a sign-in
tightening, settings changed by a session alone, a guess burst past the
throttle, saved secrets sent to a requested address, credentials following
redirects, the rating gate missed by the avatar route, unbounded archive,
ebook, pack and image decoding, images cached across profiles, secrets in
logs, missing response headers, container capabilities -- and the model, its
limits and the proxy's part written up in [SECURITY.md](SECURITY.md).
Dependencies audited (pip-audit, npm audit) clean. Deployed as 69462e94.

**Responsive and accessibility (2026-10-06):** every screen state the
visual harness knows (45, with the three Library filter states and the
reader's finish drawer repaired to the current UI) at 1440, 768, 375 and
320px through axe-core's WCAG 2.2 A/AA rules and four probes -- reflow,
24px targets, keyboard focus, reduced motion (`npm run a11y:audit`;
`v1-prototype/tests/a11y/README.md`). Result: no horizontal scroll at
320px on any screen, no motion under `prefers-reduced-motion`, every
keyboard stop indicated, no focus trap; one serious finding (the Read
button's issue number at 3.67:1, faded with `opacity`) and two kinds of
small target (13px native checkboxes, the 20px *See all* button) fixed in
the shared rules and re-audited clean. The rules it holds are written into
`DESIGN_SYSTEM.md`, "Accessibility". Not in CI: it needs a library, as the
visual harness does. Unmeasured: a screen reader's reading of each page
(axe checks names, roles and landmarks, not the experience); the reader's
page view under 200% zoom beyond what 320px stands in for.

**qBittorrent end to end (2026-10-06):** the fulfillment drill gained a
fake qBittorrent and five torrent scenarios -- a run mostly missing takes a
pack, only the wanted issues' files are fetched and the rest of the run rides
along; a hard kill mid-download; the pack lands and every issue is imported
while the torrent seeds on; the client stops it and Flipparr removes it with
its files; a refused password is held and named -- and ran them against a
**real qBittorrent 5.2.3** (a throwaway container on the NAS, no trackers
or peers, a web seed the drill serves) as well as the fake in CI. The real
client found two faults the fake had hidden, both putting the pack where
Flipparr never looked: the `comics` category was only ever created by
Settings → Test, and a torrent follows its category's folder only under
automatic management, which a fresh client does not default to. The grab
now creates the category and adds under `autoTMM`; the fake behaves as the
client does; 5 of 5 held on the re-run
([FULFILLMENT_PROOFS.md](FULFILLMENT_PROOFS.md), "qBittorrent, end to
end"; evidence in `~/flipparr-intake/drill-qbt-real-20261006/` on the NAS).
Still unmeasured: a torrent from a real indexer through a real swarm, which
needs the owner's client and a release he wants.

**Operations and documentation (2026-10-06):** checked against the
definition of done above. Start-up refuses a config folder it cannot write
and a catalog it cannot open, naming the user it runs as; the first-run
folder check, Settings → Test and the per-service `_folder` trouble cover
the library, SABnzbd and torrents mounts; every log line carries the
request id, returned as `X-Request-Id`; `/healthz` reports build and
catalog; diagnostics are support-safe. `README.md` rewritten as the public
front page (what it is, what it needs, how to run and develop it; the
pre-product scanner prose removed); `docs/RELEASE.md` carries the 0.1.0
release notes with the schema, rollback and found-and-fixed lines;
`OPERATING.md` gained the torrent-management note and its troubleshooting
entry. Setup, providers, import, backup/restore, upgrade/rollback,
troubleshooting, known limitations, privacy and security each have their
document.

**Gate 4 is complete, and 0.1.0 is released (2026-10-06).** The tag's
first CI run, on d0f040d6, failed the drill's seeding scenario on the fresh
runner and published nothing: the hourly sweeps' clock started at 0.0, and
`time.monotonic()` counts from boot, so on a machine up less than an hour
(a CI runner; a NAS just restarted) the first seeded-torrent and
kept-download sweep waited an hour. Fixed with a test (d3067de3), the tag
moved to it, and CI published `ghcr.io/silenttoots/flipparr:0.1.0` and
`:latest` (digest `sha256:48e40e49…`, stamped d3067de3). The published
image, pulled on the NAS once the owner made the package and the repository
public: smoke import ok, SQLite 3.53.4, install drill 5 of 5 against it
(evidence `~/flipparr-intake/install-drill-0.1.0/`), and production now runs
that exact image (`/healthz`: `0.1.0`, build d3067de3, catalog ok).

## Carried-forward findings

Learned on the abandoned rewrite, and true of this image:

- **SQLite's WAL-reset bug.** SQLite 3.7.0 through 3.51.2 can corrupt a
  database in WAL mode when two connections write or checkpoint at the same
  instant; the fix is in 3.51.3 (backported to 3.44.6 and 3.50.7). The image's
  Debian Bookworm SQLite is 3.40.1 and `catalog_store.py` opens the catalog in
  WAL mode with several threads writing. **Resolved 2026-10-04:** the image
  builds SQLite 3.53.4 from sqlite.org's checksum-pinned source (`Dockerfile`,
  stage `sqlite-build`) and refuses to build on anything older than 3.51.3;
  `catalog_store.journal_mode_for` keeps WAL only on a fixed SQLite and uses
  the rollback journal elsewhere (an old Python run from source), and the
  startup log records which (`sqlite_runtime`). When SQLite is updated, change
  the version, URL and both hashes together.
- **Docker bridge networking** presents the bridge gateway, not loopback, as
  the client address; trusting it is an explicit setting
  (`FLIPPARR_TRUSTED_PROXIES`, `docs/OPERATING.md`), never a default.
- **No bundled reference data.** The full Grand Comics Database download is
  about 1.7 GiB compressed; it is never shipped or silently downloaded.
- **Indexer coverage is measured before a source is built.** The torrent
  client was built only after a cohort showed whole-run packs had no reliable
  source; Volume acquisition was dropped when two bounded live checks found
  none.
- **Direct downloads are a site the operator enters** (2026-10-03): no site
  is built in, named or defaulted (`docs/DIRECT_DOWNLOADS.md`).

## Primary references

- Python on `http.server`: https://docs.python.org/3.13/library/http.server.html
- SQLite WAL-reset bug: https://sqlite.org/wal.html#walresetbug
- Docker build best practices: https://docs.docker.com/build/building/best-practices/
- GitHub Actions CI: https://docs.github.com/en/actions/get-started/continuous-integration
- OWASP ASVS: https://owasp.org/www-project-application-security-verification-standard/
