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

Status as of 2026-10-03, for the `Dockerfile` image.

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
import and request reconciliation, is in daily use. Open: written restart,
outage and failure-injection proofs for this image, including the
qBittorrent gates set when torrents were approved (2026-09-30): its
provider-outage behaviour, restart recovery (a torrent is never restarted as
a direct fetch) and the read-only torrents mount in clean install and upgrade.

### Gate 4 — Release candidate

Open: clean install, upgrade, backup/restore and rollback against
representative data; realistic-library performance; provider outages; the
responsive and accessibility matrix; security; operations; documentation.

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
