# Flipparr

Flipparr is a self-hosted comic library for one household: it catalogues the
comics you own, fills in their metadata, finds and downloads the issues you
are missing through the services you already run, and reads them -- full
screen, panel by panel if you like, right to left for manga, on a desktop or
a phone, remembering where everyone left off.

**Start here: [`docs/OPERATING.md`](docs/OPERATING.md)** -- install,
configuration, authentication, providers and download clients, backup and
restore, upgrade and rollback, troubleshooting, and known limitations.

It is pre-release software on a production track: `0.1.0` is the first
installable release, built and published by CI as
`ghcr.io/silenttoots/flipparr`. What the release promises, how each promise
is tested, and what is still open is in
[`docs/RELEASE_TRACK.md`](docs/RELEASE_TRACK.md).

## What it does

- **Library.** Points at the folders your comics are in and reads them where
  they are (`.cbz`, `.cbr`, `.cb7`, `.cbt`, `.pdf`, `.epub`); never renames,
  moves or deletes a file you put there. Filenames, embedded `ComicInfo.xml`
  and covers are first-class evidence; runs, issues and collected editions are
  grouped from them and confirmed against the Grand Comics Database, Metron,
  Comic Vine and Open Library as you connect them. Damaged files are shown
  as what they are, never hidden behind a good match.
- **Issues and editions.** One publication run holds both its single issues
  and its collected editions, with independent ownership. Issues are what
  Flipparr monitors, wants and downloads; collected editions are an opt-in
  feature for files you already own, because their metadata and availability
  are weaker.
- **Acquisition.** Follow a run and its missing issues are searched through
  Prowlarr and fetched by SABnzbd, qBittorrent (only the wanted issues of a
  pack), or a direct-download site you enter. Every download is validated
  against the issue it was for before it enters the library, and nothing is
  deleted until a replacement has passed.
- **Reading.** A full-screen reader with a page view and a panel view,
  per-profile progress, reading lists, story arcs and collections across
  runs, a weekly pull list, and a Discover shelf of what is out.
- **Household.** Plex-Home-style profiles with their own history, ratings
  and age limits; readers ask for comics and the admin approves. One sign-in,
  optionally bypassed on the home network; notifications in the app.

## What it needs

- Docker on one host (a NAS is the typical home), your comics on a mounted
  folder, and a folder for its state.
- Optional, each configured in Settings: Prowlarr with your indexers, SABnzbd
  and/or qBittorrent, metadata accounts for Metron and Comic Vine (GCD and
  Open Library need none), and an AI key if you want help with hard pages
  in panel view.

Flipparr ships no comics, catalogue data, indexers or download sources; every
service it talks to is one you configure, and nothing is sent to its authors
([`docs/PRIVACY.md`](docs/PRIVACY.md)). How to run it safely, what it defends
against and what it does not: [`docs/SECURITY.md`](docs/SECURITY.md).

## Running it

```bash
cp .env.example .env     # your paths and user
mkdir -p "$CONFIG_PATH"  # owned by the PUID:PGID in .env
docker compose up -d
```

The container serves the interface and the API from one process on port
8787, keeps its state under `/config`, reads and imports into your comics at
`/comics`, and reads your download clients' finished folders read-only until
a comic is verified in the library. Full details, a reverse-proxy example and
the first-run setup are in [`docs/OPERATING.md`](docs/OPERATING.md);
versions, upgrading and rolling back in [`docs/RELEASE.md`](docs/RELEASE.md).

## Developing

The server is `app.py` and the modules beside it (Python 3.13, dependencies
in `requirements.txt`); the interface is `v1-prototype/` (React, Vite; the
directory name is repository continuity only). From the repository root:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
python3 app.py            # the API on http://127.0.0.1:8787
cd v1-prototype && npm ci && npm run dev   # the interface on http://127.0.0.1:4173, proxying /api
```

Tests: `python3 -B -m unittest` at the root (CI runs the suites in both
orders) and `npm test` in `v1-prototype/`, where `DESIGN_SYSTEM.md` holds
the interface's rules and the visual, layout and accessibility harnesses.
The release gates and the drills that prove them (`tools/`) are described in
[`docs/RELEASE_TRACK.md`](docs/RELEASE_TRACK.md). Guidance for working on
the codebase is in [`AGENTS.md`](AGENTS.md).

## Licence

Flipparr is free software under the GNU General Public License, version 3
([`LICENSE`](LICENSE)). Third-party software, fonts, icons and data sources
are listed with their licences in [`NOTICE`](NOTICE).

Flipparr organises and reads comics you have the right to use.
