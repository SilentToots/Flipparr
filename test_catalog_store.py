import datetime as dt
import tempfile
import unittest
from pathlib import Path

from app import ParsedFile
from catalog_store import CatalogStore


class CatalogStoreTests(unittest.TestCase):
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

    def test_damaged_volume_can_be_requested_and_cancelled_as_a_replacement(self):
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

            replacement = store.request_file_replacement(
                int(review["fileId"]), "wrong_language", "English", "volumes"
            )
            self.assertEqual(replacement["targetType"], "volume")
            self.assertEqual(replacement["status"], "wanted")
            self.assertEqual(replacement["desiredLanguage"], "English")
            self.assertEqual(replacement["acquisitionPreference"], "volumes")
            self.assertEqual(replacement["coverageTarget"], "Complete the run")
            self.assertTrue(comic.exists())
            refreshed = store.catalog()
            self.assertEqual(refreshed["inbox"][0]["replacementStatus"], "wanted")
            self.assertEqual(refreshed["stats"]["openRequests"], 1)

            cancelled = store.update_file_replacement_status(int(replacement["id"]), "cancelled")
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertTrue(comic.exists())
            self.assertEqual(store.catalog()["stats"]["openRequests"], 0)

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
            self.assertFalse(series["issueCatalog"]["syncReady"])

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


if __name__ == "__main__":
    unittest.main()
