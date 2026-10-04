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
ENV FLIPPARR_PANEL_MODEL=/config/models/panels.onnx
COPY app.py catalog_store.py page_panels.py access_policy.py content_rating.py reading_list_formats.py torrent_client.py arc_catalog.py ./
COPY catalog_core_v2/ ./catalog_core_v2/
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
