# Operating SonicBoom

Install, configure, back up and troubleshoot a SonicBoom instance.

Every procedure here was executed against a real 963-file library on a Synology
NAS before being written down; the verification results are in
`docs/V1_PIVOT_ASSESSMENT.md`. Where behaviour is surprising, the reason is
given rather than just the workaround.

---

## 1. Install

SonicBoom ships as a Docker image and keeps all of its state in one `/config`
volume. It needs read access to your comics, and read access to your download
client's completed folder if you want automatic import.

```bash
cp .env.example .env      # set COMICS_PATH, SAB_COMPLETE_PATH, CONFIG_PATH
docker compose up -d
```

Then open the port you published (`SONICBOOM_PORT`, default 8787) and add your
library folder under **Settings → Library folders → Manage folders**.

### Volumes

| Mount | Mode | Purpose |
| --- | --- | --- |
| `/config` | read-write | database, settings, credentials, caches. **This is the only thing you need to back up.** |
| `/comics` | read-write | your library. Written to only when importing a completed download. |
| `/downloads/complete/comics` | read-only | where the download client puts finished files |

### Permissions

The image runs as `1000:10` by default. `/config` must be writable by whatever
user the container runs as — set `PUID`/`PGID` to match the owner of your
config directory.

This matters more than it looks. Config files created by one user are not
necessarily readable by another, and an unreadable `auth.json` is treated as a
hard failure (see [Troubleshooting](#5-troubleshooting)) rather than silently
falling back to "no authentication".

### Configuration

All settings have working defaults; you only need these if you are changing paths.

| Variable | Default | Notes |
| --- | --- | --- |
| `COMICARR_DATABASE` | `/config/comicarr.db` | catalog database |
| `COMICARR_PROVIDER_CONFIG` | `/config/metadata-providers.json` | metadata source credentials |
| `COMICARR_ACQUISITION_CONFIG` | `/config/acquisition-services.json` | Prowlarr / SABnzbd credentials |
| `COMICARR_SETTINGS_CONFIG` | `/config/settings.json` | application preferences |
| `COMICARR_AUTH_CONFIG` | `/config/auth.json` | sign-in configuration |
| `COMICARR_TRUSTED_PROXIES` | *(empty)* | **See [Behind a reverse proxy](#behind-a-reverse-proxy).** |
| `COMICARR_LIBRARY_ROOT` | `/comics` | library mount |
| `COMICARR_SAB_COMPLETE_ROOT` | `/downloads/complete/comics` | completed-download mount |
| `COMICARR_IMPORT_POLL_SECONDS` | `15` | how often to look for completed downloads |
| `COMICARR_MIN_FREE_SPACE_MB` | `100` | refuse to import below this free space |
| `COMICARR_GCD_MIN_INTERVAL_SECONDS` | `1.5` | minimum gap between Grand Comics Database requests |
| `COMICARR_METRON_MIN_INTERVAL_SECONDS` | `3.2` | Metron documents 20 requests/minute |
| `COMICARR_COMIC_VINE_MIN_INTERVAL_SECONDS` | `1.1` | Comic Vine burst control |

`GET /healthz` is unauthenticated and is what the container health check uses.

---

## 2. Authentication

**Off by default.** Turn it on under **Settings → Security**.

The app stores your metadata and download-client API keys and can start usenet
downloads, so anything that can reach the port can spend your usenet quota and
change your library. If the port is reachable by anything other than you, turn
sign-in on.

- **Require sign-in** — `none` or `forms`. Set a username and password first.
- **Skip sign-in on local addresses** — convenient on a home network or tailnet.

Passwords are stored as a salted scrypt hash and never returned by the API.
Signing in sets an `HttpOnly`, `SameSite=Lax` cookie, so page scripts cannot
read your session, and cross-site requests do not carry it.

### Behind a reverse proxy

**Read this before enabling "skip sign-in on local addresses" behind a proxy.**

A reverse proxy makes every request appear to come from the proxy. If the app
believed the `X-Forwarded-For` header unconditionally, anyone could send
`X-Forwarded-For: 127.0.0.1` and skip sign-in entirely — so the app ignores that
header by default.

The consequence: behind a proxy, with `COMICARR_TRUSTED_PROXIES` unset, either
every request looks like it came from the proxy's own (private) address and the
local bypass applies to everyone, or no request looks local at all.

Set it to your proxy's address:

```yaml
COMICARR_TRUSTED_PROXIES: "172.18.0.5"    # your reverse proxy's address
```

Then the app reads the real client address from `X-Forwarded-For`, and the
bypass means what you expect. It also picks up `X-Forwarded-Proto: https` from a
trusted proxy and marks the session cookie `Secure`.

If you are not sure of the proxy's address, leave the variable unset and turn
the local bypass **off** — you will sign in once per device, which is correct
and safe.

---

## 3. Providers and acquisition

**Settings → Metadata sources.** The Grand Comics Database works with no
account. Metron and Comic Vine are optional and need an API key; adding one
improves issue titles, dates and covers.

**Settings → Acquisition services.** Prowlarr finds releases, SABnzbd downloads
them. Both take a URL and an API key, and both have a **Test** button that
checks the connection before you save.

Credentials are write-only: after saving, the API returns only whether a
provider is configured, never the key itself. They are not baked into the image,
not placed in environment variables, and not written to logs.

### Rate limits

Provider responses are cached on disk under `/config`, so a rescan of series you
have already matched does not repeat the same lookups, and the cache survives
restarts. Requests are paced per provider.

If a provider asks the app to slow down, it backs off and keeps serving your
library from what it already knows. You will see a banner saying metadata lookup
is paused, with the time it will resume. **This is normal and self-heals** —
nothing is lost, and enrichment continues once the provider allows it.

---

## 4. Library import, backup and upgrade

### Scanning

Two modes:

- **`local`** — filename and embedded metadata only, no provider calls. Fast,
  and the right choice for a first pass over a large library.
- **`full`** — also queries your enabled metadata providers. Slower, and subject
  to provider rate limits.

Files are never renamed or moved by a scan.

### Backup and restore

Everything is in `/config`. Stop the container, archive the directory, start it
again:

```bash
docker compose stop sonicboom
tar -czf sonicboom-backup-$(date +%F).tgz -C /path/to config
docker compose start sonicboom
```

To restore, put the directory back and start the container. *Verified: a
destroy-and-restore cycle reproduced the catalog exactly, byte-for-byte on a
963-file library.*

### Upgrading and rolling back

```bash
docker compose pull && docker compose up -d
```

Back up `/config` first. The database migrates forward on start.

To roll back, restore your backup and run the previous image tag. *Verified: the
previous release image runs a config an upgraded build has already opened,
unchanged.* Rollback is clean because an upgrade does not write anything into
`/config` that an older build cannot ignore.

---

## 5. Troubleshooting

**Every series shows "Status unknown".**
Publication status comes from provider metadata. After a `local` scan, or while
a provider is rate-limiting you, it will not be filled in. It backfills on its
own as enrichment runs. Most of a freshly scanned library sitting at "Status
unknown" is expected, not a fault.

**Covers are missing or blank.**
Covers are extracted from the comic files themselves, so this almost always
means the `/comics` mount is wrong or unreadable. Check that the path inside the
container matches what you configured, and that the container's user can read it.

**`503 Authentication is misconfigured`.**
The app found `/config/auth.json` but could not read or parse it — usually
because the file is owned by a different user than the one the container runs
as. This most often happens after running the container as root once, or after
restoring a backup as a different user. The error names the file and the cause.

Fix the ownership to match `PUID`/`PGID`, or delete `auth.json` to reset to no
authentication and configure it again.

The app deliberately refuses to serve rather than fall back to "no
authentication", so a permissions problem cannot quietly leave your instance
unprotected. `/healthz` keeps answering so the container does not restart-loop.

**Sign-in is not being asked for, even though it is enabled.**
If "skip sign-in on local addresses" is on, requests from private addresses are
allowed through. Behind a reverse proxy this can mean *all* requests. See
[Behind a reverse proxy](#behind-a-reverse-proxy).

**Discover returns an error while browsing works.**
A metadata provider is unavailable or rate-limiting. The request fails quickly
rather than hanging; your library is unaffected.

**The container reports unhealthy.**
The health check uses `GET /healthz`. If you have pinned an older image whose
health check used `/`, enabling authentication will make it fail — upgrade, or
point your health check at `/healthz`.

---

## 6. Known limitations

- **Filename parsing is heuristic.** It reports ambiguity rather than silently
  guessing. Anything uncertain appears under **Library health** with a
  **Fix match** action, and every file can be re-matched at any time.
- **Duplicate runs are possible.** A filename carrying a volume subtitle can
  form its own run. Combine them with **Advanced tools → Combine duplicate run**
  in the series drawer; files are not renamed or moved.
- **Collected editions are opt-in and less complete than issues.** Trades,
  hardcovers and omnibuses are always catalogued, but their management surfaces
  are hidden unless enabled in Settings. Their metadata and file availability
  are weaker than single issues, and they never fulfil issue ownership or
  acquisition.
- **Automatic acquisition covers single issues only.** Collected editions are
  imported from files you already own.
- **No rate limiting on sign-in.** Password hashing makes brute force expensive
  but does not stop it. Do not expose this instance directly to the internet;
  put it behind a VPN, a tailnet, or a proxy that provides its own protection.
- **Cover extraction supports ZIP-based comic formats** (`.cbz`). CBR and PDF
  cover rendering are not implemented.
- **Provider data is incomplete.** The Grand Comics Database does not expose a
  complete collected-edition-to-issue relationship, so inferred contents are
  labelled as inferred rather than presented as fact.
- **Results are candidates with provenance, not authoritative matches.**
