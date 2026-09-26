# syntax=docker/dockerfile:1

# Dockerfile for Flask URL Shortener/Redirector
#
# Image layout is split in two on purpose:
#   /app        code, replaced on every upgrade (never holds user data)
#   /app/data   volume, survives every upgrade (all persistent state)
#
# Nothing else in the image is written to at runtime, which is what makes
# "pull a new image, recreate the container" a safe operation.

FROM python:3.13-slim

WORKDIR /app

# git is only needed if the version is not stamped at build time (see below).
ARG INSTALL_GIT=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ${INSTALL_GIT:+git} ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Version is stamped at build time rather than derived from the .git directory
# that happens to be in the build context. Without this, /system-info and
# /api/latest-version report whatever commit the build machine was on, so an
# upgrade can appear to change the version backwards.
ARG APP_VERSION=""
ARG GIT_COMMIT=""
ENV REDIRECTOR_APP_VERSION=${APP_VERSION} \
    REDIRECTOR_GIT_COMMIT=${GIT_COMMIT}
# Only overwrite VERSION when a real value was passed; an empty write would make
# the app fall back to its hardcoded floor and report a version that is wrong.
RUN if [ -n "${APP_VERSION}" ]; then printf '%s\n' "${APP_VERSION}" > /app/VERSION; fi

# .git must never reach the image; git describe against a baked repo produces
# versions that do not correspond to the published tag.
RUN rm -rf /app/.git

# Fix CRLF line endings from Windows and make executable (critical for entrypoint)
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh && \
    sed -i 's/\r$//' gunicorn.conf.py || true

# Writable-by-default data directory for the common bind-mount case. When a host
# folder is mounted over it, host ownership wins - see docs/DATA-PERSISTENCE.md.
ENV REDIRECTOR_DATA_DIR=/app/data
# Pinned explicitly: `flask db upgrade` in the entrypoint otherwise depends on
# Flask auto-detecting wsgi.py from the current working directory, so migrations
# silently target a different (or no) app the moment WORKDIR changes.
ENV FLASK_APP=wsgi:app
RUN mkdir -p /app/data /app/data/backups

EXPOSE 80

# Same probe as the compose healthcheck, so plain `docker run` gets one too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:80/health', timeout=3).status==200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
