# Performance on a real library

Measured on the owner's NAS (Synology, 4 cores, 15 GB, btrfs over bcache on
HDDs) against an isolated copy of production's catalog -- 2,820 files, 229
runs, 6,471 issues -- with the real library mounted read-only. Part of Gate 4
in [RELEASE_TRACK.md](RELEASE_TRACK.md). Written 2026-10-06. No comic is
named here; the figures are counts and times.

## What was found, and what changed

| | Before | After | What it was |
|---|---|---|---|
| Clean scan of the whole library, database on the NAS volume | 628 s | **18.6 s** | A connection per operation. On this volume a fresh connection paid ~17 ms to commit and ~16 ms to close; one kept open commits in 0.2 ms. Six operations a file. Each thread now keeps its connection (`_ClosingConnection`), and WAL mode runs with `synchronous=NORMAL`, which SQLite documents as safe from corruption. |
| Rescan, nothing changed | 1–2 s | 0.5 s | |
| Story-arcs grid (`/api/v1/reading-lists`, `/reading/lists`) | 0.7–2 s | 20–30 ms | `file_issue_links` had no index on `issue_id`; every arc item scanned the table. |
| Catalog poll while something is queued | 1.2 MB (18 MB of JSON) every 5 s, ~1.1 s each over the tunnel | **304, 0 bytes, ~10 ms** | The page re-fetched the whole catalog on every poll. It now carries an `ETag` (the build it came from, the viewer, the version of Flipparr) and asks with `If-None-Match`. |

The rest, unchanged and acceptable for the first release:

| Measure | Figure |
|---|---|
| Catalog build (the server's model of the library), cold start | 1.7 s (5 s on a cold disk cache) |
| Catalog answer, warm, gzipped | 320 ms to serialise and compress; 1.2 MB on the wire, 18 MB of JSON |
| Catalog cache | per viewer, reused while nothing it is made from has changed, 30 s at most |
| First page of a comic, cold (zip or RAR alike) | ~200 ms, ~380 KB |
| The same page again | 10 ms |
| Four pages asked for together | ~500 ms, so one comic's reader pre-fetch is serialised per archive |
| 48 covers, 8 at a time, first time / again | 2.9 s / 0.5 s (served with `Cache-Control: private, max-age=86400`) |
| Container memory | 190 MB at start, ~800 MB with the catalog built; CPU ~2% idle |
| Time to healthy after start | ~5 s |

## The next ceiling: the catalog payload

Everything the app shows comes from one `/api/v1/catalog` answer: 18 MB of
JSON for 2,820 files (1.2 MB gzipped), roughly 6 KB a file. It grows with
the library and with request history: `series` 6.6 MB (of which `issues`
3.8 MB, `fileDetails` 1.3 MB, `coverCandidates` 0.9 MB), `requests` 5.1 MB,
`files` 2.2 MB. A 10,000-file library would answer with ~65 MB of JSON and
hold several hundred megabytes of it in the server's cache.

For the first release this holds: one download of 1.2 MB on open, parsed
once, then 304s. Past about 10,000 files it will not, and the remedy is
known: move `requests`, `coverCandidates` and `fileDetails` out of the
catalog and fetch them where they are used (the Pull List, the cover
editor, the Files tab). That touches many screens and is not a Gate 4
change.

**2026-10-15, the 304s were not happening.** Production's log for 7–14 Oct
showed one `GET /api/v1/catalog` of 20.1 s and page renders of 5–20 s; on a
read-only copy of production's catalog (2,744 comics, 222 runs, 14.3 MB of
JSON) `store.catalog()` builds in 0.6 s, three runs in a row. The time was
the payload, not the build: the cache and the ETag were stamped with the
database and WAL files' size and mtime, which every write moves -- a page
turn's place, a first render's fingerprint, a page's panels -- so the tag
changed on nearly every poll and the whole 14 MB went again. The stamp is
now a catalog generation the store counts, moved only by writes to the
tables the catalog is made from (`CatalogStore.CATALOG_TABLES`; a test
traces a build and checks every table it read is listed). A page turn keeps
the tag; a rating, a scan, an import move it. The remedy above still stands
for the day the payload itself is too large.

The same log showed a first panel read costing three or four extractions
and decodes of one page (the 600 px cut, the member again for its hash,
the detector's 1200 px render, a spread's 1800 px). It is one extraction
now, every size rendered from it, and the hash is the fingerprint the store
already holds when it does. The RAR-per-page cost that made those
extractions slow is gone by another road: 2,730 of the 2,744 comics are
CBZ after the library conversion; the 12 `.cbr` left are damaged refusals
waiting on Replace.

## Smaller things seen

- At start-up the app asks for `/api/v1/reading`, `/reading/runs` and
  `/reading/lists` twice each (two components), ~200 ms each over the
  tunnel. Harmless; a shared fetch would remove them.
- Scanning is bounded by the archive tool for RAR comics (two `bsdtar` runs
  a file, ~27 ms) and by Python for zips (~10 ms a file); the first scan of
  a library on a cold disk cache is bounded by the disks.

## How to measure again

The isolated instance: a copy of production's `flipparr.db` in an empty
config folder, the library mounted read-only, run from the current image on
a loopback port; the other profiles disabled in the copy so the admin's
view answers without a PIN. The probe scripts are not in the repository
(they name the owner's comics in their output); the figures above are what
they printed.
