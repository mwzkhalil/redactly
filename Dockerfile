# Single-origin image: Next.js is the front door, uvicorn is reachable only on
# loopback inside the container.
#
# One origin is a requirement rather than a convenience. `request_field_unmask`
# blocks until a human decides, and the owner queue long-polls, so the proxy has
# to tolerate minute-long requests. Splitting the app and API across origins
# would also force the session cookie off `SameSite=Strict`.

# Ports are build arguments, not just runtime settings. The viewer proxies the
# API through a Next.js rewrite whose destination is resolved when the bundle is
# built, so the API port has to be known at build time or the rewrite would point
# somewhere nothing is listening.
#
# Defaults sit above the crowded registered-service range and below 32768, where
# Linux starts allocating ephemeral source ports — a published port inside that
# range can collide with an outgoing connection.
ARG WEB_PORT=24680
ARG API_PORT=24681

# ---------------------------------------------------------------- web build ---
FROM node:22-bookworm-slim AS web

WORKDIR /build
ENV NEXT_TELEMETRY_DISABLED=1

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/ ./

# Declared here rather than at the top of the stage: an ARG invalidates every
# layer below it, and npm ci has no business being redone because a port moved.
ARG API_PORT
ENV REDACTLY_API_ORIGIN=http://127.0.0.1:${API_PORT}
RUN npm run build

# ------------------------------------------------------------------ runtime ---
FROM node:22-bookworm-slim

# curl is used by the entrypoint's readiness probe; libgomp1 is required by
# onnxruntime's CPU execution provider.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

# The node image already ships a `node` user at uid 1000, so reuse it rather
# than colliding with it.
ENV HOME=/home/node \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies before source, so editing application code does not reinstall.
COPY services/api/pyproject.toml services/api/uv.lock ./services/api/
RUN cd services/api && uv sync --frozen --no-install-project --no-dev

COPY services/api/ ./services/api/
COPY fixtures/ ./fixtures/
RUN cd services/api && uv sync --frozen --no-dev

ENV REDACTLY_DETECTOR=onnx \
    REDACTLY_MODEL_ID=gravitee-io/bert-small-pii-detection \
    REDACTLY_MODEL_CACHE_DIR=/opt/model-cache \
    REDACTLY_FIXTURES_DIR=/app/fixtures/synthetic \
    REDACTLY_DATABASE_PATH=/home/node/state/redactly.sqlite3 \
    REDACTLY_KEY_FILE=/home/node/state/master.key \
    REDACTLY_COOKIE_SECURE=1

# Bake the detector into the image. Downloading on first boot would make the
# first visitor wait on a model fetch, and would make the demo depend on the
# hub being reachable at runtime.
#
# This sits above the web bundle deliberately. Below it, every edit to a React
# component would invalidate this layer and send the build back to the hub for
# the weights, turning a one-line frontend fix into a full re-download.
RUN mkdir -p /opt/model-cache /home/node/state \
 && cd services/api \
 && uv run --no-dev python scripts/prefetch_model.py \
 && chown -R node:node /opt/model-cache /home/node

# Standalone omits static assets by design; they have to be placed beside the
# server bundle. There is no public/ directory in this app, so nothing else.
COPY --from=web /build/.next/standalone ./web/
COPY --from=web /build/.next/static ./web/.next/static
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Last, so that changing a port invalidates nothing expensive above it.
ARG WEB_PORT
ARG API_PORT
ENV PORT=${WEB_PORT} \
    API_PORT=${API_PORT} \
    HOSTNAME=0.0.0.0

USER node
EXPOSE ${WEB_PORT}

# The readiness probe is the real check: the container is only useful once the
# ONNX session has loaded and the fixtures are seeded. HEALTHCHECK is not one of
# the instructions Docker expands at build time, so $PORT is resolved by the
# shell at run time and tracks the runtime value.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/backend/health" || exit 1

CMD ["/usr/local/bin/entrypoint.sh"]
