# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# uv binary from the official distroless image (pinned tag recommended in prod).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

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

EXPOSE 8000

# Central multi-user server (streamable-http, passthrough auth). Endpoint: /mcp
# `uv run --no-sync` uses the venv built above without re-resolving at start.
# The server holds no AWX credential — each request authenticates with the
# caller's own Bearer token. Override the CMD for --sse or a different host/port.
CMD ["uv", "run", "--no-sync", "awx-mcp", "--serve", "--host", "0.0.0.0", "--port", "8000"]
