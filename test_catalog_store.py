import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import ParsedFile, _metron_reprint_coverage
from catalog_core_v2.provider_evidence import native_issue_evidence
import catalog_store
from catalog_store import ADMIN_USER_ID, CatalogStore, _parse_timestamp, _utc_now


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

    def test_names_are_compared_without_accents_case_or_punctuation(self):
        from catalog_store import normalized_person
        self.assertEqual(normalized_person("Brian K. Vaughan"), "brian k vaughan")
        self.assertEqual(normalized_person("Rodríguez"), normalized_person("rodriguez"))
        self.assertEqual(normalized_person("  "), "")

    def test_a_runs_creators_come_from_what_its_files_already_say(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comics = []
            for number in ("1", "2"):
                comic = root / f"Chew {number.zfill(3)}.cbz"
                comic.write_bytes(number.encode())
                comics.append((comic, number))
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [
                ParsedFile(str(comic), comic.name, ".cbz", "Chew", issue=number) for comic, number in comics
            ], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {"source": "ComicInfo.xml", "contributors": {
                    "writer": "John Layman", "penciller": "Rob Guillory",
                    # Every variant's artist: kept out of what is searched.
                    "cover_artist": "Someone Else",
                }},
                "file_health": {"status": "ok"}, "recommendation": None, "file_cover": None,
            })
            chew = next(item for item in store.catalog()["series"] if item["title"] == "Chew")
            self.assertEqual(chew["creators"], [
                {"name": "John Layman", "roles": ["writer"]},
                {"name": "Rob Guillory", "roles": ["penciller"]},
            ])
            # Its files already say who made it, so no catalog is asked.
            self.assertIsNone(store.claim_run_creator_sync(["metron", "comic_vine"]))

    def test_a_run_whose_files_credit_nobody_is_asked_about_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            now = _utc_now()
            with store._connect() as connection:
                run_id = connection.execute(
                    """INSERT INTO series_runs(canonical_title, canonical_key, created_at, updated_at)
                       VALUES ('Saga', 'saga', ?, ?)""", (now, now),
                ).lastrowid
                for number, metron_id in (("2", "902"), ("1", "901")):
                    issue_id = connection.execute(
                        """INSERT INTO issues(series_run_id, issue_number, created_at, updated_at)
                           VALUES (?, ?, ?, ?)""", (run_id, number, now, now),
                    ).lastrowid
                    connection.execute(
                        """INSERT INTO issue_provider_ids(issue_id, provider, provider_id, updated_at)
                           VALUES (?, 'metron', ?, ?)""", (issue_id, metron_id, now),
                    )
            self.assertIsNone(store.claim_run_creator_sync(["comic_vine"]), "no Comic Vine id to ask with")
            self.assertEqual(store.claim_run_creator_sync(["metron"]), {
                "seriesRunId": run_id, "provider": "metron", "providerId": "901", "format": "comic",
            })
            self.assertIsNone(store.claim_run_creator_sync(["metron"]), "claimed once, not in a loop")
            store.release_run_creator_sync(run_id)
            self.assertIsNotNone(store.claim_run_creator_sync(["metron"]), "a rate limit lets it be asked again")
            store.set_run_catalog_creators(run_id, "metron", [
                {"name": "Brian K. Vaughan", "roles": ["Writer"]}, {"name": "Fiona Staples", "roles": ["artist"]},
            ])
            with store._connect() as connection:
                credited = connection.execute(
                    """SELECT creators.normalized_name, series_run_creators.role FROM series_run_creators
                       JOIN creators ON creators.id=series_run_creators.creator_id ORDER BY 1"""
                ).fetchall()
            self.assertEqual([tuple(row) for row in credited], [("brian k vaughan", "writer"), ("fiona staples", "artist")])
            store.remove_series_run(run_id)
            with store._connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM series_run_creators").fetchone()[0], 0)

    def test_releases_refused_for_a_misread_scene_name_are_taken_back(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            now = _utc_now()
            with store._connect() as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                for job_id, status in ((761, "queued"), (14, "fulfilled"), (5, "failed")):
                    connection.execute(
                        """INSERT INTO acquisition_jobs(id, request_id, issue_id, status, attempt_count,
                               last_attempt_at, created_at, updated_at)
                           VALUES (?, 1, ?, ?, 4, ?, ?, ?)""", (job_id, job_id, status, now, now, now),
                    )
                failures = [
                    (761, "av-1", "American.Vampire.Vol.1.No.19.Nov.2011.SCAN.Comic.eBook-iNTENSiTY",
                     "No downloaded comic confidently matched American Vampire #19"),
                    (761, "av-2", "American.Vampire.Vol.1.No.19.Nov.2011.SCAN.Comic.eBook-iNTENSiTY-1",
                     "SABnzbd no longer has this download"),
                    (14, "idbol", "Image.Comics.If.Destruction.Be.Our.Lot.No.04.2026.HYBRID.COMIC.eBook-21A1",
                     "No downloaded comic confidently matched If Destruction Be Our Lot #4"),
                    (5, "saga", "Saga 006 (2012) (Digital) (Zone-Empire)",
                     "No downloaded comic confidently matched Saga #7"),
                ]
                for job_id, key, title, error in failures:
                    connection.execute(
                        """INSERT INTO acquisition_release_failures(job_id, release_key, release_title,
                               error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)""",
                        (job_id, key, title, error, now, now),
                    )
                forgotten = CatalogStore._forget_misread_scene_releases(connection)
                left = {row["release_key"] for row in connection.execute(
                    "SELECT release_key FROM acquisition_release_failures")}
                jobs = {row["id"]: (row["status"], row["attempt_count"]) for row in connection.execute(
                    "SELECT id, status, attempt_count FROM acquisition_jobs")}
            self.assertEqual(forgotten, 2)
            # A different failure, and a genuinely wrong comic, are still on record.
            self.assertEqual(left, {"av-2", "saga"})
            self.assertEqual(jobs[761], ("queued", 0), "back to search, with its backoff reset")
            self.assertEqual(jobs[14], ("fulfilled", 4), "an issue already in the library is left alone")
            self.assertEqual(jobs[5], ("failed", 4))

    def test_an_unproven_refusal_sets_a_release_aside_for_a_day_not_for_good(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            now = _utc_now()
            with store._connect() as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """INSERT INTO acquisition_jobs(id, request_id, issue_id, status, created_at, updated_at)
                       VALUES (813, 1, 1, 'queued', ?, ?)""", (now, now),
                )
            store.record_acquisition_release_failure(813, "wrong", "Supergirl 03", "is #3", kind="contradiction")
            store.record_acquisition_release_failure(
                813, "silent", "Supergirl 02", "could not tell", kind="unidentified",
                sab_nzo_id="SAB_9", sab_storage="/data/complete/comics/Supergirl 02",
            )
            self.assertEqual(store.rejected_acquisition_release_keys(813), {"wrong", "silent"})
            yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)).isoformat()
            with store._connect() as connection:
                connection.execute("UPDATE acquisition_release_failures SET updated_at=?", (yesterday,))
            self.assertEqual(store.rejected_acquisition_release_keys(813), {"wrong"},
                             "tomorrow only the proven refusal still bars its release")
            self.assertEqual([row["release_key"] for row in store.rejected_acquisition_releases(813)], ["wrong"])

            kept = store.kept_refused_downloads(job_id=813)
            self.assertEqual([(row["sab_nzo_id"], row["sab_storage"]) for row in kept],
                             [("SAB_9", "/data/complete/comics/Supergirl 02")])
            self.assertEqual(store.kept_refused_downloads(older_than_days=7), [], "kept for a week")
            store.forget_kept_download(int(kept[0]["id"]))
            self.assertEqual(store.kept_refused_downloads(job_id=813), [])
            self.assertEqual(len(store.rejected_acquisition_releases(813)), 1, "the refusal stays on record")

    def test_the_old_checks_vague_refusals_are_taken_back(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            now = _utc_now()
            with store._connect() as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                for job_id, status in ((813, "failed"), (14, "fulfilled")):
                    connection.execute(
                        """INSERT INTO acquisition_jobs(id, request_id, issue_id, status, attempt_count,
                               created_at, updated_at) VALUES (?, 1, ?, ?, 3, ?, ?)""",
                        (job_id, job_id, status, now, now),
                    )
                for job_id, key, error in (
                    (813, "sg", "No downloaded comic confidently matched Supergirl: Woman of Tomorrow #2"),
                    (813, "sab", "SABnzbd reported that the download failed"),
                    (14, "idbol", "No downloaded comic confidently matched If Destruction Be Our Lot #4"),
                ):
                    connection.execute(
                        """INSERT INTO acquisition_release_failures(job_id, release_key, release_title,
                               error, created_at, updated_at) VALUES (?, ?, 'release', ?, ?, ?)""",
                        (job_id, key, error, now, now),
                    )
                forgotten = CatalogStore._forget_vague_refusals(connection)
                left = {row["release_key"] for row in connection.execute(
                    "SELECT release_key FROM acquisition_release_failures")}
                jobs = {row["id"]: (row["status"], row["attempt_count"]) for row in connection.execute(
                    "SELECT id, status, attempt_count FROM acquisition_jobs")}
            self.assertEqual(forgotten, 2)
            self.assertEqual(left, {"sab"}, "a download SABnzbd could not finish is still a real failure")
            self.assertEqual(jobs[813], ("queued", 0))
            self.assertEqual(jobs[14], ("fulfilled", 3))

    def test_a_files_own_comicinfo_outranks_a_lookup_that_names_another_series(self):
        """Supergirl: Woman of Tomorrow #2, dropped into its run's folder by hand.

        Its ComicInfo named the series; a lookup matched it to DC's 1972
        "Supergirl" instead, and the scan made that a run of its own.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "Supergirl Woman of Tomorrow (2021) #001.cbr"
            second = root / "Supergirl Woman of Tomorrow #02.cbz"
            for comic in (first, second):
                comic.write_bytes(comic.name.encode())
            comicinfo = {"source": "ComicInfo.xml", "series": "Supergirl: Woman of Tomorrow",
                         "year": "2021", "publisher": "DC Comics"}
            results = {
                first.name: {"recommendation": None,
                             "embedded_metadata": {**comicinfo, "number": "1"}},
                second.name: {"recommendation": {
                                  "title": "Supergirl", "issue": "2", "record_type": "single_issue",
                                  "publication_year": 1972, "source": "Grand Comics Database",
                                  "publisher": "National Periodical Publications Inc.",
                                  "identity_confidence": {"score": 50},
                              },
                              "embedded_metadata": {**comicinfo, "number": "2"}},
            }
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [
                ParsedFile(str(first), first.name, ".cbr", "Supergirl Woman of Tomorrow", issue="1", year=2021),
                ParsedFile(str(second), second.name, ".cbz", "Supergirl Woman of Tomorrow", issue="2"),
            ], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "file_health": {"status": "ok"}, "file_cover": None,
                **results[Path(item.path).name],
            })
            catalog = store.catalog()
            series = catalog["series"]
            self.assertEqual(len(series), 1, [(item["title"], item["year"]) for item in series])
            self.assertEqual(sorted(issue["number"] for issue in series[0]["issues"]), ["1", "2"])
            self.assertNotIn("1972", [item["year"] for item in series])
            # Nor is the 1972 match held up against #2 as a conflict, and with it
            # set aside #2 is identified by its own metadata, not unmatched.
            self.assertEqual([
                item["code"] for item in catalog["inbox"]
                if item.get("file") == second.name and item.get("code") in {"metadata_conflict", "no_match"}
            ], [])

        # A lookup that agrees with the file is kept, for the provider ids it carries.
        from catalog_store import _trusted_recommendation
        agreeing = {"title": "Department of Truth", "issue": "4"}
        self.assertEqual(_trusted_recommendation(
            {"recommendation": agreeing, "embedded_metadata": {"series": "The Department of Truth"}}),
            agreeing)
        self.assertEqual(_trusted_recommendation(
            {"recommendation": {"title": "Supergirl"}, "embedded_metadata": {"series": "Supergirl: Woman of Tomorrow"}}),
            {})
        self.assertEqual(_trusted_recommendation(
            {"recommendation": {"title": "Supergirl"}, "embedded_metadata": {"series": "Supergirl: Woman of Tomorrow"}},
            {"title": "Supergirl"}),
            {"title": "Supergirl"}, "a lookup agreeing with a confirmed identity is kept")
        # #5, imported and confirmed, matched to 1973's "Supergirl" #5 anyway.
        self.assertEqual(_trusted_recommendation(
            {"recommendation": {"title": "Supergirl"}}, {"seriesTitle": "Supergirl: Woman of Tomorrow"}), {})

    def test_the_scan_schedule_waits_on_the_least_recently_scanned_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            comics, manga = base / "comics", base / "manga"
            comics.mkdir()
            manga.mkdir()
            store = CatalogStore(base / "catalog.db")
            comics_id = store.register_root(str(comics), True)
            manga_id = store.register_root(str(manga), False)
            schedule = store.scan_schedule()
            self.assertEqual([root["recursive"] for root in schedule["roots"]], [True, False])
            self.assertIsNone(schedule["lastScanAt"], "a folder never scanned makes the library due")
            self.assertIsNone(schedule["activeSince"])
            with store._connect() as connection:
                connection.execute("UPDATE library_roots SET last_scan_at=? WHERE id=?",
                                   ("2026-09-15T10:00:00+00:00", comics_id))
                connection.execute("UPDATE library_roots SET last_scan_at=? WHERE id=?",
                                   ("2026-09-15T08:00:00+00:00", manga_id))
            self.assertEqual(store.scan_schedule()["lastScanAt"], "2026-09-15T08:00:00+00:00")
            store.begin_scan(str(comics), True)
            self.assertIsNotNone(store.scan_schedule()["activeSince"], "a queued scan counts as running")

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

    def test_a_later_issue_joins_the_run_whose_catalog_lists_it(self):
        """Nightwing #23 dated 2016 is the relaunch's, not the 2011 run's.

        Only issues #1-#3 carry an era, so every later issue used to fall to the
        oldest run of the title: 137 files of the 2016 relaunch landed in the
        2011 run, which then read as complete.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = {
                (2011, "1"): root / "Nightwing (2011) #001.cbz",
                (2016, "1"): root / "Nightwing (2016) #001.cbz",
                (2016, "23"): root / "Nightwing (2016) #023.cbz",
                # A number both runs reached: the 2011 run ended at #30 in 2014
                # and the relaunch passed #30 in 2017.
                (2016, "27"): root / "Nightwing (2016) #027.cbz",
            }
            for comic in files.values():
                comic.write_bytes(b"comic")

            def parsed_for(keys):
                return [
                    ParsedFile(
                        str(files[key]), files[key].name, ".cbz", "Nightwing",
                        issue=key[1], year=key[0],
                    )
                    for key in keys
                ]

            def enrich(item):
                return {
                    "parsed": item.__dict__, "lookup_identity": item.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "source": "ComicInfo.xml", "title": "Nightwing",
                        "issue": item.issue, "publication_year": item.year,
                        "record_type": "single_issue", "publisher": "DC Comics",
                    },
                    "file_cover": None,
                }

            def scan(keys):
                run = store.begin_scan(str(root), True)
                store.perform_scan(run, lambda *_: parsed_for(keys), enrich)

            def run_of(path):
                with store._connect() as connection:
                    return int(connection.execute(
                        """SELECT file_identities.series_run_id
                           FROM file_identities JOIN files ON files.id=file_identities.file_id
                           WHERE files.path=?""",
                        (str(path),),
                    ).fetchone()["series_run_id"])

            store = CatalogStore(root / "catalog.db")
            scan([(2011, "1")])
            run_2011 = run_of(files[(2011, "1")])
            store.apply_issue_list(
                run_2011, "metron", "987", "https://metron.cloud/api/series/987/",
                [
                    {
                        "number": str(number),
                        "publication_year": 2011 + min(3, (number - 1) // 8),
                        "publication_date": f"{2011 + min(3, (number - 1) // 8)}-03-01",
                        "provider_id": f"2011-{number}",
                    }
                    for number in range(1, 31)
                ],
            )

            scan([(2011, "1"), (2016, "1")])
            run_2016 = run_of(files[(2016, "1")])
            self.assertNotEqual(run_2016, run_2011, "an opening issue may still split an era")
            store.apply_issue_list(
                run_2016, "metron", "981", "https://metron.cloud/api/series/981/",
                [
                    {
                        "number": str(number),
                        "publication_year": 2016 + (number - 1) // 14,
                        "publication_date": f"{2016 + (number - 1) // 14}-08-16",
                        "provider_id": f"2016-{number}",
                    }
                    for number in range(1, 144)
                ],
            )

            scan([(2011, "1"), (2016, "1"), (2016, "23"), (2016, "27")])

            self.assertEqual(run_of(files[(2016, "23")]), run_2016)
            # #27 is in both catalogs, two years apart. The nearer one wins
            # rather than the older run winning for being older.
            self.assertEqual(run_of(files[(2016, "27")]), run_2016)
            self.assertEqual(run_of(files[(2011, "1")]), run_2011)

    def test_issue_rows_a_file_created_cannot_vouch_for_their_own_run(self):
        """Only a provider's catalog may speak for a run's era.

        Otherwise the rows a misfiled file wrote would confirm the run it was
        misfiled into, and the mistake would keep itself alive.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "Birthright 001 (2014).cbz"
            later = root / "Birthright 031 (2021).cbz"
            for comic in (first, later):
                comic.write_bytes(b"comic")

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

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(
                scan,
                lambda *_: [ParsedFile(str(first), first.name, ".cbz", "Birthright", issue="1", year=2014)],
                enrich,
            )
            now = "2026-08-28T00:00:00+00:00"
            with store._connect() as connection:
                split_id = int(connection.execute(
                    """INSERT INTO series_runs(
                           canonical_title, canonical_key, start_year, publisher, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    ("Birthright", "birthright::year:2021", 2021, "Image Comics", now, now),
                ).lastrowid)
                # What a misfiled file leaves behind: an issue row with a
                # matching year and no catalog behind it.
                connection.execute(
                    """INSERT INTO issues(
                           series_run_id, issue_number, publication_year, publication_date,
                           created_at, updated_at
                       ) VALUES (?, '31', 2021, '2021-05-05', ?, ?)""",
                    (split_id, now, now),
                )

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
            self.assertNotEqual(later_run_id, split_id)

    def test_an_import_is_filed_under_the_run_that_was_requested(self):
        """The request knows which run it wanted; the filename cannot."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            opening_2011 = root / "Nightwing (2011) #001.cbz"
            opening_2016 = root / "Nightwing (2016) #001.cbz"
            imported = root / "Nightwing (2016) #023.cbz"
            for comic in (opening_2011, opening_2016, imported):
                comic.write_bytes(b"comic")

            def enrich(item):
                return {
                    "parsed": item.__dict__, "lookup_identity": item.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "source": "ComicInfo.xml", "title": "Nightwing",
                        "issue": item.issue, "publication_year": item.year,
                        "record_type": "single_issue", "publisher": "DC Comics",
                    },
                    "file_cover": None,
                }

            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(
                scan,
                lambda *_: [
                    ParsedFile(str(opening_2011), opening_2011.name, ".cbz", "Nightwing", issue="1", year=2011),
                    ParsedFile(str(opening_2016), opening_2016.name, ".cbz", "Nightwing", issue="1", year=2016),
                ],
                enrich,
            )
            runs = {int(item["year"]): int(item["id"]) for item in store.catalog()["series"]}
            self.assertEqual(sorted(runs), [2011, 2016])

            misparsed = ParsedFile(
                str(imported), imported.name, ".cbz", "Nightwing", issue="23",
            )
            store.ingest_acquisition_import(
                str(root), misparsed,
                {
                    "parsed": misparsed.__dict__, "lookup_identity": misparsed.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": None, "file_cover": None,
                },
                {
                    "seriesTitle": "Nightwing", "recordType": "issue",
                    "issueNumber": "23", "publisher": "DC Comics",
                },
                series_run_id=runs[2016],
            )

            with store._connect() as connection:
                filed = int(connection.execute(
                    """SELECT file_identities.series_run_id
                       FROM file_identities JOIN files ON files.id=file_identities.file_id
                       WHERE files.path=?""",
                    (str(imported),),
                ).fetchone()["series_run_id"])
            self.assertEqual(filed, runs[2016])

    def test_rebuild_removes_issues_a_misfiled_file_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comic = root / "Nightwing (2011) #001.cbz"
            comic.write_bytes(b"comic")
            store = CatalogStore(root / "catalog.db")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [
                ParsedFile(str(comic), comic.name, ".cbz", "Nightwing", issue="1", year=2011),
            ], lambda item: {
                "parsed": item.__dict__, "lookup_identity": item.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "source": "ComicInfo.xml", "title": "Nightwing", "issue": item.issue,
                    "publication_year": item.year, "record_type": "single_issue",
                    "publisher": "DC Comics",
                },
                "file_cover": None,
            })
            series_id = int(store.catalog()["series"][0]["id"])
            store.apply_issue_list(
                series_id, "metron", "987", "https://metron.cloud/api/series/987/",
                [
                    {
                        "number": str(number), "publication_year": 2011,
                        "publication_date": "2011-09-21", "provider_id": f"2011-{number}",
                    }
                    for number in range(1, 31)
                ],
            )
            now = "2026-09-15T00:00:00+00:00"
            with store._connect() as connection:
                connection.execute(
                    """INSERT INTO issues(
                           series_run_id, issue_number, publication_year, created_at, updated_at
                       ) VALUES (?, '104', 2022, ?, ?)""",
                    (series_id, now, now),
                )

            outcome = store.rebuild_series_run(series_id)

            self.assertEqual(outcome["prunedIssues"], 1)
            with store._connect() as connection:
                numbers = [
                    row["issue_number"] for row in connection.execute(
                        "SELECT issue_number FROM issues WHERE series_run_id=?", (series_id,)
                    )
                ]
            self.assertNotIn("104", numbers)
            self.assertEqual(len(numbers), 30)

    def test_one_download_can_be_recorded_against_many_issues(self):
        """A pack answers several jobs; the download still belongs to one."""
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            now = _utc_now()
            with store._connect() as connection:
                run_id = int(connection.execute(
                    """INSERT INTO series_runs(canonical_title, canonical_key, created_at, updated_at)
                       VALUES ('Chew', 'chew', ?, ?)""", (now, now),
                ).lastrowid)
                request_id = int(connection.execute(
                    """INSERT INTO acquisition_requests(
                           scope_type, series_run_id, status, coverage,
                           acquisition_preference, created_at, updated_at
                       ) VALUES ('series', ?, 'open', 'run', 'either', ?, ?)""",
                    (run_id, now, now),
                ).lastrowid)
                jobs = {}
                for number in ("4", "5", "6"):
                    issue_id = int(connection.execute(
                        """INSERT INTO issues(series_run_id, issue_number, created_at, updated_at)
                           VALUES (?, ?, ?, ?)""", (run_id, number, now, now),
                    ).lastrowid)
                    jobs[number] = (int(connection.execute(
                        """INSERT INTO acquisition_jobs(request_id, issue_id, status, created_at, updated_at)
                           VALUES (?, ?, 'queued', ?, ?)""", (request_id, issue_id, now, now),
                    ).lastrowid), issue_id)
                download_id = int(connection.execute(
                    """INSERT INTO acquisition_downloads(
                           job_id, sab_nzo_id, release_title, status, created_at, updated_at
                       ) VALUES (?, 'nzo-1', 'Chew 001-060 (Digital)', 'completed', ?, ?)""",
                    (jobs["4"][0], now, now),
                ).lastrowid)

            wanted = store.wanted_run_issues(request_id)
            self.assertEqual([item["issueNumber"] for item in wanted], ["4", "5", "6"])

            for number in ("5", "6"):
                job_id, issue_id = jobs[number]
                store.record_download_import(
                    download_id, job_id, issue_id, f"/comics/Chew/Chew #{number}.cbz",
                )
            # Re-recording the same issue is the same row, not a second one.
            store.record_download_import(
                download_id, jobs["5"][0], jobs["5"][1], "/comics/Chew/Chew #5.cbz",
            )
            self.assertEqual(store.download_import_counts(), {download_id: 2})

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

    def test_a_download_failure_left_behind_by_an_old_build_is_cleared_on_open(self):
        # The latch is fixed going forward, but rows written before that fix
        # are still on disk: three Saga jobs were fulfilled with a failed
        # import download, so the bell, the rail badge and the Failed tab all
        # reported work that had actually succeeded.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, _series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            download = store.record_acquisition_download(
                job_id, "SAB-9", "Example.002.2026", "release-key-9"
            )
            store.update_acquisition_download(
                int(download["id"]), "failed", error="Import rejected",
                failure_stage="import",
            )
            # Reach past the store to leave exactly the shape an old build did:
            # the job moved on, the download row did not.
            with sqlite3.connect(root / "catalog.db") as raw:
                raw.execute("UPDATE acquisition_jobs SET status='fulfilled' WHERE id=?", (job_id,))
                # Read raw: opening a CatalogStore is what runs the backfill,
                # so the helper cannot observe the state before it.
                stale = raw.execute(
                    "SELECT status FROM acquisition_downloads WHERE job_id=?", (job_id,)
                ).fetchone()
            self.assertEqual(stale[0], "failed")

            reopened = CatalogStore(root / "catalog.db")
            self.assertIsNone(
                self._job_download_status(root, job_id),
                "opening the store must clear a download failure its job has outlived",
            )
            self.assertEqual(reopened.catalog()["stats"]["files"], 1)

    def _run_with_issue_list(self, root):
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
        return store, int(store.catalog()["series"][0]["id"])

    @staticmethod
    def _badge(store):
        return store.catalog()["series"][0]["publicationStatus"]

    def test_a_provider_with_no_end_year_opinion_does_not_erase_a_finished_run(self):
        # Every provider writes here in turn and Comic Vine has no end-year
        # field at all. Taking its silence as "still publishing" is what made a
        # finished run's badge depend on which provider happened to answer last.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, series_id = self._run_with_issue_list(root)
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [{"number": "1", "provider_id": "101"}],
                status="complete", end_evidence={"state": "ended", "year": 2016},
            )
            self.assertEqual(self._badge(store), "completed")

            store.apply_issue_list(
                series_id, "comic_vine", "99", "https://comicvine.example/volume/99/",
                [{"number": "1", "provider_id": "101"}],
                status="complete_to_date", end_evidence={"state": "unknown", "year": None},
            )
            self.assertEqual(
                self._badge(store), "completed",
                "a provider with no end-year field must not walk a finished run back",
            )
            self.assertEqual(
                CatalogStore(root / "catalog.db").catalog()["series"][0]["issueCatalog"]["status"],
                "complete",
                "the coverage claim must survive the same way the badge does",
            )

    def test_a_run_with_no_end_year_evidence_shows_no_badge(self):
        # Absence of evidence was recorded as evidence of ongoing, which is the
        # whole bug. It now says nothing, and ownership counting is unaffected.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, series_id = self._run_with_issue_list(root)
            store.apply_issue_list(
                series_id, "comic_vine", "99", "https://comicvine.example/volume/99/",
                [{"number": "1", "provider_id": "101"}, {"number": "2", "provider_id": "102"}],
                status="complete_to_date", end_evidence={"state": "unknown", "year": None},
            )
            series = store.catalog()["series"][0]
            self.assertEqual(series["publicationStatus"], "unknown")
            self.assertEqual(series["catalogKnown"], True)
            self.assertIsNotNone(series["unowned"])

    def test_a_provider_saying_the_run_continues_is_ongoing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, series_id = self._run_with_issue_list(root)
            store.apply_issue_list(
                series_id, "metron", "12", "https://metron.example/series/12/",
                [{"number": "1", "provider_id": "101"}],
                status="complete_to_date", end_evidence={"state": "ongoing", "year": None},
            )
            self.assertEqual(self._badge(store), "ongoing")

    def test_a_run_finished_before_end_years_were_stored_keeps_its_badge(self):
        # The backfill. 'complete' was only ever written because some provider
        # named a past end year, so the conclusion is evidence enough even
        # though the year itself was thrown away.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, series_id = self._run_with_issue_list(root)
            store.apply_issue_list(
                series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                [{"number": "1", "provider_id": "101"}], status="complete",
            )
            with sqlite3.connect(root / "catalog.db") as raw:
                raw.execute(
                    "UPDATE issue_catalog_status SET run_end_status='unknown', end_year_provider=NULL"
                )
                raw.execute("UPDATE schema_info SET version=28")
            reopened = CatalogStore(root / "catalog.db")
            self.assertEqual(self._badge(reopened), "completed")
            with sqlite3.connect(root / "catalog.db") as raw:
                row = raw.execute(
                    "SELECT run_end_status, end_year, end_year_provider FROM issue_catalog_status"
                ).fetchone()
            self.assertEqual(row[0], "ended")
            self.assertIsNone(row[1], "the year was never stored and must not be invented")
            self.assertEqual(row[2], "gcd")

    def _one_wanted_job(self, root):
        """A library with a single wanted issue, and the job that wants it."""
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
                 "publication_date": str(today - dt.timedelta(days=30)), "publication_year": today.year},
                {"number": "2", "provider_id": "102",
                 "publication_date": str(today - dt.timedelta(days=1)), "publication_year": today.year},
            ],
        )
        request = store.create_acquisition_request("series", series_id, "issues")
        return store, series_id, int(request["jobs"][0]["id"])

    def _job_download_status(self, root, job_id):
        for request in CatalogStore(root / "catalog.db").catalog()["requests"]:
            for job in request["jobs"]:
                if job["id"] == str(job_id):
                    return job["downloadStatus"]
        return "request-not-in-catalog"

    def _job(self, root, job_id):
        for request in CatalogStore(root / "catalog.db").catalog()["requests"]:
            for job in request["jobs"]:
                if job["id"] == str(job_id):
                    return job
        return {}

    def test_an_issue_searched_a_moment_ago_is_not_searched_again(self):
        """The sweep runs every quarter hour; the backoff is what spaces it.

        Without it the scheduled re-search would ask the indexer about the
        same unfindable comic four times an hour, forever.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, _series_id, job_id = self._one_wanted_job(root)
            self.assertIn(job_id, store.acquisition_jobs_awaiting_release(backoff=True),
                          "a job never searched is due immediately")
            store.update_acquisition_job(job_id, "searching", "Searching Prowlarr")
            store.update_acquisition_job(job_id, "queued", "No release found yet")
            self.assertNotIn(job_id, store.acquisition_jobs_awaiting_release(backoff=True))
            self.assertIn(job_id, store.acquisition_jobs_awaiting_release(),
                          "pressing the button still means now")

    def test_a_search_cut_short_by_a_restart_is_due_again_at_once(self):
        """A job left at "searching" is picked up by no pass at all.

        A deploy in the middle of a run's first pass left one issue there,
        and it stayed until someone searched it by hand.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, _series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "searching", "Searching Prowlarr")
            self.assertNotIn(job_id, store.acquisition_jobs_awaiting_release())
            self.assertEqual(store.recover_interrupted_searches(), 1)
            self.assertEqual(self._job(root, job_id)["status"], "queued")
            self.assertIn(job_id, store.acquisition_jobs_awaiting_release(backoff=True),
                          "the attempt that never finished does not count")
            self.assertEqual(store.recover_interrupted_searches(), 0)

    def test_the_wait_grows_with_each_empty_search(self):
        now = _parse_timestamp(_utc_now())
        due = CatalogStore._search_is_due
        ago = lambda minutes: (now - dt.timedelta(minutes=minutes)).isoformat()
        self.assertFalse(due(1, ago(10), now))
        self.assertTrue(due(1, ago(90), now))
        self.assertFalse(due(3, ago(240), now))
        self.assertTrue(due(3, ago(420), now))
        # Past the end of the table the wait stops growing, at a day.
        self.assertFalse(due(99, ago(720), now))
        self.assertTrue(due(99, ago(1500), now))

    def test_a_timestamp_that_will_not_read_does_not_park_an_issue(self):
        """The sweep is idempotent; one extra search is the cheaper mistake."""
        now = _parse_timestamp(_utc_now())
        self.assertTrue(CatalogStore._search_is_due(3, "not-a-date", now))
        self.assertTrue(CatalogStore._search_is_due(3, None, now))

    def test_reconcile_does_not_erase_what_a_search_found_out(self):
        """"No release found" survived one reconcile pass and no longer.

        The searcher writes what it learned; reconcile rewrote every queued
        job's reason to its own boilerplate, so an issue searched five times
        with nothing to show still read "Missing from the library and available
        to search". There was no way to tell it had ever looked.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, _series_id, job_id = self._one_wanted_job(root)
            # A real search: the job goes to searching -- which is what counts
            # the attempt -- and comes back queued with its finding.
            store.update_acquisition_job(job_id, "searching", "Searching Prowlarr")
            store.update_acquisition_job(job_id, "queued", "No release found yet")
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._job(root, job_id)["reason"], "No release found yet")

    def test_a_job_that_has_never_looked_still_tracks_reconcile(self):
        """The guard is about findings, not about queued jobs generally."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, _series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "queued", "Something stale")
            store.reconcile_acquisition_jobs()
            self.assertEqual(
                self._job(root, job_id)["reason"],
                "Missing from the library and available to search",
            )

    def test_a_searched_job_still_moves_when_its_request_ends(self):
        """Keeping the reason must not keep the status.

        Reconcile skips the write when nothing is changing; it must still act
        when something is, or a searched job would be frozen where it stands.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "searching", "Searching Prowlarr")
            store.update_acquisition_job(job_id, "queued", "No release found yet")
            store.stop_series_monitoring(series_id)
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._job(root, job_id)["status"], "cancelled")

    def test_an_arrival_is_dated_when_it_landed_not_when_it_was_last_touched(self):
        # The catalog exposed only updated_at, which unfollowing rewrites for
        # every job at once -- so a run unfollowed after it finished read as
        # thirty comics arriving at that moment, and the Pull List sorted
        # "recently acquired" by when the reader stopped following things.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            download = store.record_acquisition_download(job_id, "SAB-9", "Example.002.2026")
            store.update_acquisition_download(
                int(download["id"]), "imported",
                destination=str(root / "Example 002.cbz"),
            )
            store.update_acquisition_job(job_id, "fulfilled", "Imported and verified")
            arrived_at = self._job(root, job_id)["importedAt"]
            self.assertTrue(arrived_at, "an imported download must say when it landed")

            store.stop_series_monitoring(series_id)
            after = self._job(root, job_id)
            self.assertEqual(after["importedAt"], arrived_at,
                             "unfollowing must not restate when the comic arrived")
            self.assertGreater(after["updatedAt"], arrived_at,
                               "and it must still have touched the job")

    def test_a_download_that_never_landed_has_no_arrival_time(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, _series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            store.record_acquisition_download(job_id, "SAB-10", "Example.002.2026")
            self.assertIsNone(self._job(root, job_id)["importedAt"])

    def test_retrying_into_a_new_search_forgets_the_download_that_failed(self):
        # acquisition_downloads.status='failed' used to be a one-way latch: the
        # job went back to 'queued' and the download row stayed failed, so the
        # bell and the rail badge -- which read downloadStatus -- kept
        # reporting a failure for work that had already been abandoned.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, _series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            download = store.record_acquisition_download(
                job_id, "SAB-1", "Example.002.2026", "release-key-1"
            )
            store.update_acquisition_download(
                int(download["id"]), "failed", error="Incomplete Usenet post",
                failure_stage="download",
            )
            store.update_acquisition_job(job_id, "failed", "Download failed")
            self.assertEqual(self._job_download_status(root, job_id), "failed")

            result = store.retry_acquisition_job(job_id)
            self.assertEqual(result["action"], "research")
            self.assertIsNone(
                self._job_download_status(root, job_id),
                "a job sent back to search must not still carry a failed download",
            )

    def test_a_file_arriving_by_another_route_forgets_the_download_that_failed(self):
        # reconcile marks the job fulfilled when the issue turns up, and never
        # touched the downloads table -- so the job read fulfilled while its
        # downloadStatus stayed failed, which put the row under Following while
        # the notification sent the reader to Wanted.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, series_id, job_id = self._one_wanted_job(root)
            store.update_acquisition_job(job_id, "grabbed", "Release sent to SABnzbd")
            download = store.record_acquisition_download(
                job_id, "SAB-2", "Example.002.2026", "release-key-2"
            )
            store.update_acquisition_download(
                int(download["id"]), "failed", error="Import rejected",
                failure_stage="import",
            )
            store.update_acquisition_job(job_id, "failed", "Import failed")
            self.assertEqual(self._job_download_status(root, job_id), "failed")

            # The issue appears in the library by some other means.
            second = root / "Example 002.cbz"
            second.write_bytes(b"comic")
            found = ParsedFile(str(second), second.name, ".cbz", "Example", issue="2")
            scan = store.begin_scan(str(root), True)
            store.perform_scan(scan, lambda *_: [found], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "2", "record_type": "single_issue",
                    "publisher": "Example Press", "source": "Test",
                },
                "file_cover": None,
            })
            store.reconcile_acquisition_jobs()
            self.assertIsNone(
                self._job_download_status(root, job_id),
                "a fulfilled job must not still carry a failed download",
            )

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


class LibrarySortDataTests(unittest.TestCase):
    """The dashboard sort needs a real timestamp to order by.

    The control shipped as a bare select with no handler, so nothing had
    ever asked the payload for an ordering key.
    """

    def _scan(self, root, names):
        for name in names:
            (root / name).write_bytes(b"comic")
        items = [
            ParsedFile(str(root / name), name, ".cbz", name.split(" ")[0], issue="1")
            for name in names
        ]
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: items, lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": parsed.series, "issue": "1",
                    "record_type": "single_issue", "source": "Test",
                },
                "file_cover": None,
            })
        return store

    def test_every_run_reports_when_its_newest_comic_arrived(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._scan(Path(folder), ["Alpha 001.cbz", "Beta 001.cbz"])
            series = store.catalog()["series"]
            self.assertEqual(len(series), 2)
            for item in series:
                self.assertTrue(
                    item["addedAt"], f"{item['title']} reported no addedAt",
                )
                # Sorting compares these as strings, so they must be
                # comparable ISO timestamps, not display text.
                self.assertRegex(item["addedAt"], r"^\d{4}-\d{2}-\d{2}T")


class IssueFileCoverTests(unittest.TestCase):
    """Each issue shows the cover of the file that actually satisfies it.

    A download that turned out to be the wrong comic was only findable by
    opening the file; nothing in the issue list showed what had landed.
    """

    def _store_with(self, root, file_cover, present=True):
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
                "file_cover": file_cover,
            })
        return store

    def _issues(self, store):
        return [
            issue for series in store.catalog()["series"]
            for issue in (series.get("issues") or [])
        ]

    def test_an_owned_issue_carries_the_cover_from_its_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._store_with(
                Path(folder), {"url": "/api/file-cover?path=example.cbz"},
            )
            owned = [i for i in self._issues(store) if i["directOwned"]]
            self.assertEqual(len(owned), 1)
            self.assertEqual(owned[0]["fileCover"], "/api/file-cover?path=example.cbz")

    def test_a_chosen_file_cover_reaches_the_issue_row(self):
        """The issue row read the embedded cover directly.

        A cover the user picked for a file therefore never appeared beside
        that file's issue, which is the row it is meant to identify.
        """
        with tempfile.TemporaryDirectory() as folder:
            store = self._store_with(
                Path(folder), {"url": "/api/file-cover?path=example.cbz"},
            )
            with sqlite3.connect(store.database_path) as connection:
                file_id = int(connection.execute("SELECT id FROM files").fetchone()[0])
            store.set_file_cover_preference(file_id, "upload")
            owned = [i for i in self._issues(store) if i["directOwned"]]
            self.assertEqual(
                owned[0]["fileCover"], f"/api/v1/files/{file_id}/cover/image",
            )

    def test_an_issue_with_no_file_cover_has_none(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._store_with(Path(folder), None)
            owned = [i for i in self._issues(store) if i["directOwned"]]
            self.assertEqual(len(owned), 1)
            self.assertIsNone(owned[0]["fileCover"])


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


class LibraryFixture(unittest.TestCase):
    """A scanned library of Example issues, for tests that need real files."""

    def _library(self, root, files, preferred_language="", provider_cover="https://provider/example.jpg"):
        parsed = []
        for name, issue, embedded, cover in files:
            (root / name).write_bytes(b"comic")
            parsed.append((ParsedFile(str(root / name), name, ".cbz", "Example", issue=issue), embedded, cover))
        by_path = {item[0].path: (item[1], item[2]) for item in parsed}
        store = CatalogStore(root / "catalog.db")
        store.preferred_language = preferred_language
        store.perform_scan(
            store.begin_scan(str(root), True),
            lambda *_: [item[0] for item in parsed],
            lambda p: {
                "parsed": p.__dict__, "lookup_identity": p.__dict__,
                "embedded_metadata": by_path[p.path][0],
                "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": p.issue,
                    "record_type": "single_issue", "publisher": "Example Press",
                    "source": "Test", "cover": provider_cover,
                },
                "file_cover": {"url": by_path[p.path][1]} if by_path[p.path][1] else None,
            })
        return store

    def _run_id(self, store):
        with sqlite3.connect(store.database_path) as connection:
            return int(connection.execute("SELECT id FROM series_runs").fetchone()[0])

    def _file_id(self, store, filename):
        with sqlite3.connect(store.database_path) as connection:
            return int(connection.execute(
                "SELECT id FROM files WHERE filename=?", (filename,)
            ).fetchone()[0])

    def _series(self, store):
        return store.catalog()["series"][0]

    def _three_files(self, root, **kwargs):
        return self._library(root, [
            ("Example 001.cbz", "1", {}, "/api/file-cover?path=one.cbz"),
            ("Example 002.cbz", "2", {}, "/api/file-cover?path=two.cbz"),
            ("Example 003.cbz", "3", {}, "/api/file-cover?path=three.cbz"),
        ], **kwargs)



class SeriesCoverPreferenceTests(LibraryFixture):
    """A run's face was whatever the alphabetically-first file carried.

    That is how a French DC Saga edition became the face of an English
    Image run, and there was no way to say otherwise.
    """

    def test_a_header_background_can_only_come_from_this_run(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            files = store.series_backdrop_files(run)
            self.assertEqual([item["filename"] for item in files],
                             ["Example 001.cbz", "Example 002.cbz", "Example 003.cbz"])
            self.assertIsNone(store.series_backdrop_preference(run))
            store.set_series_backdrop(run, int(files[1]["id"]), "12.jpg", "chosen", "sig")
            self.assertEqual(store.series_backdrop_preference(run), {
                "fileId": files[1]["id"], "member": "12.jpg", "source": "chosen", "fileSignature": "sig",
            })
            with self.assertRaises(ValueError):
                store.set_series_backdrop(run, 999999, "1.jpg", "chosen")
            with self.assertRaises(ValueError):
                store.set_series_backdrop(run, int(files[0]["id"]), "1.jpg", "sideways")
            store.clear_series_backdrop(run)
            self.assertIsNone(store.series_backdrop_preference(run))
            with self.assertRaises(LookupError):
                store.series_backdrop_files(run + 1000)
            self.assertEqual(store.library_file_path(int(files[0]["id"])).name, "Example 001.cbz")
            with self.assertRaises(LookupError):
                store.library_file_path(999999)

    def test_options_name_the_issue_each_cover_came_from(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            options = store.get_series_cover_workbench(
                self._run_id(store))["covers"]["options"]
            files = [o for o in options if o["source"] == "file"]
            self.assertEqual([o["label"] for o in files],
                             ["Issue #1", "Issue #2", "Issue #3"])
            self.assertEqual(len({o["optionId"] for o in files}), 3,
                             "each file needs its own option id")
            providers = [o for o in options if o["source"] == "provider"]
            self.assertEqual(len(providers), 1, "one provider cover, deduped")

    def test_choosing_a_cover_makes_it_the_run_face(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            self.assertIn("one.cbz", self._series(store)["cover"])
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 003.cbz"))
            self.assertIn("three.cbz", self._series(store)["cover"])

    def test_the_chosen_cover_leads_and_the_fallbacks_remain(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            before = self._series(store)["coverCandidates"]
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 003.cbz"))
            after = self._series(store)["coverCandidates"]
            self.assertEqual(after[0], self._series(store)["cover"])
            # CoverArt walks this on error, so the chain must not shrink.
            self.assertEqual(set(before) - set(after), set())

    def test_a_pick_survives_a_rescan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._three_files(root)
            run = self._run_id(store)
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 003.cbz"))
            items = [
                ParsedFile(str(root / name), name, ".cbz", "Example", issue=issue)
                for name, issue in [("Example 001.cbz", "1"), ("Example 002.cbz", "2"), ("Example 003.cbz", "3")]
            ]
            store.perform_scan(
                store.begin_scan(str(root), True), lambda *_: items, lambda p: {
                    "parsed": p.__dict__, "lookup_identity": p.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "title": "Example", "issue": p.issue,
                        "record_type": "single_issue", "source": "Test",
                    },
                    "file_cover": {"url": f"/api/file-cover?path={p.filename}"},
                })
            self.assertIn(
                "three.cbz", self._series(store)["cover"],
                "the run should still wear the cover that was chosen",
            )
            self.assertEqual(self._series(store)["coverPreference"], "file")

    def test_a_pick_falls_back_when_its_comic_leaves_the_library(self):
        """Files are soft-deleted, so ON DELETE CASCADE never fires here."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._three_files(root)
            run = self._run_id(store)
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 003.cbz"))
            (root / "Example 003.cbz").unlink()
            remaining = [
                ParsedFile(str(root / name), name, ".cbz", "Example", issue=issue)
                for name, issue in [("Example 001.cbz", "1"), ("Example 002.cbz", "2")]
            ]
            store.perform_scan(
                store.begin_scan(str(root), True), lambda *_: remaining, lambda p: {
                    "parsed": p.__dict__, "lookup_identity": p.__dict__,
                    "embedded_metadata": {}, "file_health": {"status": "ok"},
                    "recommendation": {
                        "title": "Example", "issue": p.issue,
                        "record_type": "single_issue", "source": "Test",
                    },
                    "file_cover": {"url": f"/api/file-cover?path={p.filename}"},
                })
            series = self._series(store)
            self.assertTrue(series["cover"], "the run should still show a cover")
            self.assertNotIn("003", series["cover"])

    def test_a_comic_from_another_run_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            with self.assertRaises(ValueError):
                store.set_series_cover_preference(
                    self._run_id(store), "file", file_id=98765)

    def test_a_cover_from_outside_this_run_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            with self.assertRaises(ValueError):
                store.set_series_cover_preference(
                    self._run_id(store), "provider",
                    cover_url="https://elsewhere.example/art.jpg")

    def test_an_unknown_source_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            with self.assertRaises(ValueError):
                store.set_series_cover_preference(self._run_id(store), "nonsense")

    def test_choosing_again_replaces_rather_than_stacks(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 002.cbz"))
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 003.cbz"))
            with sqlite3.connect(store.database_path) as connection:
                rows = connection.execute(
                    "SELECT COUNT(*) FROM series_cover_preferences").fetchone()[0]
            self.assertEqual(rows, 1)
            self.assertIn("three.cbz", self._series(store)["cover"])

    def test_going_back_to_automatic_restores_the_derived_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 003.cbz"))
            store.set_series_cover_preference(run, "auto")
            self.assertIn("one.cbz", self._series(store)["cover"])
            self.assertEqual(self._series(store)["coverPreference"], "auto")

    def test_an_explicit_pick_overrides_the_wrong_language_guard(self):
        """The guard stops a French file being promoted silently.

        It must not stop the user saying so on purpose -- otherwise the
        picker shows a cover, accepts the click, and changes nothing.
        """
        with tempfile.TemporaryDirectory() as folder:
            store = self._library(Path(folder), [
                ("Example 001.cbz", "1", {}, "/api/file-cover?path=one.cbz"),
                ("Example 002.cbz", "2", {"language": "fr"}, "/api/file-cover?path=french.cbz"),
            ], preferred_language="en")
            run = self._run_id(store)
            self.assertNotIn(
                "french.cbz", " ".join(self._series(store)["coverCandidates"]),
                "the automatic chain must still exclude it",
            )
            options = store.get_series_cover_workbench(run)["covers"]["options"]
            french = [o for o in options if "french.cbz" in o["url"]]
            self.assertEqual(len(french), 1, "it must still be offered")
            self.assertIn("French", french[0]["detail"], "and say what it is")
            store.set_series_cover_preference(
                run, "file", file_id=self._file_id(store, "Example 002.cbz"))
            self.assertIn("french.cbz", self._series(store)["cover"])


class ReadingProgressTests(LibraryFixture):
    """Where a comic was left, kept between sittings."""

    def test_a_page_is_kept_per_file_and_the_last_write_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            first = self._file_id(store, "Example 001.cbz")
            self.assertIsNone(store.reading_progress(first, user_id=ADMIN_USER_ID))
            kept = store.set_reading_progress(first, 4, 24, "sig-a", user_id=ADMIN_USER_ID)
            self.assertEqual((kept["page"], kept["pageCount"]), (4, 24))
            self.assertIsNone(kept["finishedAt"])
            self.assertEqual(store.reading_progress(first, user_id=ADMIN_USER_ID)["page"], 4)
            # Turning another page overwrites rather than accumulating rows.
            store.set_reading_progress(first, 5, 24, "sig-a", user_id=ADMIN_USER_ID)
            self.assertEqual(store.reading_progress(first, user_id=ADMIN_USER_ID)["page"], 5)
            self.assertEqual(len(store.recent_reading(user_id=ADMIN_USER_ID)), 1)
            # The panel on the page rides along, and is the first unless said.
            self.assertEqual(store.reading_progress(first, user_id=ADMIN_USER_ID)["panel"], 0)
            self.assertEqual(store.set_reading_progress(first, 5, 24, "sig-a", panel=3, user_id=ADMIN_USER_ID)["panel"], 3)
            with self.assertRaises(ValueError):
                store.set_reading_progress(first, 5, 24, "sig-a", panel=-1, user_id=ADMIN_USER_ID)

            finished = store.set_reading_progress(first, 23, 24, "sig-a", finished=True, user_id=ADMIN_USER_ID)
            self.assertIsNotNone(finished["finishedAt"])
            # Opening it again puts it back in progress.
            self.assertIsNone(store.set_reading_progress(first, 2, 24, "sig-a", user_id=ADMIN_USER_ID)["finishedAt"])

            with self.assertRaises(ValueError):
                store.set_reading_progress(first, 24, 24, "sig-a", user_id=ADMIN_USER_ID)
            with self.assertRaises(ValueError):
                store.set_reading_progress(first, -1, 24, "sig-a", user_id=ADMIN_USER_ID)

            store.clear_reading_progress(first, user_id=ADMIN_USER_ID)
            self.assertIsNone(store.reading_progress(first, user_id=ADMIN_USER_ID))

    def test_what_was_read_lately_comes_back_newest_first_with_its_run(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = str(self._run_id(store))
            first = self._file_id(store, "Example 001.cbz")
            second = self._file_id(store, "Example 002.cbz")
            store.set_reading_progress(first, 3, 24, "sig-a", user_id=ADMIN_USER_ID)
            store.set_reading_progress(second, 1, 24, "sig-b", user_id=ADMIN_USER_ID)
            recent = store.recent_reading(user_id=ADMIN_USER_ID)
            self.assertEqual([item["filename"] for item in recent],
                             ["Example 002.cbz", "Example 001.cbz"])
            self.assertEqual({item["seriesRunId"] for item in recent}, {run},
                             "each one says which run it belongs to")
            self.assertEqual(recent[0]["seriesTitle"], "Example")
            self.assertEqual(recent[0]["issueNumber"], "2", "and which issue of it")
            self.assertEqual(store.recent_reading(1, user_id=ADMIN_USER_ID)[0]["filename"], "Example 002.cbz")

    def test_a_comic_that_left_the_library_is_not_offered_to_continue(self):
        """Half-read and gone is not something to carry on with."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            first = self._file_id(store, "Example 001.cbz")
            store.set_reading_progress(first, 3, 24, "sig-a", user_id=ADMIN_USER_ID)
            with sqlite3.connect(store.database_path) as connection:
                connection.execute("UPDATE files SET present=0 WHERE id=?", (first,))
            self.assertEqual(store.recent_reading(user_id=ADMIN_USER_ID), [])
            # Still remembered, so putting the file back resumes it.
            self.assertEqual(store.reading_progress(first, user_id=ADMIN_USER_ID)["page"], 3)

    def test_an_issue_knows_its_file_even_when_no_cover_can_be_drawn(self):
        """The file id and the cover are claimed from the same query, and it
        used to be the cover's slot that decided both. A file whose cover
        cannot be resolved is still a file that can be read."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._library(
                Path(folder), [("Example 001.cbz", "1", {}, None)], provider_cover=None)
            issue = store.catalog()["series"][0]["issues"][0]
            self.assertIsNone(issue["fileCover"], "nothing to draw")
            self.assertEqual(issue["fileId"], str(self._file_id(store, "Example 001.cbz")),
                             "but something to read")

    def test_a_run_file_says_which_issue_it_is(self):
        """Without this a Read button can only name a scene-release filename."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            details = store.catalog()["series"][0]["fileDetails"]
            self.assertEqual(
                {item["filename"]: item["issueNumber"] for item in details},
                {"Example 001.cbz": "1", "Example 002.cbz": "2", "Example 003.cbz": "3"},
            )

    def test_a_run_can_read_against_its_medium(self):
        """Manga reads right to left, except an English edition of one, which
        is printed the other way round. No provider records which, so the
        direction is kept apart from the medium rather than derived from it."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            self.assertIsNone(store.catalog()["series"][0]["readingDirection"],
                              "nothing said, so the reader follows the medium")

            store.set_series_format(run, "manga", "ltr")
            series = store.catalog()["series"][0]
            self.assertEqual((series["medium"], series["readingDirection"]), ("manga", "ltr"))

            # Filing it again says nothing about direction, so it keeps it.
            store.set_series_format(run, "comic")
            self.assertEqual(store.catalog()["series"][0]["readingDirection"], "ltr")

            store.set_series_format(run, "comic", None)
            self.assertIsNone(store.catalog()["series"][0]["readingDirection"],
                              "and it can be handed back to the medium")

            with self.assertRaises(ValueError):
                store.set_series_format(run, "comic", "sideways")
            with self.assertRaises(LookupError):
                store.set_series_format(run + 1000, "comic")

    def test_the_reader_is_told_which_way_a_run_turns(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            self.assertIsNone(store.run_reading_files(run)["readingDirection"])
            store.set_series_format(run, "manga", "ltr")
            reading = store.run_reading_files(run)
            self.assertEqual((reading["medium"], reading["readingDirection"]), ("manga", "ltr"))

    def test_a_run_keeps_its_own_rating_apart_from_its_issues(self):
        """A run whose issues average four has not been given four stars by
        anyone. Collapsing the two would invent an opinion nobody held."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            series = store.catalog()["series"][0]
            self.assertIsNone(series["yourRating"])
            self.assertIsNone(series["issueRating"], "nothing rated says nothing")

            issues = series["issues"]
            store.set_rating("issue", int(issues[0]["id"]), 4, user_id=ADMIN_USER_ID)
            store.set_rating("issue", int(issues[1]["id"]), 5, user_id=ADMIN_USER_ID)
            series = store.catalog()["series"][0]
            self.assertIsNone(series["yourRating"], "the run itself is still unrated")
            self.assertEqual(series["issueRating"], {"average": 4.5, "count": 2})
            self.assertEqual(store.catalog()["series"][0]["issues"][0]["yourRating"], 4)

            store.set_rating("series", run, 3, user_id=ADMIN_USER_ID)
            series = store.catalog()["series"][0]
            self.assertEqual(series["yourRating"], 3, "and its own rating is its own")
            self.assertEqual(series["issueRating"]["average"], 4.5)

    def test_a_rating_can_be_taken_back(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            issue = int(store.catalog()["series"][0]["issues"][0]["id"])
            store.set_rating("issue", issue, 4, user_id=ADMIN_USER_ID)
            # Pressing the star you already gave means "actually, no".
            self.assertIsNone(store.set_rating("issue", issue, None, user_id=ADMIN_USER_ID)["yourRating"])
            self.assertIsNone(store.catalog()["series"][0]["issues"][0]["yourRating"])
            self.assertIsNone(store.catalog()["series"][0]["issueRating"])

    def test_only_whole_stars_between_one_and_five_are_a_rating(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            issue = int(store.catalog()["series"][0]["issues"][0]["id"])
            for bad in (0, 6, -1, "three", 3.5, True):
                with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                    store.set_rating("issue", issue, bad, user_id=ADMIN_USER_ID)
            with self.assertRaises(ValueError):
                store.set_rating("publisher", issue, 3, user_id=ADMIN_USER_ID)
            with self.assertRaises(LookupError):
                store.set_rating("issue", 999999, 3, user_id=ADMIN_USER_ID)
            with self.assertRaises(LookupError):
                store.set_rating("series", 999999, 3, user_id=ADMIN_USER_ID)

    def test_deleting_an_issue_takes_its_rating_with_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            issue = int(store.catalog()["series"][0]["issues"][0]["id"])
            store.set_rating("issue", issue, 5, user_id=ADMIN_USER_ID)
            with sqlite3.connect(store.database_path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("DELETE FROM issues WHERE id=?", (issue,))
                left = connection.execute("SELECT COUNT(*) FROM issue_ratings").fetchone()[0]
            self.assertEqual(left, 0, "no rating pointing at an issue that has gone")

    def test_reading_order_is_issue_order_with_volumes_kept_apart(self):
        """The backdrop picker's list sorts by float(issue_number), so every
        volume becomes infinity and lands after the last issue. A reader
        walking that list offers an omnibus as the next issue."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._library(Path(folder), [
                ("Example 010.cbz", "10", {}, None),
                ("Example 002.cbz", "2", {}, None),
                ("Example 001AU.cbz", "1AU", {}, None),
                ("Example 001.cbz", "1", {}, None),
            ])
            run = self._run_id(store)
            order = [item["issueNumber"] for item in store.run_reading_files(run)["issues"]]
            self.assertEqual(order, ["1", "1AU", "2", "10"],
                             "natural order, the same one the issue list is sorted by")
            # The same key the Issues tab uses, so the two never disagree.
            self.assertEqual(order, sorted(order, key=catalog_store._natural_issue_key))
            with self.assertRaises(LookupError):
                store.run_reading_files(run + 1000)

    def test_reading_and_backdrop_lists_hold_the_same_files(self):
        """They may differ in order and in how they are split. They must never
        differ in which comics they contain -- the backdrop picker validates
        against its own list, and a file missing from one is a bug in both."""
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            reading = store.run_reading_files(run)
            self.assertEqual(
                {item["id"] for item in reading["issues"] + reading["volumes"]},
                {item["id"] for item in store.series_backdrop_files(run)},
            )

    def test_a_run_reports_where_each_of_its_comics_was_left(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run = self._run_id(store)
            first = self._file_id(store, "Example 001.cbz")
            second = self._file_id(store, "Example 002.cbz")
            self.assertEqual(store.reading_progress_for_run(run, user_id=ADMIN_USER_ID), {})

            # The signature the reader stores is the file's mtime and size in
            # hex; the scan recorded both, so a match can be decided in SQL.
            signature = self._scanned_signature(store, first)
            store.set_reading_progress(first, 3, 24, signature, user_id=ADMIN_USER_ID)
            store.set_reading_progress(second, 1, 24, "stale-signature", user_id=ADMIN_USER_ID)
            progress = store.reading_progress_for_run(run, user_id=ADMIN_USER_ID)

            self.assertEqual(progress[str(first)]["page"], 3)
            self.assertFalse(progress[str(first)]["stale"])
            self.assertTrue(progress[str(second)]["stale"],
                            "a comic replaced since it was read says so")
            self.assertNotIn(str(self._file_id(store, "Example 003.cbz")), progress,
                             "a comic never opened has no entry")
            self.assertEqual(store.reading_progress_for_run(run + 1000, user_id=ADMIN_USER_ID), {})

    def _scanned_signature(self, store, file_id):
        with sqlite3.connect(store.database_path) as connection:
            return connection.execute(
                "SELECT printf('%x-%x', mtime_ns, size_bytes) FROM files WHERE id=?", (file_id,)
            ).fetchone()[0]

    def test_deleting_a_file_takes_its_place_with_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            first = self._file_id(store, "Example 001.cbz")
            store.set_reading_progress(first, 3, 24, "sig-a", user_id=ADMIN_USER_ID)
            with sqlite3.connect(store.database_path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("DELETE FROM files WHERE id=?", (first,))
            self.assertIsNone(store.reading_progress(first, user_id=ADMIN_USER_ID), "no row pointing at nothing")


class ReaderProfileTests(LibraryFixture):
    """Several people reading one library (schema 49): each has their own place
    and their own ratings; the library, and the admin, are shared by all."""

    def _reader(self, store, name="Sam", **kwargs):
        return store.create_user(name, **kwargs)["id"]

    def test_a_library_from_before_profiles_is_the_admins_and_a_copy_is_kept_first(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            first = self._file_id(store, "Example 001.cbz")
            second = self._file_id(store, "Example 002.cbz")
            issue = int(store.catalog()["series"][0]["issues"][0]["id"])
            run = self._run_id(store)
            path = store.database_path
            # The library as schema 48 left it: personal tables keyed by what
            # was read, no profiles at all.
            with sqlite3.connect(path) as connection:
                connection.executescript(f"""
                    DROP TABLE reading_progress; DROP TABLE issue_ratings; DROP TABLE series_run_ratings;
                    DROP TABLE user_prefs; DROP TABLE users;
                    CREATE TABLE reading_progress (
                        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                        page INTEGER NOT NULL, panel INTEGER NOT NULL DEFAULT 0, page_count INTEGER NOT NULL,
                        file_signature TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                        updated_at TEXT NOT NULL);
                    CREATE INDEX reading_progress_recent ON reading_progress(updated_at DESC);
                    CREATE TABLE issue_ratings (issue_id INTEGER PRIMARY KEY REFERENCES issues(id) ON DELETE CASCADE,
                        rating INTEGER NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE series_run_ratings (series_run_id INTEGER PRIMARY KEY REFERENCES series_runs(id) ON DELETE CASCADE,
                        rating INTEGER NOT NULL, updated_at TEXT NOT NULL);
                    INSERT INTO reading_progress VALUES ({first}, 4, 2, 24, 'sig-a', 't0', NULL, 't1');
                    INSERT INTO reading_progress VALUES ({second}, 23, 0, 24, 'sig-b', 't0', 't2', 't2');
                    INSERT INTO issue_ratings VALUES ({issue}, 5, 't3');
                    INSERT INTO series_run_ratings VALUES ({run}, 3, 't4');
                    UPDATE schema_info SET version=48;
                """)
            migrated = CatalogStore(path)
            self.assertTrue(path.with_name(f"{path.name}.pre-v49").is_file(), "the library as it was, kept first")
            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    "SELECT user_id, file_id, page, panel, page_count, file_signature, finished_at, updated_at "
                    "FROM reading_progress ORDER BY file_id"
                ).fetchall()
                ratings = connection.execute("SELECT user_id, issue_id, rating FROM issue_ratings").fetchall()
                runs = connection.execute("SELECT user_id, series_run_id, rating FROM series_run_ratings").fetchall()
                version = connection.execute("SELECT version FROM schema_info").fetchone()[0]
            self.assertEqual(rows, [
                (ADMIN_USER_ID, first, 4, 2, 24, "sig-a", None, "t1"),
                (ADMIN_USER_ID, second, 23, 0, 24, "sig-b", "t2", "t2"),
            ], "every place, exactly as it was, and the admin's")
            self.assertEqual(ratings, [(ADMIN_USER_ID, issue, 5)])
            self.assertEqual(runs, [(ADMIN_USER_ID, run, 3)])
            self.assertEqual(version, catalog_store.SCHEMA_VERSION)
            self.assertEqual(migrated.user(ADMIN_USER_ID)["role"], "admin")
            self.assertEqual(migrated.reading_progress(first, user_id=ADMIN_USER_ID)["page"], 4)
            # And once is once: a second start changes nothing.
            again = CatalogStore(path)
            self.assertEqual(len(again.recent_reading(user_id=ADMIN_USER_ID)), 2)
            self.assertEqual(again.catalog()["series"][0]["yourRating"], 3)

    def test_a_profile_picture_and_reading_activity(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            first = self._file_id(store, "Example 001.cbz")
            second = self._file_id(store, "Example 002.cbz")
            sam = self._reader(store)
            self.assertIsNone(store.user(sam)["avatar"])
            pictured = store.set_user_avatar(sam, "upload")
            self.assertTrue(pictured["avatar"].startswith(f"/api/v1/profiles/{sam}/avatar?v="))
            self.assertIsNone(store.set_user_avatar(sam, None)["avatar"])
            store.set_reading_progress(first, 23, 24, "sig", finished=True, user_id=sam)
            store.set_reading_progress(second, 4, 20, "sig", user_id=sam)
            store.set_reading_progress(second, 9, 20, "sig", user_id=ADMIN_USER_ID)
            issue = int(store.catalog()["series"][0]["issues"][0]["id"])
            store.set_rating("issue", issue, 5, user_id=sam)
            activity = store.reading_activity(sam)
            self.assertEqual({key: activity["stats"][key] for key in ("issuesRead", "inProgress", "runs", "pagesRead", "rated")},
                             {"issuesRead": 1, "inProgress": 1, "runs": 1, "pagesRead": 24 + 5, "rated": 1},
                             "a finished comic counts whole, a started one to its page; the admin's reading is not Sam's")
            self.assertEqual([item["fileId"] for item in activity["history"]], [str(second), str(first)], "newest first")
            # A library from before pictures (49) gains the columns.
            with sqlite3.connect(store.database_path) as connection:
                connection.execute("ALTER TABLE users DROP COLUMN avatar_updated_at")
                connection.execute("ALTER TABLE users DROP COLUMN avatar_source")
                connection.execute("UPDATE schema_info SET version=49")
            self.assertIsNone(CatalogStore(store.database_path).user(sam)["avatar"])

    def test_each_reader_has_their_own_place_and_their_own_ratings(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            first = self._file_id(store, "Example 001.cbz")
            issue = int(store.catalog()["series"][0]["issues"][0]["id"])
            sam = self._reader(store)
            store.set_reading_progress(first, 10, 24, "sig", user_id=ADMIN_USER_ID)
            store.set_reading_progress(first, 2, 24, "sig", user_id=sam)
            self.assertEqual(store.reading_progress(first, user_id=ADMIN_USER_ID)["page"], 10)
            self.assertEqual(store.reading_progress(first, user_id=sam)["page"], 2, "the same comic, two places")
            store.set_rating("issue", issue, 2, user_id=sam)
            self.assertIsNone(store.catalog(rater_id=ADMIN_USER_ID)["series"][0]["issues"][0]["yourRating"])
            self.assertEqual(store.catalog(rater_id=sam)["series"][0]["issues"][0]["yourRating"], 2)
            self.assertIsNone(store.catalog(rater_id=None)["series"][0]["issueRating"], "asked for nobody's, gets none")
            store.clear_reading_progress(first, user_id=sam)
            self.assertIsNotNone(store.reading_progress(first, user_id=ADMIN_USER_ID), "clearing mine leaves yours")
            # A reader removed takes only what was theirs.
            store.set_reading_progress(first, 3, 24, "sig", user_id=sam)
            store.set_user_prefs(sam, {"panelMode": True})
            store.delete_user(sam)
            self.assertIsNone(store.user(sam))
            self.assertEqual(store.reading_progress(first, user_id=ADMIN_USER_ID)["page"], 10)
            with sqlite3.connect(store.database_path) as connection:
                left = connection.execute("SELECT COUNT(*) FROM reading_progress WHERE user_id=?", (sam,)).fetchone()[0]
                prefs = connection.execute("SELECT COUNT(*) FROM user_prefs WHERE user_id=?", (sam,)).fetchone()[0]
            self.assertEqual((left, prefs), (0, 0))

    def test_the_library_always_has_a_guarded_admin(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            admin = store.user(ADMIN_USER_ID)
            self.assertEqual((admin["name"], admin["role"], admin["hasPin"], admin["hasPassword"]), ("Admin", "admin", False, False))
            sam = self._reader(store)
            self.assertEqual(store.reader_count(), 1)
            with self.assertRaises(ValueError):
                store.update_user(ADMIN_USER_ID, role="reader")
            with self.assertRaises(ValueError):
                store.update_user(ADMIN_USER_ID, disabled=True)
            with self.assertRaises(ValueError):
                store.delete_user(ADMIN_USER_ID)
            # A second admin makes the first one's demotion possible.
            store.update_user(sam, role="admin")
            store.update_user(ADMIN_USER_ID, role="reader")
            self.assertEqual(store.user(ADMIN_USER_ID)["role"], "reader")

    def test_a_new_password_or_a_lost_role_ends_every_sign_in_a_pin_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            sam = self._reader(store, login_name="sam")
            version = store.user(sam)["sessionVersion"]
            store.update_user(sam, pinHash="scrypt$1234")
            self.assertEqual(store.user(sam)["sessionVersion"], version, "a PIN is not a sign-in")
            store.update_user(sam, passwordHash="scrypt$new")
            self.assertEqual(store.user(sam)["sessionVersion"], version + 1)
            store.update_user(sam, disabled=True)
            self.assertEqual(store.user(sam)["sessionVersion"], version + 2)
            self.assertEqual(store.bump_session_version(sam), version + 3)
            self.assertEqual(store.user_by_login("SAM")["id"], sam, "sign-in names ignore case")
            self.assertEqual(store.user_secrets(sam)["passwordHash"], "scrypt$new")
            self.assertNotIn("passwordHash", store.user(sam), "hashes never leave with a profile")

    def test_profile_names_colours_and_sign_in_names_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            self._reader(store, "Sam", login_name="sam")
            with self.assertRaises(ValueError):
                self._reader(store, "Also Sam", login_name="Sam")
            with self.assertRaises(ValueError):
                self._reader(store, "   ")
            with self.assertRaises(ValueError):
                self._reader(store, "Kid", colour="chartreuse")
            with self.assertRaises(ValueError):
                self._reader(store, "Kid", login_name="two words")
            kid = self._reader(store, "  Little   Reader ", colour="teal")
            self.assertEqual(store.user(kid)["name"], "Little Reader")
            with self.assertRaises(ValueError):
                store.update_user(kid, favourite="blue")
            with self.assertRaises(ValueError):
                store.set_user_prefs(kid, {"blob": "x" * 9000})
            self.assertEqual(store.set_user_prefs(kid, {"panelMode": True}), {"panelMode": True})


class IssueFileCountTests(LibraryFixture):
    """How many single issues a run owns, for the grid to know whether
    anything is left unread once the finished ones are counted."""

    def test_every_run_reports_how_many_issues_it_holds(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            self.assertEqual(store.issue_file_counts_by_run(), {str(self._run_id(store)): 3})

    def test_a_library_with_nothing_scanned_says_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            self.assertEqual(store.issue_file_counts_by_run(), {})


class PagePanelTests(LibraryFixture):
    """Where a page's panels are, kept by the page's member name like a backdrop."""

    def test_a_pages_panels_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            file_id = self._file_id(store, "Example 001.cbz")
            self.assertIsNone(store.page_panels(file_id, "p1.jpg"), "nobody has looked yet")
            panels = [{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.3}]
            store.set_page_panels(file_id, "p1.jpg", "sig-1", "auto", panels, True)
            self.assertEqual(store.page_panels(file_id, "p1.jpg"), {
                "fileSignature": "sig-1", "source": "auto", "segmented": True, "panels": panels,
            })
            store.set_page_panels(file_id, "p1.jpg", "sig-2", "manual", [], False)
            self.assertEqual(store.page_panels(file_id, "p1.jpg")["source"], "manual", "the same page, corrected")

    def test_a_vision_models_readings_from_before_46_are_asked_again(self):
        # Before 46 a model's boxes were believed on coverage alone, and a
        # grid one guessed through an eight-panel page was kept as its
        # layout. Every vision reading goes; the local tiers' rows stand.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._three_files(root)
            file_id = self._file_id(store, "Example 001.cbz")
            store.set_page_panels(file_id, "p1.jpg", "sig", "vlm", [], False)
            store.set_page_panels(file_id, "p2.jpg", "sig", "vlm", [{"x": 0, "y": 0, "w": 1, "h": 0.5}, {"x": 0, "y": 0.5, "w": 1, "h": 0.5}], True)
            store.set_page_panels(file_id, "p3.jpg", "sig", "model", [], False)
            with sqlite3.connect(root / "catalog.db") as raw:
                raw.execute("UPDATE schema_info SET version=45")
            reopened = CatalogStore(root / "catalog.db")
            self.assertIsNone(reopened.page_panels(file_id, "p1.jpg"), "asked once more, under the new rule")
            self.assertIsNone(reopened.page_panels(file_id, "p2.jpg"), "a grid believed on coverage alone")
            self.assertEqual(reopened.page_panels(file_id, "p3.jpg")["source"], "model", "not the model's rows: those were never sent")

    def test_a_vision_reading_short_of_three_quarters_from_before_47_is_asked_again(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._three_files(root)
            file_id = self._file_id(store, "Example 001.cbz")
            whole = [{"x": 0, "y": 0, "w": 1, "h": 0.5}, {"x": 0, "y": 0.5, "w": 1, "h": 0.5}]
            store.set_page_panels(file_id, "p1.jpg", "sig", "vlm", whole, True)
            store.set_page_panels(file_id, "p2.jpg", "sig", "vlm", [{"x": 0, "y": 0, "w": 1, "h": 0.66}], True)
            store.set_page_panels(file_id, "p3.jpg", "sig", "vlm", [], False)
            with sqlite3.connect(root / "catalog.db") as raw:
                raw.execute("UPDATE schema_info SET version=46")
            reopened = CatalogStore(root / "catalog.db")
            self.assertEqual(reopened.page_panels(file_id, "p1.jpg")["panels"], whole, "a full reading stands")
            self.assertIsNone(reopened.page_panels(file_id, "p2.jpg"), "two thirds of a page is not a reading")
            self.assertEqual(reopened.page_panels(file_id, "p3.jpg")["source"], "vlm", "an answer with no panels is not a short one")

    def test_a_pages_panels_can_be_forgotten(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            file_id = self._file_id(store, "Example 001.cbz")
            store.set_page_panels(file_id, "p1.jpg", "sig", "manual", [{"x": 0, "y": 0, "w": 1, "h": 1, "order": 0}], True)
            self.assertTrue(store.delete_page_panels(file_id, "p1.jpg"))
            self.assertIsNone(store.page_panels(file_id, "p1.jpg"), "read afresh next time")
            self.assertFalse(store.delete_page_panels(file_id, "p1.jpg"), "nothing left to forget")

    def test_a_comics_automatic_readings_can_be_forgotten_together_keeping_a_persons(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            file_id = self._file_id(store, "Example 001.cbz")
            other = self._file_id(store, "Example 002.cbz")
            store.set_page_panels(file_id, "p1.jpg", "sig", "vlm", [], False)
            store.set_page_panels(file_id, "p2.jpg", "sig", "auto", [{"x": 0, "y": 0, "w": 1, "h": 1}], True)
            store.set_page_panels(file_id, "p3.jpg", "sig", "manual", [{"x": 0, "y": 0, "w": 1, "h": 1, "order": 0}], True)
            store.set_page_panels(other, "p1.jpg", "sig", "auto", [], False)
            self.assertEqual(store.forget_automatic_page_panels(file_id), {"forgotten": 2, "kept": 1})
            self.assertIsNone(store.page_panels(file_id, "p1.jpg"))
            self.assertEqual(store.page_panels(file_id, "p3.jpg")["source"], "manual", "a person's stands")
            self.assertIsNotNone(store.page_panels(other, "p1.jpg"), "another comic's readings are its own")
            self.assertEqual(store.forget_automatic_page_panels(file_id), {"forgotten": 0, "kept": 1})

    def test_a_persons_fixes_nearest_a_comic_are_its_own_then_its_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            one = self._file_id(store, "Example 001.cbz")
            two = self._file_id(store, "Example 002.cbz")
            panel = [{"x": 0, "y": 0, "w": 1, "h": 1, "order": 0}]
            store.set_page_panels(two, "p1.jpg", "sig", "manual", panel, True)
            store.set_page_panels(one, "p1.jpg", "sig", "auto", panel, True)
            store.set_page_panels(one, "p2.jpg", "sig", "manual", panel, True)
            store.set_page_panels(one, "p3.jpg", "sig", "manual", panel, True)
            near = store.manual_page_panels_near(one, 2)
            self.assertEqual([(row["fileId"], row["member"]) for row in near], [(one, "p3.jpg"), (one, "p2.jpg")],
                             "its own pages first, newest first, and only a person's")
            self.assertEqual([row["fileId"] for row in store.manual_page_panels_near(one, 5)], [one, one, two],
                             "then the run's other issues")
            self.assertEqual(near[0]["panels"], panel)

    def test_only_a_known_source_for_a_known_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            file_id = self._file_id(store, "Example 001.cbz")
            with self.assertRaises(ValueError):
                store.set_page_panels(file_id, "p1.jpg", "s", "guess", [], False)
            with self.assertRaises(ValueError):
                store.set_page_panels(file_id, "", "s", "auto", [], False)
            with self.assertRaises(LookupError):
                store.set_page_panels(9999, "p1.jpg", "s", "auto", [], False)

    def test_a_comic_reads_left_to_right_and_a_manga_the_other_way_unless_told(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._three_files(Path(folder))
            run_id = self._run_id(store)
            file_id = self._file_id(store, "Example 002.cbz")
            self.assertEqual(store.file_reading_direction(file_id), "ltr")
            store.set_series_format(run_id, "manga")
            self.assertEqual(store.file_reading_direction(file_id), "rtl")
            store.set_series_format(run_id, "manga", reading_direction="ltr")
            self.assertEqual(store.file_reading_direction(file_id), "ltr", "a run may say otherwise")
            self.assertEqual(store.file_reading_direction(9999), "ltr", "a file with no run reads as a comic")


class ReplacementStaysItsOwnRequestTests(unittest.TestCase):
    """Replacing one comic must not enrol the run it belongs to.

    Replacing Saga #2 and #3 downloaded every missing issue in the run,
    twice -- once per open replacement -- and then neither replacement
    could finish, because the request it was waiting on had seventy other
    issues on it that would never all complete.
    """

    def _library(self, root, numbers=(1, 2, 3)):
        def parsed_for(number):
            path = root / f"Example {number:03d}.cbz"
            path.write_bytes(b"comic")
            return ParsedFile(str(path), path.name, ".cbz", "Example", issue=str(number))

        files = [parsed_for(n) for n in numbers]
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: files, lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": parsed.issue,
                    "record_type": "single_issue", "publisher": "Example Press",
                    "source": "Test",
                },
                "file_cover": None,
            })
        return store

    def _sync_issue_list(self, store, series_id, numbers):
        today = dt.datetime.now().astimezone().date()
        store.apply_issue_list(
            series_id, "gcd", "55", "https://www.comics.org/api/series/55/",
            [
                {"number": str(n), "provider_id": str(100 + n),
                 "publication_date": str(today - dt.timedelta(days=30)),
                 "publication_year": today.year}
                for n in numbers
            ],
        )

    def _file_id(self, store, fragment):
        return int(next(
            item for item in store.catalog()["series"][0]["fileDetails"]
            if fragment in item["filename"]
        )["id"])

    def _request_issue_numbers(self, store, request_id):
        with store._connect() as connection:
            return sorted(
                row["issue_number"] for row in connection.execute(
                    """SELECT issues.issue_number FROM acquisition_request_issues ari
                       JOIN issues ON issues.id=ari.issue_id
                       WHERE ari.request_id=?""",
                    (request_id,),
                )
            )

    def test_syncing_the_issue_list_does_not_enrol_the_whole_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._library(root)
            series_id = int(store.catalog()["series"][0]["id"])
            self._sync_issue_list(store, series_id, (1, 2, 3))
            replacement = store.request_file_replacement(
                self._file_id(store, "001"), "wrong_language", "en", "issues"
            )
            request_id = int(replacement["acquisitionRequestId"])
            self.assertEqual(self._request_issue_numbers(store, request_id), ["1"])

            # The trigger: any later issue-list sync -- a metadata refresh, a
            # rebuild -- used to enrol every issue into every open request.
            self._sync_issue_list(store, series_id, (1, 2, 3))
            self.assertEqual(
                self._request_issue_numbers(store, request_id), ["1"],
                "the replacement request must still cover only its own comic",
            )

    def test_a_followed_run_is_still_enrolled(self):
        """The exclusion must not stop an ordinary request tracking its run."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._library(root)
            series_id = int(store.catalog()["series"][0]["id"])
            self._sync_issue_list(store, series_id, (1, 2))
            run_request = int(
                store.create_acquisition_request("series", series_id, "issues")["id"]
            )
            self._sync_issue_list(store, series_id, (1, 2, 3))
            self.assertEqual(
                self._request_issue_numbers(store, run_request), ["1", "2", "3"],
            )

    def test_another_issue_landing_is_not_this_comic_arriving(self):
        """replacement_for_job matched on the request, not on the comic.

        Importing any issue in the run therefore looked like the replacement
        arriving, and would have retired the original on the strength of it.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._library(root, numbers=(1,))
            series_id = int(store.catalog()["series"][0]["id"])
            self._sync_issue_list(store, series_id, (1, 2))
            replacement = store.request_file_replacement(
                self._file_id(store, "001"), "wrong_language", "en", "issues"
            )
            request_id = int(replacement["acquisitionRequestId"])
            # Put a job for an issue this comic does not cover on the same
            # request, the way the enrolment bug used to.
            with store._connect() as connection:
                other = int(connection.execute(
                    "SELECT id FROM issues WHERE issue_number='2'"
                ).fetchone()[0])
                connection.execute(
                    """INSERT INTO acquisition_jobs(
                           request_id, issue_id, status, queue_reason,
                           created_at, updated_at)
                       VALUES (?, ?, 'queued', 'x', '2026-01-01', '2026-01-01')""",
                    (request_id, other),
                )
                foreign_job = int(connection.execute(
                    "SELECT id FROM acquisition_jobs WHERE issue_id=?", (other,)
                ).fetchone()[0])
                own_job = int(connection.execute(
                    "SELECT id FROM acquisition_jobs WHERE issue_id!=?", (other,)
                ).fetchone()[0])
            self.assertIsNone(
                store.replacement_for_job(foreign_job),
                "issue 2 has nothing to do with the comic being replaced",
            )
            own = store.replacement_for_job(own_job)
            self.assertIsNotNone(own)
            self.assertEqual(
                int(own["unfinished_jobs"]), 1,
                "only this comic's own jobs may hold the replacement open",
            )

    def test_a_replacement_that_landed_is_not_searched_again(self):
        """The job went fulfilled on import and back to queued seconds later.

        reconcile re-queued it because a replacement was still outstanding,
        which it always was, because the job it was waiting on kept being
        put back in the queue.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._library(root)
            series_id = int(store.catalog()["series"][0]["id"])
            self._sync_issue_list(store, series_id, (1, 2, 3))
            replacement = store.request_file_replacement(
                self._file_id(store, "001"), "wrong_language", "en", "issues"
            )
            request_id = int(replacement["acquisitionRequestId"])
            store.reconcile_acquisition_jobs(request_id)
            with store._connect() as connection:
                job_id = int(connection.execute(
                    "SELECT id FROM acquisition_jobs WHERE request_id=?", (request_id,)
                ).fetchone()[0])
            download = store.record_acquisition_download(job_id, "nzo-1", "Example 001 English")
            store.update_acquisition_download(int(download["id"]), "imported")
            store.update_acquisition_job(job_id, "fulfilled", "Imported and verified")

            store.reconcile_acquisition_jobs(request_id)
            with store._connect() as connection:
                status = connection.execute(
                    "SELECT status FROM acquisition_jobs WHERE id=?", (job_id,)
                ).fetchone()[0]
            self.assertEqual(status, "fulfilled", "reconcile must not re-queue it")

    def test_a_replacement_still_waiting_is_searched(self):
        """The guard must not stop a replacement that has not landed yet."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self._library(root)
            series_id = int(store.catalog()["series"][0]["id"])
            self._sync_issue_list(store, series_id, (1, 2, 3))
            replacement = store.request_file_replacement(
                self._file_id(store, "001"), "wrong_language", "en", "issues"
            )
            request_id = int(replacement["acquisitionRequestId"])
            store.reconcile_acquisition_jobs(request_id)
            with store._connect() as connection:
                row = connection.execute(
                    "SELECT id, status FROM acquisition_jobs WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual(row[1], "queued")


class UnfollowSeriesTests(unittest.TestCase):
    """Following a run was one-way.

    set_series_monitoring wrote 'monitored' and nothing wrote anything
    else, so a run followed by accident stayed followed and kept
    searching for issues nobody had asked for.
    """

    def _followed_run(self, root):
        path = root / "Example 001.cbz"
        path.write_bytes(b"comic")
        item = ParsedFile(str(path), path.name, ".cbz", "Example", issue="1")
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: [item], lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": "Example", "issue": "1",
                    "record_type": "single_issue", "source": "Test",
                },
                "file_cover": None,
            })
        run_id = int(store.catalog()["series"][0]["id"])
        # What following actually does: open a request and mark the run
        # monitored. Marking it alone would not reproduce the searching.
        store.create_acquisition_request("series", run_id, "issues")
        store.set_series_monitoring(run_id, "issues")
        return store, run_id

    def _status(self, store, run_id):
        return next(
            item["monitoringStatus"] for item in store.catalog()["series"]
            if int(item["id"]) == run_id
        )

    def test_a_followed_run_can_be_unfollowed(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._followed_run(Path(folder))
            self.assertEqual(self._status(store, run_id), "monitored")
            store.stop_series_monitoring(run_id)
            self.assertEqual(self._status(store, run_id), "cataloged")

    def test_unfollowing_stops_the_searching(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._followed_run(Path(folder))
            with sqlite3.connect(store.database_path) as connection:
                open_before = connection.execute(
                    "SELECT COUNT(*) FROM acquisition_requests WHERE status='open'"
                ).fetchone()[0]
            self.assertGreater(open_before, 0, "following should have opened a request")
            store.stop_series_monitoring(run_id)
            with sqlite3.connect(store.database_path) as connection:
                still_open = connection.execute(
                    "SELECT COUNT(*) FROM acquisition_requests WHERE status='open'"
                ).fetchone()[0]
                refreshes = connection.execute(
                    "SELECT COUNT(*) FROM series_monitor_refreshes WHERE series_run_id=?",
                    (run_id,),
                ).fetchone()[0]
            self.assertEqual(still_open, 0, "the request must not keep searching")
            self.assertEqual(refreshes, 0, "and the run must not be checked again")

    def test_the_comics_are_left_alone(self):
        """Unfollowing stops the looking; it does not touch the library."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, run_id = self._followed_run(root)
            store.stop_series_monitoring(run_id)
            series = store.catalog()["series"][0]
            self.assertEqual(series["owned"], 1)
            self.assertTrue((root / "Example 001.cbz").is_file())

    def test_following_again_afterwards_works(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._followed_run(Path(folder))
            store.stop_series_monitoring(run_id)
            store.set_series_monitoring(run_id, "issues")
            self.assertEqual(self._status(store, run_id), "monitored")

    def test_an_unknown_run_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _ = self._followed_run(Path(folder))
            with self.assertRaises(ValueError):
                store.stop_series_monitoring(999999)


class UnfollowCollectionTests(unittest.TestCase):
    """Collections had the same one-way follow that runs did."""

    def _collection(self, root, titles=("Alpha", "Beta")):
        items = []
        for title in titles:
            path = root / f"{title} 001.cbz"
            path.write_bytes(b"comic")
            items.append(ParsedFile(str(path), path.name, ".cbz", title, issue="1"))
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(
            store.begin_scan(str(root), True), lambda *_: items, lambda parsed: {
                "parsed": parsed.__dict__, "lookup_identity": parsed.__dict__,
                "embedded_metadata": {}, "file_health": {"status": "ok"},
                "recommendation": {
                    "title": parsed.series, "issue": "1",
                    "record_type": "single_issue", "source": "Test",
                },
                "file_cover": None,
            })
        run_ids = [int(item["id"]) for item in store.catalog()["series"]]
        family = store.create_series_family("Everything", run_ids)
        family_id = int(family["id"])
        store.create_acquisition_request("collection", family_id, "issues")
        store.set_collection_monitoring(family_id, "issues", True)
        return store, family_id, run_ids

    def _family_status(self, store, family_id):
        return next(
            item["monitoringStatus"] for item in store.catalog()["families"]
            if int(item["id"]) == family_id
        )

    def _refreshes(self, store, run_id):
        with sqlite3.connect(store.database_path) as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM series_monitor_refreshes WHERE series_run_id=?",
                (run_id,),
            ).fetchone()[0]

    def test_a_followed_collection_can_be_unfollowed(self):
        with tempfile.TemporaryDirectory() as folder:
            store, family_id, _ = self._collection(Path(folder))
            self.assertEqual(self._family_status(store, family_id), "monitored")
            store.stop_collection_monitoring(family_id)
            self.assertEqual(self._family_status(store, family_id), "cataloged")

    def test_unfollowing_stops_the_searching(self):
        with tempfile.TemporaryDirectory() as folder:
            store, family_id, _ = self._collection(Path(folder))
            store.stop_collection_monitoring(family_id)
            with sqlite3.connect(store.database_path) as connection:
                still_open = connection.execute(
                    "SELECT COUNT(*) FROM acquisition_requests WHERE status='open'"
                ).fetchone()[0]
            self.assertEqual(still_open, 0)

    def test_a_run_followed_in_its_own_right_keeps_being_checked(self):
        """The difference from unfollowing a single run.

        A member run can be followed on its own. Dropping every member's
        scheduled check would silently stop that run being watched too.
        """
        with tempfile.TemporaryDirectory() as folder:
            store, family_id, run_ids = self._collection(Path(folder))
            kept, dropped = run_ids[0], run_ids[1]
            store.set_series_monitoring(kept, "issues")
            store.stop_collection_monitoring(family_id)
            self.assertEqual(
                self._refreshes(store, kept), 1,
                "a run followed on its own must still be checked",
            )
            self.assertEqual(
                self._refreshes(store, dropped), 0,
                "a run only the collection was watching must not be",
            )

    def test_the_comics_are_left_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, family_id, _ = self._collection(root)
            store.stop_collection_monitoring(family_id)
            self.assertEqual(len(store.catalog()["series"]), 2)
            self.assertTrue((root / "Alpha 001.cbz").is_file())

    def test_an_unknown_collection_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _, _ = self._collection(Path(folder))
            with self.assertRaises(ValueError):
                store.stop_collection_monitoring(999999)


class PullOneIssueTests(unittest.TestCase):
    """Asking for one comic is not the same as taking on its back catalogue.

    Every request used to cover a whole scope, and three separate mechanisms
    widened it back: applying an issue list enrolled every open request on the
    run, the monitor enrolled it for daily checks, and unfollowing cancelled
    it. A request that covers named issues has to survive all three.
    """

    ISSUES = [
        {"number": "1", "provider_id": "101", "publication_date": "2020-01-01", "publication_year": 2020},
        {"number": "2", "provider_id": "102", "publication_date": "2020-02-01", "publication_year": 2020},
        {"number": "3", "provider_id": "103", "publication_date": "2020-03-01", "publication_year": 2020},
    ]

    def _run(self, root, issues=None, file_cover=None):
        """A library owning issue 1 of a three-issue run, following nothing."""
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
                "file_cover": {"url": file_cover} if file_cover else None,
            })
        run_id = int(store.catalog()["series"][0]["id"])
        store.apply_issue_list(
            run_id, "gcd", "55", "https://www.comics.org/api/series/55/",
            list(issues if issues is not None else self.ISSUES),
        )
        return store, run_id

    def _request(self, store, request_id):
        return next(
            item for item in store.catalog()["requests"] if item["id"] == str(request_id)
        )

    def _numbers(self, request):
        return sorted(job["issueNumber"] for job in request["jobs"])

    def test_asking_for_one_issue_queues_only_that_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            request = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            self.assertEqual(request["targetIssueCount"], 1)
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._numbers(self._request(store, request["id"])), ["3"])

    def test_it_does_not_start_following_the_run(self):
        """The whole point: one comic, not a subscription."""
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            series = next(
                item for item in store.catalog()["series"] if int(item["id"]) == run_id
            )
            self.assertEqual(series["monitoringStatus"], "cataloged")

    def test_a_later_metadata_sync_does_not_widen_it(self):
        # apply_issue_list enrols every open request on the run. Before the
        # coverage column, one pulled issue quietly became the whole run the
        # next time metadata refreshed.
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            request = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            store.apply_issue_list(
                run_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                self.ISSUES + [
                    {"number": "4", "provider_id": "104", "publication_date": "2020-04-01",
                     "publication_year": 2020},
                ],
            )
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._request(store, request["id"])["targetIssueCount"], 1)
            self.assertEqual(self._numbers(self._request(store, request["id"])), ["3"])

    def test_unfollowing_the_run_leaves_it_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            followed = store.create_acquisition_request("series", run_id)
            store.stop_series_monitoring(run_id)
            self.assertEqual(self._request(store, pulled["id"])["status"], "open",
                             "the comic you asked for by name was not part of the following")
            self.assertEqual(self._request(store, followed["id"])["status"], "cancelled")

    def test_the_daily_monitor_does_not_enrol_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, run_id = self._run(root)
            store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            # The monitor is seeded on open, so a fresh store is the check.
            reopened = CatalogStore(root / "catalog.db")
            with sqlite3.connect(reopened.database_path) as connection:
                rows = connection.execute(
                    "SELECT COUNT(*) FROM series_monitor_refreshes WHERE series_run_id=?",
                    (run_id,),
                ).fetchone()[0]
            self.assertEqual(rows, 0)

    def test_following_the_run_still_works_as_it_did(self):
        """The guards must be about coverage, not about series requests."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, run_id = self._run(root)
            request = store.create_acquisition_request("series", run_id)
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._numbers(self._request(store, request["id"])), ["2", "3"])
            store.apply_issue_list(
                run_id, "gcd", "55", "https://www.comics.org/api/series/55/",
                self.ISSUES + [
                    {"number": "4", "provider_id": "104", "publication_date": "2020-04-01",
                     "publication_year": 2020},
                ],
            )
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._numbers(self._request(store, request["id"])), ["2", "3", "4"],
                             "a followed run must still pick up newly listed issues")
            reopened = CatalogStore(root / "catalog.db")
            with sqlite3.connect(reopened.database_path) as connection:
                rows = connection.execute(
                    "SELECT COUNT(*) FROM series_monitor_refreshes WHERE series_run_id=?",
                    (run_id,),
                ).fetchone()[0]
            self.assertEqual(rows, 1, "and must still be checked daily")

    def test_a_run_with_no_files_yet_still_has_a_face(self):
        """Run covers were gathered from owned files only.

        Follow a run and the Pull List drew a placeholder beside "0 of 34
        owned" until the first download landed -- while every issue in it
        already carried the provider's art.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, run_id = self._run(root)
            with sqlite3.connect(store.database_path) as connection:
                connection.execute(
                    "UPDATE issues SET cover=? WHERE series_run_id=? AND issue_number='2'",
                    ("https://example.invalid/two.jpg", run_id),
                )
            series = next(
                item for item in CatalogStore(root / "catalog.db").catalog()["series"]
                if int(item["id"]) == run_id
            )
            self.assertIn("two.jpg", series["cover"] or "")

    def test_an_owned_file_still_outranks_the_provider(self):
        """The fallback is a fallback: your own scan is the better likeness."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store, run_id = self._run(root, file_cover="/api/file-cover?path=own.cbz")
            with sqlite3.connect(store.database_path) as connection:
                connection.execute(
                    "UPDATE issues SET cover=? WHERE series_run_id=?",
                    ("https://example.invalid/provider.jpg", run_id),
                )
            series = next(
                item for item in CatalogStore(root / "catalog.db").catalog()["series"]
                if int(item["id"]) == run_id
            )
            self.assertNotIn("provider.jpg", series["cover"] or "",
                             "issue 1 is owned, so its own cover leads")

    def test_the_catalog_says_a_pulled_issue_is_not_being_followed(self):
        """The screen reads this field, and it derived from open-ness alone.

        An issue pulled from Discover is open until the comic arrives, so it
        reported as monitored and the Pull List called it Following -- for a
        run it had deliberately not taken on.
        """
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            self.assertEqual(pulled["coverage"], "issues")
            self.assertEqual(pulled["monitoringStatus"], "stopped")
            followed = store.create_acquisition_request("series", run_id)
            self.assertEqual(followed["coverage"], "run")
            self.assertEqual(followed["monitoringStatus"], "monitored")

    def test_an_unknown_issue_number_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            with self.assertRaises(ValueError):
                store.create_acquisition_request("series", run_id, issue_numbers=["99"])
            with self.assertRaises(ValueError):
                store.create_acquisition_request("series", run_id, issue_numbers=[])

    def test_a_collection_cannot_be_asked_for_by_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _run_id = self._run(Path(folder))
            with self.assertRaises(ValueError):
                store.create_acquisition_request("collection", 1, issue_numbers=["3"])

    def test_following_a_run_you_already_pulled_an_issue_from_really_follows(self):
        """Reuse is by coverage, not by run.

        Joining the newest open request on the run adopted the pulled issue's
        request instead: the button said Following, monitoring stayed off, and
        the request still covered one comic.
        """
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            followed = store.create_acquisition_request("series", run_id)
            self.assertNotEqual(followed["id"], pulled["id"])
            series = next(
                item for item in store.catalog()["series"] if int(item["id"]) == run_id
            )
            self.assertEqual(series["monitoringStatus"], "monitored")
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._numbers(self._request(store, followed["id"])), ["2", "3"])
            self.assertEqual(self._numbers(self._request(store, pulled["id"])), ["3"])

    def test_pulling_an_issue_of_a_run_you_follow_joins_that_request(self):
        """It is already wanted; a second request would double-count it."""
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            followed = store.create_acquisition_request("series", run_id)
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            self.assertEqual(pulled["id"], followed["id"])
            self.assertEqual(pulled["targetIssueCount"], 3, "the run request is left as it was")
            series = next(
                item for item in store.catalog()["series"] if int(item["id"]) == run_id
            )
            self.assertEqual(series["monitoringStatus"], "monitored",
                             "and it is still followed afterwards")

    def _issue_id(self, store, run_id, number):
        series = next(item for item in store.catalog()["series"] if int(item["id"]) == run_id)
        return int(next(issue["id"] for issue in series["issues"] if issue["number"] == number))

    def test_deleting_one_pulled_issue_leaves_the_others(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["2", "3"])
            store.reconcile_acquisition_jobs()
            result = store.delete_pulled_issues(int(pulled["id"]), [self._issue_id(store, run_id, "2")])
            self.assertEqual(result, {"deleted": 1, "requestDeleted": False})
            request = self._request(store, pulled["id"])
            self.assertEqual(self._numbers(request), ["3"])
            self.assertEqual([issue["number"] for issue in request["issues"]], ["3"])
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._numbers(self._request(store, pulled["id"])), ["3"],
                             "reconcile does not bring the deleted issue back")

    def test_deleting_the_last_issue_deletes_the_pull(self):
        """Nothing is left on the Pull List -- not a cancelled row, nothing."""
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            store.reconcile_acquisition_jobs()
            result = store.delete_pulled_issues(int(pulled["id"]))
            self.assertEqual(result, {"deleted": 1, "requestDeleted": True})
            self.assertEqual(store.catalog()["requests"], [])

    def test_a_deleted_pull_can_be_made_again(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            first = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            store.delete_pulled_issues(int(first["id"]))
            again = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            store.reconcile_acquisition_jobs()
            self.assertEqual(self._numbers(self._request(store, again["id"])), ["3"])

    def test_an_issue_that_is_downloading_is_not_deleted(self):
        """Its job is what imports the file when SABnzbd finishes."""
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            store.reconcile_acquisition_jobs()
            job_id = int(self._request(store, pulled["id"])["jobs"][0]["id"])
            store.record_acquisition_download(job_id, "SABnzbd_nzo_1", "Example 003", "key")
            with self.assertRaisesRegex(ValueError, "downloading"):
                store.delete_pulled_issues(int(pulled["id"]))
            self.assertEqual(self._numbers(self._request(store, pulled["id"])), ["3"])

    def test_a_followed_run_is_not_deleted_this_way(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            followed = store.create_acquisition_request("series", run_id)
            with self.assertRaisesRegex(ValueError, "unfollowing"):
                store.delete_pulled_issues(int(followed["id"]))

    def test_an_issue_outside_the_pull_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store, run_id = self._run(Path(folder))
            pulled = store.create_acquisition_request("series", run_id, issue_numbers=["3"])
            with self.assertRaises(ValueError):
                store.delete_pulled_issues(int(pulled["id"]), [self._issue_id(store, run_id, "2")])
            with self.assertRaises(ValueError):
                store.delete_pulled_issues(999)


class MangaRunTests(unittest.TestCase):
    """A run says whether it is manga, and nothing merges the two."""

    def _format(self, store, run_id):
        return store.get_series_sync_context(int(run_id))["format"]

    def test_a_manga_run_is_not_joined_to_a_comic_of_the_same_name(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            comic = store.ensure_provider_series_run("metron", "1", "Berserk", 1995, "Dark Horse Comics")
            manga = store.ensure_provider_series_run(
                "comic_vine", "2", "Berserk", 1996, "Dark Horse Manga", run_format="manga")
            self.assertNotEqual(comic["id"], manga["id"])
            self.assertEqual(self._format(store, comic["id"]), "comic")
            self.assertEqual(self._format(store, manga["id"]), "manga")

    def test_a_run_can_be_told_it_is_manga(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            run = store.ensure_provider_series_run("comic_vine", "3", "Berserk", 2003, "Dark Horse Comics")
            self.assertEqual(store.set_series_format(int(run["id"]), "manga")["format"], "manga")
            self.assertEqual(self._format(store, run["id"]), "manga")
            with self.assertRaises(ValueError):
                store.set_series_format(int(run["id"]), "novel")
            with self.assertRaises(LookupError):
                store.set_series_format(9999, "manga")


class IssueProviderIdTests(unittest.TestCase):
    """Which catalogs can be asked about one issue."""

    def _seed(self, store, ids):
        now = _utc_now()
        with store._connect() as connection:
            run_id = connection.execute(
                """INSERT INTO series_runs(canonical_title, canonical_key, created_at, updated_at)
                   VALUES ('Saga', 'saga', ?, ?)""", (now, now),
            ).lastrowid
            issue_id = connection.execute(
                """INSERT INTO issues(series_run_id, issue_number, created_at, updated_at)
                   VALUES (?, '1', ?, ?)""", (run_id, now, now),
            ).lastrowid
            for provider, provider_id in ids.items():
                connection.execute(
                    """INSERT INTO issue_provider_ids(issue_id, provider, provider_id, updated_at)
                       VALUES (?, ?, ?, ?)""", (issue_id, provider, provider_id, now),
                )
        return int(issue_id)

    def test_every_catalog_that_knows_the_issue_is_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            issue_id = self._seed(store, {"metron": "901", "comic_vine": "5507", "gcd": "42"})
            self.assertEqual(store.issue_provider_ids(issue_id), {
                "metron": "901", "comic_vine": "5507", "gcd": "42",
            })

    def test_an_issue_no_catalog_knows_is_empty_rather_than_missing(self):
        # 132 of the library's issues have no Metron id; that is a blurb we
        # cannot fetch, not an issue that does not exist.
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            self.assertEqual(store.issue_provider_ids(self._seed(store, {})), {})

    def test_an_issue_that_is_not_ours_is_a_lookup_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            with self.assertRaisesRegex(LookupError, "Issue was not found"):
                store.issue_provider_ids(9999)


class SeriesRemovalTests(unittest.TestCase):
    """A run can be removed, with everything recorded about it."""

    def test_the_plan_counts_what_goes_and_removal_takes_it(self):
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
                    "recommendation": {"title": "Example", "issue": "1", "record_type": "single_issue",
                                       "publisher": "Example Press", "source": "Test"},
                })
            run_id = int(store.catalog()["series"][0]["id"])
            store.apply_issue_list(run_id, "gcd", "55", "https://www.comics.org/api/series/55/", [
                {"number": "1", "provider_id": "101", "publication_date": "2020-01-01", "publication_year": 2020},
                {"number": "2", "provider_id": "102", "publication_date": "2020-02-01", "publication_year": 2020},
            ])
            store.create_acquisition_request("series", run_id, issue_numbers=["2"])
            plan = store.series_removal_plan(run_id)
            self.assertEqual(plan["fileCount"], 1)
            self.assertEqual(plan["files"][0]["path"], str(path))
            self.assertEqual(plan["requestCount"], 1)
            store.remove_series_run(run_id)
            snapshot = store.catalog()
            self.assertEqual(snapshot["series"], [])
            self.assertEqual(snapshot["requests"], [])
            with self.assertRaises(LookupError):
                store.series_removal_plan(run_id)
            with self.assertRaises(LookupError):
                store.remove_series_run(run_id)
