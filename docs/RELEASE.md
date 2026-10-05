# Releasing Flipparr

How a build is identified, what a release involves, and what an upgrade or a
rollback does to your data. For day-to-day operation see [OPERATING.md](OPERATING.md).

---

## 1. What identifies a build

Three numbers, and they answer different questions.

| | Where | Changes when |
| --- | --- | --- |
| **App version** | `APP_VERSION` in `app.py` | a release is cut, by hand |
| **Build stamp** | `FLIPPARR_BUILD`, a build argument | every image build — the commit that made it |
| **Schema version** | `SCHEMA_VERSION` in `catalog_store.py` | the catalog's shape changes |

A running instance reports the first two without authentication, so you can
identify it before signing in:

```bash
curl -s http://localhost:8787/healthz
```

```json
{ "status": "ok", "version": "0.1.0", "build": "6a7a4b2…", "catalog": "ok" }
```

`catalog` says whether the database can be read; when it cannot, the answer
is 503 and Docker's health check reports the container unhealthy.

The same pair is on the first log line at start-up. The build stamp is the one
that matters when something is wrong: it names the exact commit, where the app
version only names the release.

An image built without the argument reports `build: "source"`. That is expected
for a local build; a release image should never say it. `compose.build.yaml`
passes the argument through, so a stamped build from a checkout is:

```bash
FLIPPARR_BUILD=$(git rev-parse HEAD) docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

---

## 2. Cutting a release

1. **Decide the version.** Pre-1.0, raise the minor for user-visible change and
   the patch for fixes.
2. **Bump `APP_VERSION`** in `app.py`, as its own commit.
3. **Note whether `SCHEMA_VERSION` moved** since the last release. If it did,
   the release notes must say so — that single fact decides whether a rollback
   needs a restore.
4. **Point `compose.yaml` at the new tag** (`image:` default), as part of the
   same commit, so a fresh checkout installs the release it belongs to.
5. **Tag the commit and push the tag:** `git tag v0.1.0 && git push origin v0.1.0`.
   CI (`.github/workflows/v1-forward.yml`) runs both test orders, secret
   hygiene, the fulfillment drill, the web build from the lockfile, the image
   checks and the install drill on that commit, and then the `publish` job
   builds the image with the commit stamped in and pushes it as
   `ghcr.io/silenttoots/flipparr:0.1.0` and `:latest`. Pushes to the
   development branch publish `:edge` the same way. Nothing is published
   unless everything before it passed.
6. **Verify the published artefact, not just the build:**

   ```bash
   docker pull ghcr.io/silenttoots/flipparr:0.1.0
   docker run --rm --entrypoint printenv ghcr.io/silenttoots/flipparr:0.1.0 FLIPPARR_BUILD
   python3 -B tools/install_drill.py --image ghcr.io/silenttoots/flipparr:0.1.0 \
       --previous ghcr.io/silenttoots/flipparr:<the release before> --out ./install-drill
   ```

   The drill installs it clean, upgrades the previous release to it, rolls
   back by restoring the backup, and checks that a config folder it cannot
   write and a catalog from a newer build are refused with a plain message
   (docs/INSTALL_PROOFS.md).
7. **Write the release notes below**, including the migration line.

The package on GitHub is private until its owner makes it public; a release
anyone can install needs that done once.

### What makes the build reproducible

- **Base images are pinned by digest**, not tag. `node:22-bookworm-slim` moves;
  `node@sha256:83f487e0…` does not. Refresh them deliberately, as their own
  commit, so the change is visible in history.
- **`npm ci`, never `npm install`** — installs the lockfile exactly and fails if
  `package.json` has drifted from it.
- **Python dependencies are pinned** to exact versions in `requirements.txt`.
- **SQLite is built from a pinned source tarball** (the `sqlite-build` stage),
  checked against its published hash, so the image never depends on the base
  image's older library.
- **Build from a clean export of the commit**, never from a working folder
  that earlier builds were unpacked into: unpacking adds files but never
  removes the ones the repository has since dropped, and `COPY v1-prototype/`
  then ships them. `git archive <commit> | tar -x -C <empty folder>` and
  build there.

---

## 3. Upgrading

```bash
docker compose pull && docker compose up -d
```

The catalog migrates itself forward on start.

Your library is mounted read-write, and two things write to it: importing a
completed download, and replacing a damaged file — which moves the original you
replaced into `.flipparr/quarantine/` inside the library root rather than
deleting it. Scanning never writes; it reads sizes and modification times.

**Back up `/config` before every upgrade.** Not because upgrading is risky, but
because *rolling back* needs it — see below.

---

## 4. Migration and rollback

Migrations run forward automatically and only forward. Before a build raises
the schema it keeps the catalog as it was beside the database
(`flipparr.db.pre-v<N>`, the last three kept). There is no downgrade path, by
design: a build refuses to open a catalog newer than itself, before touching
it, rather than writing to a shape it does not understand:

```
Flipparr cannot start: This library's catalog is at schema 70, newer than this build's 64: it was last opened by a newer Flipparr. Run that version, or restore the backup taken before it.
```

That is what the previous image prints, and it exits, if you roll back to it
after an upgrade that raised the schema (from 0.1.0 on; earlier builds
started and answered errors instead).

**So a rollback across a schema change is: restore the backup, then run the
previous tag.** Not just the previous tag.

```bash
docker compose down
tar -xzf flipparr-backup-YYYY-MM-DD.tgz -C /path/to     # as the config folder's owner
FLIPPARR_IMAGE=ghcr.io/silenttoots/flipparr:<previous>  # in .env
docker compose up -d
```

Rolling back a release that did *not* raise the schema needs no restore — the
previous image opens the same database unchanged.

**If you have no backup**, nothing is lost that cannot be rebuilt: your comics
were never modified. Point a fresh `/config` at the same folder and re-scan.
You lose manual corrections, aliases and request history — not files.

---

## 5. Release notes

### 0.1.0 — unreleased

First release of the V1-forward line.

**Schema:** 64. Upgrading from any earlier build raises the schema, so a
rollback to a pre-0.1.0 image requires restoring a `/config` backup. A
pre-0.1.0 build opening the upgraded catalog does not refuse it cleanly; it
starts and answers errors, which is why the restore comes first.

**Image:** `ghcr.io/silenttoots/flipparr:0.1.0`, built by CI from the tagged
commit and stamped with it. The container runs as 1000:1000 unless
`PUID`/`PGID` say otherwise; create the config folder owned by that user
before the first start, or the container says so and stops.

**Included**

- First-run setup: library folder, download services and metadata sources, with
  a folder check that reports how many comics it can see before committing.
- Authentication — *arr-style forms sign-in with an optional local bypass,
  scrypt password hashing, and HMAC-signed session cookies. Fails closed if its
  config cannot be read.
- Views are addressable: refreshing, bookmarking or sharing a link returns to
  where you were, including an open series.
- Structured logs. One JSON object per line, each carrying the id of the request
  that produced it, returned to the caller as `X-Request-Id`.
- Provider responses cached in SQLite, with definitive misses remembered so a
  rescan does not re-ask a provider a question it has already answered.
- Grand Comics Database, Metron, Comic Vine and Open Library as metadata
  sources; Prowlarr and SABnzbd for acquisition.

**Known limitations** are listed in [OPERATING.md](OPERATING.md#6-known-limitations).
The one worth reading before you start: GCD's anonymous tier allows roughly
25 series an hour, so a large library wants a Metron token.
