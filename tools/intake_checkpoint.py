#!/usr/bin/env python3
"""Gate 2 intake checkpoint: what Flipparr makes of a real library, measured.

Scans a library read-only into fresh, isolated databases and reports each
stage separately against the same cohort (AGENTS.md, "End-to-end progress"):

  1. inventory and health   -- files found, readable or not, and why
  2. discovery eligibility  -- files with enough local evidence to look up
  3. grouping               -- runs, formats and numbers assigned
  4. ownership              -- issues and volumes the files are credited to
  5. intervention           -- what is left for a person to decide

Local evidence only (filenames, embedded metadata, archive contents): no
provider is asked anything, so the run costs no quota and measures the
intake itself. Two clean intakes must agree with each other, and a rescan
of the first must change nothing. A reference catalog (a copy of
production's) can be measured alongside -- as a reference, not as truth.

Writes a JSON report and a spot-check CSV of sampled files to --out. Both
name the library's files: keep them out of the repository.

    python3 tools/intake_checkpoint.py --library /comics --out /work \\
        [--reference /work/reference.db] [--sample 40]
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _point_app_at(folder: Path) -> None:
    """Every file the app would write goes under `folder`, never /config."""
    folder.mkdir(parents=True, exist_ok=True)
    for key, name in (("DATABASE", "flipparr.db"), ("SETTINGS_CONFIG", "settings.json"),
                      ("PROVIDER_CONFIG", "providers.json"), ("ACQUISITION_CONFIG", "services.json"),
                      ("AUTH_CONFIG", "auth.json")):
        os.environ[f"FLIPPARR_{key}"] = str(folder / name)
    os.environ["FLIPPARR_TEMP_DIR"] = str(folder)


def intake(library: Path, folder: Path, *, rescan: bool = False) -> dict[str, Any]:
    """Scan `library` into the database under `folder` (a fresh one unless
    `rescan`) and return the scan's own record and how long it took."""
    _point_app_at(folder)
    import app  # noqa: PLC0415 -- after the environment points at `folder`

    store = app.catalog_store()
    started = time.monotonic()
    scan_id = store.begin_scan(str(library), True)
    store.perform_scan(scan_id, app.scan_folder, app.inventory_file)
    scan = store.get_scan(scan_id) or {}
    return {"scan": {key: scan.get(key) for key in ("status", "total_files", "processed_files", "changed_files",
                                                    "reused_files", "error")},
            "seconds": round(time.monotonic() - started, 1), "rescan": rescan}


def _json(text: Any) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def measure(database: Path) -> dict[str, Any]:
    """The five stages, read from a catalog database without changing it."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    files = connection.execute(
        "SELECT id, path, filename, extension, parsed_json, result_json FROM files WHERE present=1").fetchall()

    # 1. inventory and health
    by_extension = collections.Counter(str(row["extension"]).lower() for row in files)
    health = collections.Counter()
    unhealthy: set[int] = set()
    for row in files:
        # inspect_file_health: {"status": "ok" | "warning" | "error", "code": ...}
        state = _json(row["result_json"]).get("file_health") or {}
        status = str(state.get("status") or "ok") if isinstance(state, dict) else "ok"
        code = str(state.get("code") or "") if isinstance(state, dict) else ""
        health[f"{status}: {code}" if code and status != "ok" else status] += 1
        if status == "error":
            unhealthy.add(int(row["id"]))

    # 2. discovery eligibility: enough local evidence to look the file up
    eligible = 0
    evidence_sources = collections.Counter()
    for row in files:
        parsed = _json(row["parsed_json"])
        embedded = _json(row["result_json"]).get("embedded_metadata") or {}
        from_name = bool(parsed.get("title")) and (parsed.get("issue") is not None or parsed.get("volume") is not None)
        from_inside = bool(embedded.get("series")) and (embedded.get("number") not in (None, "") or embedded.get("volume"))
        evidence_sources["filename and embedded" if from_name and from_inside
                         else "filename only" if from_name else "embedded only" if from_inside else "neither"] += 1
        eligible += int(from_name or from_inside)

    # 3. grouping
    identities = {int(row["file_id"]): row for row in connection.execute(
        """SELECT file_identities.file_id, file_identities.identity_kind, file_identities.issue_number,
                  file_identities.volume_number, series_runs.id AS run_id, series_runs.canonical_title AS run_title,
                  series_runs.start_year AS run_year, series_runs.format AS run_format
           FROM file_identities JOIN series_runs ON series_runs.id=file_identities.series_run_id
           JOIN files ON files.id=file_identities.file_id WHERE files.present=1""")}
    runs = connection.execute(
        """SELECT series_runs.id, series_runs.canonical_title, series_runs.start_year,
                  COUNT(file_identities.file_id) AS files
           FROM series_runs
           LEFT JOIN file_identities ON file_identities.series_run_id=series_runs.id
               AND file_identities.file_id IN (SELECT id FROM files WHERE present=1)
           GROUP BY series_runs.id""").fetchall()
    kinds = collections.Counter(str(row["identity_kind"]) for row in identities.values())
    numbered = sum(1 for row in identities.values()
                   if row["issue_number"] not in (None, "") or row["volume_number"] is not None)
    by_title = collections.defaultdict(list)
    for run in runs:
        by_title[str(run["canonical_title"]).casefold()].append(run)
    same_title_runs = {title: sorted({run["start_year"] for run in group if run["start_year"] is not None})
                       for title, group in by_title.items() if len(group) > 1}

    # 4. ownership
    issue_linked = {int(row[0]) for row in connection.execute("SELECT file_id FROM file_issue_links")}
    edition_linked = {int(row[0]) for row in connection.execute("SELECT file_id FROM file_edition_links")}
    owned_issues = connection.execute("SELECT COUNT(DISTINCT issue_id) FROM file_issue_links").fetchone()[0]
    owned_editions = connection.execute("SELECT COUNT(DISTINCT edition_id) FROM file_edition_links").fetchone()[0]
    present_ids = {int(row["id"]) for row in files}
    credited = (issue_linked | edition_linked) & present_ids

    # 5. intervention, as the app itself counts it
    connection.close()
    import app  # noqa: PLC0415
    from catalog_store import CatalogStore  # noqa: PLC0415

    # Opening a store runs the app's startup upkeep, which writes: measure a
    # throwaway copy, so a reference stays as production made it and a
    # second run against it measures the same thing.
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "measure.db"
        source, target = sqlite3.connect(f"file:{database}?mode=ro", uri=True), sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()
        catalog = CatalogStore(copy).catalog(app.preferred_language())
    inbox = catalog.get("inbox") or []
    reasons = collections.Counter(str(item.get("reasonCode") or item.get("reason") or item.get("category") or "other")
                                  for item in inbox)
    stats = catalog.get("stats") or {}

    total = len(files)
    pct = (lambda part: round(100.0 * part / total, 1) if total else 0.0)
    return {
        "inventory": {"files": total, "byExtension": dict(by_extension.most_common()),
                      "health": dict(health.most_common()), "unhealthy": len(unhealthy)},
        "discovery": {"eligible": eligible, "eligiblePct": pct(eligible),
                      "evidence": dict(evidence_sources.most_common())},
        "grouping": {"runs": len(runs), "filesGrouped": len(identities), "filesGroupedPct": pct(len(identities)),
                     "byKind": dict(kinds.most_common()), "numbered": numbered, "numberedPct": pct(numbered),
                     "singleFileRuns": sum(1 for run in runs if run["files"] == 1),
                     "emptyRuns": sum(1 for run in runs if run["files"] == 0),
                     "sameTitleRuns": len(same_title_runs)},
        "ownership": {"filesCredited": len(credited), "filesCreditedPct": pct(len(credited)),
                      "issuesOwned": owned_issues, "editionsOwned": owned_editions,
                      "filesUncredited": total - len(credited)},
        "intervention": {"items": len(inbox), "byReason": dict(reasons.most_common()),
                         "needAttention": stats.get("needAttention"), "metadataReview": stats.get("metadataReview"),
                         "damaged": stats.get("damaged")},
        "_identities": {str(row["path"]): _identity_signature(identities.get(int(row["id"]))) for row in files},
        "_runOf": {str(row["path"]): int(identities[int(row["id"])]["run_id"]) for row in files
                   if int(row["id"]) in identities},
        "_sameTitleRuns": same_title_runs,
    }


def _identity_signature(row: Any) -> list[Any] | None:
    if row is None:
        return None
    return [str(row["run_title"]).casefold(), row["run_year"], row["identity_kind"],
            row["issue_number"], row["volume_number"]]


def compare(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Where two intakes of the same library disagree, file by file."""
    a, b = first["_identities"], second["_identities"]
    differ = sorted(path for path in set(a) | set(b) if a.get(path) != b.get(path))
    return {"filesCompared": len(set(a) | set(b)), "filesThatDiffer": len(differ), "examples": differ[:20]}


def grouping_agreement(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Whether the same files end up together, whatever the runs are called
    (a provider renames "Action Comics" to "Action Comics (1938)"). For each
    run of `first`: the same file set in `second`, spread over several of
    `second`'s runs (eras merged in `first`), or part of a larger one
    (split in `first`)."""
    runs_a: dict[int, set[str]] = collections.defaultdict(set)
    runs_b: dict[int, set[str]] = collections.defaultdict(set)
    for path, run in first["_runOf"].items():
        runs_a[run].add(path)
    for path, run in second["_runOf"].items():
        runs_b[run].add(path)
    titles = {run: first["_identities"][next(iter(paths))][0] for run, paths in runs_a.items()}
    same, spread, part = 0, [], []
    for run, paths in runs_a.items():
        targets = {second["_runOf"].get(path) for path in paths} - {None}
        if len(targets) > 1:
            spread.append([titles[run], len(paths), len(targets)])
        elif targets and runs_b[next(iter(targets))] == paths:
            same += 1
        else:
            part.append([titles[run], len(paths)])
    return {"runs": len(runs_a), "sameFiles": same,
            "holdsSeveralOfTheOthersRuns": len(spread), "spreadExamples": sorted(spread, key=lambda x: -x[1])[:15],
            "partOfALargerRun": len(part), "partExamples": sorted(part, key=lambda x: -x[1])[:15]}


def spot_check(database: Path, out: Path, size: int, seed: int = 20261005) -> int:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT files.id, files.filename, files.parsed_json,
                  series_runs.canonical_title AS run_title, series_runs.start_year AS run_year,
                  file_identities.identity_kind, file_identities.issue_number, file_identities.volume_number,
                  (SELECT COUNT(*) FROM file_issue_links WHERE file_issue_links.file_id=files.id) AS issue_linked,
                  (SELECT COUNT(*) FROM file_edition_links WHERE file_edition_links.file_id=files.id) AS edition_linked
           FROM files
           LEFT JOIN file_identities ON file_identities.file_id=files.id
           LEFT JOIN series_runs ON series_runs.id=file_identities.series_run_id
           WHERE files.present=1""").fetchall()
    sample = random.Random(seed).sample(rows, min(size, len(rows)))
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["filename", "parsed title", "parsed issue", "parsed volume", "parsed year",
                         "grouped run", "run year", "kind", "issue", "volume", "credited",
                         "correct? (y / n / note)"])
        for row in sorted(sample, key=lambda item: str(item["filename"]).casefold()):
            parsed = _json(row["parsed_json"])
            writer.writerow([row["filename"], parsed.get("title"), parsed.get("issue"), parsed.get("volume"),
                             parsed.get("year"), row["run_title"], row["run_year"], row["identity_kind"],
                             row["issue_number"], row["volume_number"],
                             "issue" if row["issue_linked"] else "volume" if row["edition_linked"] else "no", ""])
    return len(sample)


def _public(measured: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in measured.items() if not key.startswith("_")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, type=Path, help="the library folder (mount it read-only)")
    parser.add_argument("--out", required=True, type=Path, help="where the databases and the report go")
    parser.add_argument("--reference", type=Path, help="a copy of an existing catalog database, measured alongside")
    parser.add_argument("--sample", type=int, default=40, help="files in the spot-check CSV")
    args = parser.parse_args()
    if not args.library.is_dir():
        parser.error(f"{args.library} is not a folder")
    args.out.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"library": str(args.library), "evidence": "local only (no provider calls)"}
    first, second = args.out / "intake-a", args.out / "intake-b"
    for folder in (first, second):
        if (folder / "flipparr.db").exists():
            parser.error(f"{folder} already holds a database; use a fresh --out")

    print("intake A (clean)...", flush=True)
    report["intakeA"] = intake(args.library, first)
    measured_a = measure(first / "flipparr.db")
    print("intake B (clean, separate database)...", flush=True)
    # The app keeps one store per database path, so B gets its own process.
    import subprocess  # noqa: PLC0415
    subprocess.run([sys.executable, __file__, "--_intake-only", str(args.library), str(second)], check=True)
    measured_b = measure(second / "flipparr.db")
    print("intake A again (rescan)...", flush=True)
    report["rescanA"] = intake(args.library, first, rescan=True)
    measured_rescan = measure(first / "flipparr.db")

    report["stages"] = _public(measured_a)
    report["cleanIntakesAgree"] = compare(measured_a, measured_b)
    report["rescanChangesNothing"] = compare(measured_a, measured_rescan)
    report["sameTitleRuns"] = measured_a["_sameTitleRuns"]
    if args.reference:
        measured_reference = measure(args.reference)
        report["reference"] = _public(measured_reference)
        report["groupingAgainstReference"] = grouping_agreement(measured_a, measured_reference)
        report["referenceNote"] = "a copy of an existing catalog, built over time with provider data: a reference, not truth"
    report["spotCheckFiles"] = spot_check(first / "flipparr.db", args.out / "spot-check.csv", args.sample)

    (args.out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({key: value for key, value in report.items() if key != "sameTitleRuns"}, indent=2, default=str))
    print(f"\nwritten: {args.out / 'report.json'} and {args.out / 'spot-check.csv'}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--_intake-only":
        print(json.dumps(intake(Path(sys.argv[2]), Path(sys.argv[3]))), flush=True)
        sys.exit(0)
    sys.exit(main())
