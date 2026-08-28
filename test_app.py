import io
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from app import Handler, MetadataRateLimited, PAGE, ReleaseDownloadError, _RELEASE_CANDIDATES, _gcd_discovery_search_rows, _comic_vine_issue_entries, _hydrate_gcd_issue_entries_with_status, _metron_issue_entries, assess_identity_confidence, batch_enrich, catalog_api_payload, confirm_gcd_series_collection, confirm_gcd_series_run, discover_gcd_series, discover_metron_series, discover_series, embedded_epub_candidate, enrich, enrich_catalog_series, extract_issue_coverage, file_cover_info, find_archive_cover_member, import_downloaded_comic, inspect_file_health, inventory_file, lookup_identity, parse_filename, post_multipart_file_json, public_acquisition_service_config, public_provider_config, rank_gcd_series_runs, read_embedded_metadata, reconcile_acquisition_download, request_discovered_gcd_series, request_discovered_series, render_batch_results, run_metadata_enrichment_job, save_acquisition_service_config, save_provider_config, scan_folder, score_candidate, search_gcd, search_google_books, search_open_library, search_prowlarr_releases, send_release_to_sabnzbd, sync_gcd_issue_catalog, sync_issue_catalog, test_acquisition_service_connection


class FilenameParserTests(unittest.TestCase):
    def tearDown(self):
        _RELEASE_CANDIDATES.clear()

    def test_fast_inventory_uses_local_evidence_without_provider_requests(self):
        parsed = parse_filename(Path("Absolute Batman 003 (2025).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman",
            "title": "The Zoo, Part Three", "number": "3", "year": "2024",
            "publisher": "DC Comics", "contributors": {},
        }
        with patch("app.inspect_file_health", return_value={"status": "ok"}), patch(
            "app.read_embedded_metadata", return_value=embedded
        ), patch("app.file_cover_info", return_value={"available": True}), patch(
            "app.search_gcd"
        ) as gcd, patch("app.search_open_library") as open_library:
            result = inventory_file(parsed)

        self.assertEqual(result["recommendation"]["title"], "Absolute Batman")
        self.assertEqual(result["recommendation"]["issue"], "3")
        self.assertEqual(result["source_status"]["external_metadata"], "deferred during fast library inventory")
        gcd.assert_not_called()
        open_library.assert_not_called()

    def test_metadata_job_respects_provider_retry_after(self):
        store = Mock()
        job = {"id": 8, "series_run_id": 4, "attempt_count": 1}
        with patch("app.catalog_store", return_value=store), patch(
            "app.enrich_catalog_series",
            side_effect=MetadataRateLimited("gcd", 180, "Provider asked SonicBoom to pause."),
        ):
            result = run_metadata_enrichment_job(job)

        self.assertEqual(result, {"status": "waiting", "provider": "gcd"})
        store.finish_metadata_enrichment_job.assert_called_once_with(
            8, "waiting", "gcd", "Provider asked SonicBoom to pause.", 180
        )

    def test_series_enrichment_stops_after_first_provider_supplies_issue_list(self):
        store = Mock()
        store.get_series_sync_context.return_value = {
            "title": "Example", "year": 2024, "publisher": "Example Press",
            "metronSeriesId": None, "comicVineVolumeId": None,
        }
        store.metadata_provider_available.return_value = True
        store.apply_issue_list.return_value = {"issueCount": 2, "provider": "metron"}
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[("metron", {"token": "secret"}), ("gcd", {})],
        ), patch("app._metron_issue_entries", return_value=(
            "42", "https://metron.cloud/series/42/",
            [{"number": "1"}, {"number": "2"}],
        )), patch("app._apply_gcd_series_enrichment") as gcd:
            result = enrich_catalog_series(7)

        self.assertEqual((result["status"], result["provider"]), ("complete", "metron"))
        gcd.assert_not_called()
        store.apply_issue_list.assert_called_once()

    def test_catalog_pause_requires_every_enabled_provider_to_have_an_error_cooldown(self):
        store = Mock()
        store.catalog.return_value = {"enrichment": {
            "providerCooldowns": [{"provider": "gcd", "error": "rate limited"}],
        }}
        store.metadata_provider_available.side_effect = lambda provider: provider != "gcd"
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[("metron", {"token": "saved"}), ("comic_vine", {"apiKey": "saved"}), ("gcd", {})],
        ):
            result = catalog_api_payload()

        enrichment = result["enrichment"]
        self.assertFalse(enrichment["allProvidersCooling"])
        self.assertEqual(enrichment["availableProviders"], ["metron", "comic_vine"])

    def test_prowlarr_search_returns_safe_ranked_usenet_candidates(self):
        context = {
            "id": "12", "requestId": "4", "status": "queued",
            "issueId": "8", "issueNumber": "4", "issueTitle": "The Zoo",
            "publicationYear": 2025, "publicationDate": "2025-01-01",
            "seriesId": "2", "seriesTitle": "Absolute Batman",
            "seriesYear": 2024, "publisher": "DC Comics",
            "acquisitionPreference": "either",
        }
        store = Mock()
        store.get_acquisition_job_context.return_value = context
        releases = [
            {
                "guid": "good", "title": "Absolute Batman 004 (2025) (Digital) (ENG)",
                "protocol": "usenet", "downloadUrl": "http://prowlarr/api/v1/download?apikey=secret",
                "indexer": "Test Indexer", "size": 50_000_000,
                "publishDate": "2025-01-02T00:00:00Z", "categories": [{"id": 7030}],
            },
            {
                "guid": "wrong-issue", "title": "Absolute Batman 005 (2025)",
                "protocol": "usenet", "downloadUrl": "http://prowlarr/wrong",
                "indexer": "Test Indexer", "size": 40_000_000, "categories": [{"id": 7030}],
            },
            {
                "guid": "torrent", "title": "Absolute Batman 004 (2025)",
                "protocol": "torrent", "downloadUrl": "magnet:?xt=secret",
                "indexer": "Torrent Indexer", "size": 40_000_000, "categories": [{"id": 7030}],
            },
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"}
            }
        ), patch("app.fetch_json_with_headers", return_value=releases) as fetch:
            result = search_prowlarr_releases(12)

        self.assertEqual(result["query"], "Absolute Batman 004")
        self.assertEqual(result["candidateCount"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["title"], releases[0]["title"])
        self.assertEqual(candidate["formatTags"], ["Digital", "English"])
        self.assertNotIn("downloadUrl", candidate)
        self.assertNotIn("secret", str(result))
        cached = _RELEASE_CANDIDATES[candidate["id"]]
        self.assertEqual(cached["downloadPath"], "/api/v1/download")
        self.assertNotIn("secret", str(cached))
        requested_url = fetch.call_args.args[0]
        self.assertIn("categories=7030", requested_url)
        self.assertEqual(fetch.call_args.args[1]["X-Api-Key"], "secret")
        self.assertEqual(store.update_acquisition_job.call_args_list[0].args[1], "searching")
        self.assertEqual(store.update_acquisition_job.call_args_list[-1].args[1], "queued")

    def test_sabnzbd_grab_fetches_nzb_privately_and_uploads_a_clean_file(self):
        _RELEASE_CANDIDATES["candidate"] = {
            "jobId": 12, "downloadPath": "/9/download?id=42",
            "title": "Absolute Batman 004", "expiresAt": 9_999_999_999,
        }
        store = Mock()
        store.update_acquisition_job.return_value = {"id": "12", "status": "grabbed"}
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr:9696", "apiKey": "prowlarr-secret"},
                "sabnzbd": {"enabled": True, "url": "http://sab", "apiKey": "sab-secret", "category": "comics"},
            }
        ), patch("app.fetch_bytes_with_headers", return_value=(
            b'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"></nzb>'
        )) as fetch, patch("app.post_multipart_file_json", return_value={
            "status": True, "nzo_ids": ["queue-id"]
        }) as upload:
            result = send_release_to_sabnzbd(12, "candidate")

        self.assertEqual(fetch.call_args.args[0], "http://prowlarr:9696/9/download?id=42")
        self.assertEqual(fetch.call_args.args[1]["X-Api-Key"], "prowlarr-secret")
        upload_url = upload.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(upload_url).query)
        self.assertEqual(query["mode"], ["addfile"])
        self.assertEqual(query["nzbname"], ["Absolute Batman 004"])
        self.assertEqual(query["cat"], ["comics"])
        self.assertEqual(query["apikey"], ["sab-secret"])
        self.assertEqual(upload.call_args.kwargs["field_name"], "name")
        self.assertEqual(upload.call_args.kwargs["filename"], "Absolute Batman 004.nzb")
        self.assertTrue(upload.call_args.kwargs["content"].startswith(b"<?xml"))
        self.assertNotIn("prowlarr", upload_url)
        self.assertNotIn("prowlarr-secret", str(upload.call_args))
        self.assertEqual(result["status"], "grabbed")
        self.assertEqual(result["queueIds"], ["queue-id"])
        self.assertNotIn("secret", str(result))
        store.record_acquisition_download.assert_called_once_with(
            12, "queue-id", "Absolute Batman 004"
        )
        store.update_acquisition_job.assert_called_once()
        self.assertNotIn("candidate", _RELEASE_CANDIDATES)

    def test_sabnzbd_grab_rejects_a_non_nzb_before_upload(self):
        _RELEASE_CANDIDATES["candidate"] = {
            "jobId": 12, "downloadPath": "/9/download", "title": "Absolute Batman 004",
            "expiresAt": 9_999_999_999,
        }
        with patch("app.load_acquisition_service_config", return_value={
            "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"},
            "sabnzbd": {"enabled": True, "url": "http://sab", "apiKey": "secret"},
        }), patch("app.fetch_bytes_with_headers", return_value=b"<html>Indexer error</html>"), patch(
            "app.post_multipart_file_json"
        ) as upload:
            with self.assertRaisesRegex(ReleaseDownloadError, "not an NZB document"):
                send_release_to_sabnzbd(12, "candidate")
        upload.assert_not_called()

    def test_multipart_nzb_upload_uses_the_sab_file_field(self):
        with patch("app.urllib.request.urlopen", return_value=io.BytesIO(b'{"status": true}')) as open_url:
            result = post_multipart_file_json(
                "http://sab/api?mode=addfile",
                field_name="name",
                filename="Absolute Batman 004.nzb",
                content=b"<nzb></nzb>",
                content_type="application/x-nzb",
                headers={"Accept": "application/json"},
            )

        request = open_url.call_args.args[0]
        body = request.data
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertIn(b'Content-Disposition: form-data; name="name"; filename="Absolute Batman 004.nzb"', body)
        self.assertIn(b"Content-Type: application/x-nzb", body)
        self.assertIn(b"<nzb></nzb>", body)
        self.assertEqual(result, {"status": True})

    def test_sabnzbd_grab_rejects_an_unknown_candidate(self):
        with self.assertRaisesRegex(ValueError, "expired"):
            send_release_to_sabnzbd(12, "missing")

    def test_year_qualified_discovery_ranks_same_name_runs_by_publication_year(self):
        rows = [
            {"id": index, "series": "Wolverine", "year_began": year, "issue_count": 1}
            for index, year in enumerate(range(1980, 2010), start=1)
        ]
        rows.extend([
            {"id": 101, "series": "Wolverine", "year_began": 2024, "issue_count": 12},
            {"id": 102, "series": "Wolverine", "year_began": 2026, "issue_count": 3},
            {"id": 103, "series": "Wolverine", "year_began": 2025, "issue_count": 8},
        ])

        def response(url, _headers):
            if "/series/?" in url:
                self.assertIn("q=Wolverine", url)
                self.assertNotIn("2026", url)
                return {"results": rows}
            if url.endswith("/series/102/"):
                return {"status": "Ongoing"}
            if "/series/102/issue_list/" in url:
                return {"results": []}
            self.fail(f"Unexpected Metron request: {url}")

        store = Mock()
        store.catalog.return_value = {"series": []}
        with patch("app.fetch_json_with_headers", side_effect=response), patch(
            "app.catalog_store", return_value=store
        ):
            result = discover_metron_series("Wolverine 2026", "token")

        self.assertEqual(result["titleQuery"], "Wolverine")
        self.assertEqual(result["yearHint"], 2026)
        self.assertEqual(result["results"][0]["yearBegan"], 2026)
        self.assertEqual(len(result["results"]), 24)

    def test_metron_discovery_adds_a_role_labeled_byline_from_the_first_issue(self):
        def response(url, _headers):
            if "/series/?" in url:
                return {"results": [{
                    "id": 9, "series": "Saga (2012)", "year_began": 2012,
                    "publisher": {"name": "Image Comics"}, "issue_count": 72,
                }]}
            if url.endswith("/series/9/"):
                return {"publisher": {"name": "Image Comics"}, "status": "Ongoing"}
            if "/series/9/issue_list/" in url:
                return {"results": [{"id": 101, "number": "1", "image": "https://covers.test/saga.jpg"}]}
            if url.endswith("/issue/101/"):
                return {"credits": [
                    {"creator": "Brian K. Vaughan", "role": [{"name": "Writer"}]},
                    {"creator": "Fiona Staples", "role": [
                        {"name": "Artist"}, {"name": "Colorist"}, {"name": "Cover"},
                    ]},
                ]}
            self.fail(f"Unexpected Metron request: {url}")

        store = Mock()
        store.catalog.return_value = {"series": []}
        with patch("app.fetch_json_with_headers", side_effect=response), patch(
            "app.catalog_store", return_value=store
        ):
            result = discover_metron_series("Saga", "token")

        saga = result["results"][0]
        self.assertEqual(saga["cover"], "https://covers.test/saga.jpg")
        self.assertEqual(saga["creatorCreditsSource"], "Issue #1")
        self.assertEqual(saga["creators"], [
            {"name": "Brian K. Vaughan", "roles": ["Writer"]},
            {"name": "Fiona Staples", "roles": ["Artist", "Colorist"]},
        ])

    def test_discovery_prefers_configured_metron_over_gcd(self):
        metron_result = {
            "query": "Saga", "provider": "Metron", "providerId": "metron",
            "results": [{
                "provider": "metron", "providerSeriesId": "9", "title": "Saga",
                "yearBegan": 2012, "publisher": "Image Comics", "cover": None,
            }],
        }
        comic_result = {
            "query": "Saga", "provider": "Comic Vine", "providerId": "comic_vine",
            "results": [{
                "provider": "comic_vine", "providerSeriesId": "8", "title": "Saga",
                "yearBegan": 2012, "publisher": "Image", "cover": "https://covers.test/saga.jpg",
            }],
        }
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
            "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app.discover_metron_series", return_value=metron_result) as metron, patch(
            "app.discover_comic_vine_series", return_value=comic_result
        ) as comic_vine, patch("app.discover_gcd_series") as gcd:
            result = discover_series("Saga")
        self.assertEqual(result["providerId"], "metron")
        self.assertEqual(result["results"][0]["cover"], "https://covers.test/saga.jpg")
        self.assertEqual(result["results"][0]["coverProvider"], "Comic Vine")
        metron.assert_called_once_with("Saga", "token")
        comic_vine.assert_called_once_with("Saga", "key")
        gcd.assert_not_called()

    def test_discovery_falls_back_to_comic_vine_when_metron_is_busy(self):
        comic_result = {
            "query": "Saga", "provider": "Comic Vine", "providerId": "comic_vine",
            "results": [{"provider": "comic_vine", "providerSeriesId": "8", "title": "Saga"}],
        }
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
            "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app.discover_metron_series", side_effect=RuntimeError("rate limited")), patch(
            "app.discover_comic_vine_series", return_value=comic_result
        ), patch("app.discover_gcd_series") as gcd:
            result = discover_series("Saga")
        self.assertEqual(result["providerId"], "comic_vine")
        self.assertEqual(result["fallbacks"][0]["provider"], "Metron")
        gcd.assert_not_called()

    def test_discovered_request_routes_to_the_selected_provider(self):
        with patch("app.request_discovered_gcd_series", return_value={"series": {"id": "7"}}) as gcd:
            result = request_discovered_series("gcd", "Saga", "42", "either")
        self.assertEqual(result["series"]["id"], "7")
        gcd.assert_called_once_with("Saga", "42", "either")

    def test_discovery_checks_an_adjacent_page_for_an_exact_title(self):
        def response(url):
            if "page=2" in url:
                return {"results": [{"name": "Die Saga"}, {"name": "The Saga"}]}
            if "page=3" in url:
                return {"results": [{"name": "Saga"}]}
            return {"count": 150, "results": [{"name": "A Clone Saga"}]}

        with patch("app.fetch_gcd_json", side_effect=response) as fetch:
            rows = _gcd_discovery_search_rows("Saga")
        self.assertIn("Saga", [row["name"] for row in rows])
        self.assertEqual(fetch.call_count, 3)

    def test_discovery_search_marks_a_run_already_in_the_library(self):
        series = {
            "api_url": "https://www.comics.org/api/series/42/",
            "name": "Example Comics", "year_began": 2024, "country": "us", "language": "en",
            "active_issues": ["https://www.comics.org/api/issue/101/"],
            "issue_descriptors": ["1"],
        }
        store = Mock()
        store.catalog.return_value = {
            "series": [{"title": "Example Comics", "year": "2024", "issueCatalog": {"providerSeriesId": "42"}}]
        }
        with patch("app.fetch_gcd_json", return_value={"results": [series]}), patch(
            "app.catalog_store", return_value=store
        ):
            result = discover_gcd_series("Example Comics")
        self.assertEqual(result["results"][0]["providerSeriesId"], "42")
        self.assertTrue(result["results"][0]["inLibrary"])

    def test_discovered_run_is_imported_and_requested_in_one_action(self):
        series = {
            "api_url": "https://www.comics.org/api/series/42/",
            "name": "Example Comics", "year_began": 2024, "country": "us", "language": "en",
            "active_issues": ["https://www.comics.org/api/issue/101/"],
            "issue_descriptors": ["1"],
        }
        store = Mock()
        store.ensure_provider_series_run.return_value = {"id": "7", "title": "Example Comics", "created": True}
        store.create_acquisition_request.return_value = {"id": "9", "title": "Example Comics"}
        with patch("app.fetch_gcd_json", return_value={"results": [series]}), patch(
            "app._hydrate_gcd_issue_entries_with_status",
            return_value=([{"number": "1", "provider_id": "101", "publication_year": 2024}], {"status": "complete"}),
        ), patch("app.catalog_store", return_value=store):
            result = request_discovered_gcd_series("Example Comics", "42", "issues")
        self.assertEqual(result["request"]["id"], "9")
        store.apply_issue_list.assert_called_once()
        store.create_acquisition_request.assert_called_once_with("series", 7, "issues", True)

    def test_reveal_file_uses_finder_for_a_catalog_validated_path(self):
        handler = Handler.__new__(Handler)
        handler.send_json = Mock()
        store = Mock()
        store.cataloged_file_path.return_value = Path("/tmp/Example 001.cbz")
        completed = Mock(returncode=0, stdout="", stderr="")
        with patch("app.sys.platform", "darwin"), patch(
            "app.catalog_store", return_value=store
        ), patch("app.subprocess.run", return_value=completed) as run:
            handler.handle_reveal_file("/tmp/Example 001.cbz")

        store.cataloged_file_path.assert_called_once_with("/tmp/Example 001.cbz")
        run.assert_called_once_with(
            ["open", "-R", "/tmp/Example 001.cbz"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        handler.send_json.assert_called_once_with({
            "status": "revealed", "message": "Shown in Finder: Example 001.cbz",
        })

    def test_provider_broker_reports_repaired_and_remaining_fields(self):
        store = Mock()
        store.issue_metadata_summary.side_effect = [
            {"issueCount": 2, "missingTitleCount": 2, "missingDateCount": 1, "missingTitleIssues": ["1", "2"], "missingDateIssues": ["2"]},
            {"issueCount": 2, "missingTitleCount": 1, "missingDateCount": 0, "missingTitleIssues": ["2"], "missingDateIssues": []},
        ]
        store.get_series_sync_context.return_value = {"title": "Example", "year": 2026}
        with patch("app.catalog_store", return_value=store), patch(
            "app.sync_gcd_issue_catalog",
            return_value={"provider": "gcd", "issueCount": 2, "metadata": {"status": "partial"}},
        ), patch("app.load_provider_config", return_value={}):
            result = sync_issue_catalog(7)
        self.assertEqual(result["metadata"]["repairedTitleCount"], 1)
        self.assertEqual(result["metadata"]["repairedDateCount"], 1)
        self.assertEqual(result["metadata"]["missingTitleIssues"], ["2"])
        self.assertEqual(result["metadata"]["status"], "partial")
        detail = store.update_issue_catalog_detail.call_args.args[1]
        self.assertIn("Still unavailable", detail)
        self.assertIn("#2", detail)

    def test_optional_provider_configuration_is_local_and_masked(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.PROVIDER_CONFIG_PATH", Path(temp_dir) / "providers.json"
        ), patch.dict("app.os.environ", {"METRON_API_TOKEN": "", "COMIC_VINE_API_KEY": ""}):
            save_provider_config("metron", {"token": "secret-token", "enabled": True, "priority": 20})
            public = public_provider_config()
            metron = next(item for item in public["providers"] if item["id"] == "metron")
            self.assertTrue(metron["configured"])
            self.assertTrue(metron["enabled"])
            self.assertNotIn("token", metron)
            self.assertNotIn("secret-token", str(public))
            self.assertEqual((Path(temp_dir) / "providers.json").stat().st_mode & 0o777, 0o600)

    def test_acquisition_service_configuration_is_local_and_masked(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.ACQUISITION_CONFIG_PATH", Path(temp_dir) / "acquisition-services.json"
        ) as config_path:
            public = save_acquisition_service_config("prowlarr", {
                "url": "http://comic-nas.local:9696/",
                "apiKey": "prowlarr-secret",
                "enabled": True,
            })

            prowlarr = next(service for service in public["services"] if service["id"] == "prowlarr")
            self.assertEqual(prowlarr["url"], "http://comic-nas.local:9696")
            self.assertTrue(prowlarr["configured"])
            self.assertTrue(prowlarr["enabled"])
            self.assertEqual(prowlarr["credentialHint"], "Saved locally")
            self.assertNotIn("apiKey", prowlarr)
            self.assertNotIn("prowlarr-secret", str(public))
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_prowlarr_connection_uses_api_key_header(self):
        with patch("app.load_acquisition_service_config", return_value={
            "prowlarr": {"url": "http://comic-nas.local:9696", "apiKey": "saved-key"}
        }), patch("app.fetch_json_with_headers", return_value={"version": "1.36.3"}) as fetch:
            result = test_acquisition_service_connection("prowlarr")

        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["detail"], "Connected to Prowlarr 1.36.3.")
        url, headers = fetch.call_args.args
        self.assertEqual(url, "http://comic-nas.local:9696/api/v1/system/status")
        self.assertEqual(headers["X-Api-Key"], "saved-key")

    def test_sabnzbd_connection_uses_version_endpoint_and_category(self):
        with patch("app.load_acquisition_service_config", return_value={
            "sabnzbd": {
                "url": "http://comic-nas.local:8080", "apiKey": "saved-key", "category": "graphic-novels"
            }
        }), patch("app.fetch_json_with_headers", return_value={"version": "4.5.3"}) as fetch:
            result = test_acquisition_service_connection("sabnzbd")

        self.assertEqual(result["status"], "connected")
        self.assertIn("SABnzbd 4.5.3", result["detail"])
        self.assertIn("graphic-novels", result["detail"])
        url, headers = fetch.call_args.args
        self.assertTrue(url.startswith("http://comic-nas.local:8080/api?"))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["mode"], ["version"])
        self.assertEqual(query["apikey"], ["saved-key"])
        self.assertNotIn("X-Api-Key", headers)

    def test_acquisition_service_url_rejects_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.ACQUISITION_CONFIG_PATH", Path(temp_dir) / "acquisition-services.json"
        ):
            with self.assertRaisesRegex(ValueError, "API key field"):
                save_acquisition_service_config("sabnzbd", {
                    "url": "http://admin:secret@comic-nas.local:8080",
                    "apiKey": "key",
                })

    def test_metron_matches_by_gcd_id_and_returns_issue_enrichment(self):
        context = {"title": "Absolute Batman", "year": 2024, "gcdSeriesId": "216143"}
        responses = [
            {"results": [{"id": 77, "series": "Absolute Batman (2024)", "year_began": 2024}]},
            {"id": 77, "resource_url": "https://metron.cloud/series/absolute-batman/"},
            {"results": [{"id": 701, "number": "1", "store_date": "2024-10-09", "image": "https://covers/1.jpg"}], "next": None},
        ]
        with patch("app.fetch_json_with_headers", side_effect=responses) as fetch:
            series_id, _, entries = _metron_issue_entries(context, "token")
        self.assertEqual(series_id, "77")
        self.assertEqual(entries[0]["publication_date"], "2024-10-09")
        self.assertEqual(entries[0]["cover"], "https://covers/1.jpg")
        self.assertIn("gcd_id=216143", fetch.call_args_list[0].args[0])

    def test_comic_vine_returns_named_issue_metadata(self):
        context = {"title": "Absolute Batman", "year": 2024}
        responses = [
            {"status_code": 1, "results": [{"id": 88, "name": "Absolute Batman", "start_year": 2024, "site_detail_url": "https://comicvine/88"}]},
            {"status_code": 1, "results": [{"id": 801, "name": "The Zoo", "issue_number": "1", "store_date": "2024-10-09", "image": {"medium_url": "https://covers/cv1.jpg"}, "site_detail_url": "https://comicvine/801"}], "number_of_total_results": 1},
        ]
        with patch("app.fetch_json_with_headers", side_effect=responses):
            volume_id, _, entries = _comic_vine_issue_entries(context, "key")
        self.assertEqual(volume_id, "88")
        self.assertEqual(entries[0]["title"], "The Zoo")
        self.assertEqual(entries[0]["publication_year"], 2024)

    def test_comic_vine_prefers_canonical_issue_count_over_incorrect_local_year(self):
        context = {
            "title": "Birthright", "year": 2015, "publisher": "Image Comics Inc.",
            "gcdIssueEntries": [{"number": str(number)} for number in range(1, 51)],
        }
        responses = [
            {"status_code": 1, "results": [
                {"id": 11, "name": "Birthright", "start_year": "2015", "count_of_issues": 8,
                 "publisher": {"name": "Delcourt"}, "site_detail_url": "https://comicvine/11"},
                {"id": 88, "name": "Birthright", "start_year": "2014", "count_of_issues": 50,
                 "publisher": {"name": "Image"}, "site_detail_url": "https://comicvine/88"},
            ]},
            {"status_code": 1, "results": [
                {"id": 801, "name": None, "issue_number": "1", "store_date": "2014-10-08"},
            ], "number_of_total_results": 1},
        ]
        with patch("app.fetch_json_with_headers", side_effect=responses):
            volume_id, _, entries = _comic_vine_issue_entries(context, "key")
        self.assertEqual(volume_id, "88")
        self.assertEqual(entries[0]["publication_year"], 2014)

    def test_provider_broker_classifies_a_numbered_only_run(self):
        store = Mock()
        store.issue_metadata_summary.side_effect = [
            {"issueCount": 2, "missingTitleCount": 2, "rawMissingTitleCount": 2,
             "missingDateCount": 0, "missingTitleIssues": ["1", "2"], "missingDateIssues": []},
            {"issueCount": 2, "missingTitleCount": 2, "rawMissingTitleCount": 2,
             "missingDateCount": 0, "missingTitleIssues": ["1", "2"], "missingDateIssues": []},
        ]
        store.get_series_sync_context.return_value = {"title": "Example", "year": 2020}
        store.apply_issue_list.return_value = {
            "provider": "metron", "issueCount": 2, "titleCount": 0,
        }
        with patch("app.catalog_store", return_value=store), patch(
            "app.sync_gcd_issue_catalog",
            return_value={"provider": "gcd", "status": "complete", "issueCount": 2, "titleCount": 0,
                          "metadata": {"status": "complete", "titleCount": 0}},
        ), patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "priority": 20, "token": "token"},
        }), patch("app._metron_issue_entries", return_value=(
            "9", "https://metron/9", [{"number": "1"}, {"number": "2"}],
        )):
            result = sync_issue_catalog(7)
        self.assertEqual(result["metadata"]["titlePolicy"], "numbered_only")
        self.assertEqual(result["metadata"]["missingTitleCount"], 0)
        self.assertEqual(store.apply_issue_list.call_args.kwargs["status"], "complete")
        detail_call = store.update_issue_catalog_detail.call_args
        self.assertEqual(detail_call.kwargs["title_policy"], "numbered_only")

    def test_issue_metadata_bulk_refresh_defers_without_losing_retained_data(self):
        entries = [
            {"number": "1", "provider_id": "101", "title": "Retained Title", "publication_year": 2024},
            {"number": "2", "provider_id": "102"},
        ]
        rate_limit = urllib.error.HTTPError(
            "https://www.comics.org/api/series/55/overview/", 429,
            "Too Many Requests", {"Retry-After": "120"}, None,
        )
        with patch("app.fetch_gcd_json", side_effect=rate_limit) as fetch:
            hydrated, metadata = _hydrate_gcd_issue_entries_with_status(entries, "55")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(metadata["status"], "deferred")
        self.assertEqual(metadata["retryAfterSeconds"], 120)
        self.assertEqual(hydrated[0]["title"], "Retained Title")
        self.assertNotIn("title", hydrated[1])

    def test_confirmed_collection_imports_separate_numbering_runs(self):
        def candidate(provider_id, title, count):
            return {
                "providerSeriesId": provider_id, "title": title, "yearBegan": 2010,
                "yearEnded": 2011, "publisher": "Example Press", "issueCount": count,
                "_series": {
                    "api_url": f"https://www.comics.org/api/series/{provider_id}/",
                    "active_issues": [f"https://www.comics.org/api/issue/{provider_id}{n}/" for n in range(1, count + 1)],
                    "issue_descriptors": [str(n) for n in range(1, count + 1)],
                },
            }

        candidates = [candidate("10", "Example", 3), candidate("11", "Example: Second Arc", 2)]
        store = Mock()
        store.ensure_provider_series_run.side_effect = [
            {"id": "7", "title": "Example", "created": False},
            {"id": "11", "title": "Example: Second Arc", "created": True},
        ]
        store.catalog.return_value = {"series": [{"id": "7", "family": None}]}
        store.reassign_matching_editions.return_value = {"moved": [], "count": 0}
        store.create_series_family.return_value = {"id": "5", "name": "Example"}
        store.set_story_structure.return_value = {
            "collection": {"id": "5", "name": "Example"}, "arcCount": 2,
        }
        with patch("app._find_gcd_series_runs", return_value=({
            "title": "Example", "publisher": "Example Press",
        }, candidates)), patch("app._hydrate_gcd_issue_entries", side_effect=lambda entries, *_: entries), patch("app.catalog_store", return_value=store):
            result = confirm_gcd_series_collection(7, ["10", "11"], "Example")
        self.assertEqual((result["runCount"], result["issueCount"]), (2, 5))
        store.create_series_family.assert_called_once_with("Example", [7, 11])
        arcs = store.set_story_structure.call_args.args[1]
        self.assertEqual([arc["name"] for arc in arcs], ["Original series", "Second Arc"])

    def test_confirmed_series_run_import_records_manual_confirmation_source(self):
        raw_series = {
            "name": "Example", "year_began": 2024, "year_ended": 2025,
            "api_url": "https://www.comics.org/api/series/55/",
            "active_issues": ["https://www.comics.org/api/issue/101/"],
            "issue_descriptors": ["1"],
        }
        store = Mock()
        store.apply_issue_list.return_value = {
            "seriesId": "7", "status": "complete", "issueCount": 1, "provider": "gcd",
        }
        with patch("app._find_gcd_series_runs", return_value=({}, [{
            "providerSeriesId": "55", "_series": raw_series,
        }])), patch("app._hydrate_gcd_issue_entries", side_effect=lambda entries, *_: entries), patch("app.catalog_store", return_value=store):
            result = confirm_gcd_series_run(7, "55")
        self.assertEqual(result["issueCount"], 1)
        self.assertEqual(store.apply_issue_list.call_args.kwargs["source"], "user-confirmed series run")

    def test_series_run_ranking_prefers_title_year_publisher_and_owned_overlap(self):
        def series(provider_id, name, year, issue_count, publishing_format="limited series", publisher=None):
            return {
                "name": name, "country": "us", "language": "en", "year_began": year,
                "year_ended": year + 1, "publishing_format": publishing_format,
                "publisher": publisher, "api_url": f"https://www.comics.org/api/series/{provider_id}/",
                "active_issues": [f"https://www.comics.org/api/issue/{provider_id}{number}/" for number in range(1, issue_count + 1)],
                "issue_descriptors": [str(number) for number in range(1, issue_count + 1)],
            }

        context = {
            "title": "Southern Bastards", "aliases": [], "year": 2014,
            "publisher": "Image Comics", "ownedIssueNumbers": [str(number) for number in range(1, 9)],
        }
        candidates = rank_gcd_series_runs(context, [
            series("10", "Southern Bastards", 2014, 20, publisher="Image Comics"),
            series("11", "Southern Bastards", 2025, 3, publisher="Other Press"),
            series("12", "Southern Bastards", 2014, 4, "collected edition", "Image Comics"),
        ])
        self.assertEqual([candidate["providerSeriesId"] for candidate in candidates], ["10", "11"])
        self.assertEqual(candidates[0]["ownedOverlap"], 8)
        self.assertEqual(candidates[0]["issueCount"], 20)
        self.assertEqual(candidates[1]["ownedOverlap"], 3)

    def test_series_run_ranking_collapses_cover_variants(self):
        candidate = rank_gcd_series_runs(
            {"title": "Example", "year": 2024, "publisher": None, "ownedIssueNumbers": ["1"]},
            [{
                "name": "Example", "country": "us", "language": "en", "year_began": 2024,
                "api_url": "https://www.comics.org/api/series/55/",
                "active_issues": [
                    "https://www.comics.org/api/issue/101/",
                    "https://www.comics.org/api/issue/102/",
                    "https://www.comics.org/api/issue/103/",
                ],
                "issue_descriptors": ["1", "1 [Variant Cover]", "2"],
            }],
        )[0]
        self.assertEqual(candidate["issueNumbers"], ["1", "2"])
        self.assertEqual(candidate["issueCount"], 2)

    def test_issue_catalog_sync_uses_verified_run_and_collapses_variants(self):
        store = Mock()
        store.get_series_sync_context.return_value = {
            "id": 7, "title": "Example", "year": 2024, "publisher": "Example Press",
            "aliases": [], "knownGcdIssueIds": ["101"], "gcdSeriesId": "55",
        }
        store.apply_issue_list.return_value = {
            "seriesId": "7", "status": "complete_to_date", "issueCount": 2, "provider": "gcd",
        }
        response = {
            "name": "Example", "country": "us", "language": "en", "year_began": 2024,
            "publishing_format": "ongoing series",
            "api_url": "https://www.comics.org/api/series/55/",
            "active_issues": [
                "https://www.comics.org/api/issue/101/",
                "https://www.comics.org/api/issue/102/",
                "https://www.comics.org/api/issue/103/",
            ],
            "issue_descriptors": ["1", "1 [Variant Cover]", "2"],
        }
        overview = [
            {"issue_id": 101, "number": "1", "key_date": "2024-10-01", "longest_story": {"title": "Chapter One"}},
            {"issue_id": 103, "number": "2", "key_date": "2024-11-01", "longest_story": {"title": "Chapter Two"}},
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app.fetch_gcd_json", side_effect=[response, overview]
        ) as fetch:
            result = sync_gcd_issue_catalog(7)
        self.assertEqual(result["issueCount"], 2)
        self.assertEqual(result["metadata"]["status"], "complete")
        self.assertEqual(fetch.call_count, 2)
        args = store.apply_issue_list.call_args.args
        self.assertEqual(args[:4], (7, "gcd", "55", "https://www.comics.org/api/series/55/"))
        self.assertEqual([(entry["number"], entry["provider_id"]) for entry in args[4]], [("1", "101"), ("2", "103")])
        self.assertEqual([entry["title"] for entry in args[4]], ["Chapter One", "Chapter Two"])
        self.assertEqual([entry["publication_date"] for entry in args[4]], ["2024-10-01", "2024-11-01"])

    def test_trade_filename(self):
        item = parse_filename(Path("Batman - I Am Gotham Vol 1 (2017) TPB.cbz"))
        self.assertEqual(item.title, "Batman I Am Gotham")
        self.assertEqual(item.volume, 1)
        self.assertEqual(item.year, 2017)
        self.assertEqual(item.format, "trade paperback")

    def test_omnibus_with_isbn(self):
        item = parse_filename(Path("Wolverine Omnibus Vol 1 978-1-302-95008-8.cbz"))
        self.assertEqual(item.title, "Wolverine")
        self.assertEqual(item.volume, 1)
        self.assertEqual(item.format, "omnibus")
        self.assertEqual(item.isbn, "9781302950088")

    def test_decodes_release_filename_and_recognizes_padded_issue(self):
        item = parse_filename(Path("Absolute%20Batman%20001%20(2024)%20(c2c)%20(GD-Pmack).cbz"))
        self.assertEqual(item.filename, "Absolute Batman 001 (2024) (c2c) (GD-Pmack).cbz")
        self.assertEqual(item.title, "Absolute Batman")
        self.assertEqual(item.issue, "1")
        self.assertEqual(item.year, 2024)
        self.assertIsNone(item.volume)

    def test_four_digit_padded_issue_is_not_a_volume(self):
        item = parse_filename(Path("Birthright%200006%20(2015)%20(Digital)%20(Pym-Empire).cbz"))
        self.assertEqual(item.title, "Birthright")
        self.assertEqual(item.issue, "6")
        self.assertIsNone(item.volume)

    def test_unmarked_three_digit_issue_is_not_split_into_a_false_series(self):
        item = parse_filename(Path("Fables.105.(2011).(Digital).(NahgaEmpire).cbz"))
        self.assertEqual(item.title, "Fables")
        self.assertEqual(item.issue, "105")
        self.assertEqual(item.year, 2011)

    def test_image_resolution_token_is_not_mistaken_for_publication_year(self):
        item = parse_filename(Path("Fables.141.(2014).(2048px..c2c).cbz"))
        self.assertEqual((item.title, item.issue, item.year), ("Fables", "141", 2014))

    def test_verified_sab_import_uses_series_folder_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "library"
            completed = root / "completed"
            source_folder = completed / "Saga.001.2012"
            library.mkdir()
            source_folder.mkdir(parents=True)
            source = source_folder / "Saga 001 (2012).cbz"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("001.jpg", b"comic-page")
            store = Mock()
            store.get_acquisition_job_context.return_value = {
                "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
                "issueNumber": "1", "issueTitle": "Chapter One", "existingDirectory": None,
            }
            with patch("app.catalog_store", return_value=store):
                result = import_downloaded_comic(
                    {"job_id": 7, "sab_storage": str(source_folder), "release_title": "Saga.001.2012"},
                    library_root=library, completed_root=completed,
                )

            destination = library / "Image Comics" / "Saga (2012)" / "Saga (2012) #001 - Chapter One.cbz"
            self.assertEqual(Path(result["destination"]).resolve(), destination.resolve())
            self.assertTrue(source.is_file())
            self.assertTrue(destination.is_file())
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertFalse(result["alreadyPresent"])

    def test_sab_import_rejects_a_different_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "library"
            completed = root / "completed"
            source_folder = completed / "Saga.002.2012"
            library.mkdir()
            source_folder.mkdir(parents=True)
            with zipfile.ZipFile(source_folder / "Saga 002 (2012).cbz", "w") as archive:
                archive.writestr("001.jpg", b"comic-page")
            store = Mock()
            store.get_acquisition_job_context.return_value = {
                "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
                "issueNumber": "1", "issueTitle": None, "existingDirectory": None,
            }
            with patch("app.catalog_store", return_value=store):
                with self.assertRaisesRegex(ValueError, "No downloaded comic confidently matched Saga #1"):
                    import_downloaded_comic(
                        {"job_id": 7, "sab_storage": str(source_folder), "release_title": "Saga.002.2012"},
                        library_root=library, completed_root=completed,
                    )
            self.assertEqual(list(library.rglob("*.cbz")), [])

    def test_completed_sab_job_is_only_fulfilled_after_verified_library_import(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Saga.001.2012", "sab_storage": None,
        }
        store = Mock()
        imported = {
            "source": "/downloads/complete/comics/Saga.001.2012/Saga.001.2012.cbz",
            "destination": "/comics/Image Comics/Saga (2012)/Saga (2012) #001 - Chapter One.cbz",
            "size": 1234, "sha256": "abc123", "alreadyPresent": False,
        }
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/Saga.001.2012",
            },
        ), patch("app.import_downloaded_comic", return_value=imported) as importer:
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "imported")
        importer.assert_called_once()
        states = [call.args[1] for call in store.update_acquisition_download.call_args_list]
        self.assertEqual(states, ["completed", "importing", "imported"])
        store.update_acquisition_job.assert_called_once_with(
            7, "fulfilled", "Imported and verified as Saga (2012) #001 - Chapter One.cbz"
        )

    def test_completed_sab_job_stays_failed_when_post_processing_fails(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Saga.001.2012", "sab_storage": None,
        }
        store = Mock()
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/Saga.001.2012",
            },
        ), patch(
            "app.import_downloaded_comic",
            side_effect=ValueError("Downloaded archive did not match Saga #1"),
        ):
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "failed")
        self.assertIn("did not match", result["error"])
        self.assertEqual(
            store.update_acquisition_download.call_args_list[-1].args[1], "failed"
        )
        store.update_acquisition_job.assert_called_once_with(
            7,
            "failed",
            "Import needs attention: Downloaded archive did not match Saga #1",
        )

    def test_segments_a_title_without_spaces(self):
        item = parse_filename(Path("strangetalentoflutherstrode_vol2.cbz"))
        self.assertEqual(item.title, "strange talent of luther strode")
        self.assertEqual(item.volume, 2)
        self.assertTrue(any("word boundaries" in warning for warning in item.warnings))

    def test_segments_joiners_and_plural_words_without_false_boundaries(self):
        item = parse_filename(Path("lockeandkey_heavenandearth_headgames.epub"))
        self.assertEqual(item.title, "locke and key heaven and earth head games")

    def test_batch_enrichment_uses_bounded_parallel_workers_and_preserves_order(self):
        files = [parse_filename(Path(f"Book Vol {number}.cbz")) for number in range(1, 5)]
        barrier = threading.Barrier(4)
        thread_ids = set()
        thread_lock = threading.Lock()

        def fake_enrich(item):
            with thread_lock:
                thread_ids.add(threading.get_ident())
            barrier.wait(timeout=2)
            return {"filename": item.filename}

        with patch("app.scan_folder", return_value=files), patch("app.enrich", side_effect=fake_enrich):
            results = batch_enrich("/unused")
        self.assertEqual([result["filename"] for result in results], [item.filename for item in files])
        self.assertEqual(len(thread_ids), 4)

    def test_batch_failure_is_isolated_to_one_file(self):
        files = [parse_filename(Path("Good Vol 1.cbz")), parse_filename(Path("Bad Vol 2.cbz"))]

        def fake_enrich(item):
            if item.filename.startswith("Bad"):
                raise RuntimeError("adapter failed")
            return {"parsed": {"filename": item.filename}, "recommendation": {"title": "Good"}}

        with patch("app.scan_folder", return_value=files), patch("app.enrich", side_effect=fake_enrich):
            results = batch_enrich("/unused")
        self.assertEqual(results[0]["recommendation"]["title"], "Good")
        self.assertIsNone(results[1]["recommendation"])
        self.assertIn("adapter failed", results[1]["errors"]["analysis"])

    def test_scan_ignores_unsupported_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Book Vol 1 TPB.cbz").touch()
            (root / "Book Vol 2.epub").touch()
            (root / "notes.txt").touch()
            files = scan_folder(folder)
            self.assertEqual(len(files), 2)
            self.assertEqual({item.extension for item in files}, {".cbz", ".epub"})

    def test_page_template_does_not_interpret_css_braces(self):
        rendered = PAGE.replace("__FOLDER__", "/tmp/comics").replace("__CHECKED__", "checked").replace("__CONTENT__", "ok")
        self.assertIn("color-scheme: dark", rendered)
        self.assertIn('/tmp/comics', rendered)
        self.assertIn("/api/pick-folder", rendered)

    def test_reads_epub_package_metadata(self):
        container = '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'''
        package = '''<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><meta name="cover" content="cover-image"/><dc:title>Locke and Key: Heaven and Earth</dc:title><dc:creator>Joe Hill</dc:creator><dc:publisher>IDW Publishing</dc:publisher><dc:identifier>9781684064366</dc:identifier><dc:description>A 72-page hardcover featuring “Open the Moon”.</dc:description></metadata><manifest><item id="cover-image" href="images/cover.jpg" media-type="image/jpeg"/></manifest></package>'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lockeandkey.epub"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("META-INF/container.xml", container)
                archive.writestr("OEBPS/content.opf", package)
                archive.writestr("OEBPS/images/cover.jpg", b"fake-jpeg")
            embedded = read_embedded_metadata(path)
            self.assertEqual(embedded["title"], "Locke and Key: Heaven and Earth")
            self.assertEqual(embedded["isbns"], ["9781684064366"])
            self.assertEqual(embedded["cover_member"], "OEBPS/images/cover.jpg")
            self.assertEqual(embedded["page_count"], 72)
            self.assertEqual(embedded["format"], "hardcover")
            self.assertEqual(embedded["named_contents"], ["Open the Moon"])
            lookup = lookup_identity(parse_filename(path), embedded)
            self.assertEqual(lookup.title, "Locke and Key: Heaven and Earth")
            self.assertEqual(lookup.isbn, "9781684064366")

    def test_archive_cover_prefers_named_cover_then_first_natural_page(self):
        with tempfile.TemporaryDirectory() as folder:
            named = Path(folder) / "named.cbz"
            with zipfile.ZipFile(named, "w") as archive:
                archive.writestr("10.jpg", b"ten")
                archive.writestr("2.jpg", b"two")
                archive.writestr("Front Cover.jpg", b"cover")
                archive.writestr("Back Cover.jpg", b"back")
            self.assertEqual(find_archive_cover_member(named), "Front Cover.jpg")

            pages = Path(folder) / "pages.cbz"
            with zipfile.ZipFile(pages, "w") as archive:
                archive.writestr("10.jpg", b"ten")
                archive.writestr("2.jpg", b"two")
            self.assertEqual(find_archive_cover_member(pages), "2.jpg")
            self.assertEqual(file_cover_info(pages)["source"], "comic file")

    def test_flags_empty_corrupt_and_pageless_cbz_archives(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder) / "empty.cbz"
            with zipfile.ZipFile(empty, "w"):
                pass
            self.assertEqual(inspect_file_health(empty)["code"], "empty_archive")

            corrupt = Path(folder) / "corrupt.cbz"
            corrupt.write_bytes(b"not a zip archive")
            self.assertEqual(inspect_file_health(corrupt)["code"], "corrupt_archive")

            pageless = Path(folder) / "pageless.cbz"
            with zipfile.ZipFile(pageless, "w") as archive:
                archive.writestr("ComicInfo.xml", "<ComicInfo/>")
            self.assertEqual(inspect_file_health(pageless)["code"], "no_image_pages")

    def test_comicinfo_year_does_not_override_collection_filename(self):
        parsed = parse_filename(Path("southernbastards_vol2.cbz"))
        lookup = lookup_identity(parsed, {"source": "ComicInfo.xml", "series": "Southern Bastards", "year": "2026"})
        self.assertEqual(lookup.title, "Southern Bastards")
        self.assertIsNone(lookup.year)

    def test_exact_isbn_dominates_candidate_score(self):
        parsed = parse_filename(Path("Book 978-1-681-23456-7.epub"))
        parsed.isbn = "9781684064366"
        score, reasons = score_candidate(parsed, {"title": "Different", "isbns": ["9781684064366"]}, 4)
        self.assertGreaterEqual(score, 100)
        self.assertIn("exact ISBN", reasons)

    def test_unrelated_first_result_is_rejected(self):
        parsed = parse_filename(Path("southernbastards_vol2.cbz"))
        parsed.title = "Southern Bastards"
        score, reasons = score_candidate(parsed, {"title": "SelectEditions--Volume 3 2000", "isbns": []}, 0)
        self.assertLess(score, 0)
        self.assertTrue(any(reason.startswith("title mismatch") for reason in reasons))

    def test_extracts_issue_range_from_edition_notes(self):
        claims = extract_issue_coverage(
            "Originally published in single magazine form as Southern bastards #5-8. Rated M."
        )
        self.assertEqual(claims[0]["series"], "Southern bastards")
        self.assertEqual(claims[0]["issues"], ["5", "6", "7", "8"])

    def test_embedded_epub_coverage_has_file_provenance(self):
        lookup = parse_filename(Path("/tmp/example.epub"))
        candidate = embedded_epub_candidate(lookup, {
            "source": "EPUB package metadata", "title": "Example Book One",
            "description": "Collects Example #1-4.", "isbns": [],
        })
        claim = candidate["matched_edition"]["coverage"][0]
        self.assertEqual(claim["source"], "EPUB package description")
        self.assertEqual(claim["confidence"], "file confirmed")

    def test_generic_issue_word_does_not_become_a_series_name(self):
        claims = extract_issue_coverage("Collects issues #1-4.")
        self.assertEqual(claims[0]["series"], "")
        self.assertEqual(claims[0]["issues"], ["1", "2", "3", "4"])

    def test_batch_page_renders_coverage(self):
        recommendation = {
            "title": "Southern Bastards",
            "subtitle": "Gridiron",
            "publisher": "Image Comics",
            "publication_year": 2015,
            "isbns": ["9781632152695"],
            "url": "https://openlibrary.org/works/example",
            "cover": None,
            "match_score": 120,
            "match_reasons": ["exact normalized title"],
            "matched_edition": {
                "isbn_13": ["9781632152695"],
                "number_of_pages": 128,
                "coverage": extract_issue_coverage("Collects Southern Bastards #5-8."),
            },
        }
        rendered = render_batch_results([{
            "parsed": {"path": "/tmp/book.cbz", "filename": "book.cbz", "title": "book"},
            "embedded_metadata": {}, "lookup_identity": {"title": "Southern Bastards"},
            "candidates": {"open_library": [recommendation]}, "recommendation": recommendation,
            "gcd_search_url": "https://www.comics.org/searchNew/?q=test",
        }])
        self.assertIn("Southern Bastards: Gridiron", rendered)
        self.assertIn("#5–8", rendered)
        self.assertIn("4 issues", rendered)

    def test_batch_page_prefers_file_cover_over_catalog_cover(self):
        recommendation = {
            "title": "Absolute Batman", "issue": "1", "record_type": "single_issue",
            "cover": "https://catalog.example/cover.jpg", "match_score": 100,
            "match_reasons": [], "matched_edition": {},
        }
        rendered = render_batch_results([{
            "parsed": {"path": "/tmp/issue.cbz", "filename": "issue.cbz", "title": "Absolute Batman"},
            "embedded_metadata": {}, "lookup_identity": {"title": "Absolute Batman"},
            "file_cover": {"source": "comic file", "url": "/api/file-cover?path=%2Ftmp%2Fissue.cbz"},
            "candidates": {}, "recommendation": recommendation,
            "gcd_search_url": "https://www.comics.org/searchNew/?q=test",
        }])
        self.assertIn("/api/file-cover?path=%2Ftmp%2Fissue.cbz", rendered)
        self.assertNotIn("https://catalog.example/cover.jpg", rendered)
        self.assertIn("Cover from comic file", rendered)

    def test_batch_page_prominently_flags_an_empty_archive(self):
        rendered = render_batch_results([{
            "parsed": {"path": "/tmp/empty.cbz", "filename": "empty.cbz", "title": "Empty"},
            "file_health": {
                "status": "error", "code": "empty_archive",
                "message": "Archive is empty (22 bytes) and contains no files or comic pages.",
            },
            "embedded_metadata": {}, "lookup_identity": {"title": "Empty"},
            "candidates": {}, "recommendation": None,
            "gcd_search_url": "https://www.comics.org/searchNew/?q=empty",
        }])
        self.assertIn("Empty Archive", rendered)
        self.assertIn("File problem:", rendered)
        self.assertIn("1 file</strong> failed structural checks", rendered)

    def test_open_library_falls_back_from_isbn_to_title(self):
        parsed = parse_filename(Path("lockeandkey.epub"))
        parsed.title = "Locke and Key: Heaven and Earth"
        parsed.isbn = "9781684064366"
        with patch("app.fetch_json", side_effect=[{"docs": []}, {"docs": [{"key": "/works/OL1W", "title": parsed.title}]}]) as fetch:
            results = search_open_library(parsed)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["search_query"], 'title:"Locke and Key: Heaven and Earth"')
        self.assertEqual(fetch.call_count, 2)

    def test_embedded_epub_can_be_an_unverified_fallback(self):
        parsed = parse_filename(Path("lockeandkey.epub"))
        embedded = {
            "source": "EPUB package metadata", "title": "Locke and Key: Heaven and Earth",
            "creators": ["Joe Hill"], "publisher": "IDW Publishing",
            "isbns": ["9781684064366"], "description": "Three collected stories.",
        }
        candidate = embedded_epub_candidate(parsed, embedded)
        self.assertEqual(candidate["source"], "EPUB package metadata")
        self.assertIn("not confirmed", candidate["verification_status"])
        self.assertGreaterEqual(candidate["match_score"], 70)
        self.assertIn("external collection source", candidate["coverage_status"])

    def test_google_books_is_disabled_without_api_key(self):
        parsed = parse_filename(Path("book.epub"))
        with patch("app.GOOGLE_BOOKS_API_KEY", ""), patch("app.fetch_json") as fetch:
            self.assertEqual(search_google_books(parsed), [])
            fetch.assert_not_called()

    def test_gcd_matches_collected_volume_and_labels_inferred_coverage(self):
        parsed = parse_filename(Path("lockeandkey_vol2_headgames.epub"))
        parsed.title = "Locke and Key Vol. 2: Head Games"
        series_response = {
            "results": [
                {
                    "name": "Locke & Key", "country": "us", "language": "en",
                    "publishing_format": "limited series", "binding": "saddle-stitched",
                    "active_issues": [f"https://www.comics.org/api/issue/{number}/" for number in range(1, 7)],
                    "issue_descriptors": [str(number) for number in range(1, 7)],
                },
                {
                    "name": "Locke & Key", "country": "us", "language": "en",
                    "publishing_format": "collected edition", "binding": "trade paperback",
                    "active_issues": ["https://www.comics.org/api/issue/100/", "https://www.comics.org/api/issue/200/"],
                    "issue_descriptors": ["1 - Welcome to Lovecraft", "2 - Head Games"],
                },
                {
                    "name": "Locke & Key: Head Games", "country": "us", "language": "en",
                    "publishing_format": "limited series", "binding": "saddle-stitched",
                    "active_issues": [f"https://www.comics.org/api/issue/{300 + number}/" for number in range(1, 7)],
                    "issue_descriptors": [str(number) for number in range(1, 7)],
                },
            ]
        }
        issue_response = {
            "api_url": "https://www.comics.org/api/issue/200/",
            "series_name": "Locke & Key (2008 series)", "descriptor": "2 - Head Games",
            "number": "2", "volume": "2", "title": "Head Games",
            "key_date": "2010-10-06", "page_count": "164.000",
            "indicia_publisher": "IDW Publishing", "isbn": "978-1-60010-761-0",
            "cover": "https://files1.comics.org/cover.jpg",
            "story_set": [
                {"type": "comic story", "title": f"Chapter {number}", "script": "Joe Hill", "pencils": "Gabriel Rodriguez"}
                for number in range(1, 7)
            ],
        }

        def response(url):
            return issue_response if url.endswith("/issue/200/") else series_response

        with patch("app.fetch_gcd_json", side_effect=response):
            candidates = search_gcd(parsed)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["subtitle"], "Head Games")
        self.assertEqual(candidate["cover"], "https://files1.comics.org/cover.jpg")
        self.assertEqual(candidate["matched_edition"]["number_of_pages"], 164)
        coverage = candidate["matched_edition"]["coverage"][0]
        self.assertEqual(coverage["issues"], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(coverage["confidence"], "inferred")
        self.assertIn("not an explicit GCD", candidate["coverage_status"])

    def test_gcd_single_issue_path_rejects_collected_editions(self):
        parsed = parse_filename(Path("Absolute%20Batman%20001%20(2024)%20(c2c).cbz"))
        series_response = {
            "results": [
                {
                    "name": "Absolute Batman", "country": "us", "language": "en",
                    "publishing_format": "ongoing series", "binding": "saddle-stitched",
                    "active_issues": ["https://www.comics.org/api/issue/501/"],
                    "issue_descriptors": ["1"],
                },
                {
                    "name": "Absolute Batman", "country": "us", "language": "en",
                    "publishing_format": "collected edition", "binding": "hardcover",
                    "active_issues": ["https://www.comics.org/api/issue/999/"],
                    "issue_descriptors": ["1"],
                },
            ]
        }
        issue_response = {
            "api_url": "https://www.comics.org/api/issue/501/", "series_name": "Absolute Batman",
            "descriptor": "1", "number": "1", "title": "The Zoo",
            "key_date": "2024-10-00", "indicia_publisher": "DC Comics",
            "story_set": [
                {"type": "cover", "pencils": "Variant Artist (credited)"},
                {"type": "promo", "script": "? (promo copy)", "pencils": "various"},
                {
                    "type": "comic story", "title": "The Zoo, Part One of Five",
                    "script": "Scott Snyder (credited)",
                    "pencils": "Nick Dragotta (credited) (signed as DRAGoTTA .)",
                    "inks": "Nick Dragotta (credited)", "colors": "Frank Martin (credited)",
                    "letters": "Clayton Cowles (credited)",
                },
            ], "cover": "https://files1.comics.org/absolute-batman-1.jpg",
        }

        def response(url):
            return issue_response if url.endswith("/issue/501/") else series_response

        with patch("app.fetch_gcd_json", side_effect=response):
            candidates = search_gcd(parsed)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_id"], "501")
        self.assertEqual(candidates[0]["record_type"], "single_issue")
        self.assertEqual(candidates[0]["issue"], "1")
        self.assertIsNone(candidates[0]["volume"])
        self.assertEqual(
            candidates[0]["creators"],
            ["Scott Snyder", "Nick Dragotta", "Frank Martin", "Clayton Cowles"],
        )
        self.assertEqual(candidates[0]["cover_contributors"], ["Variant Artist"])
        self.assertEqual(candidates[0]["coverage_status"], "single issue; volume coverage does not apply")
        self.assertIn("single issue matched", candidates[0]["verification_status"])

    def test_single_issue_confidence_explains_cross_source_year_difference(self):
        parsed = parse_filename(Path("Absolute%20Batman%20003%20(2025).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman", "number": "3",
            "year": "2024", "publisher": "DC Comics", "title": "The Zoo, Part Three",
        }
        candidate = {
            "record_type": "single_issue", "title": "Absolute Batman", "issue": "3",
            "publication_year": 2024, "publication_date": "2024-12-18", "publisher": "DC Comics",
            "descriptor": "3 [Nick Dragotta Cover]", "named_contents": ["The Zoo, Part Three of Five"],
        }
        confidence = assess_identity_confidence(parsed, embedded, candidate)
        self.assertEqual(confidence["level"], "high")
        self.assertGreaterEqual(confidence["score"], 90)
        self.assertTrue(any("filename says 2025" in conflict for conflict in confidence["conflicts"]))
        self.assertTrue(any("ComicInfo.xml year agrees" in item for item in confidence["corroborated_fields"]))

    def test_comicinfo_keeps_single_issue_match_when_gcd_is_unavailable(self):
        parsed = parse_filename(Path("Absolute%20Batman%20003%20(2025).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman", "title": "The Zoo, Part Three",
            "number": "3", "year": "2024", "publisher": "DC Comics",
            "web": "https://metron.cloud/issue/132445/", "metron_id": "8477",
            "contributors": {
                "writer": "Scott Snyder", "penciller": "Nick Dragotta",
                "colorist": "Frank Martin", "letterer": "Clayton Cowles",
            },
        }
        with patch("app.read_embedded_metadata", return_value=embedded), patch("app.search_gcd", return_value=[]):
            result = enrich(parsed)
        recommendation = result["recommendation"]
        self.assertEqual(recommendation["source"], "ComicInfo.xml")
        self.assertEqual(recommendation["record_type"], "single_issue")
        self.assertEqual(recommendation["issue"], "3")
        self.assertEqual(recommendation["identity_confidence"]["level"], "medium")
        self.assertIn("not confirmed by an external catalog", recommendation["verification_status"])

    def test_comicinfo_fallback_suppresses_a_redundant_issue_title(self):
        parsed = parse_filename(Path("Absolute%20Batman%20001%20(2024).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman",
            "title": "Absolute Batman (2024) #1", "number": "1", "year": "2024",
            "publisher": "DC Comics", "contributors": {},
        }
        with patch("app.read_embedded_metadata", return_value=embedded), patch("app.search_gcd", return_value=[]):
            result = enrich(parsed)
        self.assertIsNone(result["recommendation"]["subtitle"])
        self.assertEqual(result["recommendation"]["named_contents"], [])


if __name__ == "__main__":
    unittest.main()
