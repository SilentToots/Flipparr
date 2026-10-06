# Operating Flipparr

Install, configure, back up and troubleshoot a Flipparr instance.

Every procedure here was executed against a real 963-file library on a NAS
before being written down. Where behaviour is surprising, the reason is given
rather than just the workaround.

---

## 1. Install

Flipparr ships as a Docker image, `ghcr.io/silenttoots/flipparr`, and keeps
all of its state in one `/config` volume. It needs read-write access to your
comics (see [Volumes](#volumes)) and to your download client's completed
folder if you want automatic import.

```bash
cp .env.example .env            # set CONFIG_PATH, COMICS_PATH, SAB_COMPLETE_PATH
mkdir -p "$CONFIG_PATH" && chown 1000:1000 "$CONFIG_PATH"   # the PUID:PGID in .env
docker compose up -d
```

**Create the config folder first, owned by the user the container runs as.**
Docker creates a missing bind-mount folder itself, owned by root, and the
container (which runs as `PUID:PGID`, 1000:1000 by default) cannot write it.
Flipparr then refuses to start and says so in `docker compose logs flipparr`:

```
Flipparr cannot start: the config folder (/config) is not writable by user 1000:1000 ...
```

Fix the folder's owner and start it again. The same line tells you about a
catalog it cannot open, for the same reason.

Open the port you published (`FLIPPARR_PORT`, default 8787, on
`FLIPPARR_BIND`, loopback by default). A new install opens on setup, which
asks for your library folder, then optionally a download client and a metadata
source, and scans when you finish. Everything it asks is also under
**Settings** afterwards. Sign-in is off until you turn it on in Settings →
Security; keep the port on loopback or your own network until then.

The compose file joins no network of its own. To put Flipparr on the network
your reverse proxy or download clients share, add a `compose.override.yaml`
beside it (see [Behind a reverse proxy](#behind-a-reverse-proxy)). To build the
image from a checkout instead of pulling it, use `compose.build.yaml`
([RELEASE.md](RELEASE.md)).

### Volumes

| Mount | Mode | Purpose |
| --- | --- | --- |
| `/config` | read-write | database, settings, credentials, caches. **This is the only thing you need to back up** (see [Backup and restore](#backup-and-restore) for what is in it). |
| `/comics` | read-write | your library. Written to when a completed download is imported, and when replacing a file moves the original into `.flipparr/quarantine/`. Scanning only reads. |
| `/downloads/complete/comics` | read-write | where SABnzbd puts finished files. Writable so a download's folder is removed once its comic is verified in the library (SABnzbd keeps the files otherwise); only the one folder SABnzbd reported for that job is ever removed. |
| `/downloads/torrents/complete/comics` | read-only | qBittorrent's comics category folder (optional; `TORRENT_COMPLETE_PATH`). Torrents keep seeding from it after import, so Flipparr never writes there; only qBittorrent removes anything. |

### Permissions

The image runs as `1000:1000` by default. `/config` must be writable by
whatever user the container runs as — set `PUID`/`PGID` to match the owner of
your config directory, and create that directory before the first start
(above). Flipparr checks this when it starts and refuses to run otherwise.

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
| `FLIPPARR_ACQUISITION_CONFIG` | `/config/acquisition-services.json` | Prowlarr / SABnzbd / qBittorrent credentials |
| `FLIPPARR_SETTINGS_CONFIG` | `/config/settings.json` | application preferences |
| `FLIPPARR_AUTH_CONFIG` | `/config/auth.json` | sign-in configuration |
| `FLIPPARR_TRUSTED_PROXIES` | *(empty)* | **See [Behind a reverse proxy](#behind-a-reverse-proxy).** |
| `FLIPPARR_LIBRARY_ROOT` | `/comics` | library mount |
| `FLIPPARR_SAB_COMPLETE_ROOT` | `/downloads/complete/comics` | completed-download mount |
| `FLIPPARR_TORRENT_COMPLETE_ROOT` | `/downloads/torrents/complete/comics` | qBittorrent's comics folder, mounted read-only |
| `FLIPPARR_IMPORT_POLL_SECONDS` | `15` | how often to look for completed downloads |
| `FLIPPARR_MIN_FREE_SPACE_MB` | `100` | refuse to import below this free space |
| `FLIPPARR_GCD_MIN_INTERVAL_SECONDS` | `1.5` | minimum gap between Grand Comics Database requests |
| `FLIPPARR_METRON_MIN_INTERVAL_SECONDS` | `3.2` | Metron documents 20 requests/minute |
| `FLIPPARR_COMIC_VINE_MIN_INTERVAL_SECONDS` | `1.1` | Comic Vine burst control |
| `FLIPPARR_HTTP_THREADS` | `32` | worker threads answering requests (4-128) |
| `FLIPPARR_COVER_CACHE_MB` | `512` | covers kept in `/config/cover-cache` so a restart does not re-read every cover; least recently used dropped first |
| `FLIPPARR_TEMP_DIR` | *(unset: `/config/tmp`)* | where temporary files go (uploads while they arrive, comic packs being unpacked). Flipparr makes a `flipparr-tmp` folder inside the folder you name and empties only that at start |

Flipparr is served by [Waitress](https://docs.pylonsproject.org/projects/waitress/).
A connection that goes quiet is closed after 30 seconds, up to 1,000 can be
open at once, and a slow or stalled client never holds one of the threads
that answer requests. A comic upload is held whole in the temporary folder
before the import reads it, so that folder needs free space for the largest
file you upload (up to 2 GB). On `docker stop`, requests already running get
up to 5 seconds to finish.

`GET /healthz` is unauthenticated and is what the container health check uses.

---

## 2. Authentication

**Off by default.** Turn it on under **Settings → Security**.

The app stores your metadata and download-client API keys and can start usenet
downloads, so anything that can reach the port can spend your usenet quota and
change your library. If the port is reachable by anything other than you, turn
sign-in on.

- **Require sign-in** — `none` or `forms`. Set a username and password first.
- **Skip sign-in on local addresses** — a device on a private address is a
  shared household device. Read the caveats below before turning it on.

Once a password exists, every change to these settings asks for it: a session
on its own — a shared tablet opened with a PIN, say — cannot change the
password or turn sign-in off.

Passwords are stored as a salted scrypt hash and never returned by the API.
Signing in sets an `HttpOnly`, `SameSite=Lax` cookie, so page scripts cannot
read your session, and cross-site requests do not carry it. A session lasts 30
days. Setting a new password, in Settings or with `reset-password`, signs every
other device out; the device you changed it on stays signed in. So does
turning sign-in on, or the local bypass off: every session issued while the
port was open is ended, and only the device that made the change keeps one.

### Which names Flipparr answers to

While sign-in is off, or the local bypass is on, a device is treated as one of
the household by where it connects from. A page on any website can make its
own name resolve to your server (DNS rebinding) and then act as the device it
runs on, so Flipparr answers as a household device only when opened by:

- an address (`http://192.168.1.20:8787`), `localhost`, a bare machine name
  (`http://nas:8787`), or a `.local`, `.lan`, `.home`, `.internal` or
  `.home.arpa` name;
- a name you list in `FLIPPARR_ALLOWED_HOSTS` (comma-separated): a tailnet
  name such as `nas.your-tailnet.ts.net`, or a domain behind your proxy.

Any other name answers `421` with "not known by the name you called it by".
A signed-in session works under any name; the list matters only for the two
settings above.

### Before turning the local bypass on

"Local" means the address the request arrives from is private or loopback,
which is not the same as the device being in your home:

- **Through a reverse proxy** every request arrives from the proxy's address,
  which is private. Set `FLIPPARR_TRUSTED_PROXIES` to the proxy's network so
  Flipparr can read the real client's address; without it, everyone the
  proxy admits is "local".
- **Through Tailscale Serve** (recommended for a tailnet) every request
  arrives from loopback, so every device on the tailnet is local -- which is
  usually what you want -- and with **Funnel**, so is the whole internet.
  A device connecting to the tailnet address directly is *not* local:
  Tailscale's carrier-grade NAT range (100.64/10) is not a private range.
- **Docker Desktop, rootless Docker and loopback publishing** can present
  every client as the Docker gateway, which is private.

If in doubt, leave the bypass off and sign in; readers can have their own
sign-ins.

### Failed sign-ins

The first five wrong passwords cost nothing. After that, each failure makes the
next attempt wait twice as long — 1, 2, 4, 8 … seconds, capped at 60 — and the
sign-in page says how long. During the wait attempts are refused without the
password being checked, so a correct guess cannot slip through. A successful
sign-in, a password reset, or 15 quiet minutes clears it.

There is deliberately no hard lockout for passwords. Behind a reverse proxy
every request arrives from the proxy's address, so a lock on an address or an
account would let anyone who can reach the sign-in page lock you out. A capped
delay keeps guessing slow without that. Devices that are already signed in are
never affected. The count is kept in memory, so restarting the container clears
it. The same wait applies to switching profiles with a password, and to
changing the sign-in settings.

**PINs are different.** A PIN has ten thousand values, so its waits grow to
five minutes, its failures are remembered for a day, and twenty wrong PINs
in a day lock that profile's PIN until the day is out. The admin can set the
profile a new PIN, which starts a new count. Changing a PIN ends the sessions
it opened.

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

**Tightest: a network for just the proxy and Flipparr.** If the proxy's network
also holds download clients or other apps, each of them is trusted too. Give the
two their own small internal network instead, reach Flipparr by an alias that
exists only there, and trust only that subnet:

```bash
docker network create --internal --subnet 172.16.88.0/29 flipparr-ingress
```

```yaml
# compose.override.yaml, next to Flipparr's compose.yaml. compose.yaml joins
# no network of its own, so name here every network Flipparr should be on:
# the one your download clients and indexer share, and the proxy's.
services:
  flipparr:
    networks:
      services:
      ingress:
        aliases: [flipparr-ingress]
networks:
  services:
    name: your-services-network      # the one Prowlarr and SABnzbd are on
    external: true
  ingress:
    name: flipparr-ingress
    external: true
```

Add the same network to the proxy's container, point the proxy host at
`flipparr-ingress:8787`, and set `FLIPPARR_TRUSTED_PROXIES: "172.16.88.0/29"`.
Pick any subnet no other network uses (`docker network inspect` lists them).
`--internal` means the network has no route out, so it carries only the proxy's
requests. Flipparr keeps its other network for everything it fetches.

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

### Limits worth setting at the proxy

Flipparr accepts request bodies up to 2 GB, because a comic can be uploaded
by hand (**Pull List → Upload a file**), and it reads the whole body before
it looks at who sent it. Behind a proxy, let only that one route take a large
body and keep everything else small; the proxy also decides how long a slow
client may take. For nginx:

```nginx
client_max_body_size 1m;
client_body_timeout 30s;
client_header_timeout 15s;
location ~ ^/api/v1/(reading-lists|run-collections)/[0-9]+/cover/upload$ { client_max_body_size 60m; }
location ~ ^/api/v1/profiles/[0-9]+/avatar/upload$                        { client_max_body_size 60m; }
location ~ ^/api/v1/acquisition-jobs/[0-9]+/import$                        { client_max_body_size 2g; }
```

Caddy's `request_body { max_size }` and Traefik's `buffering.maxRequestBodyBytes`
do the same. On a LAN with no proxy, the container's own limit of 200
connections is the only bound; anyone on the network could fill the config
volume's free space with half-sent uploads, which is one more reason the
port binds to loopback by default.

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

### Torrents (qBittorrent)

qBittorrent is a second download client, for what Usenet does not carry:
manga volumes, and whole runs as packs. Automatic search works with either
client, and prefers Usenet when a Usenet and a torrent release match equally.

1. **Indexers.** Add the torrent indexers you choose in Prowlarr. Some file
   English manga volumes under *Books*, so Flipparr asks that category too
   for manga runs.
2. **qBittorrent 4.5.5 or later.** Older versions cannot stop a torrent until
   its file list arrives, so they would download a whole pack; Flipparr still
   uses them for single releases but never sends them a pack.
3. **Connect it** under *Settings → Acquisition services → qBittorrent*: the
   Web UI address, a username and password if it asks for one, and a
   category (`comics`). **Test** creates the category beside qBittorrent's
   own download folder and says where it saves; a grab creates it too if it
   is missing. Flipparr adds its torrents under automatic torrent management,
   so they save in the category's folder whatever the client's default mode
   is -- do not move them by hand while they download.
4. **Mount that folder** into Flipparr read-only: set `TORRENT_COMPLETE_PATH`
   in `.env` to the category's folder on the host. Its last component must be
   the category's name. Create the folder before starting the container, so
   Docker does not create it as root and lock qBittorrent out.
5. **Seeding limits.** Flipparr copies a finished comic into the library and
   leaves the torrent seeding. When qBittorrent stops it at its own ratio or
   seeding-time limit (*Tools → Options → BitTorrent → Seeding Limits*, action
   *Stop torrent*), Flipparr removes the torrent and its files within the
   hour. Without a limit, torrents seed until you remove them. Flipparr only
   ever removes torrents it added (tagged `flipparr`).

How packs are used: pulling a whole run, one pack answers it, and between packs
of the same reach Usenet comes first, then a torrent, then direct downloads. Pulling a
single issue, every single release from every source is tried first, and a
torrent pack is the last resort. Either way only the wanted issues' files are
downloaded from a torrent pack; the run's other wanted issues it holds come in
the same download.

If qBittorrent runs behind a VPN container, bind it to the tunnel interface in
*Tools → Options → Advanced → Network interface*. Bound to the loopback, it
reaches no tracker and every torrent sits at zero peers.

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

### Panel view's optional detector

The reader's panel view finds panels with its own gutter finder, and can ask
an optional vision assistant (Settings › Metadata providers) to check them.
It can also use a panel-detection model, which no Flipparr image includes.
To use one, place an ONNX object-detection model trained for comic panels at
`/config/models/panels.onnx` (the `FLIPPARR_PANEL_MODEL` setting changes the
path) and restart the container; without one, nothing changes.

Models carry their own licences and training-data terms. Some published
panel detectors are built on AGPL-licensed architectures or trained on
research-only datasets, so check a model's terms before you use it -- they
are yours to accept, and Flipparr does not redistribute any model.

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
docker compose stop flipparr
tar -czf flipparr-backup-$(date +%F).tgz -C /path/to config
docker compose start flipparr
```

What is in it, and what you may leave out:

- `flipparr.db` (and `flipparr.db-wal`, `flipparr.db-shm` while running): the
  catalog -- your library's runs, issues, requests, profiles, reading places,
  ratings and arcs. Archive it with the container stopped, or with all three
  files together.
- `flipparr.db.pre-v<N>`: the catalog as it was before an upgrade raised the
  schema to N. Flipparr keeps the last three. They are the way back if an
  upgrade is ever doubted; archive them or not as you like.
- `settings.json`, `auth.json`, `metadata-providers.json`,
  `acquisition-services.json`: settings and **credentials** (your password
  hash and session secret, provider and download-client keys). Keep the
  archive as private as the folder.
- `cover-cache/`, `reading-cache/`, `tmp/`, `downloads/`: caches and files in
  flight, up to a few gigabytes, all rebuilt on demand. Safe to exclude
  (`--exclude=cover-cache --exclude=reading-cache --exclude=tmp
  --exclude=downloads`).
- `models/`, `avatars/`, `user-covers/`, `arc-index.json`: the panel model you
  copied in, profile pictures, covers you chose, the arc index.

The pages you fixed by hand in panel view are in the catalog, and can also be
exported on their own: **Settings → Reader → Panel view → Export panel
fixes** writes a JSON file of every fix with the page's fingerprint, and
*Import* brings one back -- into this Flipparr or another. A fix is attached
to its page by that fingerprint, so it follows the page to a replaced file,
a re-imported comic, or a run removed and added back; a fix whose page is
not in the library yet waits until the page is read.

Not in `/config`: originals set aside by a replacement, in
`<library>/.flipparr/quarantine/`.

To restore, stop the container, put the directory back **owned by the same
`PUID:PGID`** (`tar` run as root keeps the archived owner; otherwise
`chown -R` it), and start the container. An `auth.json` the container cannot
read fails closed, so a wrong owner shows as "cannot start" or a sign-in
that refuses everyone. A restored catalog is older than the library: the next
scan picks up comics that arrived since, and requests fulfilled since are
searched for again.

This is exercised, with real containers, by `tools/install_drill.py` (see
[INSTALL_PROOFS.md](INSTALL_PROOFS.md)).

### Upgrading and rolling back

`compose.yaml` names the release it shipped with
(`ghcr.io/silenttoots/flipparr:0.1.0`). To upgrade, set `FLIPPARR_IMAGE` in
`.env` to the new tag (or take the new `compose.yaml`), then:

```bash
docker compose pull && docker compose up -d
```

The database migrates forward on start. Before it does, Flipparr keeps the
catalog as it was beside the database (`flipparr.db.pre-v<N>`, the last
three), but **back up `/config` first anyway -- a rollback needs it.**

Flipparr refuses to open a catalog written by a newer build, before touching
it, and says so in the log:

```
Flipparr cannot start: This library's catalog is at schema 70, newer than this build's 64: it was last opened by a newer Flipparr. Run that version, or restore the backup taken before it.
```

So once an upgrade has raised the schema, a rollback is *restore the backup,
then run the previous tag* -- not just the previous tag:

```bash
docker compose down
tar -xzf flipparr-backup-YYYY-MM-DD.tgz -C /path/to     # as the same owner, see above
FLIPPARR_IMAGE=ghcr.io/silenttoots/flipparr:<previous>  # in .env
docker compose up -d
```

Builds from before 0.1.0 do not have that refusal: they start against a newer
catalog and answer errors to everything. Restoring the backup is the way back
from those too.

If you have no backup and need the previous version, the library itself is
safe: your comics were never modified. Point a fresh `/config` at the same
comics folder and re-scan.

Whether a given upgrade raises the schema is in the release notes; see
[RELEASE.md](RELEASE.md).

---

## 5. Troubleshooting

**Start with Settings → System** (admin). It shows whether each background
worker (metadata, imports, release searches, library scans, age ratings) is
running, when it last did something and what last went wrong; what work is
waiting (downloads, wanted issues, metadata, metadata sources asking Flipparr
to wait); and the last 200 warnings and errors since Flipparr started. A
worker that dies is logged (`thread_crashed`) and started again, up to five
times an hour; after that it stays stopped until Flipparr is restarted, and
the page says so. **Download diagnostics** saves the same facts, plus which
services are set up, as a file safe to share when asking for help: it holds
no passwords, keys, tokens or service addresses. The same data is at
`GET /api/v1/system/status` (admin).

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

### When an AI connector stops

If Claude's or ChatGPT's account runs out of credit, or its API key is
refused, every admin gets a notification in the bell ("Claude is out of
credit") that opens Settings → Reader. Flipparr stops asking that service for
fifteen minutes at a time and panel view carries on with its own reading. One
notification an outage: the next successful answer re-arms it. Neither service
lets an API key ask how much credit is left, so Flipparr cannot warn that a
balance is running low; set a low-balance email alert or auto-reload in the
provider's own billing page for that.

### When a download service stops answering

If Prowlarr, SABnzbd or qBittorrent stops answering -- it is restarting,
its address changed, it refuses Flipparr's API key, or it has been turned
off in Settings -- nothing is given up:

- downloads already in SABnzbd or qBittorrent wait, and carry on when it
  answers again;
- searches wait too, and a search that went unanswered does not count
  against the issue, so an outage does not push its next search out;
- Settings → System lists the service under **Waiting work** ("SABnzbd isn't
  answering, since 3:20 PM") with what it last said;
- after half an hour, every admin gets one notification in the bell, which
  opens Settings → Acquisition. The next answer clears the System page's
  line; the notification stays until it is read.

One issue whose own search Prowlarr answers with an error is not an outage:
when the next issue's search is answered, the failed one is counted and
waits its turn like any search that found nothing.

**qBittorrent refuses the username and password.** Flipparr tries them once
and then stops: the bell says "qBittorrent refused Flipparr's sign-in"
straight away, and torrents wait. It does not try again on a clock, because
qBittorrent bans an address after a number of failed sign-ins in a row (5 by
default, for an hour) and that count never runs down by itself. Save the
right username and password in Settings → Acquisition, or press Test once
qBittorrent's side is fixed, and Flipparr signs in again at once. If
qBittorrent has already banned the address, Flipparr waits fifteen minutes
at a time for the ban to end.

**Finished torrents wait for good.** Flipparr cannot see qBittorrent's
"comics" folder. Settings → Acquisition → qBittorrent → Test says so when
the folder is missing or unreadable; after half an hour the bell says
"Flipparr can't see the torrents folder". Mount the category's folder at
`TORRENT_COMPLETE_PATH` (section 1) and recreate the container.

**A torrent's issues say "qBittorrent saved this torrent in “…”, not in the
“comics” folder".** The torrent finished somewhere other than the category's
folder: a torrent added before 0.1.0 under manual management on a client
whose default folder is not the category's, or one you moved by hand. In
qBittorrent, right-click it → *Automatic Torrent Management* (or set its
location to the category's folder); Flipparr imports it on its next pass.
Torrents Flipparr adds now are managed by their category, so this does not
recur.

What holds through restarts, outages and bad downloads, and how each is
tested, is in [FULFILLMENT_PROOFS.md](FULFILLMENT_PROOFS.md).

### Privacy settings

Three things beyond your own services are off until you turn them on: a vision
model reading every page (*Settings → Reader → Ask about every page*), the
community reading lists' daily GitHub fetch (*Settings → Metadata sources*),
and visitor addresses in the log (*Settings → Security → Record visitor
addresses*). The full account of what leaves the server is
[`docs/PRIVACY.md`](PRIVACY.md).

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
- **Sign-in is off on a new install**, and failed sign-ins are slowed, not
  blocked (see [Failed sign-ins](#failed-sign-ins)). Do not expose this
  instance directly to the internet; put it behind a VPN, a tailnet, or a proxy
  that provides its own protection.
- **Provider data is incomplete.** The Grand Comics Database does not expose a
  complete collected-edition-to-issue relationship, so inferred contents are
  labelled as inferred rather than presented as fact.
- **Results are candidates with provenance, not authoritative matches.**
