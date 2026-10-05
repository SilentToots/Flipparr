# Fulfillment proofs

What Flipparr's downloading and importing do when the NAS does what NASes do —
a restart in the middle of things, a download client that goes away, a
download that is not what it claims — and how each behaviour is proven for
this image. This is the evidence for Gate 3 in
[RELEASE_TRACK.md](RELEASE_TRACK.md). Written 2026-10-05.

## How it is proven

- **Unit and contract tests** (`test_app.py`, `test_catalog_store.py`,
  `test_torrent_client.py`, `test_http_contract.py`), run in CI on every push
  and inside the release image on the NAS before every deploy.
- **The drill** (`tools/fulfillment_drill.py`) starts the real server as its
  own process, beside fake SABnzbd and Prowlarr, in a scratch folder in a
  throwaway container with no network, and puts it through the scenarios
  below, including a `SIGKILL`. It touches no library and no real download
  client:

  ```
  docker run --rm --network none --read-only --tmpfs /tmp:size=512m \
    -e HOME=/tmp -v ~/flipparr-build:/src:ro -v "$OUT":/out -w /src \
    --entrypoint python3 flipparr:candidate -B tools/fulfillment_drill.py --out /out
  ```

  Against the build before these fixes (fcf15e1c) it held 1 of 6 scenarios.
  It also runs in CI on every push.

A review of the first round (2026-10-05) found five flaws in it, each fixed
with a test that fails on the first version: one issue Prowlarr errors on
held up every search behind it; the RAR and 7-Zip password check read only
the archive tool's last line, which says nothing, and its test had made the
message up; the qBittorrent sign-in retry was still banned, only later; a
refused qBittorrent password was told to nobody; and trouble was worded
wrongly or left listed. The tables below describe the corrected behaviour.

## Restarts

| What happens | What Flipparr does | Proven by |
|---|---|---|
| Killed while SABnzbd downloads | The row and SABnzbd keep the state; the next pass asks SABnzbd and carries on | drill: *hard kill mid-download and mid-search* |
| Killed in the middle of a search | The search goes back on the queue at start-up, due at once, the unfinished try not counted | drill (same); `test_a_search_cut_short_by_a_restart_is_due_again_at_once` |
| Killed between an import's copy and its rename | The half-written copy (`.<name>.flipparr-….partial.cbz`) is never catalogued; the next import of that comic removes it, and the next start removes any that are left | drill (same); `ImportRestartSafetyTests` |
| Killed while importing a direct download | Imported again from its folder; a copy already in place counts as imported | `test_an_import_a_restart_cut_off_is_done_again` |
| Killed during a direct download | Fetched again from the start, once | `DirectDownloadRecoveryTests.test_a_restart_starts_the_fetch_again_from_the_row` |
| Killed while a replacement swaps files | The original is in quarantine, never deleted; a retry after the swap finds itself done; a failure after it puts the original back and keeps the new copy | `test_a_replacement_retried_after_its_swap_finished_is_already_done`, `test_a_replacement_that_fails_after_the_swap_puts_the_original_back` |
| A torrent in flight at a restart | Followed by its hash in qBittorrent; never restarted as a direct download | `test_a_torrent_is_in_use_while_an_issue_downloads_from_it_or_keeps_it_as_evidence` |

`docker stop` does not wait for an import in progress. Every step above is
safe to repeat, so the next start finishes the work instead.

## Outages

| What happens | What Flipparr does | Proven by |
|---|---|---|
| SABnzbd or qBittorrent drops the connection, answers half a reply or garbage, or is turned off in Settings | Downloads wait; nothing is failed; one log line a pass. Turned off is said as turned off. An error from inside a comic being imported is that download's own, whatever its type | drill: *sabnzbd drops the line*; `test_a_download_client_that_drops_the_line_fails_no_download`, `test_a_client_turned_off_is_said_as_turned_off`, `test_a_json_error_from_inside_an_import_is_the_downloads_own` |
| SABnzbd refuses the API key (it answers 200 with an error) | Not read as an empty queue, so no download is given up as lost | drill: *sabnzbd refuses the API key*; `test_sabnzbd_refusing_the_api_key_is_not_every_download_lost` |
| Prowlarr does not answer | The search goes back without counting against the issue; a sweep stops once a second issue confirms the silence; a search by hand says so and leaves the issue queued | drill: *prowlarr down while searching by hand*; `ServiceOutageTests`, `test_a_search_by_hand_during_an_outage_does_not_fail_the_issue`, `test_a_search_the_indexer_never_answered_does_not_count` |
| Prowlarr errors on one issue's search only | When the next issue is answered, the failure is counted as that issue's own; the issues behind it are searched | drill: *one issue prowlarr cannot search*; `test_one_issue_prowlarr_cannot_search_does_not_hold_up_the_rest`, `test_an_unanswered_search_that_was_the_issues_own_is_counted_after_all` |
| A service stays silent | Named on Settings → System at once; every admin's bell after 30 minutes, once an outage | drill (SABnzbd, Prowlarr); `test_the_admins_hear_of_an_outage_once_it_has_lasted`, `test_the_bell_says_what_waits` |
| qBittorrent refuses the username and password | Tried once, then not again until the credentials are saved or a Test connects (qBittorrent's count of failed sign-ins never runs down, so any retry on a clock is banned in the end); every admin's bell says so at once; a ban already in force is waited out | `test_refused_credentials_are_never_tried_again_by_a_client_that_holds_them`, `test_a_ban_is_waited_out_and_then_asked_again`, `test_a_refused_qbittorrent_sign_in_is_told_at_once_and_says_what_to_do`, `test_a_test_that_connects_lets_the_workers_sign_in_again` |
| The download's trouble ends, or its download is stopped | A whole pass with nothing wrong clears it from Settings → System | `test_trouble_goes_when_the_download_that_was_waiting_is_gone` |
| The download site is unreachable | The fetch fails, the release is set aside for a day, the issue is searched again | `DirectSiteDownloadTests.test_a_failed_fetch_leaves_the_issue_wanted_and_says_why` |

## Bad downloads

Every refusal is made before anything reaches the library. A refused
download is kept as evidence (seven days, or until the issue arrives another
way), its release is recorded, and the next strong release is grabbed, at
most three times an issue.

| What arrives | What Flipparr does | Proven by |
|---|---|---|
| Password-protected archive (any source) | Refused, release barred. A zip says so in its own flags; RAR and 7-Zip are asked of bsdtar, and all it says is read | drill: *password-protected download* (a zip); `test_a_password_protected_download_is_refused_and_bars_its_release`; `test_the_archive_tool_is_believed_on_everything_it_says_not_its_last_line` (the real tool on a really encrypted archive -- a zip, as nothing free writes an encrypted RAR; **an encrypted RAR itself has not been tried**); SABnzbd's own pause: `test_a_download_sabnzbd_paused_as_encrypted_is_refused_and_the_next_release_tried` |
| Another issue or series | Refused, release barred | `test_sab_import_rejects_a_different_issue`, `DownloadedFileMatchTests`, `WrongDownloadIsNotOfferedAgainTests` |
| Another language | Refused, release barred | `test_a_download_labelled_in_another_language_is_refused_at_import` |
| Missing data (zero-filled) | Refused as incomplete, release barred | `test_a_zero_filled_download_is_incomplete_and_bars_its_release` |
| Unreadable, or nothing names the issue | Set aside for a day (perhaps only beyond this machine) | `test_an_unreadable_file_is_unproven`, `test_two_files_that_name_nothing_are_not_guessed_between` |
| No comic at all (download or torrent) | Refused, release barred | `test_a_download_with_no_comic_in_it_is_refused_as_such`, `test_a_torrent_with_no_comic_in_it_is_refused_and_removed` |
| Too large | Never grabbed | `PackEligibilityTests` |
| Would leave the disk below its floor | Nothing written | `test_an_import_that_would_fill_the_disk_writes_nothing` |

## The torrents mount

`compose.yaml` mounts qBittorrent's comics category folder read-only, and
Flipparr never writes or deletes there: a torrent keeps seeding after import
(`test_a_torrent_seeds_on_after_import_and_is_not_swept_for_other_issues`).
An older compose file without the line, or a missing host folder, leaves
Flipparr without it:

- Settings → qBittorrent → Test says Flipparr cannot read its torrents
  folder (`test_the_connection_test_says_when_flipparr_cannot_read_the_torrents_folder`);
- a finished torrent Flipparr cannot see is named on Settings → System, and
  in the admins' bell after 30 minutes
  (`test_a_finished_download_flipparr_cannot_see_is_a_folder_in_trouble`);
- a torrent saved outside the category's folder waits and says where
  (`test_a_torrent_saved_outside_the_comics_folder_waits_and_says_where`).

## Intended, and why

- **The hourly seeding sweep removes Flipparr's own unfinished torrents that
  no download row holds** — those of a cancelled or retried request. After a
  restore from an older backup that can include one still downloading; its
  issue is searched and grabbed again. Its files were download data, never
  the library.
- **A direct download interrupted by a restart starts again from zero.** The
  download site gives no resume point to trust.
- **A SABnzbd that answers properly but has forgotten a download** (a new
  instance, its history cleared) is believed after ten minutes: the download
  is given up as lost and the next release grabbed.

## Results for this image

Recorded per build in `~/flipparr-intake/drill-<build>/` on the NAS
(`fulfillment-drill.json` and the server's log); the latest is summarised in
[RELEASE_TRACK.md](RELEASE_TRACK.md) under Gate 3.
