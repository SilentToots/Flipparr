# Operating Flipparr

Install, configure, back up and troubleshoot a Flipparr instance.

Every procedure here was executed against a real 963-file library on a Synology
NAS before being written down; the verification results are in
`docs/V1_PIVOT_ASSESSMENT.md`. Where behaviour is surprising, the reason is
given rather than just the workaround.

---

## 1. Install

Flipparr ships as a Docker image and keeps all of its state in one `/config`
volume. It needs read-write access to your comics (see [Volumes](#volumes)) and
read access to your download client's completed folder if you want automatic
import.

```bash
cp .env.example .env      # set CONFIG_PATH, COMICS_PATH, SAB_COMPLETE_PATH
docker compose up -d
```

Open the port you published (`FLIPPARR_PORT`, default 8787). A new install
opens on setup, which asks for your library folder, then optionally a download
client and a metadata source, and scans when you finish. Everything it asks is
also under **Settings** afterwards.

The compose file attaches to an external network named `home-services-proxy`.
If you are not running one, delete the `networks:` blocks from
`compose.yaml` before starting.

### Volumes

| Mount | Mode | Purpose |
| --- | --- | --- |
| `/config` | read-write | database, settings, credentials, caches. **This is the only thing you need to back up.** |
| `/comics` | read-write | your library. Written to when a completed download is imported, and when replacing a file moves the original into `.flipparr/quarantine/`. Scanning only reads. |
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
| `FLIPPARR_DATABASE` | `/config/flipparr.db` | catalog database |
| `FLIPPARR_PROVIDER_CONFIG` | `/config/metadata-providers.json` | metadata source credentials |
| `FLIPPARR_ACQUISITION_CONFIG` | `/config/acquisition-services.json` | Prowlarr / SABnzbd credentials |
| `FLIPPARR_SETTINGS_CONFIG` | `/config/settings.json` | application preferences |
| `FLIPPARR_AUTH_CONFIG` | `/config/auth.json` | sign-in configuration |
| `FLIPPARR_TRUSTED_PROXIES` | *(empty)* | **See [Behind a reverse proxy](#behind-a-reverse-proxy).** |
| `FLIPPARR_LIBRARY_ROOT` | `/comics` | library mount |
| `FLIPPARR_SAB_COMPLETE_ROOT` | `/downloads/complete/comics` | completed-download mount |
| `FLIPPARR_IMPORT_POLL_SECONDS` | `15` | how often to look for completed downloads |
| `FLIPPARR_MIN_FREE_SPACE_MB` | `100` | refuse to import below this free space |
| `FLIPPARR_GCD_MIN_INTERVAL_SECONDS` | `1.5` | minimum gap between Grand Comics Database requests |
| `FLIPPARR_METRON_MIN_INTERVAL_SECONDS` | `3.2` | Metron documents 20 requests/minute |
| `FLIPPARR_COMIC_VINE_MIN_INTERVAL_SECONDS` | `1.1` | Comic Vine burst control |

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
read your session, and cross-site requests do not carry it. A session lasts 30
days. Setting a new password, in Settings or with `reset-password`, signs every
other device out; the device you changed it on stays signed in.

### Failed sign-ins

The first five wrong passwords cost nothing. After that, each failure makes the
next attempt wait twice as long — 1, 2, 4, 8 … seconds, capped at 60 — and the
sign-in page says how long. During the wait attempts are refused without the
password being checked, so a correct guess cannot slip through. A successful
sign-in, a password reset, or 15 quiet minutes clears it.

There is deliberately no hard lockout. Behind a reverse proxy every request
arrives from the proxy's address, so a lock on an address or an account would
let anyone who can reach the sign-in page lock you out. A capped delay keeps
guessing slow without that. Devices that are already signed in are never
affected. The count is kept in memory, so restarting the container clears it.

### Forgot your password

There is no email to send a reset link to; being able to run a command on the
server is the proof of ownership. **Forgot your password?** on the sign-in page
shows this command with a copy button:

```bash
docker exec -it flipparr python app.py reset-password
```

It asks for the new password twice, keeps sign-in on, and signs every device
out. Add `--username NAME` to change the username too, or `--password-stdin` to
read the password from standard input in a script. Your settings, library and
stored credentials are untouched.

### Behind a reverse proxy

**Read this before enabling "skip sign-in on local addresses" behind a proxy.**

A reverse proxy makes every request appear to come from the proxy. If the app
believed the `X-Forwarded-For` header unconditionally, anyone could send
`X-Forwarded-For: 127.0.0.1` and skip sign-in entirely — so the app ignores that
header by default.

The consequence: behind a proxy, with `FLIPPARR_TRUSTED_PROXIES` unset, either
every request looks like it came from the proxy's own (private) address and the
local bypass applies to everyone, or no request looks local at all.

Set it to your proxy's address or network. Entries are comma-separated, and
each is an address or a network in CIDR form:

```yaml
FLIPPARR_TRUSTED_PROXIES: "172.18.0.0/16"   # the proxy's Docker network
```

**If the proxy runs in Docker, use its network's subnet, not its container
address.** Docker assigns container addresses on a bridge network in start
order, so after a reboot the proxy can come back at a different address and
another container can inherit the old one. The subnet does not change:

```bash
docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' <network>
```

Every container on that network is then trusted to report a client address, so
keep it to the proxy and the apps behind it. A proxy on the host itself, or on
another machine, is named by its address as before. An entry that does not
parse is ignored and logged once as `invalid_trusted_proxy`.

Then the app reads the real client address from `X-Forwarded-For`, and the
bypass means what you expect. The header is read from the right: each proxy
appends the address it received the request from, so the right-hand end is
written by your proxies and the left-hand end by whoever sent the request. The
client is the right-most address that is not a trusted proxy. A client that
sends its own `X-Forwarded-For: 127.0.0.1` gains nothing, even through a proxy
that appends to the header rather than replacing it (nginx's
`$proxy_add_x_forwarded_for`, which Nginx Proxy Manager uses). The app also
picks up `X-Forwarded-Proto: https` from a trusted proxy and marks the session
cookie `Secure`.

If you are not sure of the proxy's address, leave the variable unset and turn
the local bypass **off** — you will sign in once per device, which is correct
and safe.

If a request arrives claiming `X-Forwarded-Proto: https` from an address that is
*not* in the list, the app ignores it and logs a warning once:

```json
{"level": "warning", "event": "untrusted_forwarded_proto", "detail": "…"}
```

That is the case worth catching. Without it the site looks correctly served over
HTTPS while its session cookie quietly lacks `Secure`.

### HTTPS

Flipparr speaks plain HTTP and does not terminate TLS, which is why a browser
shows **Not Secure** against it directly. That is not something the app can fix
from the inside: something in front has to hold the certificate. Two routes:

**A reverse proxy with a certificate.** In Nginx Proxy Manager, add a Proxy Host:

| Field | Value |
| --- | --- |
| Domain Names | `flipparr.example.com` |
| Scheme | `http` |
| Forward Hostname / IP | the container or host address |
| Forward Port | your `FLIPPARR_PORT` |
| Websockets Support | on |
| Block Common Exploits | on |
| SSL → Certificate | request a Let's Encrypt certificate |
| SSL → Force SSL | on |
| SSL → HTTP/2 | on |

Then set `FLIPPARR_TRUSTED_PROXIES` to the proxy's address, or the app will
ignore the proxy's headers and the cookie will not be marked `Secure`.

The domain must resolve to the proxy. On a local network that usually means a
DNS override on your router or Pi-hole pointing `flipparr.example.com` at the
proxy, so the name works inside the house as well as outside.

**Tailscale.** If the machine is on a tailnet, Tailscale can issue a real
certificate for its `*.ts.net` name with no domain and no port forwarding —
enable **HTTPS Certificates** in the tailnet admin console, then:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8787
```

Either way the padlock comes from the thing in front, and Flipparr's job is to
believe it only when told whom to believe.

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

#### What each provider actually allows

| Provider | Published limit | Measured behaviour |
| --- | --- | --- |
| Grand Comics Database | none published | Anonymous traffic is throttled on an **hourly** window of roughly **50–60 requests**. Exhausting it returns `429` with `Retry-After` and a body of `{"detail": "Request was throttled. Expected available in N seconds."}`; waits of **2922 s and 3189 s** (49 and 53 min) were observed — measured 2026-09-05. |
| Metron | 20 requests/minute sustained | paced at one request per 3.2 s |
| Comic Vine | hourly resource quota plus burst control | paced at one request per 1.1 s |

The GCD figure is the one worth knowing, because an hourly window does not
repay a burst in a few seconds: spend the quota and metadata lookup is paused
for up to the rest of the hour.

Enrichment costs about **2 GCD requests per series**, so that hourly window is
worth roughly **25 series an hour** — and the quota, not the pacing, is the
binding constraint. A 75-series library takes around three hours on GCD alone,
spread across several throttle windows; a library of a few thousand series is
not practical on the anonymous tier at all. This is measured, not estimated: a
run enriching 8 series at a time hit the throttle after 56 successful requests
and paused for 49 minutes.

**If you have more than a hundred or so series, configure Metron.** Its
documented 20 requests/minute is around 1200 an hour against GCD's ~55, so it
carries bulk enrichment comfortably while GCD stays the zero-configuration
default and the stronger source for independent and small-press material.
Nothing breaks without it; enrichment simply proceeds in hourly bursts.

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
tar -czf flipparr-backup-$(date +%F).tgz -C /path/to config
docker compose start sonicboom
```

To restore, put the directory back and start the container. *Verified: a
destroy-and-restore cycle reproduced the catalog exactly, byte-for-byte on a
963-file library.*

### Upgrading and rolling back

```bash
docker compose pull && docker compose up -d
```

The database migrates forward on start. **Back up `/config` first — a rollback
needs it.** The catalog refuses to open a database newer than the build reading
it, so once an upgrade has raised the schema, the previous image will not start
against that file:

```
RuntimeError: Unsupported catalog schema version 28
```

That is a deliberate refusal rather than a corruption: the older build stops
instead of writing to a shape it does not understand. But it does mean a
rollback is *restore the backup, then run the previous tag* — not just the
previous tag. To roll back:

```bash
docker compose down
tar -xzf flipparr-backup-YYYY-MM-DD.tgz -C /path/to
docker compose up -d          # with the previous image tag pinned
```

If you have no backup and need the previous version, the library itself is
safe: your comics were never modified. Point a fresh `/config` at the same
comics folder and re-scan.

Whether a given upgrade raises the schema is in the release notes; see
[RELEASE.md](RELEASE.md).

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
authentication and configure it again. (To recover a forgotten password, use
[`reset-password`](#forgot-your-password) instead: it keeps sign-in on.)

The app deliberately refuses to serve rather than fall back to "no
authentication", so a permissions problem cannot quietly leave your instance
unprotected. `/healthz` keeps answering so the container does not restart-loop.

**"Too many failed sign-ins. Try again in N seconds."**
Someone — possibly a saved password on another device — has failed to sign in
more than five times recently. Wait it out (a minute at most), or reset the
password with [`reset-password`](#forgot-your-password), which ends the wait.

**Sign-in is not being asked for, even though it is enabled.**
If "skip sign-in on local addresses" is on, requests from private addresses are
allowed through. Behind a reverse proxy this can mean *all* requests. See
[Behind a reverse proxy](#behind-a-reverse-proxy).

**Discover returns an error while browsing works.**
A metadata provider is unavailable or rate-limiting. The request fails quickly
rather than hanging; your library is unaffected.

**Flipparr is down after the machine reboots, and the proxy returns 502.**
`docker ps -a` shows the container `Exited`, and the Docker log (`journalctl -u
docker` or the system log) has `failed to bind host port <address>:<port>:
cannot assign requested address`. `FLIPPARR_BIND` is set to an address that did
not exist yet when Docker started — typically a Tailscale or VPN interface
address. Docker's restart policies only apply to a container that started
successfully, so one that fails its first start at boot is never retried.

Set `FLIPPARR_BIND=127.0.0.1` (or a LAN address that is up before Docker) and
reach the tailnet through [Tailscale Serve](#https) instead. A reverse proxy on
the same Docker network needs no published port at all; it reaches the
container by name. Then recreate, not just start, the container — a failed
start can leave it attached to no network, which a plain start keeps:

```bash
docker compose up -d --force-recreate flipparr
```

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
