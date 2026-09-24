# Spike S2: the environment app/ai/runner.py will run in.
# Build:  docker build -t markmik-s2 spikes/agent-sdk
# Run:    see docs/spikes/agent-sdk.md (token file mounted read-only, never baked in)
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /app

# Claude Code, pinned, as in ~/Projects/stock-oracle/Dockerfile. The auto-updater
# is off so the pin holds at runtime. NOTE: the claude-agent-sdk wheel bundles
# its own CLI and prefers it over PATH, so the runner must pass
# cli_path=CLAUDE_CLI_PATH for this pin to be the one that runs.
ARG CLAUDE_CODE_VERSION=2.1.280
ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CONFIG_DIR=/app/.claude-markmik \
    CLAUDE_CLI_PATH=/home/app/.local/bin/claude \
    PATH=/app/.venv/bin:/home/app/.local/bin:$PATH
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_CODE_VERSION" \
    && claude --version | grep -qF "$CLAUDE_CODE_VERSION" \
    # Own empty config dir, so nothing from a personal ~/.claude can reach the runner.
    && mkdir -p /app/.claude-markmik

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

ARG CLAUDE_AGENT_SDK_VERSION=0.2.159
RUN uv venv /app/.venv \
    && uv pip install --python /app/.venv/bin/python "claude-agent-sdk==$CLAUDE_AGENT_SDK_VERSION"

COPY --chown=app *.py ./
CMD ["python", "-c", "import claude_agent_sdk; print(claude_agent_sdk.__version__)"]
