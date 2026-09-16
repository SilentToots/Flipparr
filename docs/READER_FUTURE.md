# A built-in reader — deferred

Not scheduled. Written down after looking at what it would take, so the
assessment does not have to be made twice.

`PROJECT.md` puts an integrated reader out of the initial user release, and
`AGENTS.md` positions Kavita and friends as optional downstream readers rather
than something to replace. Building one is therefore a scope decision, not a gap
to fill.

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
