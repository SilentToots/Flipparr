# Privacy

What Flipparr keeps, and what leaves the server it runs on. The same notice is
in the app under **Settings → About**. Flipparr is run by whoever installs it;
they decide which services it talks to, and this describes what each one is
sent.

## Stays on the server

- The library, its catalogue, and each profile's reading history, place,
  ratings and requests (`/config/flipparr.db`).
- Passwords, stored only as scrypt hashes, and API keys and service passwords
  (`/config/*.json`, owner-only permissions), which are never sent back to a
  browser.
- No analytics, no telemetry, no update checks, nothing sent to Flipparr's
  authors. Fonts and icons are part of the app; it loads no outside scripts.

## Goes to the services the operator sets up

| Service | When | What it is sent |
| --- | --- | --- |
| Grand Comics Database, Open Library | Always (no account) | Titles, issue numbers and ISBNs read from files, as searches; cover requests |
| Metron, Comic Vine, Google Books | Once a key or account is added | The same searches, with the operator's credentials |
| Prowlarr, SABnzbd, qBittorrent | Once connected | Searches for wanted issues; the releases chosen |
| A direct-download site, a page fetcher | Once entered and switched on | Searches for wanted issues; the pages of posts chosen |
| Anthropic (Claude) or OpenAI (ChatGPT) | Once a key is added | Images of the pages read in panel view -- every page only with *Ask about every page* on (off by default), readers' pages only with *Readers' pages too* on; a run's cover for its age rating only with that setting on |
| GitHub | Only with *Suggest community reading lists* on (off by default) | A request for the list of community reading lists once a day; a list's file when one is picked |

Each service sees the server's address and receives the requests above under
its own terms and privacy policy.

## In the browser

- `flipparr_session`, the sign-in cookie, and on a shared device
  `flipparr_device`, which remembers the profiles opened on it. No tracking or
  advertising cookies.
- Some cover previews (match candidates, discovery results) load straight from
  a metadata source. Every page is served with `Referrer-Policy: no-referrer`,
  so those requests and any link out carry no address of the instance.

## In the server log

Each request's method, path (never its query string), result, duration and
profile go to the container's log. The address a request came from is
included only with **Settings → Security → Record visitor addresses** on (off
by default). Sign-in throttling counts failures in memory either way.

## Removing data

Deleting a profile removes its history, ratings and requests. Removing the
`/config` volume removes everything Flipparr kept; the library folders are
only ever read, except where an import writes a comic you asked for.
