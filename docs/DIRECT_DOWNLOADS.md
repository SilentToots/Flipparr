# Direct downloads — a site you configure

Flipparr can search one direct-download site beside Usenet and torrents, for
issues your indexers do not carry. **No site is built in.** The address field
starts empty and the source is off; it does nothing until the operator enters a
site and switches it on under Settings → Acquisition services → Direct
downloads. Which site that is, and whether using it is lawful where they live
and allowed by the site's terms, is the operator's decision and
responsibility -- the same boundary as the indexers configured in Prowlarr.

Installs from before 2026-10-03 kept their saved site: the setting, the source
order and the download records moved to the neutral name `direct_site` in
schema 64 (`load_acquisition_service_config`, `load_app_settings`,
`catalog_store.py`), and the staging folder moves the first time it is used.

## What a site has to offer

- **A search feed.** Flipparr asks `<site>/?s=<query>&feed=rss2` and reads
  each item's title, link and size (`direct_site_search`). An answer that is
  not a feed -- a browser check, an error page -- is refused, never read as
  "found nothing".
- **Posts with download links** under a `/dls/` path, read by
  `_DIRECT_SITE_LINK`. Links to file-hosting mirrors are not followed.

Results are scored like any release (`_release_candidate_score`), so the
language, year and issue checks apply unchanged, and a post below the bar that
names the series is set aside for a person to take by hand.

## Why it is not an indexer setting

Prowlarr manages Usenet and torrent indexers only, so a direct-download site
cannot arrive through it. It is a download path of its own beside SABnzbd and
qBittorrent: Flipparr fetches the file over HTTP into its own staging folder,
tracks progress itself (`acquisition_downloads.bytes_fetched`), and hands the
folder to the same import check every download goes through.

## The page fetcher

Some sites answer a scripted request for their search feed but show post pages
only to a full browser. Three ways past that were weighed (2026-09-15):

- **A page fetcher you run** -- a FlareSolverr-compatible service. Flipparr
  posts a URL and gets the page back, and stays browser-free. It fits the
  service-card pattern beside Prowlarr and SABnzbd, and it is the operator's
  container to run or not. **This is the path taken**, as an optional service.
- A headless browser inside the image: hundreds of megabytes, often blocked
  anyway, and a poor thing to put in an image meant for public distribution.
- A browser cookie pasted by the user: bound to their address and browser,
  expires, and breaks silently.

Without a page fetcher, results are listed but marked "Not fetchable yet".

## Packs

Direct-download sites often post whole runs. `_choose_downloaded_comic` picks
the wanted issue out of a folder, so a run pack can satisfy one missing issue;
when pulling a run, packs are weighed from every source -- see
[complete-run packs](RUN_PACKS_FUTURE.md). There is no par2, so a truncated
file only shows up at import, where the incomplete-download check catches it.
