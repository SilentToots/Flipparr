# Security

How Flipparr is meant to be run safely, what it defends against, and what it
does not. For reporting a problem, open an issue marked *security* on the
repository, or write to the maintainer privately if it is sensitive.

## The model

Flipparr is a single-household application: one admin, optional reader
profiles, on a NAS or home server. It holds credentials for your download
clients and metadata providers and can start downloads with them, so
**whoever can reach the port with the admin's rights can spend your quota
and change your library.** Everything below follows from that.

- **Bind to loopback or your own network** (`FLIPPARR_BIND`, loopback by
  default). Reach it from elsewhere through a reverse proxy with HTTPS, or a
  tailnet, never by publishing the port to the internet.
- **Turn sign-in on** before anything other than you can reach the port
  (Settings → Security). It is off on a new install so setup needs no
  password; the sign-in settings themselves need the password once one
  exists, and turning sign-in on ends every session issued before it.
- **Use the local-network bypass with care.** "Local" is where the request
  arrives from, which behind a proxy, a tunnel or Docker's own networking is
  not where the device is; see [OPERATING.md](OPERATING.md), section 2.
- **Open it by a name it knows.** While sign-in is off or the bypass is on,
  only addresses, local names and `FLIPPARR_ALLOWED_HOSTS` are treated as
  household devices, so a page on another website cannot point its own name
  at your server and act as you (DNS rebinding).

## What is in place

| Area | What Flipparr does |
|---|---|
| Sessions | HMAC-signed, `HttpOnly`, `SameSite=Lax` cookies, `Secure` behind a trusted HTTPS proxy; 30 days; revoked by a new password, a new PIN or lock, "forget devices", turning sign-in on or the bypass off |
| Passwords and PINs | scrypt; a growing wait after five failures, counted before the password is checked; PINs capped at twenty failures a day |
| Authorization | one default-deny route table (readers reach only the listed routes), the admin's routes never reachable by a reader, a profile's rating limit applied on the server to every comic, page, cover and picture |
| Cross-site requests | `Origin` checked on every state change; `frame-ancestors 'none'`, `X-Frame-Options: DENY` and `nosniff` on every response; the app shell under a Content-Security-Policy that allows scripts from the app only |
| Credentials | stored in files only the container's user can read (`umask 077`), never returned to the browser; a saved key goes only to the address it was saved for; redirects to another host drop them; a provider's "next page" must be the provider's own address; `file://` is never fetched |
| Files | every path contained to a library root, the config folder or a download folder before it is read, written or removed; nothing is ever extracted by a name inside an archive; the archive tool's output, decoded images, ebooks and packs are all bounded |
| Logs and support | request paths without query strings, no headers or bodies, client addresses only when switched on; secrets scrubbed from exceptions before they are logged; the diagnostics file names no key, token or address |
| Container | non-root, read-only root filesystem, no capabilities, `no-new-privileges`, bounded memory and processes, pinned base images, SQLite built from a checksummed source, dependencies audited in CI |

## What is not in place, and why

- **No rate limit on reading.** A household's readers are trusted to read.
  The one paid thing a reader can trigger, a vision model reading a page, is
  off for readers by default and capped per profile per day when on.
- **Large uploads are read before they are checked.** A comic uploaded by
  hand may be 2 GB, and the server reads a body before it looks at the
  sender. Keep the port off the open internet, and put the per-route limits
  from [OPERATING.md](OPERATING.md) on your proxy.
- **Encrypted archives are refused, not opened.** Flipparr never asks you for
  an archive's password.
- **A leaked database leaks hashes.** Profile PINs are short by nature; a
  copy of `flipparr.db` lets them be guessed offline. Keep backups as
  private as the config folder.

## Reviewed

The code was reviewed against this model on 2026-10-06 (three independent
passes: identity and access; files, archives and fetching; the HTTP layer and
the container). What they found is fixed in the two commits of that day and
tested; the remaining items are the ones listed above.
