# Run drawer tabs: pending changes

Agreed on 2026-09-15, to be made together with the UI updates now in progress
rather than on their own.

## 1. Aliases: remove the tab, keep the mechanism

Aliases are how a scan files a comic under the right run when the file's name
differs from the run's title (`catalog_store.py`, the `series_aliases` lookups
used when resolving a claim). They are not a collected-editions feature, and
the scan depends on them.

The Aliases tab adds little: in the production library every run (34 of 34)
has aliases, all created automatically (Metron, Comic Vine, canonical hints,
catalog claims), and most only repeat the run's own title. Fix match, moving
a file to another run, and the automatic aliases cover what the manual form
was for.

- Remove the Aliases tab from the run drawer.
- Add a small **Alternate titles** card under Advanced that lists only aliases
  differing from the run's title, and keeps the add form for the rare case a
  scan keeps missing.

## 2. Collections: show only when collected editions are on

Collections (series families) group related runs -- relaunches, story arcs,
following a whole collection -- and belong with collected-editions support.
The production library has none, and support is off.

With support off, the library's Runs/Collections switch is already hidden and
`openCollection` refuses to open one, but the run drawer still shows the
**Collection** tab and the family chip. A collection made there could never be
opened.

- Show the Collection tab and the family chip only when
  `collectedEditionsEnabled` is on, the way the Volumes tab already is.
- Nothing is deleted; turning support back on brings them back.

## Result

The drawer's tabs become Overview, Issues, Files and Advanced, plus Volumes and
Collection when collected editions are on.
