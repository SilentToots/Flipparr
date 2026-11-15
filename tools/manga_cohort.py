"""Manga coverage cohort: what the catalogs list, what the indexers carry, and
what Flipparr would take on its own. Read-only: nothing is imported, followed
or grabbed.

Run inside the Flipparr image, which holds the catalog and the services'
settings, with this file piped in:

    docker exec -i -w /app flipparr python3 - --out /config/benchmarks/manga.json \
        --title "Spy x Family" < tools/manga_cohort.py

`--app PATH` scores with another app.py (a candidate) instead of the image's,
so one cohort can be measured before and after a change. Every manga run in
the library is included; `--title` adds series the library does not hold.

Per series it reports the catalog's released volumes, which of them are owned,
the single volumes the indexers carry (a release that names this series and
one volume), the missing volumes that would be grabbed unattended (best score
at the automatic bar), and the volumes the indexers carry that no catalog
lists. Brandon, 2026-11-15: Star Wars: The Mandalorian: The Manga came back as
two volumes and none of them was taken, with four posted.
"""
import argparse
import importlib.util
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

AUTOMATIC_BAR = 85


def load_app(path):
    if not path:
        import app  # noqa: PLC0415 -- the image's own
        return app
    spec = importlib.util.spec_from_file_location("app_candidate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["app_candidate"] = module
    spec.loader.exec_module(module)
    return module


def prowlarr_search(app, title):
    """The series' releases as Flipparr's own manga search asks for them: its
    function, its book categories, and -- where the app knows it -- the title
    without the catalog's "The Manga" too."""
    service = app._enabled_acquisition_service("prowlarr")
    forms = app._manga_title_forms(title) if hasattr(app, "_manga_title_forms") else [title]
    found, seen = [], set()
    for form in forms:
        for release in app._prowlarr_search(service, app._plain_query_title(form) or form,
                                            categories=app.MANGA_CATEGORIES):
            key = (release.get("title"), release.get("indexer"), int(release.get("size") or 0))
            if key not in seen:
                seen.add(key)
                found.append(release)
    return found


def library_series(app):
    connection = sqlite3.connect(f"file:{app.catalog_store().database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT r.id, r.canonical_title, r.start_year, r.publisher FROM series_runs r WHERE r.format='manga' "
        "ORDER BY r.canonical_title").fetchall()
    found = []
    for row in rows:
        ids = {provider: str(value) for provider, value in connection.execute(
            "SELECT provider, provider_id FROM series_provider_ids WHERE series_run_id=?", (row["id"],))}
        found.append({"title": row["canonical_title"], "year": row["start_year"], "publisher": row["publisher"],
                      "ids": ids, "inLibrary": True})
    return found


def discovered_series(app, title):
    result = app.discover_series(title)
    rows = result.get("results") or []
    pick = next((row for row in rows if row.get("medium") == "manga"), rows[0] if rows else None)
    if not pick:
        return None
    return {"title": pick["title"], "year": pick.get("yearBegan"), "publisher": pick.get("publisher"),
            "ids": pick.get("providerIds") or {}, "inLibrary": bool(pick.get("inLibrary")), "asked": title}


def issue_numbers(preview):
    """Every number the catalog lists, released or not: the scan offers only the rest."""
    return [int(issue["number"]) for issue in preview.get("issues") or [] if str(issue.get("number") or "").isdigit()]


def measure(app, series):
    preview = app.preview_discovered_run(series["ids"], series["title"])
    issues = [issue for issue in preview.get("issues") or [] if str(issue.get("number") or "").isdigit()]
    released = {int(issue["number"]): issue for issue in issues if issue.get("releaseState") == "released"}
    owned = {number for number, issue in released.items() if issue.get("owned")}
    publisher = series.get("publisher") or preview.get("publisher")
    releases = prowlarr_search(app, series["title"])
    seen, best, posts = set(), {}, {}
    for release in releases:
        name = app._manga_release_name(release.get("title"))
        marker = app._MANGA_VOLUME.search(name)
        volumes = app._manga_volumes(name)
        context = {"seriesTitle": series["title"], "seriesYear": series.get("year"), "publisher": publisher,
                   "format": "manga", "issueNumber": "1"}
        if marker and len(volumes) == 1 and not app._MANGA_RANGE.search(name) \
                and app._manga_names_series(name[: marker.start()], context):
            seen |= volumes
        if len(volumes) == 1 and not app._MANGA_RANGE.search(name):
            # The step-2 gate (scan_indexer_volumes): one volume at the
            # automatic bar, counted per post (indexer, name, size).
            number = next(iter(volumes))
            score, _reasons = app._release_candidate_score(
                {"title": release.get("title"), "publishDate": release.get("publishDate"),
                 "categories": release.get("categories")}, {**context, "issueNumber": str(number)})
            if score is not None and score >= AUTOMATIC_BAR:
                posts.setdefault(number, set()).add(
                    (release.get("indexer"), release.get("title"), int(release.get("size") or 0)))
        for number, issue in released.items():
            if number in owned:
                continue
            context = {**context, "issueNumber": str(number),
                       "publicationYear": issue.get("publicationYear")}
            score, _reasons = app._release_candidate_score(
                {"title": release.get("title"), "publishDate": release.get("publishDate"),
                 "categories": release.get("categories")}, context)
            if score is not None and score > best.get(number, (-1, ""))[0]:
                best[number] = (score, release.get("title"))
    missing = sorted(set(released) - owned)
    grabbable = sorted(number for number in missing if best.get(number, (-1,))[0] >= AUTOMATIC_BAR)
    return {
        "series": series["title"], "asked": series.get("asked"), "publisher": publisher,
        "inLibrary": series["inLibrary"], "catalogProvider": preview.get("provider"),
        "catalogVolumes": sorted(released), "owned": sorted(owned), "missing": missing,
        "grabbable": grabbable, "notGrabbable": [n for n in missing if n not in grabbable],
        "bestFor": {str(n): best[n] for n in missing if n in best},
        "indexerVolumes": sorted(seen), "indexerOnly": sorted(seen - set(released)),
        "offered": sorted(number for number, found in posts.items()
                          if number not in set(issue_numbers(preview)) and number != 0 and len(found) >= 2),
        "releasesSearched": len(releases),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app")
    parser.add_argument("--out")
    parser.add_argument("--title", action="append", default=[])
    args = parser.parse_args()
    app = load_app(args.app)
    app.persist_provider_cache = lambda *a, **k: None  # read-only: no cache rows written
    cohort = library_series(app)
    for title in args.title:
        found = discovered_series(app, title)
        if found and found["title"] not in {series["title"] for series in cohort}:
            cohort.append(found)
        elif not found:
            print(f"no catalog run for {title!r}", file=sys.stderr)
    rows = []
    for series in cohort:
        try:
            rows.append(measure(app, series))
        except Exception as exc:  # noqa: BLE001 -- one series failing is a row, not the cohort
            rows.append({"series": series["title"], "error": f"{type(exc).__name__}: {exc}"})
        time.sleep(2)
    totals = {key: sum(len(row.get(key) or []) for row in rows)
              for key in ("catalogVolumes", "owned", "missing", "grabbable", "notGrabbable", "indexerOnly", "offered")}
    report = {"app": args.app or "image", "series": len(rows), "totals": totals, "rows": rows}
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(report, handle, indent=1)
    for row in rows:
        if "error" in row:
            print(f"{row['series']}: ERROR {row['error']}")
            continue
        print(f"{row['series'][:40]:40} catalog {len(row['catalogVolumes']):>3} owned {len(row['owned']):>3} "
              f"missing {len(row['missing']):>3} grab {len(row['grabbable']):>3} "
              f"indexer-only {row['indexerOnly']} offered {row['offered']}")
    print("TOTALS", totals)


if __name__ == "__main__":
    main()
