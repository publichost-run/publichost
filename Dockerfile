# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv
# PUBLICHOST_HOST_KEY_PATH is a file path, not key material — safe as ENV.

# --- Build stage: install dependencies with uv ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Manifests first: this layer is cached until dependencies change
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Now the package itself. --no-editable: install a real wheel into the venv
# so the runtime stage needs only /app/.venv, not the sources.
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# --- Runtime stage ---
FROM python:3.12-slim-bookworm
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

# /data holds the SSH host key (mounted as a volume in compose).
# /app/weblogs holds WebLogs CSV files (mounted as a volume in compose).
# Named volumes inherit this ownership on first mount.
# /app itself must be owned by app too: celery beat's default schedule file
# (celerybeat-schedule) is written relative to the CWD, which is /app.
RUN mkdir -p /data /app/weblogs && chown app:app /data /app /app/weblogs

ENV PATH="/app/.venv/bin:$PATH" \
    PUBLICHOST_SSH_HOST=0.0.0.0 \
    PUBLICHOST_SSH_PORT=2222 \
    PUBLICHOST_HTTP_HOST=0.0.0.0 \
    PUBLICHOST_HTTP_PORT=8080 \
    PUBLICHOST_HOST_KEY_PATH=/data/host_key \
    PUBLICHOST_WEBLOGS_PATH=/app/weblogs

USER app
EXPOSE 2222 8080 8000
CMD ["publichost"]
