# syntax=docker/dockerfile:1.7

# Base images are pinned by digest, not tag. A tag is a moving target: the same
# Dockerfile built a month apart otherwise produces a different image with no
# change in this repository to explain it. Refresh these deliberately, as their
# own commit, so the change is visible in history.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS web-build

WORKDIR /build/v1-prototype
COPY v1-prototype/package.json v1-prototype/package-lock.json ./
RUN npm ci
COPY v1-prototype/ ./
RUN npm run build

# SQLite, built from source. Debian 12's libsqlite3 is 3.40.1, inside the range
# of SQLite's WAL-reset bug (3.7.0-3.51.2; https://sqlite.org/wal.html#walresetbug),
# and Debian has not patched it. The catalog is written from several threads in
# WAL mode, so the image carries a fixed release instead of giving WAL up. The
# tarball is pinned by checksum: SQLite publishes its SHA3-256 (454e45f6...0338
# for 3.53.4, verified 2026-10-04); ADD checks the SHA-256 of the same file.
# The -D options keep what Debian's build turned on that changes behaviour.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS sqlite-build
ADD --checksum=sha256:0e9483900e92cd5de8fd48d16bf9200145a61f7fd5be542a5ac81d8a9516eb9c \
    https://sqlite.org/2026/sqlite-autoconf-3530400.tar.gz /tmp/sqlite.tar.gz
RUN apt-get update \
 && apt-get install --no-install-recommends --yes gcc libc6-dev make \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir /tmp/sqlite && tar -xzf /tmp/sqlite.tar.gz -C /tmp/sqlite --strip-components=1 \
 && cd /tmp/sqlite \
 && CFLAGS="-O2 -DSQLITE_MAX_VARIABLE_NUMBER=250000 -DSQLITE_SECURE_DELETE -DSQLITE_LIKE_DOESNT_MATCH_BLOBS \
      -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 \
      -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_COLUMN_METADATA \
      -DSQLITE_ENABLE_UNLOCK_NOTIFY -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_SOUNDEX" \
    ./configure --prefix=/opt/sqlite --disable-static \
 && make -j"$(nproc)" && make install \
 && mkdir /out && cp -L /opt/sqlite/lib/libsqlite3.so.0 /out/libsqlite3.so.0

FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS runtime

# Stamped by the build with the commit that produced it, and reported by
# /healthz, so a running container can be traced back to its source.
ARG FLIPPARR_BUILD=source

ENV FLIPPARR_BUILD=${FLIPPARR_BUILD} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLIPPARR_WEB_ROOT=/app/web \
    FLIPPARR_DATABASE=/config/flipparr.db \
    FLIPPARR_PROVIDER_CONFIG=/config/metadata-providers.json \
    FLIPPARR_ACQUISITION_CONFIG=/config/acquisition-services.json \
    FLIPPARR_SETTINGS_CONFIG=/config/settings.json \
    FLIPPARR_AUTH_CONFIG=/config/auth.json

# RAR is about a quarter of a real comic library and its compression cannot be
# decoded in pure Python. bsdtar (libarchive) reads it, and unlike unrar or
# 7-Zip's RAR decoder it is BSD-licensed, so redistributing this image with it
# carries no condition. It also covers .cb7 and .cbt, which were routed to a
# zip reader and could never have worked.
RUN apt-get update \
 && apt-get install --no-install-recommends --yes libarchive-tools \
 && rm -rf /var/lib/apt/lists/*

# The fixed SQLite (see sqlite-build) ahead of Debian's: /usr/local/lib comes
# first in the loader's search path, so Python's sqlite3 module loads it.
COPY --from=sqlite-build /out/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0
RUN ldconfig && python -c "import sqlite3, sys; sys.exit(sqlite3.sqlite_version_info < (3, 51, 3))"

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt
# The reader's optional panel detector runs through ONNX Runtime (MIT) on the
# CPU, used only to refine pages the gutter finder left coarse. No model ships
# in this image: the operator may place one at /config/models/panels.onnx
# (docs/OPERATING.md says where models come from and that their licences are
# the operator's to accept). Without one the gutter finder and the optional
# vision assistant still work.
COPY requirements-panels.txt ./
RUN pip install --no-cache-dir --requirement requirements-panels.txt
# ONNX Runtime's official builds send usage telemetry to Microsoft by default;
# this is its documented off switch, read before it initialises. page_panels.py
# sets it too, for anyone running outside this image.
ENV FLIPPARR_PANEL_MODEL=/config/models/panels.onnx \
    ORT_DISABLE_TELEMETRY=1
COPY LICENSE NOTICE ./
COPY app.py catalog_store.py page_panels.py access_policy.py content_rating.py reading_list_formats.py torrent_client.py arc_catalog.py \
     comic_language.py provider_evidence.py ./
COPY --from=web-build /build/v1-prototype/dist/client ./web

EXPOSE 8787
VOLUME ["/config", "/comics", "/downloads/complete/comics", "/downloads/torrents/complete/comics"]

# Default to a non-root user so the image is not privileged when run without an
# explicit `user:`. compose still overrides this with PUID/PGID. Matters because
# config files written as root become unreadable once the service runs as a
# normal user, and this app fails closed when it cannot read its auth config.
USER 1000:10

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).read(1)"]

CMD ["python", "-B", "app.py", "--host", "0.0.0.0", "--port", "8787"]
