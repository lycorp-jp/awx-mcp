# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# uv binary from the official distroless image, pinned to a stable release + the
# multi-arch index digest (covers both linux/amd64 and linux/arm64 manifests).
# To update: pick a new release from https://github.com/astral-sh/uv/releases,
# then resolve its index digest with:
#   docker buildx imagetools inspect ghcr.io/astral-sh/uv:<new-version>
# and paste the reported "Digest:" value below.
COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Copy the source (.dockerignore keeps out .venv/.git/tests/caches).
COPY . /app/

# Create the project venv from the locked dependencies (uv.lock).
# NOTE: cross-building linux/amd64 on Apple Silicon runs this under qemu, where
# `uv sync` can segfault (exit 139). Build on a native amd64 host / CI runner,
# or fall back to `RUN uv pip install --system .` if you must emulate.
RUN uv sync --frozen --no-dev

# Drop root after the venv is built. Fixed uid/gid so volume/file ownership is
# reproducible across rebuilds; no login shell needed for --serve. HOME points
# at /app (already chowned below) instead of creating a separate home dir,
# since `uv run` needs a writable HOME for its cache dir even with --no-sync.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER 10001

EXPOSE 8000

# Liveness probe for the --serve HTTP endpoint: any HTTP response (including
# 400/406 from a GET with no session) counts as healthy — it proves the
# process is up and accepting connections; only a connection failure is
# unhealthy. curl isn't in this image, so probe with python3's urllib instead.
# NOTE: this HEALTHCHECK targets --serve on 127.0.0.1:8000/mcp. If you run this
# image in stdio mode instead (overriding CMD), the container will show
# unhealthy — expected, since this image is --serve-focused.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "import sys, urllib.request, urllib.error\ntry:\n    urllib.request.urlopen('http://127.0.0.1:8000/mcp', timeout=3)\nexcept urllib.error.HTTPError:\n    pass\nexcept Exception:\n    sys.exit(1)\n"]

# Central multi-user server (streamable-http, passthrough auth). Endpoint: /mcp
# `uv run --no-sync` uses the venv built above without re-resolving at start.
# The server holds no AWX credential — each request authenticates with the
# caller's own Bearer token. Override the CMD for --sse or a different host/port.
CMD ["uv", "run", "--no-sync", "awx-mcp", "--serve", "--host", "0.0.0.0", "--port", "8000"]
