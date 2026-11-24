# Temporal Workflow API Dockerfile
# foundry-temporal package is installed from pre-built wheels for better production patterns

FROM cgr.dev/chainguard/python:latest-dev AS base

# switch to root user
USER root


RUN apk update && apk add --no-cache ca-certificates curl \
    && /usr/sbin/update-ca-certificates \
    && mkdir -p /app \
    && addgroup -g 777 apiuser \
    && adduser -u 777 -G apiuser -h /home/apiuser -D apiuser

###

FROM base AS builder

# Copy uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=true \
    UV_LINK_MODE=copy \
    UV_NO_INSTALLER_METADATA=true \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-editable --no-install-project --no-install-workspace

# Copy source code
COPY src/ ./src/
COPY tests/ ./tests/
COPY README.md LICENSE NOTICE ./

# Install the project itself (not just its dependencies), so the running
# image has its own package metadata, so the app can report its version.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-editable

###

FROM base AS production

# Copy built application from builder
COPY --from=builder --chown=apiuser:apiuser /app /app

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    TEMPORAL_HOST=temporal-server:7233 \
    TEMPORAL_NAMESPACE=default \
    LOG_LEVEL=INFO

# Set ownership and switch to non-root user
RUN chown -R apiuser:apiuser /app
USER apiuser

WORKDIR /app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose API port
EXPOSE 8000

# Clear Chainguard base ENTRYPOINT so CMD runs directly
ENTRYPOINT []

# Start the API server
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
