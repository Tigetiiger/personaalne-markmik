# Browser code (Muya, FullCalendar, force-graph, fonts) is built with Node in its own stage;
# Node does not stay in the final image. One final image serves web and worker.
FROM node:24-alpine AS browser
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tools/ tools/
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
WORKDIR /app

# The Claude Agent SDK wheel ships its own Claude Code CLI and uses it ahead of anything on PATH
# (docs/spikes/agent-sdk.md), so uv.lock pins the CLI version; no separate install. The CLI gets
# its own config dir on tmpfs (compose), so nothing from a personal ~/.claude reaches the app's
# model calls and per-call transcripts do not pile up on disk.
ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CONFIG_DIR=/tmp/claude-markmik \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    PATH=/app/.venv/bin:$PATH \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

COPY --chown=app pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-cache
COPY --chown=app app/ app/
COPY --chown=app site/ site/
COPY --chown=app --from=browser /build/site/muya site/muya
COPY --chown=app --from=browser /build/site/vendor site/vendor
COPY --chown=app --from=browser /build/site/fonts site/fonts

ENV DATABASE_PATH=/data/markmik.db DATA_DIR=/data
EXPOSE 8000
# One worker: live-editing presence is kept in memory. Threads serve polling and SSE streams.
# No control socket: nothing uses gunicornc, and it would need a writable ~/.gunicorn on the
# read-only root (compose).
CMD ["gunicorn", "-w", "1", "--threads", "16", "-b", "0.0.0.0:8000", "--timeout", "120", "--no-control-socket", "app:create_app()"]
