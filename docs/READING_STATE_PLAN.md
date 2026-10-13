# Reading state, panels and covers belong to the comic, not the file

Plan, 2026-10-12. Status: **built** (steps 1-6, 2026-10-12, overnight); steps
2a and 2b in production, steps 3-6 committed and awaiting the owner's review
and deploy; 2c (stop writing the file's row) waits until 2b has run a while.

| Step | Commits | State |
|---|---|---|
| 1 Page fingerprints | 65a006b, 7f7a00b | production (with 2a) |
| 2a Reading per comic, written beside the file's row | 7d306d6 | production; migration rehearsed on production's database (46 -> 46, finished per run identical) |
| 2b Readers read the comic | 1918ac5, bfea825, c024ae7 | production |
| 3 Panels per comic page | a1b1972 | committed |
| 4 Covers and backdrops | 588d53a | committed; schema 72 rehearsed on production's database |
| 5 Merges, rebuilds, re-matches | 00c97a7 | committed |
| 6 Convert existing CBR to CBZ (opt-in job) | d8b1c9b, see below | committed; not run on the library |
| 2c Stop writing reading_progress | -- | later |

Before the first library run (2026-10-13), the conversion was made a
non-change: the pages are the same bytes under the same names, so what was
kept with the RAR's signature (panels, places, fingerprints, a backdrop, the
server's download records) moves to the CBZ's signature in the same write
that repoints the file, and `cbz_conversions` records the file and both
signatures (schema 73) so a device holding the RAR is told its copy is still
good. Without it, every automatic and paid panel reading on a converted comic
would have read as stale. The job's scan guard now ignores a scan cut off by
a restart, as the automatic scan does (production had one "scanning" since
2026-09-07).

Not yet done, and why: the comic page panels live in `page_panels` (found by
the comic's page fingerprint across its copies) rather than in a new
`comic_page_panels` table -- the same effect without moving every reading;
issue-level reading for a volume finishes its claimed issues; the iOS app
needed no change (the server maps file to comic).

## Why

The owner's words (2026-10-12):

> Progress and chosen covers should be aligned to … the issue, run,
> collection or story arc, not a particular file.

And: fixed panels and panel view should live with the issue record.

Today most of what a person does while reading is stored against a **file
id**. A file id is really the file's path: `files.path` is unique and
`_upsert_file` upserts on it. So the data is lost from view whenever the file
underneath changes:

| Event | What happens to the file id |
|---|---|
| A file is replaced through Replace file | The original row moves to quarantine with `present=0`. The new comic gets a **new id**. Nothing migrates. |
| A comic is moved or renamed outside Flipparr | The old path goes `present=0` at the next scan. The new path gets a new id. |
| A run is removed | The files are deleted, and progress, cover choices and automatic panels are cascade-deleted. Only manual panel fixes are archived. |
| A comic is converted to CBZ | New path, so a new id. This is why conversion skips existing comics today. |
| Runs are merged | File ids survive, but a duplicate issue number's **ratings are deleted** with the source issue, and the source run's cover, backdrop and rating go too. |
| A run is rebuilt or repaired | An issue whose only file went `present=0` can be deleted, taking its ratings with it. |

Already stored per issue or run, and so already safe across file changes:

- issue ratings (`issue_ratings`)
- run ratings (`series_run_ratings`)
- bookmarks
- story arcs and collections

### Production, 2026-10-12 (read-only)

- 46 reading-progress rows, none stranded yet.
- 2 of 5 chosen issue covers already point at files that are gone.
- 19 file rows are no longer present.
- 90 issues have two or more present files. Their read state depends on which copy was opened. The arc and catalog views look only at the lowest file id, so a read of the second copy is invisible there.

### Why it was keyed by file

The schema comment gives the reason: a **place** is a page number, and page
numbers belong to one file. Page 40 of a 24-page replacement is a blank
screen.

That is a reason to locate the place carefully. It is not a reason to key the
reading state by file: started, finished and last read don't depend on page
numbers at all.

## The model

**Rule:** what a person does belongs to the comic they meant: the issue, or
the volume (edition) for collected editions. Where a page matters, it belongs
to that comic's page, identified by the page's image, never by its number.

### 1. Page fingerprints (the foundation)

A new table, `page_fingerprints(file_id, page_member, file_signature, sha256, dhash)`:

- `sha256` is the hash of the page's bytes as stored in the archive.
- `dhash` is the 64-bit difference hash already used for panels (`page_panels.page_dhash`).

**When they are recorded:** whenever Flipparr already has a page's bytes in
hand, so no extra reads:

- rendering a page (`render_file_cover_thumbnail` reads the bytes on a cache miss);
- packing a comic for Flipparr View;
- CBZ conversion;
- reading panels.

A page that is already in the render cache has no fingerprint yet. The first
time one is needed it is computed in the background lane: one read for a zip,
one pass of the whole archive for a RAR (`ComicPages`).

**Why bytes and not page numbers:** an inserted ad makes page 5 of one file
page 7 of another. CBZ conversion and the download package keep page bytes
unchanged, so `sha256` matches exactly across them. A different scan of the
same page matches by `dhash` within 6 bits, which is how manual panel fixes
are found again today.

### 2. Reading state per issue (or volume), per profile

- **State:** `issue_reading(user_id, issue_id, started_at, finished_at, last_read_at)`, and `edition_reading(...)` for volumes.
- **Place:** `reading_places(user_id, subject, file_id, page, panel, page_count, page_sha256, page_dhash, updated_at)`. It is one row per comic, recording the last file read and the page's fingerprint.

**Opening a comic:**

1. **Same file and signature:** use the page and panel as today.
2. **A different file, or the file changed:** find the page with the same `sha256` in the new file. Failing that, the nearest `dhash`. Failing that, the same fraction of the way through (page / page count).
3. **The place can't be found exactly:** say so once. Never open on a page that isn't there.

**Run, collection and arc progress** are worked out from their issues. This
also fixes counts:

- duplicate copies no longer count twice;
- a read of the second copy shows;
- finished volumes no longer inflate a run's issue count.

**The API stays the same for clients.** `GET/POST /files/{id}/progress` keep
their shapes (reader API 1, pinned in `reader_api_1_shapes.json`, which the
iOS app depends on). The server maps file to comic. Old app builds and the
offline queue (keyed by file) keep working unchanged. Responses gain
issue-level fields; none are removed.

### 3. Panels per issue page

A new table, `comic_page_panels(subject, page_sha256, page_dhash, source, panels_json, segmented, fixed_by, updated_at)`.
It replaces `page_panels` as the place readings live.

**Lookup for a page:**

1. exact `sha256` within the comic;
2. else the nearest `dhash` within the comic;
3. else read anew.

**Effects:**

- Manual fixes **and** automatic readings follow the page into any copy, replacement or conversion of that issue.
- The vision model is never asked twice about the same page image, which saves money.
- The "file changed" signature check is no longer needed for panels: the same bytes are the same image.

**History stays:** `page_panel_history` and `page_vision_answers` stay as the
record, now carrying the subject and fingerprint. The export and import of
panel fixes (`flipparr-panel-fixes/1`) already carries hashes.

### 4. Covers and backdrops kept as images

**Issue cover** moves from `file_cover_preferences` to
`issue_cover_preferences(issue_id, …)`.

- A cover chosen from a page is copied as an image when it is chosen, so it survives the file.
- Uploads are named by issue, not file id. File ids are not AUTOINCREMENT, so a reused id could inherit a stale upload today.

**Run cover, run backdrop, arc and collection backdrops:**

- The chosen page is stored by fingerprint, so it can be found again in the run's other files.
- A copy of the chosen image is kept.
- Nothing cascade-deletes them with a file.

### 5. Integrity of issues that hold personal data

- **Merge.** An issue merged into a duplicate number moves its ratings, reading state, places and panels to the surviving issue, newest wins, instead of losing them.
- **Rebuild and repair.** They never delete an issue holding a profile's ratings or reading state while its run exists. If the issue's number changes, that data moves with it.
- **Re-identification.** A file whose issue link changes is a fix-match or a run correction. A reading made while the file was mis-identified was really a reading of the comic it truly is, so that profile's state moves with the file to the corrected issue. This applies only when no other file of the old issue was read by that profile.

### 6. Migration (schema 70), nothing lost

- **Reading state:** every `reading_progress` row maps through `file_issue_links` or `file_edition_links`, **present or not**, to its comic. Where two files of one comic have rows, the latest wins for the place, the earliest `started_at` and any `finished_at` win for the state. Rows already stranded on hidden files are recovered.
- **Panels:** `page_panels` rows with hashes move directly. Rows from before schema 66 have none, so their fingerprints are computed by a one-off background job: one pass per affected comic, in the background lane, bounded to comics that have panel rows.
- **Covers and backdrops:** copied to their new tables and images.
- **Kept:** the old tables stay, read-only, for one release (the way back), then go.
- **Rehearsal:** on a read-only copy of production's database, with row counts before and after, and every profile's read count per run unchanged or higher.

## Order of work

1. Fingerprints, recorded where bytes are already read.
2. Reading state and places, with the migration and the server mapping. Then the web and the app read issue-level state.
3. Panels per issue page.
4. Covers and backdrops.
5. Integrity: merge, rebuild and re-identification.
6. **Then** converting the existing library to CBZ becomes safe. It would be an opt-in job.

Each step ships on its own and keeps the API compatible.

## The owner's decisions (2026-10-12)

1. **Finishing a volume marks the issues it collects read**, for that profile,
   where its contents are known (`edition_coverage_claims`). Ownership stays
   separate, as the release scope requires.
2. **A place that cannot be found exactly** opens at the same fraction of the
   way through, with a short note once that the file changed.
3. **A re-identified file's reading moves with it** to the corrected issue,
   unless that profile also read another copy of the old issue.

## Verification

**Compatibility tests, for each event in the first table:**
- replace, move or rename, convert to CBZ, remove and re-import, merge, rebuild;
- reading state, the place (located by fingerprint), manual and automatic panels, covers, backdrops and ratings all survive.

**Contract tests:**
- reader API 1 shapes unchanged;
- an old iOS build's offline queue replays onto the right issue.

**Duplicates:**
- read copy B of an issue: it shows as read in the run, the arc and the catalog;
- run counts count issues, not files.

**Migration rehearsal:** on a copy of production's database. Report row counts and per-run read counts before and after, and the stranded rows recovered.

**On device:** read part way, replace the file on the server, and reopen on the web and in Flipparr View. It opens on the same page and the panels are there.
