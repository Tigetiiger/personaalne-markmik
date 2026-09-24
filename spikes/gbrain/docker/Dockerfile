# Sketch (spike S1). NOT built: Docker Hub DNS was down during the spike; the
# same entrypoint was tested on an offline-equivalent image (see docs/spikes/gbrain.md).
FROM oven/bun:1.4-debian
# gbrain sync shells out to git
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ARG GBRAIN_REF=6040075
USER bun
# pinned: with XDG_CACHE_HOME set, bun puts globals under $XDG_CACHE_HOME/.bun
ENV BUN_INSTALL=/home/bun/.bun
# github:garrytan/gbrain -- the npm package named "gbrain" is a different project
RUN bun install -g "github:garrytan/gbrain#${GBRAIN_REF}"
ENV PATH="/home/bun/.bun/bin:${PATH}" \
    GBRAIN_HOME=/data BRAIN_DIR=/brain \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/brain
COPY --chmod=755 entrypoint.sh /entrypoint.sh
EXPOSE 8083
ENTRYPOINT ["/entrypoint.sh"]
