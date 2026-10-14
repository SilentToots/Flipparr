# Panel view: how well the panels are found, and how that is measured

Panel view steps through a page one panel at a time. The panels come from
`page_panels.py` -- a gutter finder, an optional detector model, a pass for
the ink those left out -- and, when a vision connector is configured, from
the model's answer corrected against the page's gutters. Pages a reader
fixes by hand in the editor are kept as the reader's (`source = manual`).

This page records how far the automatic readings are from the pages the
owner fixed by hand, build by build, so that a change to the detector is
judged on the library it serves rather than on drawn fixtures, and so that
a change that moves nothing is not kept. Written 2026-10-06.

## When a page is read

A page is read the first time someone opens it in panel view (the reader
asks two pages ahead) and the reading is kept. With the vision connector
reading every page, that first reading takes a few seconds -- felt at the
start of a new comic. Since 2026-10-14 an **overnight read** reads ahead
between the hours set (midnight to 6 am by default): for each profile, admin
first, Keep Reading's comics, each run's next issue, issues imported in the
last 7 days, then comics on Flipparr View devices -- only what that profile
may see. It goes through the same `file_page_panels` path as an opened page,
in the background lane, and skips pages whose reading is already final. An
admin's comics go to the vision connector when it reads every page, up to a
nightly page limit counted from the catalog's run rows so
a restart does not reset it; once the limit is reached the remaining vision
comics wait for the next night rather than being read locally, because a
local reading would be replaced by vision when the page is opened (panels
must not change mid-read). Readers' comics are read locally unless readers'
pages may use vision. Each night is a row in `overnight_panel_runs` (pages,
vision pages, settled pages skipped, why it stopped), shown on Settings ›
Reader and System. Pages it does not reach are read when opened, as before;
nothing about the on-demand path changes.

## The benchmark

`tools/panel_benchmark.py` runs the local tiers over every hand-fixed page
of a catalog copy, with the library mounted read-only, and compares each
reading with the reader's: boxes matched by overlap, merges (one automatic
box holding two of the reader's), splits (the reverse), order inversions,
and the mean distance between matched edges. Two levels are reported,
because a reader's fixes encode intent as well as geometry:

- **box agreement** -- each of the reader's boxes has one automatic box like it;
- **group agreement** -- each of the reader's boxes is one automatic box or
  the union of several: the reader grouped what the machine split, which is
  a choice about reading, not a failure to find a panel;
- **exact structure** -- box agreement with close edges and the same order.

It never asks a vision connector: the app it starts has no provider
configured and the connector's entry point is replaced with one that
raises. Since schema 65 the catalog keeps the reading a correction replaced
(`page_panel_history`), and the benchmark reports that reading's agreement
too (`before`), which is how the vision tier is measured without a key.

On the NAS, from the build tree, with a copy of the catalog in a work
folder (the composites and masks it writes name the owner's comics, so the
work folder stays outside the repository):

```
docker run --rm --network none -v ~/flipparr-build:/src:ro -v "$COMICS":/comics:ro \
  -v "$CONFIG/models/panels.onnx":/work/panels.onnx:ro -v "$WORK":/work -w /src \
  --entrypoint python3 flipparr:local -B tools/panel_benchmark.py \
  --catalog /work/flipparr.db --out /work/<name> --model /work/panels.onnx --masks
```

## Baseline, 2026-10-06 (build 89b32124)

126 hand-fixed pages across 18 files (120 single pages, 6 spreads skipped);
the owner fixes about 27% of the pages he reads in panel view.

| Reading | Exact | Box | Group | Unsegmented | Fewer | More | Merges | Splits | Inversions | Edge |
|---|---|---|---|---|---|---|---|---|---|---|
| Local tiers (cut + model + leftover) | 25 | 32 | 51 | 24 | 19 | 40 | 30 | 35 | 13 | 1.5% |
| Cut only | 20 | 22 | 33 | 53 | 33 | 11 | 33 | 12 | 3 | 1.5% |
| White-gutter pages (77) | 17 | | 36 | 9 | | | | | | |
| Black-gutter pages (43) | 8 | | 15 | 15 | | | | | | |

Counts are pages; "Edge" is the mean distance between matched edges as a
share of the page. **Edges are right; structure is wrong.** The vision
tier's own agreement is not in the baseline: before schema 65 a correction
erased the reading it corrected.

What the disagreements are, from the composites and the masks:

- **Black-gutter pages read as nothing.** The gutters are plain to see as
  valleys in the ink profile (5-18% ink, framed by the panels' border lines
  at 80-95%), but light crossing them -- rain, bokeh glow -- keeps them
  above the 3% the cut calls bare, so no gap is found. Once & Future is
  almost entirely such pages, and a vision answer is corrected against the
  same mask.
- **Phantom panels from solid strips.** A black band at the foot of a
  panel is dense with "ink" and the leftover pass makes a panel of it.
- **Insets.** A panel drawn inside another is merged into it by the vision
  path (containment counts as overlap) and shredded into slivers by the cut.

Each is held by a fixture in `test_page_panels.py` (`FailureClasses`) that
failed on this build.

## Changes, and what each did

Each change to the detector is one commit, run on the benchmark before it
is kept. The baseline is the row above; the gate is that the aggregate
moves and white-gutter pages do not get worse.

| Date | Change | Exact | Box | Group | Unseg. | More | Merges | Splits | Kept? |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-06 | Baseline (build 89b32124) | 25 | 32 | 51 | 24 | 40 | 30 | 35 | — |
| 2026-10-06 | The leftover pass on a page read with no model and no connector (`_file_page_panels` called the detector directly and skipped it) | — | — | — | — | — | — | — | yes: not measurable here (the benchmark reads through `_read_page_panels`, which always ran it), held by a test |
| 2026-10-06 | A relative "valley" pass in the cut for gutters light crosses (≤30% ink, flanked by ≥2× more, no long runs) | 23 | 27 | 45 | 26 | 43 | 30 | 42 | **no**: it split more real panels than it found gutters; dark pages went from 8 exact to 4 |
| 2026-10-06 | Solid thin islands at a panel's edge are not made panels of by the leftover pass (`LEFTOVER_THIN`, `LEFTOVER_SOLID`); first as "grow the nearest panel" (merges 30 → 32: a band under two panels grew one over the other), then as "leave it alone" | 27 | 34 | 52 | 24 | 35 | 30 | 32 | yes: 8 pages changed, none for the worse |

Still open, with fixtures that fail (`expectedFailure` in `test_page_panels.py`
until they hold): black-gutter pages crossed by light, and insets.
