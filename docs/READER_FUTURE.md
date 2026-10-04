# A built-in reader — superseded, 2026-09-20

**This assessment was acted on and is now history.** The reader is built and in
production: a full-screen viewer with progress kept per file, manga read right
to left, spreads shown alone, zoom and pan, a scrubber that previews the page
under your thumb, a jump-to-page field, and night reading. Read is offered on
the Comics grid, in a run's drawer, on its issue cards and rows, and in the
Files tab; `docs/` keeps this page for the reasoning that led there.

The estimate below ("roughly two focused days") was close. What it missed is
recorded in the commits: the CBR extract-window optimisation turned out to be
unnecessary (110ms against 90ms for zip on this library, measured), and the
part that actually needed care was where a comic's place is saved, not how its
pages are drawn.

Written down after looking at what it would take, so the assessment did not
have to be made twice.

At the time, the project plan put an integrated reader out of the initial user
release, and `AGENTS.md` positioned Kavita and friends as optional downstream
readers rather than something to replace. Building one was therefore a scope
decision rather than a gap to fill — and the decision was taken on 2026-09-20.
Both documents now say so.

## Most of it already exists

The drawer's page backgrounds needed the same machinery a reader needs, so it is
already built and in production:

- `GET /api/v1/files/{id}/pages` — a comic's pages in reading order
  (`archive_page_members`, `app.py`), naturally sorted so 2 comes before 10,
  with macOS resource forks and hidden files dropped.
- `GET /api/v1/files/{id}/pages/{n}` — one page rendered as JPEG
  (`render_file_page`).
- Addressed **by file id, not path**, through `library_file_path`, so a request
  can only reach comics the library holds. The archive type is read from the
  file's bytes, so a `.cbr` that is secretly a zip still opens.

## What a basic reader adds

1. **A reading size.** Pages cap at 1200px for a backdrop and ~600 for a
   thumbnail. Reading wants 1600–2400.
2. **The viewer**: full-screen page, next/previous by tap zone, arrow key and
   swipe, a page counter, and one or two pages preloaded. A few hundred lines in
   `App.jsx`, the shape of the full-screen surfaces already there.
3. **Remembering the place.** Nothing stores reading progress: a small table,
   and "continue reading" on the card.

Roughly two focused days for something pleasant on a phone and a desktop.

## Where a naive version disappoints

- **CBR speed.** Every page render re-opens the archive. Fine for zip, slow for
  RAR, and obvious when paging quickly. Wants a page cache or a per-session
  extract.
- **Manga reads right to left**, and this library has manga runs. That is a
  per-run setting and a mirrored layout, not a later detail.
- **Double-page spreads** want to be shown alone rather than squeezed; the
  backdrop picker already half-detects them (`automatic_backdrop_page` looks for
  an aspect ratio over 1.15).

## The cheapest useful experiment

A page-turner with no progress tracking and no manga mode — half a day — is
enough to find out whether reading in Flipparr is worth having at all, before
any of the above is worth doing.
