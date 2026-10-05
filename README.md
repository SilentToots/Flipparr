# Flipparr

Flipparr is a pre-release, self-hosted comic catalog, acquisition product and
reader: it inventories the comics you already own, matches them against
metadata providers, can search and download missing issues through Prowlarr and
SABnzbd, and reads them — full screen, right to left for manga, remembering
where you left off.

**Start here: [`docs/OPERATING.md`](docs/OPERATING.md)** — install,
configuration, authentication, backup and restore, upgrade and rollback,
troubleshooting, and known limitations.

Single issues and collected editions are both catalogued. Automatic provider
search and downloader acquisition are for single issues only; collected editions
are imported from files you already own, and are an opt-in feature because their
metadata and file availability are markedly weaker. Release gates and the
supported first-release target are in
[`docs/RELEASE_TRACK.md`](docs/RELEASE_TRACK.md).

The `v1-prototype/` directory name is repository continuity only. It is the
production frontend.

## How it works

The catalog stores its local database at `.data/flipparr.db` by default. A library inventory records roots, scan runs, file size/mtime fingerprints, enrichment results, series groups, and resolved review items. Subsequent scans only enrich files whose fingerprint changed; deleted files are removed from the active inventory without deleting the source file.

Initial setup is progressive. The first pass reads only local filenames, embedded metadata, covers, and archive health so a large library becomes visible without consuming provider limits. Flipparr then creates one durable enrichment job per canonical series—not per comic file—and processes those jobs serially in the background. Provider cooldowns, HTTP `Retry-After` values, attempts, and ambiguous matches survive restarts in SQLite. The library banner identifies a provider-requested pause, shows the automatic retry time, and points users to optional Metron or Comic Vine setup instead of looking stuck. Successful remote responses are cached on disk under the configured database directory and reused across restarts, with a longer lifetime for stable identity lookups than for issue lists. Files awaiting background metadata are not counted as damaged or shown as manual fixes.

## Acquisition services

Prowlarr and SABnzbd (and optionally qBittorrent, for torrents) are configured under **Settings → Acquisition services**. Enter the service URL and use **Test connection** before saving. The Mac development server needs host addresses reachable from the Mac. A container on the same Docker network as those services can reach them by name (for example `http://prowlarr:9696` and `http://sabnzbd:8080`) without publishing either to the LAN.

API keys are stored only in `.data/acquisition-services.json`, which is excluded from source control and written with owner-only permissions. They are never returned to the browser after saving. The equivalent environment variables are `PROWLARR_URL`, `PROWLARR_API_KEY`, `SABNZBD_URL`, `SABNZBD_API_KEY`, and the optional `SABNZBD_CATEGORY`.

Start the API from the repository root:

```bash
python3 app.py
```

Start the interface in another terminal:

```bash
cd v1-prototype
npm run dev
```

Open <http://127.0.0.1:4173>. The Vite development server proxies `/api` to the local service on port 8787. Use **Add comics** to select a folder through Finder or enter an absolute path. The inventory remains read-only.

## Running it with Docker Compose

Install with `compose.yaml` as described in [`docs/OPERATING.md`](docs/OPERATING.md): copy `.env.example` to `.env`, set your own paths and user, create the config folder owned by that user, and `docker compose up -d`. It pulls `ghcr.io/silenttoots/flipparr`. The container serves the interface and the API from one process, keeps its state under `/config`, reads and imports into your comics at `/comics`, and reads SABnzbd's completed folder, removing a download's folder once its comic is verified in the library.

The checked-in Compose example runs as an unprivileged media user, keeps state in its config directory, mounts your comics directory, attaches to a reverse-proxy network, uses a loopback-only host port, drops privilege escalation, and limits container logs.

Flipparr retrieves a selected NZB from Prowlarr through the private configured service URL, validates the bounded XML payload, and uploads the NZB file to SABnzbd under a clean release name. Prowlarr download URLs and API keys are never handed to SABnzbd. A successful SAB queue response is only the start of the request lifecycle, not completion.

The completed-download importer follows SABnzbd by its stable queue ID, waits for a successful history result, and validates the selected comic against the requested series and issue. It copies into the existing series folder when one is known, otherwise using `Publisher/Series (Year)`, and names a new issue `Series (Year) #001 - Issue Title.cbz`. The copy is written to a hidden partial file, flushed, size- and SHA-256-verified, structurally checked, and atomically promoted. Imports stop if they would cross the configurable minimum-free-space threshold. Name conflicts stop for review. Flipparr marks the request fulfilled and rescans the library only after this verified import succeeds, and only then removes the one download folder SABnzbd reported for that job; a refused download is kept as evidence for a week. The library mount is written only by this verified import path and by a replacement moving the original aside.

Damaged-file replacement uses the same durable acquisition jobs, but it does not count the damaged copy as satisfying those jobs. A replacement can start only after Flipparr has mapped the file to specific issues; collected-volume replacement therefore requests its verified issue contents rather than guessing from a filename. The existing comic remains active until every required download is validated and imported. Flipparr then moves it into `.flipparr/quarantine/<replacement-id>/` under the library root and completes the replacement request. Failed validation leaves the original untouched, and a failed catalog swap restores it automatically while retaining the rejected copy for inspection.

The same naming policy is intended to power a future **Organize library** function. Existing files will be handled separately through an explicit preview of old path → proposed path, conflict checks, and an undo manifest; intake does not silently reorganize a user's current library.

Docker runtime state, `.env`, the SQLite catalog, and locally stored API keys are excluded from Git. Before committing, verify `git status --ignored` shows no credential or comic-library files staged.

What it does today:

- Persistent SQLite catalog with schema versioning.
- Canonical series runs, normalized aliases, and per-file identity assignments with source evidence.
- First-class issue and edition records with direct-file ownership, collection ownership, and source-attributed coverage claims.
- Verified GCD run IDs and persistent provider issue lists, with cover variants collapsed to one canonical issue number.
- A user-confirmed **Find series run** workflow for collection-only libraries, ranked by title, start year, publisher metadata, and overlap with issue numbers already satisfied by collected editions.
- First-class series families that group publication runs for aggregate coverage without merging their canonical issue, edition, file, alias, or provider identities.
- Persisted edition types for collected volumes, omnibuses, compendiums, deluxe editions, hardcovers, and graphic novels, kept separate from single issues.
- A Plex-style file metadata workbench with alternate-match selection, manual field corrections, persistent field locks, reset-to-provider behavior, and an audit history.
- Decision-oriented match cards with covers, bibliographic metadata, issue/collection associations, evidence, and a field-by-field selection impact preview.
- A Plex-style cover picker that can use art embedded in the comic, choose an available metadata-provider image, upload custom artwork, or return to automatic selection.
- A collection-contents workbench that shows source-attributed issue claims and stores manual inclusions or exclusions without destroying provider evidence.
- Background scan runs with progress and failure state.
- Incremental rescans based on size and nanosecond modification time.
- Real series, file, cover, health, and metadata-inbox data in the interface.
- Durable review resolutions tied to a file fingerprint, so changed files return to review.
- Local-cover priority, external-cover fallback, and a non-broken placeholder when every source fails.
- Exact issue publication dates when available, with released, upcoming, and release-date-unknown acquisition states.
- Persistent monitoring preferences for collections and standalone runs.
- Catalog-backed acquisition requests whose target issue sets expand automatically after verified issue-list updates.
- A real Requests queue showing released gaps, upcoming issues, release-metadata exceptions, covered targets, and automatic fulfillment.
- Configurable metadata providers: anonymous GCD and Open Library work without setup, while users can add, test, disable, reprioritize, replace, or remove locally stored Metron tokens and Comic Vine API keys.
- Configurable Prowlarr and SABnzbd connections, interactive release search for individual wanted issues, credential-free candidate comparison, explicit server-side handoff of the selected NZB to SABnzbd, completion tracking, and verified non-destructive import into the comic library.
- A provider-backed issue refresh path that retains GCD structure and fills missing issue titles, release dates, covers, and cross-provider identities from enabled Metron and Comic Vine accounts without overwriting locked local corrections.

Canonical aliases are visible in each series drawer. A manually added alias is marked confirmed and is used to resolve later files to the same internal series ID. Equivalent punctuation and joiner forms—such as `Locke & Key` and `Locke and Key`—share a normalized key, while full collected-edition titles remain stored as attributed aliases.

Series drawers now separate Overview, Issues, Editions, Files, and Aliases. Each series exposes direct single-issue files and collected editions separately; individual edition cards retain their volume number and classified edition type. Explicit collection claims can create canonical collection-owned issues. Inferred coverage whose series numbering does not match a confirmed alias remains attached to the edition as unresolved evidence and does not satisfy canonical ownership. A run with at least one verified GCD single-issue match can import its provider issue list from the Overview tab. Collection-only series can use **Find series run** to compare plausible GCD publication runs, inspect issue totals and overlap, open the source record, and explicitly confirm the correct run. Provider IDs, confirmation source, and sync provenance are retained in the catalog schema.

The Library can switch between **Families** and **Runs**. A family is a non-destructive grouping whose ownership total is the sum of its run-scoped issues, so identically numbered issues in different runs remain distinct. The Grouping tab in every run drawer can create a family, add or move another run, move the current run to another family, or remove any member back to an independent entry. Empty families are removed automatically. This relationship layer is intentionally separate from the legacy destructive merge operation and provides the parent model for future ordered story arcs and user-curated collections.

Each file row now provides **Contents** for collected editions alongside **Cover**, **Fix match**, and **Edit**. Fix Match compares each retained candidate's cover availability, record type, publisher, year, format, ISBNs, creators, page count, verification status, issue associations, collection coverage, and exact field changes before selection. The selected candidate itself is persisted in schema V9, so its coverage and source relationships survive rescans instead of retaining associations from the previous recommendation. Edit can correct the canonical series, display title, subtitle, issue or volume number, edition type, publisher, publication year, ISBN/GTIN, and format. Local corrections win over refreshed provider evidence without modifying the source comic. **Restore provider metadata** removes the local override and reconciles the original evidence again.

The collection-contents workbench displays each provider or file-derived issue claim with its source, confidence, evidence, canonical-series resolution, and ownership effect. Explicit `collects` statements in EPUB package descriptions are ingested as file-confirmed claims. Users can add comma-separated issues or numeric ranges, exclude an incorrect provider claim, restore an excluded claim, or reset all local content corrections. Local additions and exclusions survive rescans; original provider claims remain stored as evidence.

The cover picker presents every retained cover source for that file: an image readable from the comic archive, catalog art supplied by a matched metadata service, and a custom upload. Uploaded JPEG, PNG, WebP, GIF, or HEIC/HEIF art is normalized to an app-managed JPEG no larger than 600 pixels on its longest edge. Selecting or uploading artwork stores only a local preference; it never edits the original comic. **Use automatic cover** returns to the default file-first, provider-second priority.

The UI still reports raw catalog ownership separately from acquisition state. Exact dates returned by the provider classify unowned issues as released gaps or upcoming releases; current-year or undated issues remain visible as release-metadata exceptions instead of being falsely queued as missing. **Acquire missing** persists monitoring and the canonical issue target set, while the Requests page recalculates covered, wanted, upcoming, and unknown counts from the current library. Schema V15 adds one durable acquisition job per released, unowned target, groups those jobs by publication run in Requests, and automatically closes them when library coverage appears. Upcoming and date-unknown targets remain monitored without becoming search jobs. Requests become active immediately; there is no requester/admin approval step. Individual jobs can search Prowlarr interactively, send an explicitly selected Usenet result to SABnzbd, show download/import progress, and finish only after a verified library copy exists. Automatic grabbing remains disabled until release matching has been QA'd against representative downloads.

Canonical identity endpoints used by the interface:

```text
POST /api/v1/series/{series_id}/aliases
POST /api/v1/families
POST /api/v1/series/{series_id}/family
GET  /api/v1/series/{series_id}/issue-runs
GET  /api/v1/providers
POST /api/v1/providers/{provider_id}
POST /api/v1/providers/{provider_id}/test
POST /api/v1/series/{series_id}/issue-run
POST /api/v1/series/{series_id}/issues/sync
POST /api/v1/series/{series_id}/monitoring
POST /api/v1/collections/{collection_id}/monitoring
POST /api/v1/requests
POST /api/v1/acquisition-jobs/{job_id}/status
POST /api/v1/acquisition-jobs/{job_id}/search
POST /api/v1/acquisition-jobs/{job_id}/grab
POST /api/v1/series/merge
GET  /api/v1/files/{file_id}
POST /api/v1/files/{file_id}/metadata
POST /api/v1/files/{file_id}/match
POST /api/v1/files/{file_id}/metadata/reset
POST /api/v1/files/{file_id}/cover
POST /api/v1/files/{file_id}/cover/upload
GET  /api/v1/files/{file_id}/cover/image
POST /api/v1/files/{file_id}/contents
POST /api/v1/files/{file_id}/contents/reset
```

## Historical metadata scanner

The repository began with a read-only local metadata experiment that scans comic
filenames and returns candidates. This historical path does not define the
current Flipparr product architecture or release readiness. It does not rename,
move, edit, or delete comic files.

## Run

Requires Python 3.10 or newer and no third-party packages.

```bash
python3 app.py
```

Open <http://127.0.0.1:8787>. On macOS, use **Choose Folder…** to select a folder in Finder; alternatively, enter an absolute folder path. Then select **Scan folder**.

After scanning, select **Analyze all files** for a readable batch report showing recommended matches, covers, ISBNs, confidence reasons, and any issue coverage found in Open Library edition notes or conservatively inferred from GCD records.

For ZIP-based comics and ebooks (`.cbz` and `.epub`), the results page prefers the cover already inside the file. It extracts the declared cover or first plausible image page and creates a cached JPEG thumbnail no larger than 600 pixels on its longest edge. The original archive and page image are never modified. External catalog art is used only when a local cover is unavailable.

Each file also receives a lightweight structural check before lookup. Empty files, empty or unreadable archives, mislabeled archive formats, invalid PDFs, and CBZ files without supported image pages are returned in `file_health` and shown as prominent warnings in the scan and results views. These checks do not alter the source files and intentionally avoid reading every page solely to verify its checksum.

The scanner recognizes `.cbz`, `.cbr`, `.pdf`, `.epub`, `.cb7`, and `.cbt`. It extracts likely titles, volume numbers, issue numbers, years, formats, and ISBNs from filenames. **Lookup JSON** also reads EPUB package metadata and ComicInfo.xml when present, uses the embedded title or ISBN to improve external matching, and queries Open Library plus the anonymous Grand Comics Database API. Google Books is optional and disabled by default.

URL-encoded release filenames are decoded for display and parsing. Padded standalone numbers such as `001` and `0006` are treated as issue numbers when they appear before a publication year. Confirmed single issues bypass book catalogs and GCD collected-edition matching, preventing a numbered issue from being returned as a trade volume.

Single-issue recommendations include a separate `identity_confidence` assessment. It cross-checks the filename, ComicInfo.xml, and GCD series, issue number, publisher, year, and story title where available. Agreements, conflicts, and limitations are returned separately. `match_score` remains an internal candidate-ranking value and is not a percentage. Core creators come only from GCD comic-story sequences; cover credits are separated and promotional or unknown credits are filtered out.

Long alphabetic filename tokens are segmented with a local dictionary and reported with a verification warning—for example, `strangetalentoflutherstrode` becomes `strange talent of luther strode`. Folder analysis uses four bounded workers, shares remote-response caches across files, coalesces duplicate requests, and limits slow edition-detail lookups. A failure on one file no longer stops the rest of the batch.

Successful external responses are cached locally for seven days so restarting the scanner does not repeatedly consume anonymous API limits. When GCD is unavailable or rate-limited, a single issue with matching filename and ComicInfo.xml series/number remains a medium-confidence recommendation instead of disappearing. This fallback is explicitly marked as not externally confirmed.

GCD is used specifically for collected-edition records. When a requested volume matches a GCD collection descriptor, the result can include its cover, ISBN, page count, publisher, creators, and named stories. Issue coverage is returned only when the number of comic stories in that collection agrees with the complete issue list of a same-named GCD limited series. That relationship is marked `inferred`; it is not presented as an explicit GCD reprint link.

## API

Scan a folder:

```text
GET /api/scan?folder=/absolute/path/to/comics&recursive=1
```

Enrich one supported file:

```text
GET /api/enrich?path=/absolute/path/to/comic.cbz
```

Enrich every supported file in a folder:

```text
GET /api/batch?folder=/absolute/path/to/comics&recursive=1
```

## Current limitations

- Filename parsing is heuristic and intentionally reports ambiguity rather than silently choosing a match.
- Open Library and GCD require internet access and may return incomplete or conflicting records. Anonymous GCD access is rate limited, so responses are cached on disk under `/config` and reused across restarts, and requests are paced per provider.
- Google Books is disabled by default. To opt in, create a Google Books API key and start the app with `GOOGLE_BOOKS_API_KEY=your_key python3 app.py`.
- The current GCD API exposes edition and story data but not a complete explicit collected-edition-to-source-issue relationship. Inferred ranges remain clearly labeled until the forthcoming API reprint support is available.
- GCD issue-list sync can use a verified owned-issue anchor or a run explicitly confirmed through **Find series run**. Multi-run franchises still require separate canonical run relationships. Exact issue dates are retained when returned, but provider gaps and rate limits can leave some issues in an explicit release-date-unknown state.
- Embedded metadata may be incomplete or incorrect, so it is returned with provenance and does not silently overwrite the filename-derived record.
- Local cover extraction currently supports ZIP-based comic formats. CBR and PDF cover rendering remain future adapters.
- Results are candidates with source provenance, not authoritative matches.

## Tests

```bash
python3 -m unittest -v
```

## Licence

Flipparr is free software under the GNU General Public License, version 3
([`LICENSE`](LICENSE)). Third-party software, fonts, icons and data sources
are listed with their licences in [`NOTICE`](NOTICE).

Flipparr organises and reads comics you have the right to use. It ships no
comics, catalogue data or download sources; every indexer, downloader and site
it talks to is one you configure.

## Privacy

What Flipparr keeps and what leaves your server, service by service, is in
[`docs/PRIVACY.md`](docs/PRIVACY.md) and in the app under **Settings → About**.
Nothing is sent to Flipparr's authors.
