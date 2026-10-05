# Install, upgrade and rollback proofs

What a clean install, an upgrade, a rollback and a restore do, and how each is
proven for this image. The evidence for the first part of Gate 4 in
[RELEASE_TRACK.md](RELEASE_TRACK.md). Written 2026-10-05.

## How it is proven

- **`tools/install_drill.py`** runs real containers from the host's Docker,
  against a made-up library of twelve comics, and checks each promise below.
  It runs in CI on every push (the `image` job), and on the NAS against the
  previous image before a deploy:

  ```
  python3 -B tools/install_drill.py --image flipparr:ci \
      --previous ghcr.io/silenttoots/flipparr:<previous release> --out ./install-drill
  ```

  Without `--previous` (a fork, or before the first release) the upgrade and
  rollback scenarios are reported as not tried rather than passed.

- **Unit tests** in `test_catalog_store.py` and `test_app.py` for the
  migration guard, the pre-upgrade copies and the start-up checks.

## What was wrong before (2026-10-05)

The survey that opened Gate 4 found, and the probes on the NAS confirmed:

- **An older image opening a newer catalog started and reported healthy**,
  with every page a 500. The schema check ran only after the tables had been
  created and rows cleaned up, so the old build had already re-created what
  the newer schema dropped, and switched the journal mode, before refusing;
  and `app.py` swallowed the refusal and served anyway. The documents said it
  "will not start" and "stops before touching the file".
- **A config folder the container could not write** (what Docker makes when
  the folder did not exist yet, or any anonymous volume) also came up
  "healthy": `/healthz` never looked at the catalog. CI's image check ran in
  exactly that state, proving only that the port answered.
- **Only the move to schema 49 kept a copy** of the catalog as it was.
- **Nothing was published**, so `docker compose pull` and "pin the previous
  tag" in the documents could not be followed, and the shipped compose file
  carried the owner's proxy network, time zone and group.

## Promises

| What happens | What Flipparr does | Proven by |
|---|---|---|
| First start on an empty config folder the container's user owns | It comes up, `/healthz` says `catalog: ok`, the catalog answers, setup opens, a scan files the library; the files it writes are the container's user's | drill: *clean install*; CI's image check (a mounted, writable config) |
| First start on a config folder it cannot write | One line in the log says which folder, which user, and what to do (`PUID:PGID`); the process exits non-zero; Docker never calls it healthy | drill: *config folder the container cannot write*; `test_a_config_folder_flipparr_cannot_write_refuses_to_start` |
| The catalog cannot be read while running | `/healthz` answers 503 with `status: unhealthy`; Docker's health check turns unhealthy | `test_health_is_unhealthy_when_the_catalog_cannot_be_read` |
| Upgrade: the new image on the previous release's config folder | The library and catalog are kept, the schema moves forward, and the catalog as it was is left beside the database (`flipparr.db.pre-v<N>`, the last three kept) | drill: *upgrade from the previous image* (from the build of 2026-09-07, schema 28, to this one, schema 64); `test_every_upgrade_keeps_a_copy_and_the_oldest_copies_go` |
| Rollback across a schema change | Restore the backup taken before the upgrade, run the previous tag: the same library, served | drill: *rollback by restoring the backup* |
| An older image (this release or later) opens a newer catalog | Refused before a byte is touched; the log says the schema it found, the schema it has, and to run the newer version or restore the backup; the process exits non-zero | drill: *catalog from a newer build*; `test_a_library_from_a_newer_build_is_refused_before_it_is_touched`, `test_a_catalog_from_a_newer_build_refuses_to_start_and_says_what_to_do` |
| Opening the same schema again | No copy is made and nothing moves | `test_every_upgrade_keeps_a_copy_and_the_oldest_copies_go`; the Gate 2/3 check that opening production's catalog with a new build moves no file |

## Results for this image

| Where | Image under test | Previous image | Held |
|---|---|---|---|
| NAS, 2026-10-05 | the Gate 4 build | `flipparr:latest` of 2026-09-07 (schema 28) | 5 of 5 |

Recorded per run in `install-drill.json` and the containers' logs under
`--out`.

## Known limits

- Builds from before 0.1.0 do not have the refusal: rolled back onto a newer
  catalog they start and answer errors. The restore-first rule in
  [OPERATING.md](OPERATING.md) covers them.
- The drill's library is made up; the full-library intake check is Gate 2's
  (`tools/intake_checkpoint.py`).
- The drill runs the image as the host user, as `PUID:PGID` would; it does
  not run as root, where a folder's mode would not stop a write.
- Permissions on `/comics` (an unreadable library) are not checked at start:
  the setup's folder check and the System page report them.
