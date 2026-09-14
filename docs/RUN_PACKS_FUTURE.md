# Complete-run packs as a source — deferred

Not scheduled; the UI comes first. Worth picking up beside
[DirectSite (DDL)](DIRECT_DOWNLOADS.md), which posts whole runs as a matter
of course, so half the work below is shared.

## What happens today

Nothing offers a pack, in the automatic search or in Find release. Every search
is per issue -- a job for Chew #4 asks for "Chew 004", then "Chew 4", then
"Chew" -- and a result must score 85 to be offered. Scored against that job:

| Release | Score |
| --- | --- |
| `Chew 001-060 (2009-2016) (Digital) (Zone-Empire)` | 0 |
| `Chew Complete Series (001-060) (2016) (Digital)` | 0 |
| `Chew v01-v12 Complete (Digital) (Zone-Empire)` | 0 |
| `Chew 004 (2009) (D) (Kingpin-Empire)` | 85 |

A pack scores zero because `_release_issue_matches` looks for the wanted number
in the title and a range is not that number.

## The half that already works

- `_import_selected_comic` **copies** out of the download folder rather than
  moving, so the pack survives being read again.
- `_choose_downloaded_comic` already picks the one matching comic out of a
  folder of many, which is what a pack is.

## Why "just allow packs" would be wrong

A download row belongs to one job (`acquisition_downloads.job_id` is unique)
and a followed run fans out into a job per issue. Let packs through the scorer
and following a 60-issue run means sixty jobs each grabbing the same 40 GB
pack. One download has to satisfy many jobs, and that is the actual feature.

## What it would take

1. Read the range out of a release name: `001-060`, `#1-60`, `v01-v12`,
   "Complete Series", "Full Run".
2. Score a pack for a job when the wanted issue falls inside its range, below
   an exact single issue so singles still win.
3. After it imports for the job that grabbed it, sweep the same folder for the
   run's other wanted issues, import those, and mark their jobs fulfilled --
   before `_sab_remove_job` clears the folder.
4. Guards: do not fetch a whole run to fill one gap, and cap by size.
5. UI: label a pack candidate with what it covers, and say "12 issues from one
   download" on the Pull List row.
