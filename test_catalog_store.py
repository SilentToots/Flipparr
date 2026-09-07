import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import ParsedFile, _metron_reprint_coverage
from catalog_core_v2.provider_evidence import native_issue_evidence
from catalog_store import CatalogStore


class CatalogStoreTests(unittest.TestCase):
    def test_operation_context_releases_its_sqlite_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            connection = store._connect()

            with connection:
                self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)

            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
                connection.execute("SELECT 1")

    def test_merge_keeps_the_clean_title_over_an_identifier_title(self):
        """Merging a stamped duplicate must not rename the run the user kept."""
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "merge.db")
            with store._connect() as connection:
                for title in ("saga", "Saga 1398374447"):
                    connection.execute(
                        """INSERT INTO series_runs(canonical_title, canonical_key, start_year,
                               publisher, created_at, updated_at)
                           VALUES (?, ?, NULL, NULL, '2026-01-01', '2026-01-01')""",
                        (title, title.lower().replace(" ", "")),
                    )
                runs = {
                    row["canonical_title"]: int(row["id"])
                    for row in connection.execute("SELECT id, canonical_title FROM series_runs")
                }
            result = store.merge_series(runs["Saga 1398374447"], runs["saga"])
            self.assertEqual(result["title"], "saga")

    def test_library_roots_can_be_managed_without_changing_comic_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            first = base / "comics"
            second = base / "archive"
            first.mkdir()
            second.mkdir()
            comic = first / "Example 001.cbz"
            comic.write_bytes(b"comic")
            store = CatalogStore(base / "catalog.db")

            first_scan = store.begin_scan(str(first), True)
            store.perform_scan(first_scan, lambda *_: [
                ParsedFile(str(comic), comic.name, ".cbz", "Example", issue="1")
            ], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": None, "file_cover": None,
            })
            second_id = store.register_root(str(second), False)
            updated = store.update_root(second_id, True)
            self.assertEqual(updated["recursive"], 1)

            first_id = next(root["id"] for root in store.catalog()["roots"] if Path(root["path"]) == first.resolve())
            removed = store.remove_root(first_id)
            self.assertEqual(removed["removedFileRecords"], 1)
            self.assertFalse(removed["filesChanged"])
            self.assertTrue(comic.exists())
            self.assertEqual([Path(root["path"]) for root in store.catalog()["roots"]], [second.resolve()])

    def test_library_roots_reject_overlapping_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "comics"
            child = root / "publisher"
            child.mkdir(parents=True)
            store = CatalogStore(Path(folder) / "catalog.db")
            store.register_root(str(root), True)

            with self.assertRaisesRegex(ValueError, "cannot overlap"):
                store.register_root(str(child), True)

    def test_verified_acquisition_import_uses_request_identity_instead_of_filename_guess(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root / "Absolute Flash 017 (2026).cbz"
            imported = root / "Absolute Flash (2025) #018 - Now You See Me.cbz"
            existing.write_bytes(b"existing")
            imported.write_bytes(b"imported")
            store = CatalogStore(root / "catalog.db")

            existing_parsed = ParsedFile(
                str(existing), existing.name, ".cbz", "Absolute Flash",
                issue="17", year=2026,
            )
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [existing_parsed], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": None, "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            store.apply_issue_list(
                series_id, "gcd", "221952", "https://www.comics.org/api/series/221952/",
                [
                    {"number": "17", "title": "In Gorilla City: Part 2 of 2", "publication_year": 2026},
                    {"number": "18", "title": "Now You See Me", "publication_year": 2026},
                ],
            )

            misparsed = ParsedFile(
                str(imported), imported.name, ".cbz", "Absolute Flash Now You See Me",
                issue="18", year=2025,
            )
            result = {
                "parsed": misparsed.__dict__, "lookup_identity": misparsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": None, "file_cover": None,
            }
            store.ingest_acquisition_import(str(root), misparsed, result, {
                "seriesTitle": "Absolute Flash", "recordType": "issue",
                "issueNumber": "18", "publisher": "DC Comics", "publicationYear": 2026,
            })

            catalog = store.catalog()
            self.assertEqual(len(catalog["series"]), 1)
            self.assertEqual((catalog["series"][0]["title"], catalog["series"][0]["owned"]), ("Absolute Flash", 2))
            issue = next(item for item in catalog["series"][0]["issues"] if item["number"] == "18")
            self.assertTrue(issue["directOwned"])

    def test_initial_import_queues_one_durable_job_per_series_without_calling_it_a_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comics = [root / "Example 001.cbz", root / "Example 002.cbz"]
            for comic in comics:
                comic.write_bytes(b"comic")
            parsed = [
                ParsedFile(str(comic), comic.name, ".cbz", "Example", issue=str(index), year=2024)
                for index, comic in enumerate(comics, 1)
            ]
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: parsed, lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": None, "file_cover": None,
                "source_status": {"external_metadata": "deferred during fast library inventory"},
            })

            queued = store.enqueue_metadata_enrichment()
            self.assertEqual(queued, {"queued": 1, "retained": 0})
            catalog = store.catalog()
            self.assertEqual(catalog["stats"]["needAttention"], 0)
            self.assertEqual(catalog["enrichment"]["active"], 1)

            job = store.claim_metadata_enrichment_job()
            self.assertEqual(job["canonical_title"], "Example")
            store.finish_metadata_enrichment_job(int(job["id"]), "complete", "metron")
            reopened = CatalogStore(root / "catalog.db")
            self.assertEqual(reopened.metadata_enrichment_summary()["complete"], 1)

    def test_metadata_summary_exposes_provider_cooldown_for_honest_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            store.record_metadata_provider_outcome(
                "gcd", "Grand Comics Database asked Flipparr to pause.", 180
            )
            cooldown = store.metadata_enrichment_summary()["providerCooldowns"][0]
            self.assertEqual(cooldown["provider"], "gcd")
            self.assertIn("asked Flipparr to pause", cooldown["error"])
            self.assertIsNotNone(cooldown["nextRetryAt"])

    def test_same_title_provider_runs_remain_distinct_and_are_retry_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            first = store.ensure_provider_series_run(
                "gcd", "137831", "Farmhand", 2018, "Image"
            )
            store.apply_issue_list(
                int(first["id"]), "gcd", "137831",
                "https://www.comics.org/api/series/137831/",
                [{"number": "1", "provider_id": "210001", "title": "The Farm"}],
            )

            second = store.ensure_provider_series_run(
                "gcd", "230002", "Farmhand", 2025, "Image"
            )
            self.assertNotEqual(first["id"], second["id"])
            store.apply_issue_list(
                int(second["id"]), "gcd", "230002",
                "https://www.comics.org/api/series/230002/",
                [{"number": "1", "provider_id": "310001", "title": "New Growth"}],
            )

            retried = store.ensure_provider_series_run(
                "gcd", "230002", "Farmhand", 2025, "Image"
            )
            self.assertEqual(retried["id"], second["id"])
            farmhand_runs = [
                item for item in store.catalog()["series"] if item["title"] == "Farmhand"
            ]
            self.assertEqual(len(farmhand_runs), 2)
            self.assertEqual(
                {item["issueCatalog"]["providerSeriesId"] for item in farmhand_runs},
                {"137831", "230002"},
            )

    def test_same_title_local_runs_from_different_eras_do_not_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_file = root / "Batman 001 (1940).cbz"
            new_file = root / "Batman 001 (2025).cbz"
            old_file.write_bytes(b"old")
            new_file.write_bytes(b"new")
            parsed = [
                ParsedFile(str(old_file), old_file.name, ".cbz", "Batman", issue="1", year=1940),
                ParsedFile(str(new_file), new_file.name, ".cbz", "Batman", issue="1", year=2025),
            ]
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: parsed, lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "ComicInfo.xml", "title": "Batman",
                    "subtitle": f"Issue {item.issue}", "issue": item.issue,
                    "publication_year": item.year, "record_type": "single_issue",
                },
                "file_cover": None,
            })

            runs = [item for item in store.catalog()["series"] if item["title"] == "Batman"]
            self.assertEqual(len(runs), 2)
            self.assertEqual({int(item["year"]) for item in runs}, {1940, 2025})

    def test_later_issue_publication_year_does_not_split_one_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "Birthright 001 (2014).cbz"
            later = root / "Birthright 031 (2021).cbz"
            first.write_bytes(b"first")
            later.write_bytes(b"later")
            parsed = [
                ParsedFile(str(first), first.name, ".cbz", "Birthright", issue="1", year=2014),
                ParsedFile(str(later), later.name, ".cbz", "Birthright", issue="31", year=2021),
            ]
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: parsed, lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "ComicInfo.xml", "title": "Birthright",
                    "issue": item.issue, "publication_year": item.year,
                    "record_type": "single_issue", "publisher": "Image Comics",
                },
                "file_cover": None,
            })

            runs = [item for item in store.catalog()["series"] if item["title"] == "Birthright"]
            self.assertEqual(len(runs), 1)
            self.assertEqual((int(runs[0]["year"]), runs[0]["owned"]), (2014, 2))

    def test_later_issue_does_not_repopulate_a_legacy_year_split(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "Birthright 001 (2014).cbz"
            later = root / "Birthright 031 (2021).cbz"
            first.write_bytes(b"first")
            store = CatalogStore(root / "catalog.db")

            def enrich(item):
                return {
                    "parsed": item.__dict__, "lookup_identity": item.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "source": "ComicInfo.xml", "title": "Birthright",
                        "issue": item.issue, "publication_year": item.year,
                        "record_type": "single_issue", "publisher": "Image Comics",
                    },
                    "file_cover": None,
                }

            scan = store.begin_scan(str(root), True)
            store.perform_scan(
                scan,
                lambda *_: [ParsedFile(str(first), first.name, ".cbz", "Birthright", issue="1", year=2014)],
                enrich,
            )
            with store._connect() as connection:
                now = "2026-08-28T00:00:00+00:00"
                cursor = connection.execute(
                    """INSERT INTO series_runs(
                           canonical_title, canonical_key, start_year, publisher, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    ("Birthright", "birthright::year:2021", 2021, "Image Comics", now, now),
                )
                legacy_split_id = int(cursor.lastrowid)

            later.write_bytes(b"later")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(
                scan,
                lambda *_: [
                    ParsedFile(str(first), first.name, ".cbz", "Birthright", issue="1", year=2014),
                    ParsedFile(str(later), later.name, ".cbz", "Birthright", issue="31", year=2021),
                ],
                enrich,
            )

            with store._connect() as connection:
                later_run_id = int(connection.execute(
                    """SELECT file_identities.series_run_id
                       FROM file_identities JOIN files ON files.id=file_identities.file_id
                       WHERE files.path=?""",
                    (str(later),),
                ).fetchone()["series_run_id"])
                canonical_run_id = int(connection.execute(
                    "SELECT id FROM series_runs WHERE canonical_key='birthright'"
                ).fetchone()["id"])
            self.assertEqual(later_run_id, canonical_run_id)
            self.assertNotEqual(later_run_id, legacy_split_id)

    def test_collected_edition_year_does_not_create_a_second_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            issue = root / "Example 001 (2014).cbz"
            volume = root / "Example Vol 4 (2022).cbz"
            issue.write_bytes(b"issue")
            volume.write_bytes(b"volume")
            parsed = [
                ParsedFile(str(issue), issue.name, ".cbz", "Example", issue="1", year=2014),
                ParsedFile(str(volume), volume.name, ".cbz", "Example", volume=4, year=2022),
            ]
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: parsed, lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "ComicInfo.xml", "title": "Example",
                    "issue": item.issue, "publication_year": item.year,
                    "record_type": "single_issue" if item.issue else "collected_edition",
                    "publisher": "Example Press",
                },
                "file_cover": None,
            })

            runs = [item for item in store.catalog()["series"] if item["title"] == "Example"]
            self.assertEqual(len(runs), 1)
            self.assertEqual(int(runs[0]["year"]), 2014)
            self.assertEqual(len(runs[0]["fileDetails"]), 2)

    def test_duplicate_run_merge_preserves_issue_and_request_history(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            target = store.ensure_provider_series_run(
                "gcd", "birthright-original", "birth right", 2014, "Image Comics"
            )
            source = store.ensure_provider_series_run(
                "gcd", "birthright-late-split", "Birthright", 2021, "Image Comics"
            )
            store.apply_issue_list(
                int(target["id"]), "gcd", "birthright-original", None,
                [{"number": "1", "provider_id": "birthright-1", "title": "Homecoming"}],
            )
            store.apply_issue_list(
                int(source["id"]), "gcd", "birthright-late-split", None,
                [{"number": "31", "provider_id": "birthright-31", "title": "Family Tree"}],
            )
            store.set_series_monitoring(int(source["id"]))

            now = dt.datetime.now(dt.timezone.utc).isoformat()
            with store._connect() as connection:
                source_issue = connection.execute(
                    "SELECT id FROM issues WHERE series_run_id=? AND issue_number='31'",
                    (int(source["id"]),),
                ).fetchone()
                request_id = int(connection.execute(
                    """INSERT INTO acquisition_requests(
                           scope_type, series_run_id, status, acquisition_preference,
                           include_specials, created_at, updated_at
                       ) VALUES ('series', ?, 'open', 'either', 1, ?, ?)""",
                    (int(source["id"]), now, now),
                ).lastrowid)
                connection.execute(
                    "INSERT INTO acquisition_request_issues(request_id, issue_id, created_at) VALUES (?, ?, ?)",
                    (request_id, int(source_issue["id"]), now),
                )
                job_id = int(connection.execute(
                    """INSERT INTO acquisition_jobs(
                           request_id, issue_id, status, created_at, updated_at
                       ) VALUES (?, ?, 'fulfilled', ?, ?)""",
                    (request_id, int(source_issue["id"]), now, now),
                ).lastrowid)
                connection.execute(
                    "INSERT INTO acquisition_job_events(job_id, status, detail, created_at) VALUES (?, 'fulfilled', 'Imported', ?)",
                    (job_id, now),
                )

            preview = store.series_merge_preview(int(source["id"]), int(target["id"]))
            self.assertTrue(preview["requiresProviderConfirmation"])
            with self.assertRaisesRegex(ValueError, "different provider identities"):
                store.merge_series(int(source["id"]), int(target["id"]))

            result = store.merge_series(
                int(source["id"]), int(target["id"]), allow_provider_conflicts=True
            )
            self.assertEqual(result["status"], "merged")
            catalog = store.catalog()
            birthright_runs = [
                item for item in catalog["series"]
                if item["title"].casefold().replace(" ", "") == "birthright"
            ]
            self.assertEqual(len(birthright_runs), 1)
            self.assertEqual((birthright_runs[0]["title"], int(birthright_runs[0]["year"])), ("Birthright", 2014))
            self.assertEqual({item["number"] for item in birthright_runs[0]["issues"]}, {"1", "31"})
            self.assertEqual(birthright_runs[0]["monitoringStatus"], "monitored")

            with store._connect() as connection:
                request = connection.execute(
                    "SELECT series_run_id FROM acquisition_requests WHERE id=?", (request_id,)
                ).fetchone()
                job = connection.execute(
                    """SELECT acquisition_jobs.issue_id, issues.series_run_id
                       FROM acquisition_jobs JOIN issues ON issues.id=acquisition_jobs.issue_id
                       WHERE acquisition_jobs.id=?""",
                    (job_id,),
                ).fetchone()
                event_count = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM acquisition_job_events WHERE job_id=?", (job_id,)
                ).fetchone()["count"])
            self.assertEqual(int(request["series_run_id"]), int(target["id"]))
            self.assertEqual(int(job["series_run_id"]), int(target["id"]))
            self.assertGreaterEqual(event_count, 2)

    def test_issue_list_rejects_provider_run_from_conflicting_era_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Batman 001 (2025).cbz"
            comic.write_bytes(b"comic")
            item = ParsedFile(str(comic), comic.name, ".cbz", "Batman", issue="1", year=2025)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "ComicInfo.xml", "title": "Batman",
                    "subtitle": "Vast Colors in the Dark", "issue": "1",
                    "publication_year": 2025, "record_type": "single_issue",
                },
                "file_cover": None,
            })
            run_id = int(store.catalog()["series"][0]["id"])

            with self.assertRaisesRegex(ValueError, "1940 publication run"):
                store.apply_issue_list(
                    run_id, "comic_vine", "796", "https://example.test/796",
                    [
                        {"number": "1", "title": "The Legend of the Batman",
                         "publication_year": 1940, "publication_date": "1940-04-25",
                         "provider_id": "100"},
                        {"number": "2", "publication_year": 1940, "provider_id": "101"},
                    ],
                )

            catalog = store.catalog()["series"][0]
            self.assertEqual(int(catalog["year"]), 2025)
            self.assertEqual(catalog["issues"][0]["title"], "Vast Colors in the Dark")
            self.assertIsNone(catalog["issueCatalog"].get("provider"))

    def test_provider_run_mismatch_repair_preserves_owned_issues_and_removes_stale_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Batman 001 (2025).cbz"
            comic.write_bytes(b"comic")
            item = ParsedFile(str(comic), comic.name, ".cbz", "Batman", issue="1", year=2025)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "ComicInfo.xml", "title": "Batman",
                    "subtitle": "Vast Colors in the Dark", "issue": "1",
                    "publication_year": 2025, "record_type": "single_issue",
                },
                "file_cover": None,
            })
            run_id = int(store.catalog()["series"][0]["id"])
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            with store._connect() as connection:
                owned_issue_id = int(connection.execute(
                    "SELECT id FROM issues WHERE series_run_id=? AND issue_number='1'", (run_id,)
                ).fetchone()["id"])
                connection.execute(
                    "UPDATE issues SET publication_year=1940, publication_date='1940-04-25' WHERE id=?",
                    (owned_issue_id,),
                )
                connection.execute(
                    "INSERT INTO issue_provider_ids(issue_id, provider, provider_id, updated_at) VALUES (?, 'comic_vine', '100', ?)",
                    (owned_issue_id, now),
                )
                stale_issue_id = int(connection.execute(
                    """INSERT INTO issues(series_run_id, issue_number, title, publication_year,
                                           publication_date, created_at, updated_at)
                       VALUES (?, '13', 'Batman Makes His Mark', 1941, '1941-03-01', ?, ?)""",
                    (run_id, now, now),
                ).lastrowid)
                connection.execute(
                    "INSERT INTO issue_provider_ids(issue_id, provider, provider_id, updated_at) VALUES (?, 'comic_vine', '113', ?)",
                    (stale_issue_id, now),
                )
                connection.execute(
                    """INSERT INTO series_provider_ids(series_run_id, provider, provider_id,
                                                       confirmed, source, updated_at)
                       VALUES (?, 'comic_vine', '796', 1, 'bad match', ?)""",
                    (run_id, now),
                )
                connection.execute(
                    """INSERT INTO issue_catalog_status(series_run_id, status, provider,
                                                        provider_series_id, issue_count, last_synced_at)
                       VALUES (?, 'complete_to_date', 'comic_vine', '796', 716, ?)""",
                    (run_id, now),
                )
                request_id = int(connection.execute(
                    """INSERT INTO acquisition_requests(scope_type, series_run_id, status,
                                                        acquisition_preference, include_specials,
                                                        created_at, updated_at)
                       VALUES ('series', ?, 'open', 'either', 1, ?, ?)""",
                    (run_id, now, now),
                ).lastrowid)
                connection.execute(
                    "INSERT INTO acquisition_request_issues(request_id, issue_id, created_at) VALUES (?, ?, ?)",
                    (request_id, stale_issue_id, now),
                )
                connection.execute(
                    """INSERT INTO acquisition_jobs(request_id, issue_id, status, created_at, updated_at)
                       VALUES (?, ?, 'queued', ?, ?)""",
                    (request_id, stale_issue_id, now, now),
                )

            repaired = store.repair_provider_run_mismatch(run_id)
            self.assertEqual(repaired["status"], "repaired")
            self.assertEqual(repaired["removedProviderOnlyIssues"], 1)
            self.assertEqual(repaired["removedQueuedJobs"], 1)
            run = store.catalog()["series"][0]
            self.assertEqual(int(run["year"]), 2025)
            self.assertEqual([issue["number"] for issue in run["issues"]], ["1"])
            self.assertEqual(run["issues"][0]["title"], "Vast Colors in the Dark")
            self.assertEqual(run["issues"][0]["publicationYear"], 2025)
            self.assertIsNone(run["issues"][0]["publicationDate"])
            self.assertIsNone(run["issueCatalog"].get("provider"))

    def test_stale_fileless_provider_run_can_be_retired_without_merging_its_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = CatalogStore(root / "catalog.db")
            stale = store.ensure_provider_series_run(
                "comic_vine", "796", "Batman", 1940, "DC Comics"
            )
            store.apply_issue_list(
                int(stale["id"]), "comic_vine", "796", "https://example.test/796",
                [
                    {"number": "1", "title": "The Legend of the Batman",
                     "publication_year": 1940, "publication_date": "1940-04-25",
                     "provider_id": "100"},
                    {"number": "13", "title": "Batman Makes His Mark",
                     "publication_year": 1941, "provider_id": "113"},
                ],
            )
            # Reproduce the legacy hybrid state: the local file's year had
            # overlaid the provider year, while the provider date still proved
            # this was the 1940 catalog.
            with store._connect() as connection:
                connection.execute(
                    """UPDATE issues SET publication_year=2025
                       WHERE series_run_id=? AND issue_number='1'""",
                    (int(stale["id"]),),
                )
            request = store.create_acquisition_request(
                "series", int(stale["id"]), "issues"
            )

            comic = root / "Batman 001 (2025).cbz"
            comic.write_bytes(b"comic")
            item = ParsedFile(str(comic), comic.name, ".cbz", "Batman", issue="1", year=2025)
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "ComicInfo.xml", "title": "Batman",
                    "subtitle": "Vast Colors in the Dark", "issue": "1",
                    "publication_year": 2025, "record_type": "single_issue",
                },
                "file_cover": None,
            })
            current = next(
                run for run in store.catalog()["series"]
                if run["title"] == "Batman" and int(run["year"]) == 2025
            )
            self.assertTrue(
                store.get_series_sync_context(int(current["id"]))["strictYear"]
            )

            repaired = store.retire_stale_provider_run(
                int(stale["id"]), int(current["id"])
            )

            self.assertEqual(repaired["status"], "repaired")
            self.assertEqual(repaired["removedProviderIssues"], 2)
            self.assertEqual(repaired["requestId"], request["id"])
            batman = [run for run in store.catalog()["series"] if run["title"] == "Batman"]
            self.assertEqual(len(batman), 1)
            self.assertEqual((int(batman[0]["year"]), batman[0]["owned"]), (2025, 1))
            self.assertEqual(batman[0]["monitoringStatus"], "monitored")
            self.assertIsNone(batman[0]["issueCatalog"].get("provider"))
            with store._connect() as connection:
                moved_request = connection.execute(
                    "SELECT series_run_id FROM acquisition_requests WHERE id=?",
                    (int(request["id"]),),
                ).fetchone()
                self.assertEqual(int(moved_request["series_run_id"]), int(current["id"]))
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM issues WHERE publication_year=1940"
                    ).fetchone()["count"],
                    0,
                )

    def test_issue_provider_refresh_replaces_stale_identity_without_constraint_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            run = store.ensure_provider_series_run(
                "gcd", "42", "Refresh Example", 2024, "Example Press"
            )
            for provider_issue_id in ("1001", "1002"):
                store.apply_issue_list(
                    int(run["id"]), "gcd", "42",
                    "https://www.comics.org/api/series/42/",
                    [{"number": "1", "provider_id": provider_issue_id}],
                )
            context = store.get_series_sync_context(int(run["id"]))
            self.assertEqual(context["knownGcdIssueIds"], ["1002"])

    def test_rescan_rehomes_misparsed_issue_and_prunes_false_series_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Fables.105.(2011).cbz"
            comic.write_bytes(b"first")
            store = CatalogStore(root / "catalog.db")

            def result(item):
                return {
                    "parsed": item.__dict__, "lookup_identity": item.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": None, "file_cover": None,
                }

            wrong = ParsedFile(str(comic), comic.name, ".cbz", "Fables 105", year=2011)
            first = store.begin_scan(str(root), True)
            store.perform_scan(first, lambda *_: [wrong], result)
            store.enqueue_metadata_enrichment()
            self.assertEqual(store.catalog()["series"][0]["title"], "Fables 105")

            comic.write_bytes(b"second-pass")
            corrected = ParsedFile(str(comic), comic.name, ".cbz", "Fables", issue="105", year=2011)
            second = store.begin_scan(str(root), True)
            store.perform_scan(second, lambda *_: [corrected], result)
            store.enqueue_metadata_enrichment()
            catalog = store.catalog()
            self.assertEqual([(item["title"], item["owned"]) for item in catalog["series"]], [("Fables", 1)])
            self.assertEqual(catalog["enrichment"]["total"], 1)

    def test_damaged_volume_requires_mapped_contents_before_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Example Vol 1.cbz"
            comic.write_bytes(b"broken")
            parsed = ParsedFile(str(comic), comic.name, ".cbz", "Example", volume=1)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [parsed], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__, "embedded_metadata": {},
                "file_health": {
                    "status": "error", "code": "no_image_pages", "message": "No image pages",
                },
                "recommendation": {
                    "title": item.title, "volume": item.volume, "record_type": "edition",
                    "publisher": "Example Press",
                },
                "file_cover": None,
            })
            catalog = store.catalog()
            review = catalog["inbox"][0]
            self.assertEqual((review["category"], review["fileId"]), ("file", catalog["files"][0]["id"]))

            with self.assertRaisesRegex(ValueError, "mapped"):
                store.request_file_replacement(
                    int(review["fileId"]), "wrong_language", "English", "issues"
                )
            self.assertTrue(comic.exists())

    def test_damaged_issue_creates_hidden_replacement_jobs_despite_existing_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Example 001.cbz"
            comic.write_bytes(b"broken")
            parsed = ParsedFile(str(comic), comic.name, ".cbz", "Example", issue="1", year=2024)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [parsed], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__, "embedded_metadata": {},
                "file_health": {"status": "error", "code": "corrupt_archive", "message": "Corrupt"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "publisher": "Example Press", "publication_year": 2024,
                },
                "file_cover": None,
            })
            file_id = int(store.catalog()["files"][0]["id"])

            replacement = store.request_file_replacement(file_id, "corrupt", None, "issues")
            self.assertEqual(replacement["status"], "wanted")
            self.assertEqual(replacement["jobCount"], 1)
            self.assertEqual(replacement["jobs"][0]["status"], "queued")
            self.assertIn("Replacement needed", replacement["jobs"][0]["reason"])
            catalog = store.catalog()
            self.assertEqual(catalog["requests"], [])
            self.assertEqual(catalog["stats"]["openRequests"], 1)

            cancelled = store.update_file_replacement_status(int(replacement["id"]), "cancelled")
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled["jobs"][0]["status"], "cancelled")
            self.assertTrue(comic.exists())

    def test_active_legacy_replacement_is_backfilled_with_durable_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Example 001.cbz"
            comic.write_bytes(b"broken")
            parsed = ParsedFile(str(comic), comic.name, ".cbz", "Example", issue="1", year=2024)
            database = root / "catalog.db"
            store = CatalogStore(database)
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [parsed], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__, "embedded_metadata": {},
                "file_health": {"status": "error", "code": "corrupt_archive", "message": "Corrupt"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "publisher": "Example Press", "publication_year": 2024,
                },
                "file_cover": None,
            })
            file_id = int(store.catalog()["files"][0]["id"])
            with store._connect() as connection:
                connection.execute(
                    """INSERT INTO file_replacement_requests(
                           file_id, reason_code, desired_language, acquisition_preference,
                           status, created_at, updated_at
                       ) VALUES (?, 'corrupt', NULL, 'either', 'wanted', ?, ?)""",
                    (file_id, "2026-08-28T12:00:00+00:00", "2026-08-28T12:00:00+00:00"),
                )

            migrated = CatalogStore(database).catalog()["replacementRequests"][0]
            self.assertIsNotNone(migrated["acquisitionRequestId"])
            self.assertEqual(migrated["jobCount"], 1)
            self.assertEqual(migrated["jobs"][0]["status"], "queued")

    def test_cataloged_file_path_only_returns_present_library_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Example 001.cbz"
            outsider = root / "Not scanned.cbz"
            comic.write_bytes(b"comic")
            outsider.write_bytes(b"outsider")
            parsed = ParsedFile(str(comic), comic.name, ".cbz", "Example", issue="1")
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [parsed], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__, "embedded_metadata": {},
                "file_health": {"status": "ok"},
                "recommendation": {
                    "title": item.title, "issue": item.issue, "record_type": "single_issue",
                },
                "file_cover": None,
            })

            self.assertEqual(store.cataloged_file_path(str(comic)), comic.resolve())
            with self.assertRaisesRegex(ValueError, "not part of the current library"):
                store.cataloged_file_path(str(outsider))
            comic.unlink()
            with self.assertRaisesRegex(ValueError, "could not be found"):
                store.cataloged_file_path(str(comic))

    def test_series_families_group_move_and_split_runs_without_merging_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = [root / "Example Alpha 001 (2020).cbz", root / "Example Beta 001 (2022).cbz"]
            for path in paths:
                path.write_bytes(b"comic")
            parsed = [
                ParsedFile(str(paths[0]), paths[0].name, ".cbz", "Example Alpha", issue="1", year=2020),
                ParsedFile(str(paths[1]), paths[1].name, ".cbz", "Example Beta", issue="1", year=2022),
            ]
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: parsed, lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__, "embedded_metadata": {},
                "file_health": {"status": "ok"},
                "recommendation": {
                    "title": item.title, "issue": item.issue, "record_type": "single_issue",
                    "publisher": "Example Press", "publication_year": item.year,
                },
                "file_cover": None,
            })
            runs = {item["title"]: int(item["id"]) for item in store.catalog()["series"]}
            for title, count, provider_base in (("Example Alpha", 3, 100), ("Example Beta", 2, 200)):
                store.apply_issue_list(
                    runs[title], "gcd", str(provider_base),
                    f"https://www.comics.org/api/series/{provider_base}/",
                    [
                        {"number": str(number), "provider_id": str(provider_base + number),
                         "api_url": f"https://www.comics.org/api/issue/{provider_base + number}/"}
                        for number in range(1, count + 1)
                    ],
                )

            gamma = store.ensure_provider_series_run(
                "gcd", "300", "Example Gamma", 2024, "Example Press"
            )
            store.apply_issue_list(
                int(gamma["id"]), "gcd", "300", "https://www.comics.org/api/series/300/",
                [{"number": str(number), "provider_id": str(300 + number)} for number in range(1, 5)],
                status="complete",
            )
            catalog_only = next(item for item in store.catalog()["series"] if item["id"] == gamma["id"])
            self.assertEqual((catalog_only["owned"], catalog_only["total"]), (0, 4))
            self.assertEqual(catalog_only["fileDetails"], [])

            family = store.create_series_family("Example Universe", list(runs.values()))
            monitoring = store.set_collection_monitoring(
                int(family["id"]), "volumes", include_specials=False
            )
            self.assertEqual(monitoring["acquisitionPreference"], "volumes")
            self.assertFalse(monitoring["includeSpecials"])
            catalog = store.catalog()
            grouped = catalog["families"][0]
            self.assertEqual(grouped["id"], family["id"])
            self.assertEqual(grouped["acquisitionPreference"], "volumes")
            self.assertFalse(grouped["includeSpecials"])
            self.assertEqual(grouped["monitoringStatus"], "monitored")
            self.assertEqual((grouped["runCount"], grouped["owned"], grouped["total"], grouped["unowned"]), (2, 2, 5, 3))
            self.assertEqual({run["title"] for run in grouped["runs"]}, {"Example Alpha", "Example Beta"})
            self.assertEqual(len({issue["id"] for run in grouped["runs"] for issue in run["issues"]}), 5)

            proposal = store.story_structure_proposal(int(family["id"]))
            self.assertEqual([arc["name"] for arc in proposal["arcs"]], ["Example Alpha", "Example Beta"])
            structured = store.set_story_structure(int(family["id"]), [
                {"name": "Opening arc", "type": "main", "runIds": [runs["Example Alpha"]]},
                {"name": "Side stories", "type": "specials", "runIds": [runs["Example Beta"]]},
            ])
            self.assertEqual(structured["arcCount"], 2)
            self.assertEqual(structured["collection"]["structureStatus"], "confirmed")
            self.assertEqual(structured["collection"]["mainArcCount"], 1)
            self.assertEqual(structured["collection"]["specialGroupCount"], 1)
            self.assertEqual(structured["collection"]["storyArcs"][0]["issueCount"], 3)

            second = store.create_series_family("Example Side Stories", [runs["Example Beta"]])
            catalog = store.catalog()
            self.assertEqual({family["name"]: family["runCount"] for family in catalog["families"]}, {
                "Example Side Stories": 1, "Example Universe": 1,
            })
            original = next(item for item in catalog["families"] if item["name"] == "Example Universe")
            self.assertEqual([arc["name"] for arc in original["storyArcs"]], ["Opening arc"])
            store.set_series_run_family(runs["Example Beta"], None)
            catalog = store.catalog()
            self.assertEqual([family["name"] for family in catalog["families"]], ["Example Universe"])
            beta = next(run for run in catalog["series"] if run["title"] == "Example Beta")
            self.assertIsNone(beta["family"])
            self.assertEqual(second["name"], "Example Side Stories")

    def test_collected_edition_title_becomes_an_alias_of_canonical_series(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first_path = root / "lockeandkey_heavenandearth.epub"
            second_path = root / "lockeandkey_vol2_headgames.epub"
            first_path.write_bytes(b"first")
            second_path.write_bytes(b"second")
            parsed = [
                ParsedFile(str(first_path), first_path.name, ".epub", "locke and key heaven and earth"),
                ParsedFile(str(second_path), second_path.name, ".epub", "locke and key", volume=2),
            ]

            def enrich(item):
                title = "Locke and Key: Heaven and Earth" if "heaven" in item.filename else "Locke & Key"
                recommendation = {
                    "title": title, "publisher": "IDW Publishing", "publication_year": 2010,
                    "source": "Open Library", "match_score": 90,
                }
                if "headgames" in item.filename:
                    recommendation["subtitle"] = "Head Games"
                    recommendation["matched_edition"] = {"coverage": [{
                        "series": "Locke & Key: Head Games", "issues": ["1", "2"],
                        "source": "GCD inference", "confidence": "inferred",
                        "source_text": "Story counts agree, but the main-series numbering is not explicit.",
                    }]}
                return {
                    "parsed": item.__dict__, "lookup_identity": item.__dict__,
                    "embedded_metadata": {"source": "EPUB package metadata", "publisher": "IDW Publishing"},
                    "file_health": {"status": "ok", "code": "readable", "message": "Readable"},
                    "recommendation": recommendation,
                    "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: parsed, enrich)
            catalog = store.catalog()
            self.assertEqual(catalog["stats"]["series"], 1)
            self.assertEqual(catalog["series"][0]["title"], "Locke & Key")
            self.assertEqual(catalog["series"][0]["owned"], 2)
            self.assertFalse(catalog["series"][0]["catalogKnown"])
            self.assertEqual(catalog["series"][0]["status"], "unknown")
            self.assertIn("complete-series progress unknown", catalog["series"][0]["ownership"])
            aliases = {alias["name"] for alias in catalog["series"][0]["aliases"]}
            self.assertIn("Locke and Key: Heaven and Earth", aliases)
            self.assertIn("Locke & Key", aliases)
            self.assertEqual(catalog["series"][0]["issues"], [])
            head_games = next(edition for edition in catalog["series"][0]["editions"] if edition["subtitle"] == "Head Games")
            self.assertFalse(head_games["coverageGroups"][0]["resolved"])

            heaven_file_id = next(
                int(file["id"])
                for file in catalog["series"][0]["fileDetails"]
                if "heavenandearth" in file["filename"]
            )
            moved = store.move_file_to_series_run(
                heaven_file_id,
                title="Locke & Key: Heaven and Earth",
                start_year=2017,
                publisher="IDW Publishing",
            )
            self.assertEqual(moved["title"], "Locke & Key: Heaven and Earth")
            separated = store.catalog()
            self.assertEqual(separated["stats"]["series"], 2)
            self.assertEqual(
                {series["title"] for series in separated["series"]},
                {"Locke & Key", "Locke & Key: Heaven and Earth"},
            )
            heaven_run = next(
                series for series in separated["series"]
                if series["title"] == "Locke & Key: Heaven and Earth"
            )
            self.assertEqual([file["id"] for file in heaven_run["fileDetails"]], [str(heaven_file_id)])
            self.assertEqual(heaven_run["inventory"]["editionCount"], 1)
            self.assertEqual(
                store.get_file_workbench(heaven_file_id)["current"]["seriesTitle"],
                "Locke & Key: Heaven and Earth",
            )

            reopened = CatalogStore(root / "catalog.db")
            self.assertEqual(reopened.catalog()["stats"]["series"], 2)

    def test_explicit_collection_coverage_creates_collection_owned_issues(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "southern_bastards_vol2.cbz"
            path.write_bytes(b"collection")
            item = ParsedFile(str(path), path.name, ".cbz", "Southern Bastards", volume=2)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Southern Bastards", "subtitle": "Gridiron", "source": "Open Library",
                    "isbns": ["9781632152695"], "matched_edition": {"coverage": [{
                        "series": "Southern Bastards", "issues": ["5", "6", "7", "8"],
                        "source": "Open Library edition notes", "source_text": "Southern Bastards #5-8",
                    }]},
                },
                "file_cover": None,
            })
            series = store.catalog()["series"][0]
            self.assertEqual([issue["number"] for issue in series["issues"]], ["5", "6", "7", "8"])
            self.assertTrue(all(issue["collectionOwned"] for issue in series["issues"]))
            self.assertEqual(series["editions"][0]["coverageGroups"][0]["issueLabel"], "5–8")
            self.assertTrue(series["editions"][0]["coverageGroups"][0]["resolved"])
            self.assertEqual(series["editions"][0]["contentsStatus"], "verified")
            self.assertEqual(series["editions"][0]["contentsIssueCount"], 4)
            self.assertEqual(series["editions"][0]["seriesPlacementStatus"], "matched")
            self.assertFalse(series["issueCatalog"]["syncReady"])

    def test_native_metron_evidence_survives_restart_without_claiming_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "synthetic_vol1.cbz"
            path.write_bytes(b"collection")
            item = ParsedFile(str(path), path.name, ".cbz", "Synthetic", volume=1)
            detail = {"id": 900, "number": "1", "series": {"id": 90, "name": "Synthetic"},
                      "desc": "Source prose needs validation.", "reprints": [{"id": 101, "issue": "Synthetic #1"}]}
            evidence = native_issue_evidence("metron", detail)
            store = CatalogStore(root / "catalog.db")

            def enrichment(parsed):
                return {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"}, "file_cover": None,
                    "recommendation": {
                        "title": "Synthetic", "source": "Metron", "description": evidence["description"],
                        "provider_evidence": evidence,
                        "matched_edition": {"coverage": _metron_reprint_coverage(detail["reprints"])},
                    },
                }

            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], enrichment)
            reopened = CatalogStore(root / "catalog.db")
            series = reopened.catalog()["series"][0]
            self.assertFalse(series["issues"][0]["collectionOwned"])
            self.assertEqual(series["editions"][0]["contentsIssueCount"], 0)
            self.assertEqual(series["editions"][0]["coverageGroups"][0]["relationKind"], "unknown")
            with reopened._connect() as connection:
                saved = json.loads(connection.execute("SELECT result_json FROM files").fetchone()[0])
                self.assertEqual(saved["recommendation"]["provider_evidence"], evidence)
                source = json.loads(connection.execute("SELECT evidence FROM edition_coverage_claims").fetchone()[0])
                self.assertEqual(source["relationships"][0]["targetProviderId"], "101")

    def test_partial_reprint_evidence_does_not_mark_the_issue_owned(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "example_sampler_vol1.cbz"
            path.write_bytes(b"collection")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", volume=1)
            store = CatalogStore(root / "catalog.db")

            def enrichment(parsed):
                return {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "title": "Example", "subtitle": "Sampler", "source": "Metron",
                        "matched_edition": {"coverage": [{
                            "series": "Example", "issues": ["1"],
                            "source": "Metron reprints", "confidence": "provider confirmed",
                            "source_text": "Includes one story from Example #1",
                            "relation_kind": "partial_story",
                        }]},
                    },
                    "file_cover": None,
                }

            first_scan = store.begin_scan(str(root), True)
            store.perform_scan(first_scan, lambda *_: [item], enrichment)
            first = store.catalog()["series"][0]
            self.assertFalse(first["issues"][0]["collectionOwned"])
            self.assertEqual(first["editions"][0]["contentsStatus"], "partial")
            self.assertEqual(first["editions"][0]["contentsIssueCount"], 0)
            self.assertEqual(
                first["editions"][0]["coverageGroups"][0]["relationKind"],
                "partial_story",
            )

            second_scan = store.begin_scan(str(root), True)
            store.perform_scan(second_scan, lambda *_: [item], enrichment)
            rescanned = store.catalog()["series"][0]
            self.assertEqual(len(rescanned["editions"][0]["coverage"]), 1)
            self.assertFalse(rescanned["issues"][0]["collectionOwned"])

    def test_imported_run_claims_a_clearly_matching_local_volume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "lockeandkey_vol2_headgames.epub"
            path.write_bytes(b"volume")
            parsed = ParsedFile(str(path), path.name, ".epub", "Locke & Key", volume=2)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [parsed], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__, "embedded_metadata": {},
                "file_health": {"status": "ok"}, "file_cover": None,
                "recommendation": {
                    "title": "Locke & Key", "subtitle": "Head Games", "source": "Open Library",
                    "matched_edition": {"coverage": [{
                        "series": "Locke & Key: Head Games", "issues": ["1", "2"],
                        "source": "Publisher statement",
                    }]},
                },
            })
            source_id = int(store.catalog()["series"][0]["id"])
            target = store.ensure_provider_series_run(
                "gcd", "35508", "Locke & Key: Head Games", 2009, "IDW Publishing"
            )
            store.apply_issue_list(
                int(target["id"]), "gcd", "35508", "https://www.comics.org/api/series/35508/",
                [{"number": "1"}, {"number": "2"}], status="complete",
            )
            result = store.reassign_matching_editions(source_id, [target])
            self.assertEqual(result["count"], 1)
            head_games = next(item for item in store.catalog()["series"] if item["id"] == target["id"])
            self.assertEqual(len(head_games["fileDetails"]), 1)
            self.assertTrue(all(issue["collectionOwned"] for issue in head_games["issues"]))
            reopened = CatalogStore(root / "catalog.db")
            persisted = next(item for item in reopened.catalog()["series"] if item["id"] == target["id"])
            self.assertEqual(len(persisted["fileDetails"]), 1)

    def test_manual_collection_contents_override_provider_coverage_and_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "example_vol1.cbz"
            path.write_bytes(b"collection")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", volume=1)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "subtitle": "Book One", "source": "Publisher catalog",
                    "matched_edition": {"coverage": [{
                        "series": "Example", "issues": ["1", "2", "3"],
                        "source": "Publisher collecting statement", "confidence": "publisher confirmed",
                        "source_text": "Collects Example #1-3",
                    }]},
                },
                "file_cover": None,
            })
            catalog = store.catalog()
            series_id = int(catalog["series"][0]["id"])
            file_id = int(catalog["series"][0]["fileDetails"][0]["id"])

            store.set_file_collection_contents(file_id, series_id, ["2"], False, "Contents page omits #2")
            store.set_file_collection_contents(file_id, series_id, ["4"], True, "Verified from contents page")
            catalog = store.catalog()
            ownership = {issue["number"]: issue["collectionOwned"] for issue in catalog["series"][0]["issues"]}
            self.assertFalse(ownership["2"])
            self.assertTrue(ownership["4"])
            workbench = store.get_file_workbench(file_id)
            contents = {item["issueNumber"]: item for item in workbench["collectionContents"]["items"]}
            self.assertFalse(contents["2"]["included"])
            self.assertTrue(contents["4"]["manual"])
            self.assertEqual(workbench["collectionContents"]["includedCount"], 3)

            store.reset_file_collection_contents(file_id)
            catalog = store.catalog()
            ownership = {issue["number"]: issue["collectionOwned"] for issue in catalog["series"][0]["issues"]}
            self.assertTrue(ownership["2"])
            self.assertFalse(ownership["4"])
            self.assertFalse(store.get_file_workbench(file_id)["collectionContents"]["hasOverrides"])

    def test_manual_alias_is_persisted_and_confirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Example.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Example")
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {"title": "Example", "source": "Test", "match_score": 90},
                "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            stored = store.add_series_alias(series_id, "The Example Series")
            self.assertTrue(stored["confirmed"])
            aliases = store.catalog()["series"][0]["aliases"]
            self.assertTrue(any(alias["name"] == "The Example Series" and alias["confirmed"] for alias in aliases))

    def test_incremental_scan_reuses_unchanged_files_and_groups_issues(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = [root / "Example 001 (2024).cbz", root / "Example 002 (2024).cbz"]
            for path in paths:
                path.write_bytes(b"comic")
            parsed = [
                ParsedFile(str(path), path.name, ".cbz", "Example", issue=str(index), year=2024)
                for index, path in enumerate(paths, 1)
            ]
            calls = []

            def enrich(item):
                calls.append(item.path)
                return {
                    "parsed": item.__dict__,
                    "lookup_identity": item.__dict__,
                    "file_health": {"status": "ok", "code": "ok", "message": "Readable"},
                    "embedded_metadata": {},
                    "recommendation": {
                        "title": "Example", "issue": item.issue, "record_type": "single_issue",
                        "publisher": "Example Press", "publication_year": 2024,
                    },
                    "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            first = store.begin_scan(str(root), True)
            store.perform_scan(first, lambda *_: parsed, enrich)
            self.assertEqual(len(calls), 2)
            self.assertEqual(store.get_scan(first)["changed_files"], 2)

            second = store.begin_scan(str(root), True)
            store.perform_scan(second, lambda *_: parsed, enrich)
            self.assertEqual(len(calls), 2)
            self.assertEqual(store.get_scan(second)["reused_files"], 2)

            catalog = store.catalog()
            self.assertEqual(catalog["stats"]["series"], 1)
            self.assertEqual(catalog["stats"]["files"], 2)
            self.assertEqual(catalog["series"][0]["owned"], 2)

    def test_verified_issue_list_exposes_unowned_catalog_issues(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Example 001 (2024).cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1", year=2024)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "source": "Grand Comics Database", "source_id": "1001",
                    "publisher": "Example Press", "publication_year": 2024,
                },
                "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            sync_context = store.get_series_sync_context(series_id)
            self.assertEqual(sync_context["knownGcdIssueIds"], ["1001"])
            self.assertEqual(sync_context["ownedIssueNumbers"], ["1"])
            self.assertTrue(store.catalog()["series"][0]["issueCatalog"]["syncReady"])

            store.apply_issue_list(
                series_id, "gcd", "200", "https://www.comics.org/api/series/200/",
                [
                    {"number": str(number), "provider_id": str(1000 + number),
                     "title": "Opening Chapter" if number == 1 else None,
                     "publication_year": 2024,
                     "api_url": f"https://www.comics.org/api/issue/{1000 + number}/"}
                    for number in range(1, 6)
                ],
                detail="Verified from an owned issue",
            )
            catalog = store.catalog()
            series = catalog["series"][0]
            self.assertEqual((series["owned"], series["total"], series["unowned"]), (1, 5, 4))
            self.assertIsNone(series["missing"])
            self.assertEqual(catalog["stats"]["unownedIssues"], 4)
            self.assertEqual(series["issueCatalog"]["status"], "complete_to_date")
            self.assertEqual(series["issueCatalog"]["missingTitleCount"], 4)
            self.assertEqual(series["issueCatalog"]["missingDateCount"], 0)
            self.assertFalse(series["issueCatalog"]["metadataComplete"])
            self.assertEqual(series["issues"][0]["title"], "Opening Chapter")
            self.assertEqual(series["issues"][0]["publicationYear"], 2024)
            refreshed_context = store.get_series_sync_context(series_id)
            self.assertEqual(refreshed_context["gcdSeriesId"], "200")
            self.assertEqual(len(refreshed_context["gcdIssueEntries"]), 5)
            first_issue_id = int(series["issues"][0]["id"])
            store.update_issue_metadata(first_issue_id, "Corrected Chapter Title", 2025)
            corrected = store.catalog()["series"][0]["issues"][0]
            self.assertEqual(corrected["title"], "Corrected Chapter Title")
            self.assertEqual(corrected["providerTitle"], "Opening Chapter")
            self.assertEqual(corrected["publicationYear"], 2025)
            self.assertTrue(corrected["metadataLocked"])
            store.reset_issue_metadata(first_issue_id)
            restored = store.catalog()["series"][0]["issues"][0]
            self.assertEqual(restored["title"], "Opening Chapter")
            self.assertEqual(restored["publicationYear"], 2024)
            self.assertFalse(restored["metadataLocked"])
            self.assertEqual(
                [issue["number"] for issue in series["issues"] if issue["ownership"] == "unowned"],
                ["2", "3", "4", "5"],
            )
            store.record_issue_sync_error(series_id, "gcd", "temporary rate limit")
            retained = store.catalog()["series"][0]["issueCatalog"]
            self.assertEqual(retained["status"], "complete_to_date")
            self.assertEqual(retained["error"], "temporary rate limit")

    def test_explicit_embedded_issue_match_preserves_external_provider_identity(self):
        cases = (
            ("metron", "https://metron.cloud/issue/151432/", "151432"),
            (
                "comic_vine",
                "https://comicvine.gamespot.com/issue/4000-123456/",
                "123456",
            ),
        )
        for expected_provider, provider_url, expected_id in cases:
            with self.subTest(provider=expected_provider), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / "Batman 001 (2025).cbz"
                path.write_bytes(b"comic")
                item = ParsedFile(str(path), path.name, ".cbz", "Batman", issue="1", year=2025)
                candidate = {
                    "source": "ComicInfo.xml",
                    "source_id": "12829",
                    "record_type": "single_issue",
                    "title": "Batman",
                    "subtitle": "Vast Colors in the Dark",
                    "issue": "1",
                    "publisher": "DC Comics",
                    "publication_year": 2025,
                    "url": provider_url,
                }
                store = CatalogStore(root / "catalog.db")
                scan = store.begin_scan(str(root), True)
                store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": candidate, "candidates": {"embedded": [candidate]},
                    "file_cover": None,
                })
                series = store.catalog()["series"][0]
                self.assertFalse(series["issueCatalog"]["syncReady"])

                file_id = int(store.catalog()["files"][0]["id"])
                selected = store.get_file_workbench(file_id)["candidates"][0]
                store.apply_file_match(file_id, selected["key"])

                issue_catalog = store.catalog()["series"][0]["issueCatalog"]
                self.assertTrue(issue_catalog["syncReady"])
                self.assertEqual(issue_catalog["anchorProviders"], [expected_provider])
                with store._connect() as connection:
                    provider = connection.execute(
                        "SELECT provider, provider_id FROM issue_provider_ids"
                    ).fetchone()
                self.assertEqual(
                    (provider["provider"], provider["provider_id"]),
                    (expected_provider, expected_id),
                )

                reopened = CatalogStore(root / "catalog.db")
                reopened_catalog = reopened.catalog()["series"][0]["issueCatalog"]
                self.assertTrue(reopened_catalog["syncReady"])
                self.assertEqual(reopened_catalog["anchorProviders"], [expected_provider])

    def test_acquisition_request_persists_release_aware_wanted_issues_and_expands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Example 001.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1")
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            today = dt.datetime.now().astimezone().date()
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [
                    {"number": "1", "provider_id": "101", "publication_date": str(today - dt.timedelta(days=30)), "publication_year": today.year},
                    {"number": "2", "provider_id": "102", "publication_date": str(today - dt.timedelta(days=1)), "publication_year": today.year},
                    {"number": "3", "provider_id": "103", "publication_date": str(today + dt.timedelta(days=30)), "publication_year": today.year},
                    {"number": "4", "provider_id": "104", "publication_year": today.year},
                ],
            )
            request = store.create_acquisition_request("series", series_id, "issues")
            self.assertEqual(request["status"], "open")
            self.assertEqual(request["targetIssueCount"], 4)
            self.assertEqual(request["ownedIssueCount"], 1)
            self.assertEqual(request["wantedIssueCount"], 1)
            self.assertEqual(request["upcomingIssueCount"], 1)
            self.assertEqual(request["unknownReleaseIssueCount"], 1)
            self.assertEqual(request["jobCount"], 1)
            self.assertEqual(request["queuedJobCount"], 1)
            self.assertEqual(request["jobs"][0]["issueNumber"], "2")
            self.assertEqual(request["jobs"][0]["status"], "queued")
            self.assertEqual(
                {issue["number"]: issue["acquisitionState"] for issue in request["issues"]},
                {"1": "owned", "2": "wanted", "3": "awaiting_release", "4": "metadata_pending"},
            )
            self.assertEqual(store.catalog()["series"][0]["monitoringStatus"], "monitored")

            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [{"number": "5", "provider_id": "105", "publication_date": str(today - dt.timedelta(days=2)), "publication_year": today.year}],
            )
            reopened = CatalogStore(root / "catalog.db").catalog()["requests"][0]
            self.assertEqual(reopened["targetIssueCount"], 5)
            self.assertEqual(reopened["wantedIssueCount"], 2)
            self.assertEqual(reopened["jobCount"], 2)
            self.assertEqual(reopened["queuedJobCount"], 2)

            job_id = int(reopened["jobs"][0]["id"])
            searching = store.update_acquisition_job(job_id, "searching", "Indexer search started")
            self.assertEqual(searching["status"], "searching")
            self.assertEqual(searching["attemptCount"], 1)
            failed = store.update_acquisition_job(job_id, "failed", "No matching release")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error"], "No matching release")
            persisted_job = next(
                job for job in CatalogStore(root / "catalog.db").catalog()["requests"][0]["jobs"]
                if job["id"] == str(job_id)
            )
            self.assertEqual(persisted_job["status"], "failed")
            self.assertEqual(persisted_job["attemptCount"], 1)

            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            download = store.record_acquisition_download(
                job_id, "SAB-123", "Example.002.2026", "release-key-123"
            )
            self.assertEqual(
                store.rejected_acquisition_release_keys(job_id), set()
            )
            failure = store.record_acquisition_release_failure(
                job_id, "failed-release-key", "Example.002.bad", "Incomplete Usenet post"
            )
            self.assertEqual(failure["failureCount"], 1)
            reopened_store = CatalogStore(root / "catalog.db")
            self.assertEqual(
                reopened_store.rejected_acquisition_release_keys(job_id),
                {"failed-release-key"},
            )
            self.assertEqual(
                reopened_store.rejected_acquisition_releases(job_id)[0]["release_title"],
                "Example.002.bad",
            )
            store.update_acquisition_download(
                int(download["id"]), "imported", sab_storage="/downloads/Example.002.2026",
                local_source="/downloads/Example.002.2026/Example 002.cbz",
                destination="/comics/Example (2026)/Example (2026) #002.cbz",
                source_size=1234, source_sha256="abc123",
            )
            store.update_acquisition_job(job_id, "fulfilled", "Imported and verified")
            imported_job = next(
                job for job in CatalogStore(root / "catalog.db").catalog()["requests"][0]["jobs"]
                if job["id"] == str(job_id)
            )
            self.assertEqual(imported_job["downloadStatus"], "imported")
            self.assertTrue(imported_job["downloadDestination"].endswith("Example (2026) #002.cbz"))

            retry_job_id = int(next(job["id"] for job in reopened["jobs"] if int(job["id"]) != job_id))
            store.update_acquisition_job(retry_job_id, "searching", "Indexer search started")
            store.update_acquisition_job(retry_job_id, "failed", "Prowlarr timed out")
            research = store.retry_acquisition_job(retry_job_id)
            self.assertEqual(research["action"], "research")
            self.assertEqual(research["status"], "queued")

            store.update_acquisition_job(retry_job_id, "grabbed", "Replacement release sent to SABnzbd")
            retry_download = store.record_acquisition_download(
                retry_job_id, "SAB-456", "Example.005.2026"
            )
            store.update_acquisition_download(
                int(retry_download["id"]), "failed",
                local_source="/downloads/Example.005.2026/Example 005.cbz",
                error="Destination was temporarily unavailable",
                failure_stage="import",
            )
            store.update_acquisition_job(
                retry_job_id, "failed", "Destination was temporarily unavailable"
            )
            failed_import = next(
                job for job in store.catalog()["requests"][0]["jobs"]
                if job["id"] == str(retry_job_id)
            )
            self.assertEqual(failed_import["downloadFailureStage"], "import")
            reprocess = store.retry_acquisition_job(retry_job_id)
            self.assertEqual(reprocess["action"], "reprocess")
            self.assertEqual(reprocess["status"], "grabbed")
            pending = next(
                download for download in store.pending_acquisition_downloads()
                if int(download["job_id"]) == retry_job_id
            )
            self.assertEqual(pending["status"], "completed")
            self.assertIsNone(pending["error"])
            self.assertIsNone(pending["failure_stage"])

    def test_up_to_date_run_can_be_followed_and_refreshes_reopen_wanted_issues(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Example 001.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1")
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            today = dt.datetime.now().astimezone().date()
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [{
                    "number": "1", "provider_id": "101",
                    "publication_date": str(today - dt.timedelta(days=30)),
                    "publication_year": today.year,
                }],
            )

            request = store.create_acquisition_request("series", series_id, "issues")
            self.assertEqual(request["status"], "fulfilled")
            self.assertEqual(request["storedStatus"], "open")
            self.assertEqual(request["monitoringStatus"], "monitored")
            self.assertEqual(request["jobCount"], 0)

            store.queue_monitored_series_refresh(series_id)
            claimed = store.claim_monitored_series_refresh()
            self.assertEqual(int(claimed["series_run_id"]), series_id)
            store.finish_monitored_series_refresh(series_id, "gcd")
            self.assertEqual(store.catalog()["series"][0]["monitorRefresh"]["status"], "complete")

            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [{
                    "number": "2", "provider_id": "102",
                    "publication_date": str(today - dt.timedelta(days=1)),
                    "publication_year": today.year,
                }],
            )
            reopened = store.catalog()["requests"][0]
            self.assertEqual(reopened["status"], "open")
            self.assertEqual(reopened["wantedIssueCount"], 1)
            self.assertEqual(reopened["jobs"][0]["issueNumber"], "2")

    def test_mixed_series_tracks_issues_volumes_and_omnibuses_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            issue_path = root / "Example 001 (2024).cbz"
            volume_path = root / "Example Vol 2 TPB.cbz"
            omnibus_path = root / "Example Omnibus Vol 1.cbz"
            for path in (issue_path, volume_path, omnibus_path):
                path.write_bytes(b"comic")
            items = [
                ParsedFile(str(issue_path), issue_path.name, ".cbz", "Example", issue="1", year=2024),
                ParsedFile(str(volume_path), volume_path.name, ".cbz", "Example", volume=2, format="trade paperback"),
                ParsedFile(str(omnibus_path), omnibus_path.name, ".cbz", "Example", volume=1, format="omnibus"),
            ]

            def enrich(item):
                recommendation = {"title": "Example", "publisher": "Example Press", "source": "Test"}
                if item.issue:
                    recommendation.update({"issue": item.issue, "record_type": "single_issue"})
                else:
                    recommendation.update({"format": item.format, "isbns": [f"978000000000{item.volume}"]})
                return {
                    "parsed": item.__dict__, "lookup_identity": item.__dict__, "embedded_metadata": {},
                    "file_health": {"status": "ok"}, "recommendation": recommendation, "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: items, enrich)
            series = store.catalog()["series"][0]
            self.assertEqual(series["inventory"]["directIssueFiles"], 1)
            self.assertEqual(series["inventory"]["editionCount"], 2)
            self.assertEqual(series["inventory"]["editionTypes"], {"omnibus": 1, "collected_volume": 1})
            self.assertEqual(
                {edition["volume"]: edition["editionKind"] for edition in series["editions"]},
                {1: "omnibus", 2: "collected_volume"},
            )
            self.assertIn("Single issues", series["format"])
            self.assertIn("Omnibus", series["format"])
            self.assertIn("Collected volume", series["format"])

    def test_manual_metadata_override_is_locked_and_can_be_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Wrong Name Vol 2.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Wrong Name", volume=2)

            def enrich(parsed):
                return {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "title": "Wrong Name", "publisher": "Wrong Press", "source": "Test",
                        "format": "trade paperback", "isbns": ["9780000000001"],
                    },
                    "candidates": {"test": [{
                        "source": "Test Catalog", "source_id": "right-1", "title": "Right Series",
                        "subtitle": "The Correct Book", "publisher": "Right Press",
                        "publication_year": 2020, "isbns": ["9780000000002"],
                        "cover": "https://covers.example/right.jpg",
                        "matched_edition": {"coverage": [{
                            "series": "Right Series", "issues": ["1", "2"],
                            "source": "Publisher collecting statement", "confidence": "publisher confirmed",
                            "source_text": "Collects Right Series #1-2",
                        }]},
                    }]},
                    "file_cover": {"source": "comic file", "url": "/api/file-cover?path=example"},
                }

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], enrich)
            file_id = int(store.catalog()["series"][0]["fileDetails"][0]["id"])
            workbench = store.get_file_workbench(file_id)
            self.assertEqual(len(workbench["candidates"]), 2)
            self.assertEqual({option["source"] for option in workbench["covers"]["options"]}, {"file", "provider"})
            store.set_file_cover_preference(file_id, "provider", "https://covers.example/right.jpg")
            self.assertEqual(store.catalog()["series"][0]["cover"], "https://covers.example/right.jpg")
            store.set_file_cover_preference(file_id, "file")
            self.assertEqual(store.catalog()["series"][0]["cover"], "/api/file-cover?path=example")
            store.set_file_cover_preference(file_id, "upload")
            self.assertEqual(store.catalog()["series"][0]["cover"], f"/api/v1/files/{file_id}/cover/image")
            store.set_file_cover_preference(file_id, "auto")
            self.assertEqual(store.catalog()["series"][0]["cover"], "/api/file-cover?path=example")

            corrected = store.update_file_metadata(file_id, {
                "seriesTitle": "Right Series", "title": "Right Series",
                "subtitle": "Omnibus One", "recordType": "edition", "volumeNumber": 1,
                "editionKind": "omnibus", "publisher": "Right Press", "publicationYear": 2020,
                "isbn": "9780000000002", "format": "hardcover",
            })
            self.assertIn("seriesTitle", corrected["lockedFields"])
            catalog = store.catalog()
            self.assertEqual(catalog["series"][0]["title"], "Right Series")
            self.assertEqual(catalog["series"][0]["editions"][0]["editionKind"], "omnibus")
            self.assertTrue(catalog["series"][0]["fileDetails"][0]["metadataLocked"])

            second = store.begin_scan(str(root), True)
            store.perform_scan(second, lambda *_: [item], enrich)
            self.assertEqual(store.catalog()["series"][0]["title"], "Right Series")

            store.reset_file_metadata(file_id)
            restored = store.catalog()["series"][0]
            self.assertEqual(restored["title"], "Wrong Name")
            self.assertFalse(restored["fileDetails"][0]["metadataLocked"])

            candidates = store.get_file_workbench(file_id)["candidates"]
            candidate = next(item for item in candidates if item.get("source_id") == "right-1")
            candidate_key = candidate["key"]
            self.assertTrue(candidate["selectionPreview"]["hasCover"])
            self.assertEqual(candidate["selectionPreview"]["associations"][0]["issueLabel"], "1–2")
            matched = store.apply_file_match(file_id, candidate_key)
            self.assertEqual(matched["matchSource"], "Test Catalog")
            self.assertEqual(matched["selectedCandidateKey"], candidate_key)
            self.assertEqual(store.catalog()["series"][0]["title"], "Right Series")
            self.assertTrue(store.catalog()["series"][0]["fileDetails"][0]["metadataLocked"])
            self.assertEqual(
                [issue["number"] for issue in store.catalog()["series"][0]["issues"] if issue["collectionOwned"]],
                ["1", "2"],
            )

            path.write_bytes(b"comic changed")
            third = store.begin_scan(str(root), True)
            store.perform_scan(third, lambda *_: [item], enrich)
            rescanned = store.get_file_workbench(file_id)
            self.assertEqual(rescanned["selectedCandidateKey"], candidate_key)
            self.assertEqual(rescanned["matchSource"], "Test Catalog")

    def test_series_title_correction_heals_connected_volume_set(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            items = []
            for volume in (1, 2, 3):
                path = root / f"alexandada_vol{volume}.cbz"
                path.write_bytes(b"comic")
                items.append(
                    ParsedFile(
                        str(path), path.name, ".cbz", "alexandada", volume=volume
                    )
                )

            def enrich(parsed):
                return {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": None, "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: items, enrich)
            file_id = int(store.catalog()["series"][0]["fileDetails"][0]["id"])

            corrected = store.update_file_metadata(file_id, {
                "seriesTitle": "Alex + Ada", "title": "Alex + Ada Volume 1",
                "recordType": "edition", "volumeNumber": 1,
                "publisher": "Image", "publicationYear": 2014,
            })

            self.assertEqual(corrected["seriesHealing"]["status"], "healed")
            self.assertEqual(corrected["seriesHealing"]["healedFileCount"], 3)
            catalog = store.catalog()
            self.assertEqual(len(catalog["series"]), 1)
            self.assertEqual(catalog["series"][0]["title"], "Alex + Ada")
            self.assertEqual(catalog["series"][0]["inventory"]["editionCount"], 3)

    def test_equivalent_edition_files_are_one_logical_volume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            items = []
            for filename in ("alexandada_vol1.cbz", "AlexAndAda_Vol1_1420484117.cbz"):
                path = root / filename
                path.write_bytes(b"comic")
                items.append(ParsedFile(str(path), path.name, ".cbz", "Alex + Ada", volume=1))

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: items, lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Alex + Ada", "publisher": "Image", "source": "Local metadata",
                },
                "file_cover": None,
            })

            series = store.catalog()["series"][0]
            self.assertEqual(series["inventory"]["editionCount"], 1)
            self.assertEqual(len(series["editions"]), 1)
            self.assertEqual(series["editions"][0]["copyCount"], 2)
            self.assertEqual(len(series["editions"][0]["fileIds"]), 2)
            self.assertEqual(len(series["editions"][0]["files"]), 2)
            self.assertEqual(len(series["editions"][0]["editionIds"]), 2)
            self.assertEqual(series["editions"][0]["contentsStatus"], "unknown")
            self.assertEqual(series["editions"][0]["contentsIssueCount"], 0)

    def test_same_numbered_volume_groups_provider_title_variants_within_one_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            items = []
            for filename in ("alexandada_vol1.cbz", "AlexAndAda_Vol1.cbz"):
                path = root / filename
                path.write_bytes(b"comic")
                items.append(ParsedFile(str(path), path.name, ".cbz", "Alex + Ada", volume=1))

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            titles = iter(("Alex + Ada", "Alex and Ada: The Complete Collection"))
            store.perform_scan(scan, lambda *_: items, lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": next(titles), "publisher": "Image", "source": "Test provider",
                },
                "file_cover": None,
            })

            series = store.catalog()["series"][0]
            self.assertEqual(series["inventory"]["editionCount"], 1)
            self.assertEqual(series["editions"][0]["copyCount"], 2)

    def test_placeholder_run_rename_moves_aliases_and_survives_rescan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            items = []
            for volume in (1, 2):
                path = root / f"wrongtitle_vol{volume}.cbz"
                path.write_bytes(b"comic")
                items.append(
                    ParsedFile(
                        str(path), path.name, ".cbz", "wrongtitle", volume=volume
                    )
                )

            def enrich(parsed):
                return {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": None, "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: items, enrich)
            file_id = int(store.catalog()["series"][0]["fileDetails"][0]["id"])
            corrected = store.update_file_metadata(file_id, {
                "seriesTitle": "Right Series", "title": "Right Series Volume 1",
                "recordType": "edition", "volumeNumber": 1,
            })
            self.assertEqual(corrected["seriesHealing"]["healedFileCount"], 2)

            rescan = store.begin_scan(str(root), True)
            store.perform_scan(rescan, lambda *_: items, enrich)
            catalog = store.catalog()
            self.assertEqual(len(catalog["series"]), 1)
            self.assertEqual(catalog["series"][0]["title"], "Right Series")
            self.assertEqual(catalog["series"][0]["inventory"]["editionCount"], 2)

    def test_fix_match_search_prefers_corrections_and_retains_selectable_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "adafterdeathhc.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "adafterdeathhc", volume=1)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": None, "candidates": {}, "file_cover": None,
            })
            file_id = int(store.catalog()["series"][0]["fileDetails"][0]["id"])

            store.update_file_metadata(file_id, {
                "seriesTitle": "A.D.: After Death", "title": "A.D.: After Death",
                "recordType": "edition", "volumeNumber": 1,
                "publisher": "Image Comics", "publicationYear": 2016,
                "format": "hardcover", "editionKind": "hardcover",
            })
            corrected = store.get_file_workbench(file_id)
            self.assertEqual(corrected["suggestedSearchQuery"], "A.D.: After Death 2016")

            searched = store.retain_file_search_candidates(
                file_id,
                "A.D. After Death 2016",
                [{
                    "source": "Test Catalog", "source_id": "ad-2016",
                    "title": "A.D.: After Death", "subtitle": "The Complete Collection",
                    "publisher": "Image Comics", "publication_year": 2016,
                    "cover": "https://covers.example/ad-after-death.jpg",
                    "matched_edition": {"coverage": []},
                }],
                providers_checked=["Test Catalog"],
                errors=[],
            )
            self.assertEqual(searched["candidateSearch"]["query"], "A.D. After Death 2016")
            self.assertEqual(searched["candidateSearch"]["resultCount"], 1)
            candidate = next(
                option for option in searched["candidates"]
                if option.get("source_id") == "ad-2016"
            )
            matched = store.apply_file_match(file_id, candidate["key"])
            self.assertEqual(matched["matchSource"], "Test Catalog")
            self.assertEqual(matched["current"]["seriesTitle"], "A.D.: After Death")

    def test_metadata_conflict_contains_file_specific_comparison_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Absolute Example 001 (2025).cbz"
            path.write_bytes(b"comic")
            parsed = ParsedFile(str(path), path.name, ".cbz", "Absolute Example", issue="1", year=2025)
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [parsed], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "Grand Comics Database", "title": "Absolute Example", "issue": "1",
                    "record_type": "single_issue", "publisher": "Example Press",
                    "publication_year": 2024, "publication_date": "2024-11-06",
                    "named_contents": ["The First Story"], "format": "saddle-stitched",
                    "url": "https://www.comics.org/issue/1/",
                },
                "embedded_metadata": {
                    "source": "ComicInfo.xml", "series": "Absolute Example", "number": "1",
                    "title": "The First Story", "publisher": "Example Press", "year": "2025",
                    "volume": "2025",
                },
                "file_cover": None,
            })
            conflict = store.catalog()["inbox"][0]
            self.assertEqual(conflict["file"], path.name)
            self.assertEqual(conflict["comparison"]["catalogLabel"], "Grand Comics Database")
            self.assertEqual(conflict["comparison"]["fileLabel"], "ComicInfo.xml")
            rows = {row["field"]: row for row in conflict["comparison"]["rows"]}
            self.assertEqual(rows["Series"]["status"], "match")
            self.assertEqual(rows["Issue"]["status"], "match")
            self.assertEqual(rows["Year"], {
                "field": "Year", "catalog": "2024", "file": "2025", "status": "conflict",
            })
            self.assertEqual(rows["Format"]["status"], "missing")
            self.assertNotIn("Southern Bastards", str(conflict))

    def test_review_resolution_is_invalidated_when_file_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Unknown.cbz"
            path.write_bytes(b"first")

            def parsed_file():
                return ParsedFile(str(path), path.name, ".cbz", "Unknown")

            def enrich(item):
                return {
                    "parsed": item.__dict__, "lookup_identity": item.__dict__,
                    "file_health": {"status": "ok", "code": "ok", "message": "Readable"},
                    "embedded_metadata": {}, "recommendation": None, "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            first = store.begin_scan(str(root), True)
            store.perform_scan(first, lambda *_: [parsed_file()], enrich)
            item = store.catalog()["inbox"][0]
            store.resolve_review(item["path"], item["code"], item["fingerprint"])
            self.assertEqual(store.catalog()["stats"]["needAttention"], 0)

            path.write_bytes(b"changed and longer")
            second = store.begin_scan(str(root), True)
            store.perform_scan(second, lambda *_: [parsed_file()], enrich)
            self.assertEqual(store.catalog()["stats"]["needAttention"], 1)

    def test_provider_cache_round_trips_hits_and_remembered_misses(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            store.provider_cache_put("gcd:abc:/series/1/", "gcd", saved_at=1000.0, data={"id": 1})
            store.provider_cache_put("gcd:abc:/issue/9/", "gcd", saved_at=1000.0, status=404)

            hit = store.provider_cache_get("gcd:abc:/series/1/")
            miss = store.provider_cache_get("gcd:abc:/issue/9/")

            self.assertEqual(hit["data"], {"id": 1})
            self.assertEqual(hit["status"], 200)
            self.assertIsNone(miss.get("data"))
            self.assertEqual(miss["status"], 404)
            self.assertIsNone(store.provider_cache_get("gcd:abc:/nothing/"))

    def test_provider_cache_writes_one_row_rather_than_the_whole_cache(self):
        """Rewriting every entry per miss is what this table replaced."""
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            for index in range(50):
                store.provider_cache_put(
                    f"gcd:abc:/issue/{index}/", "gcd", saved_at=1000.0, data={"id": index}
                )
            store.provider_cache_put("gcd:abc:/issue/7/", "gcd", saved_at=2000.0, data={"id": 7})

            updated = store.provider_cache_get("gcd:abc:/issue/7/")
            untouched = store.provider_cache_get("gcd:abc:/issue/8/")
            self.assertEqual(updated["saved_at"], 2000.0)
            self.assertEqual(untouched["saved_at"], 1000.0)
            self.assertEqual(len(store.provider_cache_load(0.0)), 50)

    def test_provider_cache_load_and_prune_respect_the_cutoff(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            store.provider_cache_put("gcd:abc:/old/", "gcd", saved_at=100.0, data={"a": 1})
            store.provider_cache_put("gcd:abc:/new/", "gcd", saved_at=900.0, data={"a": 2})

            self.assertEqual(set(store.provider_cache_load(500.0)), {"gcd:abc:/new/"})
            self.assertEqual(store.provider_cache_prune(500.0), 1)
            self.assertIsNone(store.provider_cache_get("gcd:abc:/old/"))
            self.assertIsNotNone(store.provider_cache_get("gcd:abc:/new/"))

    def test_importing_the_retired_cache_file_never_overwrites_newer_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            store.provider_cache_put("gcd:abc:/series/1/", "gcd", saved_at=900.0, data={"v": "new"})

            imported = store.provider_cache_import([
                ("gcd:abc:/series/1/", "gcd", 100.0, 200, {"v": "old"}),
                ("gcd:abc:/series/2/", "gcd", 100.0, 200, {"v": "fresh"}),
            ])

            self.assertEqual(imported, 1)
            self.assertEqual(store.provider_cache_get("gcd:abc:/series/1/")["data"], {"v": "new"})
            self.assertEqual(store.provider_cache_get("gcd:abc:/series/2/")["data"], {"v": "fresh"})

    def test_provider_retry_reports_the_wait_that_is_actually_outstanding(self):
        """A flat minute made a 15s cooldown stall enrichment for 60."""
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")

            # Nothing recorded: nothing is waiting, so the default stands.
            self.assertEqual(store.metadata_provider_retry_seconds(["gcd"]), 60)

            store.record_metadata_provider_outcome("gcd", minimum_delay_seconds=15)
            wait = store.metadata_provider_retry_seconds(["gcd"])
            self.assertGreater(wait, 0)
            self.assertLessEqual(wait, 15)

            # The soonest provider wins, and the default caps the answer.
            store.record_metadata_provider_outcome("metron", minimum_delay_seconds=3)
            self.assertLessEqual(store.metadata_provider_retry_seconds(["gcd", "metron"]), 3)
            self.assertEqual(store.metadata_provider_retry_seconds([]), 60)


if __name__ == "__main__":
    unittest.main()


class CatalogSlugGroupTests(unittest.TestCase):
    def test_catalog_survives_a_group_that_has_no_series_run_yet(self):
        """A file not yet matched to a run must not take the catalog down.

        Such a group is keyed by a slug of its title rather than a row id --
        the state every file is in while a scan is still running. Calling
        int() on that slug raised ValueError out of catalog(), so the endpoint
        the whole dashboard depends on answered 500 mid-scan.
        """
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / "comics"
            root.mkdir()
            comic = root / "Ice Cream Man 001.cbz"
            comic.write_bytes(b"comic")
            store = CatalogStore(base / "catalog.db")

            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [
                ParsedFile(str(comic), comic.name, ".cbz", "Ice Cream Man", issue="1")
            ], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": None, "file_cover": None,
            })

            # Drop the identity row, which is how a file looks between being
            # seen by a scan and being matched to a series run.
            with store._connect() as connection:
                connection.execute("DELETE FROM file_identities")

            catalog = store.catalog()

            entry = next(item for item in catalog["series"] if item["title"] == "Ice Cream Man")
            self.assertEqual(entry["id"], "ice-cream-man")
            self.assertIsNone(entry["family"])


class JobsAwaitingReleaseTests(unittest.TestCase):
    def test_a_job_stops_waiting_once_something_is_sent_to_the_download_client(self):
        """The automatic search must not grab a second release for one issue."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Example 001.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1")
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            today = dt.datetime.now().astimezone().date()
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [
                    {"number": "1", "provider_id": "101",
                     "publication_date": str(today - dt.timedelta(days=30)),
                     "publication_year": today.year},
                    {"number": "2", "provider_id": "102",
                     "publication_date": str(today - dt.timedelta(days=1)),
                     "publication_year": today.year},
                ],
            )
            request = store.create_acquisition_request("series", series_id, "issues")
            request_id = int(request["id"])

            waiting = store.acquisition_jobs_awaiting_release(request_id)
            self.assertEqual(len(waiting), 1, "the one wanted issue is waiting")

            job_id = waiting[0]
            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            store.record_acquisition_download(
                job_id, "SAB-1", "Example.002.2026", "release-key-1"
            )

            self.assertEqual(store.acquisition_jobs_awaiting_release(request_id), [])

    def test_another_request_s_jobs_are_not_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            self.assertEqual(store.acquisition_jobs_awaiting_release(9999), [])


class WholeBacklogAwaitingReleaseTests(unittest.TestCase):
    def test_a_cancelled_request_is_not_searched_again(self):
        """Cancelling a request must take its issues off the missing list."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Example 001.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1")
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            today = dt.datetime.now().astimezone().date()
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [
                    {"number": "1", "provider_id": "101",
                     "publication_date": str(today - dt.timedelta(days=30)),
                     "publication_year": today.year},
                    {"number": "2", "provider_id": "102",
                     "publication_date": str(today - dt.timedelta(days=1)),
                     "publication_year": today.year},
                ],
            )
            request = store.create_acquisition_request("series", series_id, "issues")
            request_id = int(request["id"])
            self.assertEqual(len(store.acquisition_jobs_awaiting_release()), 1)

            with store._connect() as connection:
                connection.execute(
                    "UPDATE acquisition_requests SET status='cancelled' WHERE id=?",
                    (request_id,),
                )

            self.assertEqual(store.acquisition_jobs_awaiting_release(), [])


class ReplacementScopeTests(unittest.TestCase):
    def test_a_replacement_sharing_a_run_s_request_only_wants_its_own_issue(self):
        """A replacement must not turn a whole followed run back into wanted items.

        Current code gives each replacement its own acquisition request, but
        libraries carry rows from builds that attached it to the followed run's
        request instead. Matched on the request alone, every issue on that run
        then looked like a replacement target and reconciliation kept it queued
        however much was already owned -- 160 of 161 issues, in the library
        that found this, all re-downloaded.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def parsed_for(number):
                path = root / f"Example {number:03d}.cbz"
                path.write_bytes(b"comic")
                return ParsedFile(str(path), path.name, ".cbz", "Example",
                                  issue=str(number))

            def inventory(parsed):
                return {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "title": "Example", "issue": parsed.issue,
                        "record_type": "single_issue",
                        "publisher": "Example Press", "source": "Test",
                    },
                    "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            files = [parsed_for(n) for n in (1, 2, 3)]
            store.perform_scan(
                store.begin_scan(str(root), True), lambda *_: files, inventory
            )
            series_id = int(store.catalog()["series"][0]["id"])
            today = dt.datetime.now().astimezone().date()
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [
                    {"number": str(n), "provider_id": str(100 + n),
                     "publication_date": str(today - dt.timedelta(days=30)),
                     "publication_year": today.year}
                    for n in (1, 2, 3)
                ],
            )
            run_request = int(
                store.create_acquisition_request("series", series_id, "issues")["id"]
            )

            damaged = next(
                item for item in store.catalog()["series"][0]["fileDetails"]
                if "001" in item["filename"]
            )
            store.request_file_replacement(int(damaged["id"]), "corrupt")

            # The shape older builds left behind: the replacement points at the
            # followed run's request, which covers every issue in the run.
            with store._connect() as connection:
                connection.execute(
                    "UPDATE file_replacement_requests SET acquisition_request_id=?",
                    (run_request,),
                )
            store.reconcile_acquisition_jobs()

            waiting = store.acquisition_jobs_awaiting_release()
            self.assertEqual(
                len(waiting), 1,
                "only the replaced issue is wanted; #2 and #3 are owned",
            )


class UpcomingIsNotMissingTests(unittest.TestCase):
    def test_an_unpublished_issue_is_counted_as_upcoming_not_missing(self):
        """A run still being published is not a library with a gap in it."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Example 001.cbz"
            path.write_bytes(b"comic")
            item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1")
            store = CatalogStore(root / "catalog.db")
            store.perform_scan(
                store.begin_scan(str(root), True), lambda *_: [item], lambda parsed: {
                    "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "title": "Example", "issue": "1", "record_type": "single_issue",
                        "publisher": "Example Press", "source": "Test",
                    },
                    "file_cover": None,
                })
            series_id = int(store.catalog()["series"][0]["id"])
            today = dt.datetime.now().astimezone().date()
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [
                    # Owned.
                    {"number": "1", "provider_id": "101",
                     "publication_date": str(today - dt.timedelta(days=60)),
                     "publication_year": today.year},
                    # Out, and not owned.
                    {"number": "2", "provider_id": "102",
                     "publication_date": str(today - dt.timedelta(days=30)),
                     "publication_year": today.year},
                    # Not out yet.
                    {"number": "3", "provider_id": "103",
                     "publication_date": str(today + dt.timedelta(days=30)),
                     "publication_year": today.year},
                ],
            )
            stats = store.catalog()["stats"]
            self.assertEqual(stats["unownedIssues"], 1, "only the released gap")
            self.assertEqual(stats["unpublishedIssues"], 1, "the unpublished issue")


class FailedReleasesAreSearchedAgainTests(unittest.TestCase):
    """A bad release must not need a person to replace it.

    A download that turned out to be the wrong comic left its job failed with
    a download row against it, so the missing search skipped it on both counts
    and the only way forward was to open the release list by hand.
    """

    def _library_with_one_wanted_issue(self, root):
        path = root / "Example 001.cbz"
        path.write_bytes(b"comic")
        item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1")
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "1", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
        series_id = int(store.catalog()["series"][0]["id"])
        today = dt.datetime.now().astimezone().date()
        store.apply_issue_list(
            series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
            [
                {"number": str(n), "provider_id": str(100 + n),
                 "publication_date": str(today - dt.timedelta(days=30)),
                 "publication_year": today.year}
                for n in (1, 2)
            ],
        )
        store.create_acquisition_request("series", series_id, "issues")
        waiting = store.acquisition_jobs_awaiting_release()
        self.assertEqual(len(waiting), 1)
        return store, waiting[0]

    def _fail_the_download(self, store, job_id, *, blame_release):
        store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
        download = store.record_acquisition_download(
            job_id, "SAB-1", "Thor The Deviants Saga 002", "deviants-key"
        )
        self.assertEqual(store.acquisition_jobs_awaiting_release(), [],
                         "a live download means nothing to search for")
        if blame_release:
            store.record_acquisition_release_failure(
                job_id, "deviants-key", "Thor The Deviants Saga 002",
                "No downloaded comic confidently matched Example #2",
            )
        store.update_acquisition_download(
            int(download["id"]), "failed", error="import failed",
            failure_stage="import",
        )
        store.update_acquisition_job(job_id, "failed", "Import needs attention")

    def test_a_job_whose_release_was_blamed_is_searched_again(self):
        with tempfile.TemporaryDirectory() as folder:
            store, job_id = self._library_with_one_wanted_issue(Path(folder))
            self._fail_the_download(store, job_id, blame_release=True)
            self.assertEqual(
                store.acquisition_jobs_awaiting_release(), [job_id],
                "the release was the problem, so another one should be tried",
            )

    def test_a_local_failure_is_not_retried_with_a_different_release(self):
        """A disk problem meets the same wall whatever is downloaded."""
        with tempfile.TemporaryDirectory() as folder:
            store, job_id = self._library_with_one_wanted_issue(Path(folder))
            self._fail_the_download(store, job_id, blame_release=False)
            self.assertEqual(store.acquisition_jobs_awaiting_release(), [])

    def test_an_imported_issue_is_never_searched_again(self):
        with tempfile.TemporaryDirectory() as folder:
            store, job_id = self._library_with_one_wanted_issue(Path(folder))
            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            download = store.record_acquisition_download(
                job_id, "SAB-2", "Example 002 (2020)", "good-key"
            )
            store.update_acquisition_download(int(download["id"]), "imported")
            self.assertEqual(store.acquisition_jobs_awaiting_release(), [])


class RebuildSeriesRunTests(unittest.TestCase):
    """A run whose data is wrong has to be fixable without touching files.

    A provider refresh fills a blank and never corrects a value, so a title
    written onto a shared issue by a file that turned out to be a different
    comic survived every refresh: "Numéro 2", from a French edition filed as
    Saga #2.
    """

    def _run_with_a_bad_title(self, root):
        path = root / "Example 002.cbz"
        path.write_bytes(b"comic")
        item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="2")
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "2", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
        series_id = int(store.catalog()["series"][0]["id"])
        store.apply_issue_list(
            series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
            [{"number": "2", "provider_id": "102", "publication_year": 2012}],
        )
        with store._connect() as connection:
            connection.execute(
                "UPDATE issues SET title='Numéro 2' WHERE series_run_id=?", (series_id,)
            )
        return store, series_id

    def test_a_refresh_cannot_take_a_wrong_title_back_off(self):
        """The behaviour that made a rebuild necessary."""
        with tempfile.TemporaryDirectory() as folder:
            store, series_id = self._run_with_a_bad_title(Path(folder))
            # The provider has no title for this issue, so COALESCE keeps it.
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [{"number": "2", "provider_id": "102", "publication_year": 2012}],
            )
            issue = store.catalog()["series"][0]["issues"][0]
            self.assertEqual(issue["title"], "Numéro 2")

    def test_rebuilding_clears_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store, series_id = self._run_with_a_bad_title(Path(folder))
            result = store.rebuild_series_run(series_id)
            self.assertGreaterEqual(result["clearedIssues"], 1)
            issue = store.catalog()["series"][0]["issues"][0]
            self.assertIsNone(issue["title"])

    def test_a_manual_correction_survives_the_rebuild(self):
        """Corrections are layered over the derived values, not stored in them."""
        with tempfile.TemporaryDirectory() as folder:
            store, series_id = self._run_with_a_bad_title(Path(folder))
            issue_id = int(store.catalog()["series"][0]["issues"][0]["id"])
            store.update_issue_metadata(issue_id, "The one I typed")
            store.rebuild_series_run(series_id)
            issue = store.catalog()["series"][0]["issues"][0]
            self.assertEqual(issue["title"], "The one I typed")

    def test_an_unknown_run_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            with self.assertRaisesRegex(ValueError, "not found"):
                store.rebuild_series_run(9999)


class WrongLanguageIsFlaggedTests(unittest.TestCase):
    """A comic in a language nobody asked for has to be visible.

    The French DC Saga edition filed as Saga #2 looked correct everywhere:
    right series, right issue number, right place on disk. The only way to
    find it was to open the file.
    """

    def _library_with(self, root, embedded_language, filename="Example 002.cbz"):
        path = root / filename
        path.write_bytes(b"comic")
        item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="2")
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": (
                    {"language": embedded_language} if embedded_language else {}
                ),
                "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "2", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
        return store

    def _language_items(self, store, wanted):
        return [
            item for item in store.catalog(wanted)["inbox"]
            if item["code"] == "wrong_language"
        ]

    def test_a_french_file_is_flagged_when_english_is_wanted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with(Path(folder), "fr")
            items = self._language_items(store, "en")
            self.assertEqual(len(items), 1)
            self.assertIn("French", items[0]["issue"])
            self.assertEqual(items[0]["severity"], "warning")
            self.assertEqual(items[0]["comparison"]["file"], "French")

    def test_the_flag_offers_replace_rather_than_edit(self):
        """The record is right and the file is wrong.

        Library health renders the metadata category without a Replace
        button, so a wrong-language file filed as metadata would tell the
        user to replace it and then offer no way to do so.
        """
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with(Path(folder), "fr")
            self.assertEqual(self._language_items(store, "en")[0]["category"], "file")

    def test_a_regional_tag_still_reads_as_its_language(self):
        """ComicInfo often writes en-GB or pt_BR rather than a bare code."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with(Path(folder), "fr-CA")
            self.assertEqual(len(self._language_items(store, "en")), 1)

    def test_the_wanted_language_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with(Path(folder), "en")
            self.assertEqual(self._language_items(store, "en"), [])

    def test_a_file_that_says_nothing_is_not_guessed_at(self):
        """Most comics state no language; inferring would flag the library."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with(Path(folder), None)
            self.assertEqual(self._language_items(store, "en"), [])

    def test_the_filename_is_read_when_the_file_says_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with(
                Path(folder), None, filename="Example 002 (French).cbz"
            )
            self.assertEqual(len(self._language_items(store, "en")), 1)

    def _library_with_embedded(self, root, embedded, filename="Example 002.cbz"):
        """Like _library_with, but the caller supplies the whole metadata block."""
        path = root / filename
        path.write_bytes(b"comic")
        item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="2")
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": embedded,
                "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "2", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": {"url": "/api/file-cover?path=the-file.cbz"},
            })
        return store

    def _only_series_run(self, store):
        with sqlite3.connect(store.database_path) as connection:
            rows = connection.execute("SELECT id FROM series_runs").fetchall()
        self.assertEqual(len(rows), 1, "the fixture should make exactly one run")
        return int(rows[0][0])

    def _issue_titles(self, store):
        with sqlite3.connect(store.database_path) as connection:
            connection.row_factory = sqlite3.Row
            return [
                row["title"] for row in connection.execute(
                    "SELECT title FROM issues ORDER BY id"
                ).fetchall()
            ]

    def test_a_wrong_language_file_does_not_name_the_issue(self):
        """Rebuilding a run re-derives from the files that are present.

        Saga's French edition supplied the title "Numero 2", so a rebuild
        cleared the bad title and then wrote the same one straight back.
        The run could not be repaired while that file was its input.
        """
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with_embedded(
                Path(folder), {"language": "fr", "title": "Numero 2"},
            )
            store.preferred_language = "en"
            run_id = self._only_series_run(store)
            store.rebuild_series_run(run_id)
            self.assertEqual(self._issue_titles(store), [None])

    def test_a_file_in_the_wanted_language_still_names_the_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with_embedded(
                Path(folder), {"language": "en", "title": "The Chapter"},
            )
            store.preferred_language = "en"
            run_id = self._only_series_run(store)
            store.rebuild_series_run(run_id)
            self.assertEqual(self._issue_titles(store), ["The Chapter"])

    def test_a_wrong_language_file_does_not_become_the_series_cover(self):
        """The French Saga file was filed first and became the run's face."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with_embedded(
                Path(folder), {"language": "fr", "title": "Numero 2"},
            )
            series = store.catalog("en")["series"]
            self.assertEqual(len(series), 1)
            self.assertNotIn(
                "the-file.cbz", str(series[0].get("cover") or ""),
                "the cover of a file in the wrong language",
            )

    def test_a_file_in_the_wanted_language_may_be_the_series_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with_embedded(
                Path(folder), {"language": "en", "title": "The Chapter"},
            )
            series = store.catalog("en")["series"]
            self.assertIn("the-file.cbz", str(series[0].get("cover") or ""))

    def test_no_preference_flags_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._library_with(Path(folder), "fr")
            self.assertEqual(self._language_items(store, ""), [])
            self.assertEqual(
                [i for i in store.catalog()["inbox"] if i["code"] == "wrong_language"],
                [], "no preference passed at all",
            )
